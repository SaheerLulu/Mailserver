"""Turn a raw RFC 5322 message into the fields we store."""
from email import message_from_bytes
from email.policy import default as default_policy
from email.utils import getaddresses, parsedate_to_datetime

from django.utils import timezone


def _addr_list(msg, header):
    values = msg.get_all(header, [])
    return ", ".join(addr for _, addr in getaddresses(values) if addr)


def parse_message(raw: bytes) -> dict:
    """Parse ``raw`` into a dict of header/body fields + a list of attachments."""
    msg = message_from_bytes(raw, policy=default_policy)

    # --- Date -----------------------------------------------------------
    date = None
    if msg["Date"]:
        try:
            date = parsedate_to_datetime(msg["Date"])
        except (TypeError, ValueError):
            date = None
    if date is None:
        date = timezone.now()
    elif date.tzinfo is None:
        date = timezone.make_aware(date, timezone.get_default_timezone())

    # --- Body + attachments --------------------------------------------
    body_text, body_html = "", ""
    attachments = []

    if msg.is_multipart() or msg.get_content_maintype() != "text":
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = part.get_content_disposition()
            ctype = part.get_content_type()
            if disposition == "attachment" or (part.get_filename() and disposition != "inline"):
                payload = part.get_payload(decode=True) or b""
                attachments.append({
                    "filename": part.get_filename() or "attachment",
                    "content_type": ctype,
                    "content": payload,
                })
            elif ctype == "text/plain" and not body_text:
                body_text = part.get_content()
            elif ctype == "text/html" and not body_html:
                body_html = part.get_content()
    else:
        content = msg.get_content()
        if msg.get_content_type() == "text/html":
            body_html = content
        else:
            body_text = content

    return {
        "message_id": (msg["Message-ID"] or "").strip(),
        "in_reply_to": (msg["In-Reply-To"] or "").strip(),
        "references": (msg["References"] or "").strip(),
        "auto_submitted": (msg["Auto-Submitted"] or "").strip(),
        "list_id": (msg["List-Id"] or "").strip(),
        "precedence": (msg["Precedence"] or "").strip(),
        "has_dkim_header": msg["DKIM-Signature"] is not None,
        "from_addr": (msg["From"] or "").strip(),
        "to_addrs": _addr_list(msg, "To"),
        "cc_addrs": _addr_list(msg, "Cc"),
        "subject": (msg["Subject"] or "").strip(),
        "date": date,
        "body_text": body_text or "",
        "body_html": body_html or "",
        "attachments": attachments,
    }
