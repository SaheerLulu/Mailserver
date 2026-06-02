"""Synchronous DB operations backing the IMAP server.

IMAP "mailboxes" map to our message folders; the IMAP UID of a message is its
database primary key (monotonic, stable), so sequence order = id order.
"""
from email.utils import parseaddr

from django.utils import timezone

from mail import storage
from mail.models import Mailbox, Message

UIDVALIDITY = 1  # UIDs are DB pks and never reused, so this is constant.

# IMAP folder name  ->  (Message.Folder value, special-use attribute or None)
_SPECIAL = {
    "INBOX": (Message.Folder.INBOX, None),
    "Sent": (Message.Folder.SENT, "\\Sent"),
    "Drafts": (Message.Folder.DRAFTS, "\\Drafts"),
    "Junk": (Message.Folder.JUNK, "\\Junk"),
    "Trash": (Message.Folder.TRASH, "\\Trash"),
    "Archive": (Message.Folder.ARCHIVE, "\\Archive"),
}


def authenticate(email, password):
    return storage.authenticate(email, password)


def folder_for(name: str):
    n = (name or "").strip().strip('"')
    if n.upper() == "INBOX":
        return Message.Folder.INBOX
    for imap_name, (folder, _su) in _SPECIAL.items():
        if imap_name.upper() == n.upper():
            return folder
    return None


def list_folders():
    """Return [(imap_name, special_use_or_None), ...]."""
    return [(name, su) for name, (_f, su) in _SPECIAL.items()]


def flags_of(msg: Message):
    flags = []
    if msg.is_read:
        flags.append("\\Seen")
    if msg.is_flagged:
        flags.append("\\Flagged")
    if msg.is_answered:
        flags.append("\\Answered")
    if msg.imap_deleted:
        flags.append("\\Deleted")
    return flags


def uid_list(mailbox, folder):
    return list(Message.objects.filter(mailbox=mailbox, folder=folder)
                .order_by("id").values_list("id", flat=True))


def status(mailbox, folder):
    qs = Message.objects.filter(mailbox=mailbox, folder=folder)
    last = qs.order_by("-id").values_list("id", flat=True).first() or 0
    return {
        "MESSAGES": qs.count(),
        "UNSEEN": qs.filter(is_read=False).count(),
        "RECENT": 0,
        "UIDNEXT": last + 1,
        "UIDVALIDITY": UIDVALIDITY,
    }


def get_message(mailbox, uid):
    return Message.objects.filter(mailbox=mailbox, id=uid).first()


def raw_bytes(msg: Message) -> bytes:
    if msg.eml:
        try:
            with msg.eml.open("rb") as fh:
                return fh.read()
        except (OSError, ValueError):
            pass
    # Reconstruct a minimal message if the .eml is missing.
    headers = (f"From: {msg.from_addr}\r\nTo: {msg.to_addrs}\r\n"
               f"Subject: {msg.subject}\r\n"
               f"Date: {msg.date:%a, %d %b %Y %H:%M:%S %z}\r\n")
    return (headers + "\r\n" + (msg.body_text or "")).encode("utf-8", "replace")


def set_flags(mailbox, uid, flags, mode="set"):
    msg = get_message(mailbox, uid)
    if not msg:
        return None
    wanted = {f.lower() for f in flags}
    mapping = {"\\seen": "is_read", "\\flagged": "is_flagged",
               "\\answered": "is_answered", "\\deleted": "imap_deleted"}
    if mode == "set":
        for flag, field in mapping.items():
            setattr(msg, field, flag in wanted)
    else:
        for flag, field in mapping.items():
            if flag in wanted:
                setattr(msg, field, mode == "add")
    msg.save(update_fields=list(mapping.values()))
    return flags_of(msg)


def expunge(mailbox, folder):
    """Delete \\Deleted messages; return their (descending) sequence numbers."""
    uids = uid_list(mailbox, folder)
    seqs = []
    for seq, uid in enumerate(uids, start=1):
        msg = get_message(mailbox, uid)
        if msg and msg.imap_deleted:
            seqs.append(seq)
            msg.delete()
    return sorted(seqs, reverse=True)


def append(mailbox, folder, flags, raw: bytes):
    wanted = {f.lower() for f in (flags or [])}
    msg = storage.store_to_mailbox(
        mailbox, raw, folder=folder,
        mark_read="\\seen" in wanted, star="\\flagged" in wanted,
    )
    if "\\answered" in wanted or "\\deleted" in wanted:
        msg.is_answered = "\\answered" in wanted
        msg.imap_deleted = "\\deleted" in wanted
        msg.save(update_fields=["is_answered", "imap_deleted"])
    return msg.id


def search(mailbox, folder, criteria):
    qs = Message.objects.filter(mailbox=mailbox, folder=folder).order_by("id")
    tokens = list(criteria)
    i = 0
    while i < len(tokens):
        t = tokens[i].upper()
        if t in ("ALL", "RECENT", "NEW"):
            pass
        elif t == "SEEN":
            qs = qs.filter(is_read=True)
        elif t in ("UNSEEN",):
            qs = qs.filter(is_read=False)
        elif t == "FLAGGED":
            qs = qs.filter(is_flagged=True)
        elif t == "UNFLAGGED":
            qs = qs.filter(is_flagged=False)
        elif t == "ANSWERED":
            qs = qs.filter(is_answered=True)
        elif t == "DELETED":
            qs = qs.filter(imap_deleted=True)
        elif t in ("FROM", "SUBJECT", "TEXT", "BODY", "TO") and i + 1 < len(tokens):
            arg = tokens[i + 1].strip('"')
            i += 1
            if t == "FROM":
                qs = qs.filter(from_addr__icontains=arg)
            elif t == "TO":
                qs = qs.filter(to_addrs__icontains=arg)
            elif t == "SUBJECT":
                qs = qs.filter(subject__icontains=arg)
            else:
                qs = qs.filter(body_text__icontains=arg)
        i += 1
    return list(qs.values_list("id", flat=True))


def envelope(msg: Message) -> str:
    """A minimal IMAP ENVELOPE structure."""
    def addr(value):
        name, email = parseaddr(value or "")
        if not email:
            return "NIL"
        mbox, _, host = email.partition("@")
        q = lambda s: '"%s"' % s.replace('"', '') if s else "NIL"  # noqa: E731
        return f'(({q(name)} NIL {q(mbox)} {q(host)}))'

    date = msg.date.strftime("%a, %d %b %Y %H:%M:%S %z") if msg.date else ""
    subj = (msg.subject or "").replace('"', "'")
    frm = addr(msg.from_addr)
    return (f'("{date}" "{subj}" {frm} {frm} {frm} '
            f'{addr(msg.to_addrs)} NIL NIL "" "{msg.message_id}")')
