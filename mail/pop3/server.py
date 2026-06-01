"""A minimal POP3 server (RFC 1939) over the Django ORM.

Serves the INBOX: USER/PASS, STAT, LIST, UIDL, RETR, DELE, RSET, NOOP, QUIT.
Messages marked DELE are removed on QUIT (moved to Trash). UID = database id.
"""
import asyncio
import logging

from asgiref.sync import sync_to_async

from mail import storage
from mail.imap import backend
from mail.models import Message

log = logging.getLogger("mail")


class POP3Connection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.mailbox = None
        self.user = None
        self.uids = []          # snapshot of INBOX message ids
        self.deleted = set()

    async def send(self, line: str):
        self.writer.write(line.encode("utf-8") + b"\r\n")
        await self.writer.drain()

    async def send_multiline(self, body: bytes):
        # Byte-stuff leading dots, terminate with ".".
        stuffed = b"\r\n".join(
            (b"." + ln if ln.startswith(b".") else ln)
            for ln in body.replace(b"\r\n", b"\n").split(b"\n"))
        self.writer.write(stuffed + b"\r\n.\r\n")
        await self.writer.drain()

    async def handle(self):
        await self.send("+OK POP3 server ready")
        while True:
            line = await self.reader.readline()
            if not line:
                break
            parts = line.decode("utf-8", "replace").strip().split(" ", 1)
            cmd = parts[0].upper()
            arg = parts[1] if len(parts) > 1 else ""
            try:
                if not await self.dispatch(cmd, arg):
                    break
            except Exception:  # noqa: BLE001
                log.exception("POP3 error")
                await self.send("-ERR internal error")

    async def dispatch(self, cmd, arg):
        if cmd == "USER":
            self.user = arg
            await self.send("+OK")
        elif cmd == "PASS":
            self.mailbox = await sync_to_async(storage.authenticate)(self.user or "", arg)
            if not self.mailbox:
                await self.send("-ERR invalid credentials")
                return True
            self.uids = await sync_to_async(backend.uid_list)(
                self.mailbox, Message.Folder.INBOX)
            await self.send(f"+OK mailbox ready, {len(self.uids)} messages")
        elif cmd == "CAPA":
            await self.send("+OK")
            self.writer.write(b"USER\r\nUIDL\r\nTOP\r\n.\r\n")
            await self.writer.drain()
        elif not self.mailbox:
            await self.send("-ERR not authenticated")
        elif cmd == "STAT":
            active = [u for u in self.uids if u not in self.deleted]
            total = await sync_to_async(self._size_of)(active)
            await self.send(f"+OK {len(active)} {total}")
        elif cmd == "LIST":
            await self._list(arg, uidl=False)
        elif cmd == "UIDL":
            await self._list(arg, uidl=True)
        elif cmd == "RETR":
            await self._retr(arg)
        elif cmd == "DELE":
            idx = self._index(arg)
            if idx is None:
                await self.send("-ERR no such message")
            else:
                self.deleted.add(self.uids[idx])
                await self.send("+OK marked for deletion")
        elif cmd == "RSET":
            self.deleted.clear()
            await self.send("+OK")
        elif cmd == "NOOP":
            await self.send("+OK")
        elif cmd == "QUIT":
            await sync_to_async(self._commit_deletes)()
            await self.send("+OK bye")
            return False
        else:
            await self.send("-ERR unknown command")
        return True

    def _index(self, arg):
        try:
            i = int(arg) - 1
        except ValueError:
            return None
        if 0 <= i < len(self.uids) and self.uids[i] not in self.deleted:
            return i
        return None

    def _size_of(self, uids):
        return sum(Message.objects.filter(id__in=uids)
                   .values_list("size", flat=True)) if uids else 0

    async def _list(self, arg, uidl):
        if arg:
            idx = self._index(arg)
            if idx is None:
                await self.send("-ERR no such message")
                return
            uid = self.uids[idx]
            if uidl:
                await self.send(f"+OK {idx + 1} {uid}")
            else:
                size = await sync_to_async(self._size_of)([uid])
                await self.send(f"+OK {idx + 1} {size}")
            return
        await self.send("+OK")
        lines = []
        for i, uid in enumerate(self.uids, 1):
            if uid in self.deleted:
                continue
            if uidl:
                lines.append(f"{i} {uid}")
            else:
                size = await sync_to_async(self._size_of)([uid])
                lines.append(f"{i} {size}")
        self.writer.write(("\r\n".join(lines) + "\r\n.\r\n").encode())
        await self.writer.drain()

    async def _retr(self, arg):
        idx = self._index(arg)
        if idx is None:
            await self.send("-ERR no such message")
            return
        msg = await sync_to_async(backend.get_message)(self.mailbox, self.uids[idx])
        raw = await sync_to_async(backend.raw_bytes)(msg)
        await self.send(f"+OK {len(raw)} octets")
        await self.send_multiline(raw)

    def _commit_deletes(self):
        for uid in self.deleted:
            msg = Message.objects.filter(mailbox=self.mailbox, id=uid).first()
            if msg:
                msg.folder = Message.Folder.TRASH
                msg.save(update_fields=["folder"])


async def handle_client(reader, writer):
    conn = POP3Connection(reader, writer)
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
