"""Outbound mail: DKIM signing, MX lookup and SMTP relay.

This is the "sending MTA" half of the server, implemented in Python. Local
recipients are stored straight into their mailbox; remote recipients are
delivered by connecting to their domain's MX hosts on port 25.
"""
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import dns.resolver
from django.conf import settings

from . import dkimtools
from .models import Domain, Message

log = logging.getLogger("mail")


def domain_of(address: str) -> str:
    return address.split("@", 1)[1].lower() if "@" in (address or "") else ""


def split_addresses(value: str):
    if not value:
        return []
    return [a.strip() for a in value.replace(";", ",").split(",") if a.strip()]


def sign_for_sender(mail_from: str, raw: bytes) -> bytes:
    """DKIM-sign ``raw`` using the sending domain's key, if one exists."""
    domain = Domain.objects.filter(name=domain_of(mail_from)).first()
    if domain and domain.has_dkim:
        try:
            return dkimtools.sign(
                raw, domain.name,
                domain.dkim_selector or settings.DKIM_SELECTOR,
                domain.dkim_private_key,
            )
        except Exception as exc:  # noqa: BLE001 - never block delivery on signing
            log.warning("DKIM signing failed for %s: %s", domain.name, exc)
    return raw


def mx_hosts(domain: str):
    """Ordered list of mail exchangers for ``domain`` (falls back to the A record)."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        ranked = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        return [host for _, host in ranked] or [domain]
    except Exception as exc:  # noqa: BLE001
        log.info("No MX for %s (%s); falling back to A record", domain, exc)
        return [domain]


def relay(mail_from: str, rcpts, raw: bytes):
    """Deliver ``raw`` to remote recipients, grouped and tried per MX host."""
    by_domain = {}
    for rcpt in rcpts:
        by_domain.setdefault(domain_of(rcpt), []).append(rcpt)

    for domain, domain_rcpts in by_domain.items():
        if not domain:
            continue
        for host in mx_hosts(domain):
            try:
                with smtplib.SMTP(host, 25, timeout=30) as client:
                    client.ehlo(settings.MAIL_SERVER_HOSTNAME)
                    if client.has_extn("starttls"):
                        client.starttls()
                        client.ehlo(settings.MAIL_SERVER_HOSTNAME)
                    client.sendmail(mail_from or "", domain_rcpts, raw)
                log.info("Relayed to %s via %s", domain_rcpts, host)
                break
            except Exception as exc:  # noqa: BLE001 - try the next MX
                log.warning("Relay to %s via %s failed: %s", domain, host, exc)
        else:
            log.error("Delivery to %s failed on all MX hosts", domain_rcpts)


def send_outbound(sender_mailbox, mail_from, rcpts, raw: bytes, store_sent=True):
    """Sign, optionally file in Sent, then deliver to local + remote recipients."""
    from . import storage  # lazy: avoid import cycle

    signed = sign_for_sender(mail_from, raw)

    if store_sent and sender_mailbox is not None:
        storage.store_to_mailbox(sender_mailbox, signed, Message.Folder.SENT,
                                 mark_read=True)

    remote = []
    for rcpt in rcpts:
        for kind, target in storage.resolve_recipients(rcpt):
            if kind == "local":
                storage.store_to_mailbox(target, signed, Message.Folder.INBOX)
            else:
                remote.append(target)
    if remote:
        relay(mail_from, remote, signed)


def send_from_webmail(mailbox, data: dict):
    """Build a message from the compose form and send it."""
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
    em.set_content(data.get("body", ""))

    send_outbound(mailbox, mailbox.email, to_list + cc_list, em.as_bytes(),
                  store_sent=True)
