"""Server-side filter engine: apply a mailbox's rules to an incoming message."""
import logging
import re

from .models import Filter, Message

log = logging.getLogger("mail")


def _haystack(parsed: dict, field: str) -> str:
    if field == Filter.Field.FROM:
        return parsed.get("from_addr", "")
    if field == Filter.Field.TO:
        return f"{parsed.get('to_addrs', '')} {parsed.get('cc_addrs', '')}"
    if field == Filter.Field.SUBJECT:
        return parsed.get("subject", "")
    if field == Filter.Field.BODY:
        return parsed.get("body_text", "")
    # ANY
    return " ".join([
        parsed.get("from_addr", ""), parsed.get("to_addrs", ""),
        parsed.get("cc_addrs", ""), parsed.get("subject", ""),
        parsed.get("body_text", ""),
    ])


def _matches(rule: Filter, text: str) -> bool:
    value = rule.value
    if rule.match == Filter.Match.CONTAINS:
        return value.lower() in text.lower()
    if rule.match == Filter.Match.EQUALS:
        return text.strip().lower() == value.lower()
    if rule.match == Filter.Match.STARTSWITH:
        return text.strip().lower().startswith(value.lower())
    if rule.match == Filter.Match.REGEX:
        try:
            return re.search(value, text, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def apply_filters(mailbox, parsed: dict, base_folder=Message.Folder.INBOX) -> dict:
    """Return the delivery decision after running all of a mailbox's filters.

    Result keys: folder, labels (list of names), mark_read, star, is_spam.
    All active filters run in priority order; later actions win for folder.
    """
    result = {"folder": base_folder, "labels": [], "mark_read": False,
              "star": False, "is_spam": False}

    for rule in mailbox.filters.filter(is_active=True):
        if not _matches(rule, _haystack(parsed, rule.field)):
            continue
        log.info("Filter %s matched for %s", rule, mailbox.email)
        action = rule.action
        if action == Filter.Action.MOVE and rule.action_arg:
            folder = rule.action_arg.upper()
            if folder in Message.Folder.values:
                result["folder"] = folder
        elif action == Filter.Action.LABEL and rule.action_arg:
            result["labels"].append(rule.action_arg)
        elif action == Filter.Action.READ:
            result["mark_read"] = True
        elif action == Filter.Action.STAR:
            result["star"] = True
        elif action == Filter.Action.SPAM:
            result["is_spam"] = True
            result["folder"] = Message.Folder.JUNK
        elif action == Filter.Action.DELETE:
            result["folder"] = Message.Folder.TRASH

    return result
