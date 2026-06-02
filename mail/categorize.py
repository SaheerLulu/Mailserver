"""Heuristic inbox categorization (Gmail-style tabs).

Best-effort classification of an incoming message into one of the Message
categories. Deterministic and offline.
"""
import re

from .models import Message

SOCIAL_DOMAINS = (
    "facebook.com", "facebookmail.com", "twitter.com", "x.com", "linkedin.com",
    "instagram.com", "pinterest.com", "tiktok.com", "youtube.com", "reddit.com",
    "discord.com", "meetup.com", "nextdoor.com",
)
_PROMO_RE = re.compile(
    r"\b(sale|discount|% off|coupon|deal|offer|save now|limited time|"
    r"unsubscribe|newsletter|promo|black friday|clearance)\b", re.IGNORECASE)
_UPDATE_RE = re.compile(
    r"\b(receipt|invoice|order|confirmation|confirmed|shipped|tracking|"
    r"statement|notification|verify|password|security alert|reminder)\b",
    re.IGNORECASE)


def categorize(parsed: dict) -> str:
    from_addr = (parsed.get("from_addr") or "").lower()
    domain = from_addr.split("@")[-1].strip(">") if "@" in from_addr else ""
    subject = parsed.get("subject", "") or ""
    body = parsed.get("body_text", "") or ""
    haystack = f"{subject} {body}"
    bulk = (parsed.get("precedence", "").lower() in ("bulk", "list", "junk"))

    if domain and any(domain == d or domain.endswith("." + d) for d in SOCIAL_DOMAINS):
        return Message.Category.SOCIAL
    if parsed.get("list_id"):
        # Mailing-list traffic: promotions if it reads promotional, else forums.
        return (Message.Category.PROMOTIONS if _PROMO_RE.search(haystack)
                else Message.Category.FORUMS)
    if _PROMO_RE.search(haystack) or bulk:
        return Message.Category.PROMOTIONS
    if _UPDATE_RE.search(haystack) or from_addr.startswith(("no-reply", "noreply", "notifications")):
        return Message.Category.UPDATES
    return Message.Category.PRIMARY
