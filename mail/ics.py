"""Minimal vCard 3.0 and iCalendar (VEVENT) serialization + parsing.

Enough to round-trip the fields we store for CardDAV/CalDAV sync; not a full
RFC 6350/5545 implementation.
"""
import re
from datetime import datetime
from datetime import timezone as dt_tz

from django.utils import timezone

_DTUTC = "%Y%m%dT%H%M%SZ"
_DTLOCAL = "%Y%m%dT%H%M%S"
_DATE = "%Y%m%d"


# --- line folding -----------------------------------------------------------
def fold(line: str) -> str:
    out, b = [], line.encode("utf-8")
    while len(b) > 73:
        out.append(b[:73].decode("utf-8", "ignore"))
        b = b[73:]
    out.append(b.decode("utf-8", "ignore"))
    return "\r\n ".join(out)


def unfold(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _props(text: str):
    """Yield (name, params, value) for each content line."""
    for line in unfold(text):
        if not line or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *params = head.split(";")
        yield name.upper(), params, value


# --- datetimes --------------------------------------------------------------
def fmt_dt(dt: datetime, all_day: bool) -> str:
    if all_day:
        return dt.strftime(_DATE)
    return dt.astimezone(dt_tz.utc).strftime(_DTUTC)


def parse_dt(value: str):
    """Return (datetime, is_all_day)."""
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value, _DATE)
        return timezone.make_aware(d, dt_tz.utc), True
    if value.endswith("Z"):
        return timezone.make_aware(datetime.strptime(value, _DTUTC), dt_tz.utc), False
    if re.fullmatch(r"\d{8}T\d{6}", value):
        return timezone.make_aware(datetime.strptime(value, _DTLOCAL), dt_tz.utc), False
    # Fallback: now.
    return timezone.now(), False


def _esc(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;") \
        .replace(",", "\\,").replace("\n", "\\n")


def _unesc(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


# --- vCard ------------------------------------------------------------------
def to_vcard(contact) -> str:
    last_first = ";".join((contact.name or "").split(" ", 1)[::-1]) or contact.name
    lines = [
        "BEGIN:VCARD", "VERSION:3.0", f"UID:{contact.uid}",
        f"FN:{_esc(contact.name or contact.email)}",
        f"N:{_esc(last_first)};;;",
        f"EMAIL;TYPE=INTERNET:{contact.email}",
    ]
    if contact.organization:
        lines.append(f"ORG:{_esc(contact.organization)}")
    if contact.phone:
        lines.append(f"TEL:{_esc(contact.phone)}")
    lines.append("END:VCARD")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def parse_vcard(text: str) -> dict:
    out = {"uid": "", "name": "", "email": "", "organization": "", "phone": ""}
    for name, _params, value in _props(text):
        if name == "UID":
            out["uid"] = value.strip()
        elif name == "FN":
            out["name"] = _unesc(value)
        elif name == "EMAIL" and not out["email"]:
            out["email"] = value.strip()
        elif name == "ORG":
            out["organization"] = _unesc(value).replace(";", " ").strip()
        elif name == "TEL":
            out["phone"] = _unesc(value)
    return out


# --- iCalendar --------------------------------------------------------------
def to_ical(event) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//django-mailserver//EN",
             "CALSCALE:GREGORIAN", "BEGIN:VEVENT", f"UID:{event.uid}",
             f"DTSTAMP:{fmt_dt(event.updated_at, False)}",
             f"SEQUENCE:{event.sequence}"]
    if event.all_day:
        lines.append(f"DTSTART;VALUE=DATE:{fmt_dt(event.dtstart, True)}")
        if event.dtend:
            lines.append(f"DTEND;VALUE=DATE:{fmt_dt(event.dtend, True)}")
    else:
        lines.append(f"DTSTART:{fmt_dt(event.dtstart, False)}")
        if event.dtend:
            lines.append(f"DTEND:{fmt_dt(event.dtend, False)}")
    if event.summary:
        lines.append(f"SUMMARY:{_esc(event.summary)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_esc(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_esc(event.location)}")
    if event.rrule:
        lines.append(f"RRULE:{event.rrule}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def parse_ical(text: str) -> dict:
    out = {"uid": "", "summary": "", "description": "", "location": "",
           "rrule": "", "dtstart": None, "dtend": None, "all_day": False}
    for name, _params, value in _props(text):
        if name == "UID":
            out["uid"] = value.strip()
        elif name == "SUMMARY":
            out["summary"] = _unesc(value)
        elif name == "DESCRIPTION":
            out["description"] = _unesc(value)
        elif name == "LOCATION":
            out["location"] = _unesc(value)
        elif name == "RRULE":
            out["rrule"] = value.strip()
        elif name == "DTSTART":
            out["dtstart"], out["all_day"] = parse_dt(value)
        elif name == "DTEND":
            out["dtend"], _ = parse_dt(value)
    if out["dtstart"] is None:
        out["dtstart"] = timezone.now()
    return out
