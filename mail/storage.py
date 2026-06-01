"""Database-facing operations used by the SMTP service and the webmail views.

These functions are plain synchronous Django ORM code. The async SMTP handlers
call them through ``asgiref.sync.sync_to_async``.
"""
import logging

from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename

from . import parsing
from .models import Alias, Domain, Mailbox, Message

log = logging.getLogger("mail")


def normalize(address: str) -> str:
    return (address or "").strip().lower()


def is_local_domain(domain: str) -> bool:
    return Domain.objects.filter(name=domain, is_active=True).exists()


def is_deliverable(address: str) -> bool:
    """Whether we accept mail for ``address`` (mailbox, alias, or catch-all)."""
    address = normalize(address)
    if "@" not in address:
        return False
    domain = address.split("@", 1)[1]
    if not is_local_domain(domain):
        return False
    return (
        Mailbox.objects.filter(email=address, is_active=True).exists()
        or Alias.objects.filter(source=address, is_active=True).exists()
        or Alias.objects.filter(source=f"@{domain}", is_active=True).exists()
    )


def resolve_recipients(address: str, _seen=None):
    """Expand ``address`` to a list of ``("local", Mailbox)`` / ``("remote", str)``.

    Aliases (including ``@domain`` catch-alls) are followed recursively with a
    loop guard.
    """
    address = normalize(address)
    if _seen is None:
        _seen = set()
    if address in _seen or "@" not in address:
        return []
    _seen.add(address)

    domain = address.split("@", 1)[1]
    if not is_local_domain(domain):
        return [("remote", address)]

    results = []
    mailbox = Mailbox.objects.filter(email=address, is_active=True).first()
    if mailbox:
        results.append(("local", mailbox))

    aliases = list(Alias.objects.filter(source=address, is_active=True))
    if not aliases:
        aliases = list(Alias.objects.filter(source=f"@{domain}", is_active=True))
    for alias in aliases:
        results.extend(resolve_recipients(alias.destination, _seen))
    return results


def authenticate(email: str, password: str):
    mailbox = Mailbox.objects.filter(email=normalize(email), is_active=True).first()
    if mailbox and mailbox.check_password(password):
        return mailbox
    return None


def store_to_mailbox(mailbox, raw: bytes, folder=Message.Folder.INBOX,
                     parsed=None, mark_read=False) -> Message:
    """Persist a message (and its attachments) into ``mailbox``/``folder``."""
    parsed = parsed or parsing.parse_message(raw)
    msg = Message(
        mailbox=mailbox,
        folder=folder,
        message_id=parsed["message_id"][:998],
        in_reply_to=parsed["in_reply_to"][:998],
        from_addr=parsed["from_addr"][:998],
        to_addrs=parsed["to_addrs"],
        cc_addrs=parsed["cc_addrs"],
        subject=parsed["subject"][:998],
        date=parsed["date"],
        body_text=parsed["body_text"],
        body_html=parsed["body_html"],
        size=len(raw),
        is_read=mark_read,
    )
    base = (parsed["message_id"].strip("<>") or "message").split("@")[0]
    msg.eml.save(get_valid_filename(f"{base}.eml"), ContentFile(raw), save=False)
    msg.save()

    for att in parsed["attachments"]:
        attachment = msg.attachments.create(
            filename=att["filename"][:255],
            content_type=att["content_type"][:255],
            size=len(att["content"]),
        )
        attachment.file.save(get_valid_filename(att["filename"]),
                             ContentFile(att["content"]), save=True)
    return msg


def handle_inbound(rcpt_tos, raw: bytes) -> int:
    """Deliver an inbound message to every resolved local recipient."""
    from . import delivery  # lazy: avoid import cycle

    parsed = parsing.parse_message(raw)
    delivered = 0
    for rcpt in rcpt_tos:
        for kind, target in resolve_recipients(rcpt):
            if kind == "local":
                store_to_mailbox(target, raw, Message.Folder.INBOX, parsed)
                delivered += 1
            else:
                # Alias forwarding to a remote address; use a null envelope
                # sender to avoid generating backscatter.
                delivery.relay("", [target], raw)
    if delivered == 0:
        log.warning("Inbound message for %s had no local recipients", rcpt_tos)
    return delivered


def handle_submission(auth_login, mail_from, rcpt_tos, raw: bytes):
    """An authenticated user is sending mail."""
    from . import delivery  # lazy: avoid import cycle

    mailbox = Mailbox.objects.filter(email=normalize(auth_login)).first()
    delivery.send_outbound(mailbox, mail_from, rcpt_tos, raw, store_sent=True)
