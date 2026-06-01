"""Database-facing operations used by the SMTP service and the webmail views.

Plain synchronous Django ORM code; the async SMTP handlers call these through
``asgiref.sync.sync_to_async``.
"""
import logging
import re
import uuid

from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename

from . import parsing, rules
from .models import Alias, Domain, Label, Mailbox, Message

log = logging.getLogger("mail")

_MSGID_RE = re.compile(r"<[^>]+>")
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fwd|fw|aw|sv)\s*:\s*", re.IGNORECASE)


def normalize(address: str) -> str:
    return (address or "").strip().lower()


def is_local_domain(domain: str) -> bool:
    return Domain.objects.filter(name=domain, is_active=True).exists()


def is_deliverable(address: str) -> bool:
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


# --- Conversation threading -------------------------------------------------
def _normalize_subject(subject: str) -> str:
    prev = None
    s = subject or ""
    while s != prev:
        prev = s
        s = _SUBJECT_PREFIX_RE.sub("", s)
    return s.strip().lower()


def resolve_thread_id(mailbox, parsed: dict) -> str:
    refs = _MSGID_RE.findall(
        f"{parsed.get('in_reply_to', '')} {parsed.get('references', '')}")
    if refs:
        existing = (Message.objects.filter(mailbox=mailbox, message_id__in=refs)
                    .exclude(thread_id="").first())
        if existing:
            return existing.thread_id
    if parsed.get("message_id"):
        return parsed["message_id"][:255]
    subject = _normalize_subject(parsed.get("subject", ""))
    return (f"subj:{subject}"[:255]) if subject else f"thread:{uuid.uuid4()}"


# --- Persisting -------------------------------------------------------------
def store_to_mailbox(mailbox, raw: bytes, folder=Message.Folder.INBOX,
                     parsed=None, mark_read=False, spam_score=0.0,
                     is_spam=False, star=False, label_names=None) -> Message:
    parsed = parsed or parsing.parse_message(raw)
    msg = Message(
        mailbox=mailbox,
        folder=folder,
        message_id=parsed["message_id"][:998],
        in_reply_to=parsed["in_reply_to"][:998],
        references=parsed.get("references", ""),
        thread_id=resolve_thread_id(mailbox, parsed),
        from_addr=parsed["from_addr"][:998],
        to_addrs=parsed["to_addrs"],
        cc_addrs=parsed["cc_addrs"],
        subject=parsed["subject"][:998],
        date=parsed["date"],
        body_text=parsed["body_text"],
        body_html=parsed["body_html"],
        size=len(raw),
        spam_score=spam_score,
        is_spam=is_spam,
        is_read=mark_read,
        is_flagged=star,
    )
    base = (parsed["message_id"].strip("<>") or "message").split("@")[0]
    msg.eml.save(get_valid_filename(f"{base}.eml"), ContentFile(raw), save=False)
    msg.save()

    for name in (label_names or []):
        label, _ = Label.objects.get_or_create(mailbox=mailbox, name=name[:64])
        msg.labels.add(label)

    for att in parsed["attachments"]:
        attachment = msg.attachments.create(
            filename=att["filename"][:255],
            content_type=att["content_type"][:255],
            size=len(att["content"]),
        )
        attachment.file.save(get_valid_filename(att["filename"]),
                             ContentFile(att["content"]), save=True)
    return msg


def deposit(mailbox, raw: bytes, parsed=None, spam_score=0.0, allow_spam=True,
            force_spam=False) -> Message:
    """Run a mailbox's filters + spam routing, then store the message."""
    parsed = parsed or parsing.parse_message(raw)
    decision = rules.apply_filters(mailbox, parsed)

    is_spam = decision["is_spam"] or force_spam
    if allow_spam and spam_score >= mailbox.spam_threshold:
        is_spam = True
    folder = Message.Folder.JUNK if is_spam else decision["folder"]

    return store_to_mailbox(
        mailbox, raw, folder=folder, parsed=parsed,
        mark_read=decision["mark_read"], star=decision["star"],
        spam_score=spam_score, is_spam=is_spam, label_names=decision["labels"],
    )


# --- SMTP entry points ------------------------------------------------------
def handle_inbound(rcpt_tos, raw: bytes, peer_ip="", mail_from="", helo="") -> int:
    """Score, filter and deliver an inbound message to local recipients."""
    from . import antivirus, delivery, spam  # lazy: avoid import cycle

    parsed = parsing.parse_message(raw)
    score, reasons = spam.score_message(parsed, raw, peer_ip, mail_from, helo)

    # Virus scan (best-effort): an infected message is force-routed to Junk.
    clean, signature = antivirus.scan(raw)
    force_spam = False
    if not clean:
        score += 100.0
        force_spam = True
        reasons.append(f"virus: {signature}")
    if reasons:
        log.info("spam score %.2f for <%s> (%s)", score, mail_from, "; ".join(reasons))

    delivered = 0
    for rcpt in rcpt_tos:
        for kind, target in resolve_recipients(rcpt):
            if kind == "local":
                msg = deposit(target, raw, parsed, spam_score=score,
                              force_spam=force_spam)
                delivered += 1
                if not msg.is_spam:
                    _maybe_autoreply(target, parsed, mail_from)
            else:
                delivery.enqueue(None, "", [target], raw)  # alias forward
    if delivered == 0:
        log.warning("Inbound message for %s had no local recipients", rcpt_tos)
    return delivered


def handle_submission(auth_login, mail_from, rcpt_tos, raw: bytes):
    from . import delivery  # lazy: avoid import cycle

    mailbox = Mailbox.objects.filter(email=normalize(auth_login)).first()
    delivery.send_outbound(mailbox, mail_from, rcpt_tos, raw, store_sent=True)


def _maybe_autoreply(mailbox, parsed, mail_from):
    """Send a vacation auto-reply, guarding against loops/bulk mail."""
    if not mailbox.vacation_enabled or not mailbox.vacation_message:
        return
    sender = normalize(mail_from)
    if not sender or sender == normalize(mailbox.email):
        return
    if parsed.get("auto_submitted") and parsed["auto_submitted"].lower() != "no":
        return
    if parsed.get("list_id") or parsed.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return
    from . import delivery
    delivery.send_autoreply(mailbox, sender)
