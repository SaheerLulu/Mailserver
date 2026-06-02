"""A minimal but real IMAP4rev1 server backed by the Django ORM.

Implements the command set mainstream clients (Thunderbird, Apple Mail, mobile)
need for basic sync: CAPABILITY, LOGIN, LIST/LSUB, SELECT/EXAMINE, STATUS,
(UID) FETCH, (UID) SEARCH, (UID) STORE, APPEND, EXPUNGE, CLOSE, NOOP, LOGOUT.
Folders map to message folders; UID = database id.
"""
import asyncio
import logging
import re

from asgiref.sync import sync_to_async

from . import backend

log = logging.getLogger("mail")

CAPABILITY = "IMAP4rev1 LITERAL+ AUTH=PLAIN"
_LITERAL_RE = re.compile(rb"\{(\d+)(\+?)\}\r?\n$")


def _tokenize(line: str):
    """Split an IMAP command line, honouring double-quoted strings."""
    tokens, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c == " ":
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and line[j] != '"':
                if line[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(line[j])
                j += 1
            tokens.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and line[j] != " ":
                j += 1
            tokens.append(line[i:j])
            i = j
    return tokens


class IMAPConnection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.mailbox = None
        self.folder = None
        self.readonly = False

    def _w(self, data: bytes):
        self.writer.write(data)

    async def _flush(self):
        await self.writer.drain()

    async def send(self, text: str):
        self._w(text.encode("utf-8") + b"\r\n")
        await self._flush()

    async def read_command(self):
        """Read one command, resolving any literals into a token list."""
        literals = []
        chunks = []
        while True:
            line = await self.reader.readline()
            if not line:
                return None, None
            m = _LITERAL_RE.search(line)
            if not m:
                chunks.append(line)
                break
            length = int(m.group(1))
            chunks.append(line[: m.start()])
            if m.group(2) != b"+":
                await self.send("+ OK")
            literals.append(await self.reader.readexactly(length))
            chunks.append(b"\x00L%d\x00" % (len(literals) - 1))
        full = b"".join(chunks).decode("utf-8", "replace").rstrip("\r\n")
        return full, literals

    async def handle(self):
        await self.send(f"* OK [CAPABILITY {CAPABILITY}] mail ready")
        while True:
            full, literals = await self.read_command()
            if full is None:
                break
            parts = full.split(" ", 2)
            if len(parts) < 2:
                await self.send("* BAD invalid command")
                continue
            tag, cmd = parts[0], parts[1].upper()
            rest = parts[2] if len(parts) > 2 else ""
            try:
                cont = await self.dispatch(tag, cmd, rest, literals)
            except Exception:  # noqa: BLE001
                log.exception("IMAP command error")
                await self.send(f"{tag} NO internal error")
                continue
            if cont is False:
                break

    async def dispatch(self, tag, cmd, rest, literals):
        if cmd == "CAPABILITY":
            await self.send(f"* CAPABILITY {CAPABILITY}")
            await self.send(f"{tag} OK CAPABILITY completed")
        elif cmd == "NOOP" or cmd == "CHECK":
            await self.send(f"{tag} OK {cmd} completed")
        elif cmd == "LOGOUT":
            await self.send("* BYE logging out")
            await self.send(f"{tag} OK LOGOUT completed")
            return False
        elif cmd == "LOGIN":
            await self.cmd_login(tag, rest)
        elif cmd == "AUTHENTICATE":
            await self.cmd_authenticate(tag, rest)
        elif cmd in ("LIST", "LSUB"):
            await self.cmd_list(tag)
        elif cmd in ("SELECT", "EXAMINE"):
            await self.cmd_select(tag, rest, readonly=(cmd == "EXAMINE"))
        elif cmd == "STATUS":
            await self.cmd_status(tag, rest)
        elif cmd == "FETCH":
            await self.cmd_fetch(tag, rest, use_uid=False)
        elif cmd == "STORE":
            await self.cmd_store(tag, rest, use_uid=False)
        elif cmd == "SEARCH":
            await self.cmd_search(tag, rest, use_uid=False)
        elif cmd == "UID":
            await self.cmd_uid(tag, rest)
        elif cmd == "APPEND":
            await self.cmd_append(tag, rest, literals)
        elif cmd == "EXPUNGE":
            await self.cmd_expunge(tag)
        elif cmd == "CLOSE":
            if self.folder and not self.readonly:
                await sync_to_async(backend.expunge)(self.mailbox, self.folder)
            self.folder = None
            await self.send(f"{tag} OK CLOSE completed")
        elif cmd in ("SUBSCRIBE", "UNSUBSCRIBE"):
            await self.send(f"{tag} OK {cmd} completed")
        else:
            await self.send(f"{tag} BAD command not supported")
        return True

    def _require_login(self, tag):
        if not self.mailbox:
            return False
        return True

    async def cmd_login(self, tag, rest):
        toks = _tokenize(rest)
        if len(toks) < 2:
            await self.send(f"{tag} BAD LOGIN needs user and password")
            return
        mailbox = await sync_to_async(backend.authenticate)(toks[0], toks[1])
        if mailbox is None:
            await self.send(f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials")
            return
        self.mailbox = mailbox
        await self.send(f"{tag} OK LOGIN completed")

    async def cmd_authenticate(self, tag, rest):
        import base64

        from mail import oauth
        toks = rest.split()
        mech = (toks[0].upper() if toks else "")
        initial = toks[1] if len(toks) > 1 else None

        if mech == "XOAUTH2":
            if initial is None:
                self._w(b"+ \r\n")
                await self._flush()
                line = (await self.reader.readline()).decode().strip()
            else:
                line = initial
            mailbox = await sync_to_async(oauth.xoauth2_from_b64)(line)
            if mailbox is None:
                # client must send an empty line to finish a failed exchange
                self._w(b"+ \r\n")
                await self._flush()
                await self.reader.readline()
                await self.send(f"{tag} NO [AUTHENTICATIONFAILED] invalid token")
                return
            self.mailbox = mailbox
            await self.send(f"{tag} OK AUTHENTICATE completed")
        elif mech == "PLAIN":
            if initial is None:
                self._w(b"+ \r\n")
                await self._flush()
                initial = (await self.reader.readline()).decode().strip()
            try:
                _authzid, user, pw = base64.b64decode(initial).decode().split("\x00", 2)
            except Exception:  # noqa: BLE001
                await self.send(f"{tag} NO invalid PLAIN response")
                return
            mailbox = await sync_to_async(backend.authenticate)(user, pw)
            if mailbox is None:
                await self.send(f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials")
                return
            self.mailbox = mailbox
            await self.send(f"{tag} OK AUTHENTICATE completed")
        else:
            await self.send(f"{tag} NO unsupported mechanism")

    async def cmd_list(self, tag):
        if not self._require_login(tag):
            await self.send(f"{tag} NO not authenticated")
            return
        for name, su in backend.list_folders():
            attrs = "\\HasNoChildren" + (f" {su}" if su else "")
            await self.send(f'* LIST ({attrs}) "/" "{name}"')
        await self.send(f"{tag} OK LIST completed")

    async def cmd_select(self, tag, rest, readonly):
        if not self._require_login(tag):
            await self.send(f"{tag} NO not authenticated")
            return
        folder = backend.folder_for(_tokenize(rest)[0] if rest else "")
        if folder is None:
            await self.send(f"{tag} NO mailbox does not exist")
            return
        self.folder = folder
        self.readonly = readonly
        st = await sync_to_async(backend.status)(self.mailbox, folder)
        await self.send(f"* {st['MESSAGES']} EXISTS")
        await self.send("* 0 RECENT")
        await self.send(r"* FLAGS (\Seen \Flagged \Answered \Deleted)")
        await self.send(r"* OK [PERMANENTFLAGS (\Seen \Flagged \Answered \Deleted)] limited")
        await self.send(f"* OK [UIDVALIDITY {st['UIDVALIDITY']}] uids valid")
        await self.send(f"* OK [UIDNEXT {st['UIDNEXT']}] next uid")
        mode = "READ-ONLY" if readonly else "READ-WRITE"
        await self.send(f"{tag} OK [{mode}] {'EXAMINE' if readonly else 'SELECT'} completed")

    async def cmd_status(self, tag, rest):
        toks = _tokenize(rest)
        folder = backend.folder_for(toks[0] if toks else "")
        if folder is None:
            await self.send(f"{tag} NO mailbox does not exist")
            return
        st = await sync_to_async(backend.status)(self.mailbox, folder)
        wanted = re.findall(r"[A-Z]+", rest.split("(", 1)[1]) if "(" in rest else st.keys()
        items = " ".join(f"{k} {st[k]}" for k in wanted if k in st)
        await self.send(f'* STATUS "{toks[0]}" ({items})')
        await self.send(f"{tag} OK STATUS completed")

    # --- FETCH ----------------------------------------------------------
    def _resolve_set(self, spec, uids, use_uid):
        """Map a sequence/UID set spec to a list of (seq, uid) pairs."""
        if not uids:
            return []
        max_uid, max_seq = uids[-1], len(uids)
        pairs = []
        for part in spec.split(","):
            if ":" in part:
                a, b = part.split(":", 1)
                lo = max_uid if a == "*" else int(a)
                hi = max_uid if b == "*" else int(b)
                if lo > hi:
                    lo, hi = hi, lo
            else:
                lo = hi = (max_uid if part == "*" else int(part))
            if use_uid:
                for seq, uid in enumerate(uids, 1):
                    if lo <= uid <= hi:
                        pairs.append((seq, uid))
            else:
                hi = min(hi, max_seq)
                for seq in range(lo, hi + 1):
                    if 1 <= seq <= max_seq:
                        pairs.append((seq, uids[seq - 1]))
        return pairs

    async def cmd_fetch(self, tag, rest, use_uid):
        if self.folder is None:
            await self.send(f"{tag} NO no mailbox selected")
            return
        spec, _, items = rest.partition(" ")
        items_u = items.upper()
        uids = await sync_to_async(backend.uid_list)(self.mailbox, self.folder)
        for seq, uid in self._resolve_set(spec, uids, use_uid):
            msg = await sync_to_async(backend.get_message)(self.mailbox, uid)
            if not msg:
                continue
            await self._emit_fetch(seq, uid, msg, items_u, use_uid)
        await self.send(f"{tag} OK {'UID ' if use_uid else ''}FETCH completed")

    async def _emit_fetch(self, seq, uid, msg, items_u, use_uid):
        pieces = []  # list of (text, optional_literal_bytes)
        need_raw = any(x in items_u for x in
                       ("RFC822", "BODY[", "BODY.PEEK["))
        raw = await sync_to_async(backend.raw_bytes)(msg) if need_raw else b""

        if use_uid or "UID" in items_u:
            pieces.append((f"UID {uid}", None))
        if "FLAGS" in items_u:
            flags = " ".join(await sync_to_async(backend.flags_of)(msg))
            pieces.append((f"FLAGS ({flags})", None))
        if "RFC822.SIZE" in items_u:
            pieces.append((f"RFC822.SIZE {msg.size or len(raw)}", None))
        if "INTERNALDATE" in items_u:
            pieces.append((f'INTERNALDATE "{msg.date:%d-%b-%Y %H:%M:%S %z}"', None))
        if "ENVELOPE" in items_u:
            pieces.append((f"ENVELOPE {backend.envelope(msg)}", None))

        # Body variants
        header_only = "BODY[HEADER]" in items_u or "RFC822.HEADER" in items_u
        text_only = "BODY[TEXT]" in items_u or "RFC822.TEXT" in items_u
        full_body = ("BODY[]" in items_u or "BODY.PEEK[]" in items_u
                     or re.search(r"\bRFC822\b", items_u))
        if header_only:
            body = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0] + b"\r\n\r\n"
            label = "RFC822.HEADER" if "RFC822.HEADER" in items_u else "BODY[HEADER]"
            pieces.append((f"{label} ", body))
        elif text_only:
            body = raw.split(b"\r\n\r\n", 1)[-1]
            label = "RFC822.TEXT" if "RFC822.TEXT" in items_u else "BODY[TEXT]"
            pieces.append((f"{label} ", body))
        elif full_body:
            label = "RFC822" if re.search(r"\bRFC822\b", items_u) else "BODY[]"
            pieces.append((f"{label} ", raw))

        # Mark \Seen unless a PEEK was used.
        if (full_body or text_only) and ".PEEK" not in items_u and not self.readonly:
            await sync_to_async(backend.set_flags)(self.mailbox, uid, ["\\Seen"], "add")

        # Assemble: `* seq FETCH (item1 item2 LABEL {n}\r\n<bytes> ...)`
        out = f"* {seq} FETCH (".encode()
        rendered = []
        for text, lit in pieces:
            if lit is None:
                rendered.append(text.encode())
            else:
                rendered.append(text.encode() + b"{%d}\r\n" % len(lit) + lit)
        out += b" ".join(rendered) + b")\r\n"
        self._w(out)
        await self._flush()

    # --- STORE ----------------------------------------------------------
    async def cmd_store(self, tag, rest, use_uid):
        if self.folder is None:
            await self.send(f"{tag} NO no mailbox selected")
            return
        m = re.match(r"(\S+)\s+([+-]?)FLAGS(?:\.SILENT)?\s*\((.*)\)", rest, re.IGNORECASE)
        if not m:
            await self.send(f"{tag} BAD STORE syntax")
            return
        spec, sign, flagstr = m.group(1), m.group(2), m.group(3)
        silent = ".SILENT" in rest.upper()
        flags = flagstr.split()
        mode = {"+": "add", "-": "remove"}.get(sign, "set")
        uids = await sync_to_async(backend.uid_list)(self.mailbox, self.folder)
        for seq, uid in self._resolve_set(spec, uids, use_uid):
            new = await sync_to_async(backend.set_flags)(self.mailbox, uid, flags, mode)
            if not silent and new is not None:
                extra = f" UID {uid}" if use_uid else ""
                await self.send(f"* {seq} FETCH (FLAGS ({' '.join(new)}){extra})")
        await self.send(f"{tag} OK {'UID ' if use_uid else ''}STORE completed")

    # --- SEARCH ---------------------------------------------------------
    async def cmd_search(self, tag, rest, use_uid):
        if self.folder is None:
            await self.send(f"{tag} NO no mailbox selected")
            return
        criteria = _tokenize(rest)
        ids = await sync_to_async(backend.search)(self.mailbox, self.folder, criteria)
        if use_uid:
            results = ids
        else:
            uids = await sync_to_async(backend.uid_list)(self.mailbox, self.folder)
            pos = {uid: i + 1 for i, uid in enumerate(uids)}
            results = [pos[i] for i in ids if i in pos]
        await self.send("* SEARCH " + " ".join(str(x) for x in results))
        await self.send(f"{tag} OK {'UID ' if use_uid else ''}SEARCH completed")

    async def cmd_uid(self, tag, rest):
        sub, _, sub_rest = rest.partition(" ")
        sub = sub.upper()
        if sub == "FETCH":
            await self.cmd_fetch(tag, sub_rest, use_uid=True)
        elif sub == "STORE":
            await self.cmd_store(tag, sub_rest, use_uid=True)
        elif sub == "SEARCH":
            await self.cmd_search(tag, sub_rest, use_uid=True)
        else:
            await self.send(f"{tag} BAD UID {sub} not supported")

    async def cmd_append(self, tag, rest, literals):
        if not self.mailbox:
            await self.send(f"{tag} NO not authenticated")
            return
        # rest looks like:  "INBOX" (\Seen) \x00L0\x00
        mbox = _tokenize(rest.split("(")[0])[0] if rest else "INBOX"
        folder = backend.folder_for(mbox)
        if folder is None:
            await self.send(f"{tag} NO [TRYCREATE] no such mailbox")
            return
        flags = []
        fm = re.search(r"\(([^)]*)\)", rest)
        if fm:
            flags = fm.group(1).split()
        if not literals:
            await self.send(f"{tag} BAD APPEND needs a literal")
            return
        uid = await sync_to_async(backend.append)(self.mailbox, folder, flags, literals[-1])
        await self.send(f"{tag} OK [APPENDUID {backend.UIDVALIDITY} {uid}] APPEND completed")

    async def cmd_expunge(self, tag):
        if self.folder is None or self.readonly:
            await self.send(f"{tag} NO cannot expunge")
            return
        for seq in await sync_to_async(backend.expunge)(self.mailbox, self.folder):
            await self.send(f"* {seq} EXPUNGE")
        await self.send(f"{tag} OK EXPUNGE completed")


async def handle_client(reader, writer):
    conn = IMAPConnection(reader, writer)
    try:
        await conn.handle()
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
