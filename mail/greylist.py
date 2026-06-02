"""Greylisting: defer the first delivery attempt from an unseen triplet.

Legitimate senders retry after a short delay (and are then allowed and
remembered); much spam never retries. Triplet = (client IP, envelope sender,
recipient).
"""
import hashlib
import logging

from django.conf import settings
from django.utils import timezone

from .models import GreylistEntry

log = logging.getLogger("mail")

# Minimum wait before a retry is accepted, and how long an accepted triplet is
# trusted without re-greylisting.
DELAY = getattr(settings, "GREYLIST_DELAY_SECONDS", 60)
TTL_DAYS = 36


def _key(ip: str, mail_from: str, rcpt: str) -> str:
    raw = f"{ip}|{(mail_from or '').lower()}|{(rcpt or '').lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def should_defer(ip: str, mail_from: str, rcpts) -> bool:
    """Return True if the message should be temporarily refused (451).

    Defers only when *every* recipient triplet is still in its greylist window;
    once any triplet is accepted the message goes through.
    """
    if not ip:
        return False  # no client IP (e.g. local testing) — don't greylist

    now = timezone.now()
    any_allowed = False
    for rcpt in rcpts or [""]:
        entry, created = GreylistEntry.objects.get_or_create(key=_key(ip, mail_from, rcpt))
        if created:
            continue  # first sighting → stays deferred unless another rcpt is allowed
        if entry.accepted:
            any_allowed = True
            continue
        entry.attempts += 1
        if (now - entry.first_seen).total_seconds() >= DELAY:
            entry.accepted = True
            any_allowed = True
        entry.save(update_fields=["attempts", "accepted", "last_seen"])

    return not any_allowed
