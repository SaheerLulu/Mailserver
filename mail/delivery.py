"""Outbound mail: DKIM signing, a persistent retry queue, MX lookup and relay.

Submitted/forwarded mail destined for remote domains is written to the
``OutboundMessage`` queue and delivered by the ``runqueue`` worker, which
retries with exponential backoff. Local recipients are delivered immediately.
"""
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import dns.resolver
from django.conf import settings
from django.core.files.base import ContentFile

from . import dkimtools
from .models import Domain, Message, OutboundMessage

log = logging.getLogger("mail")


def domain_of(address: str) -> str:
    return address.split("@", 1)[1].lower() if "@" in (address or "") else ""


def split_addresses(value: str):
    if not value:
        return []
    return [a.strip() for a in value.replace(";", ",").split(",") if a.strip()]


def sign_for_sender(mail_from: str, raw: bytes) -> bytes:
    domain = Domain.objects.filter(name=domain_of(mail_from)).first()
    if domain and domain.has_dkim:
        try:
            return dkimtools.sign(
                raw, domain.name,
                domain.dkim_selector or settings.DKIM_SELECTOR,
                domain.dkim_private_key,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("DKIM signing failed for %s: %s", domain.name, exc)
    return raw


def mx_hosts(domain: str):
    try:
        answers = dns.resolver.resolve(domain, "MX")
        ranked = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        return [host for _, host in ranked] or [domain]
    except Exception as exc:  # noqa: BLE001
        log.info("No MX for %s (%s); falling back to A record", domain, exc)
        return [domain]


def _smtp_send(host: str, mail_from: str, rcpts, raw: bytes):
    """Deliver to a single MX host. Raises on failure."""
    with smtplib.SMTP(host, 25, timeout=30) as client:
        client.ehlo(settings.MAIL_SERVER_HOSTNAME)
        if client.has_extn("starttls"):
            client.starttls()
            client.ehlo(settings.MAIL_SERVER_HOSTNAME)
        client.sendmail(mail_from or "", rcpts, raw)


def attempt_delivery(mail_from: str, rcpts, raw: bytes):
    """Try to deliver to all recipients. Returns ``(ok: bool, error: str)``."""
    by_domain = {}
    for rcpt in rcpts:
        by_domain.setdefault(domain_of(rcpt), []).append(rcpt)

    errors = []
    for domain, domain_rcpts in by_domain.items():
        if not domain:
            continue
        delivered = False
        last_error = "no MX hosts"
        for host in mx_hosts(domain):
            try:
                _smtp_send(host, mail_from, domain_rcpts, raw)
                log.info("Relayed to %s via %s", domain_rcpts, host)
                delivered = True
                break
            except Exception as exc:  # noqa: BLE001 - try next MX
                last_error = f"{host}: {exc}"
                log.warning("Relay to %s via %s failed: %s", domain, host, exc)
        if not delivered:
            errors.append(f"{domain}: {last_error}")

    return (not errors, "; ".join(errors))


def enqueue(sender_mailbox, mail_from: str, rcpts, raw: bytes,
            send_at=None, scheduled=False) -> OutboundMessage:
    """Add a message to the outbound delivery queue.

    ``send_at`` delays the first attempt (scheduled send / undo-send hold);
    ``scheduled`` marks rows the worker must *finalize* (Sent copy + local
    delivery + relay) rather than just relay.
    """
    from django.utils import timezone
    om = OutboundMessage(
        sender_mailbox=sender_mailbox,
        mail_from=mail_from or "",
        recipients=", ".join(rcpts),
        next_attempt=send_at or timezone.now(),
        scheduled=scheduled,
    )
    om.raw.save(f"out-{make_msgid()[1:20]}.eml", ContentFile(raw), save=False)
    om.save()
    log.info("Queued outbound message %s to %s (send_at=%s)", om.pk, rcpts, send_at)
    return om


def schedule_send(mailbox, mail_from, rcpts, raw: bytes, send_at, scheduled=True):
    """Sign and queue a message for delivery at ``send_at`` (whole-message)."""
    signed = sign_for_sender(mail_from, raw)
    return enqueue(mailbox, mail_from, list(rcpts), signed,
                   send_at=send_at, scheduled=scheduled)


def send_outbound(sender_mailbox, mail_from, rcpts, raw: bytes, store_sent=True):
    """Sign, file in Sent, deliver local recipients now, queue remote ones."""
    from . import storage  # lazy: avoid import cycle

    signed = sign_for_sender(mail_from, raw)

    if store_sent and sender_mailbox is not None:
        storage.store_to_mailbox(sender_mailbox, signed, Message.Folder.SENT,
                                 mark_read=True)

    remote = []
    for rcpt in rcpts:
        for kind, target in storage.resolve_recipients(rcpt):
            if kind == "local":
                storage.deposit(target, signed, allow_spam=False)
            else:
                remote.append(target)
    if remote:
        enqueue(sender_mailbox, mail_from, remote, signed)


def build_message(mailbox, data: dict) -> bytes:
    """Build an RFC 5322 message (text, optionally with an HTML alternative)."""
    to_list = split_addresses(data.get("to", ""))
    cc_list = split_addresses(data.get("cc", ""))

    em = EmailMessage()
    em["From"] = mailbox.email
    em["To"] = ", ".join(to_list)
    if cc_list:
        em["Cc"] = ", ".join(cc_list)
    em["Subject"] = data.get("subject", "")
    em["Date"] = formatdate(localtime=True)
    em["Message-ID"] = make_msgid(domain=domain_of(mailbox.email))
    if data.get("in_reply_to"):
        em["In-Reply-To"] = data["in_reply_to"]
        em["References"] = data.get("references") or data["in_reply_to"]
    em.set_content(data.get("body", ""))
    if data.get("body_html"):
        em.add_alternative(data["body_html"], subtype="html")
    return em.as_bytes()


def send_from_webmail(mailbox, data: dict):
    """Send now, schedule for later, or hold for an undo window.

    Returns the OutboundMessage when the send was deferred (scheduled/undo) so
    the caller can offer a cancel/undo action, else None.
    """
    to_list = split_addresses(data.get("to", ""))
    cc_list = split_addresses(data.get("cc", ""))
    rcpts = to_list + cc_list
    raw = build_message(mailbox, data)

    send_at = data.get("send_at")            # datetime or ISO string → scheduled
    if isinstance(send_at, str):
        from datetime import datetime
        from django.utils import timezone
        try:
            dt = datetime.fromisoformat(send_at)
            send_at = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            send_at = None
    if not send_at and data.get("undo_seconds"):
        from datetime import timedelta
        from django.utils import timezone
        send_at = timezone.now() + timedelta(seconds=int(data["undo_seconds"]))

    if send_at:
        return schedule_send(mailbox, mailbox.email, rcpts, raw, send_at)

    send_outbound(mailbox, mailbox.email, rcpts, raw, store_sent=True)
    return None


def send_autoreply(mailbox, to_addr: str):
    em = EmailMessage()
    em["From"] = mailbox.email
    em["To"] = to_addr
    em["Subject"] = mailbox.vacation_subject or "Out of office"
    em["Date"] = formatdate(localtime=True)
    em["Message-ID"] = make_msgid(domain=domain_of(mailbox.email))
    em["Auto-Submitted"] = "auto-replied"
    em.set_content(mailbox.vacation_message)

    signed = sign_for_sender(mailbox.email, em.as_bytes())
    if domain_of(to_addr) and not _is_local(to_addr):
        enqueue(mailbox, mailbox.email, [to_addr], signed)
    else:
        from . import storage
        for kind, target in storage.resolve_recipients(to_addr):
            if kind == "local":
                storage.deposit(target, signed, allow_spam=False)


def _is_local(address: str) -> bool:
    from . import storage
    return storage.is_local_domain(domain_of(address))
