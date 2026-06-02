"""Gmail-style search query parsing.

Supports operators: from:, to:, subject:, label:, in:<folder>, category:<cat>,
is:unread|read|starred|important|muted, has:attachment, before:YYYY-MM-DD,
after:YYYY-MM-DD, plus free-text (matched against subject/body/from/to).
Quote multi-word values: subject:"status report".
"""
import re
import shlex
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from .models import Message

_OP_RE = re.compile(r"^(?P<op>from|to|subject|label|in|category|is|has|before|after):(?P<val>.*)$",
                    re.IGNORECASE)


def _parse_date(value):
    try:
        return timezone.make_aware(datetime.strptime(value, "%Y-%m-%d"))
    except (ValueError, TypeError):
        return None


def build_query(text: str):
    """Return ``(Q, folder_or_None)`` for a search string.

    ``folder`` is returned separately so the caller can decide scope (e.g. an
    ``in:`` operator narrows folders; otherwise everything except Trash/Spam).
    """
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()

    q = Q()
    folder = None
    free_terms = []

    for token in tokens:
        m = _OP_RE.match(token)
        if not m:
            free_terms.append(token)
            continue
        op, val = m.group("op").lower(), m.group("val").strip('"')
        if not val:
            continue
        if op == "from":
            q &= Q(from_addr__icontains=val)
        elif op == "to":
            q &= Q(to_addrs__icontains=val) | Q(cc_addrs__icontains=val)
        elif op == "subject":
            q &= Q(subject__icontains=val)
        elif op == "label":
            q &= Q(labels__name__iexact=val)
        elif op == "in":
            up = val.upper()
            if up in Message.Folder.values:
                folder = up
        elif op == "category":
            up = val.upper()
            if up in Message.Category.values:
                q &= Q(category=up)
        elif op == "has" and val.lower() in ("attachment", "attachments", "file"):
            q &= Q(attachments__isnull=False)
        elif op == "before":
            d = _parse_date(val)
            if d:
                q &= Q(date__lt=d)
        elif op == "after":
            d = _parse_date(val)
            if d:
                q &= Q(date__gte=d)
        elif op == "is":
            flag = val.lower()
            if flag == "unread":
                q &= Q(is_read=False)
            elif flag == "read":
                q &= Q(is_read=True)
            elif flag in ("starred", "flagged"):
                q &= Q(is_flagged=True)
            elif flag == "important":
                q &= Q(is_important=True)
            elif flag == "muted":
                q &= Q(is_muted=True)
            elif flag == "spam":
                q &= Q(is_spam=True)
        else:
            free_terms.append(token)

    for term in free_terms:
        q &= (Q(subject__icontains=term) | Q(body_text__icontains=term)
              | Q(from_addr__icontains=term) | Q(to_addrs__icontains=term))

    return q, folder
