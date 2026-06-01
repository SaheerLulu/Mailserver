"""Live protocol tests: IMAP, POP3 and SMTP XOAUTH2 against real clients.

These start the asyncio servers in a thread, so they need a database whose
commits are visible across connections — they're skipped on SQLite (whose
in-memory test DB is per-connection) and run on PostgreSQL (CI).
"""
import base64
import imaplib
import os
import poplib
import smtplib
import time
import unittest

from django.db import connection
from django.test import TransactionTestCase

# XOAUTH2 validation in the SMTP class touches the ORM from the event loop.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

from mail import storage  # noqa: E402
from mail.models import ApiToken, Mailbox, Message  # noqa: E402

from .helpers import ThreadedServer, raw_message  # noqa: E402

skip_sqlite = unittest.skipIf(connection.vendor == "sqlite",
                              "protocol servers need a cross-connection-visible DB")


@skip_sqlite
class ImapTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        storage.store_to_mailbox(self.alice, raw_message(
            "a@x.com", "alice@example.com", "First", "hello one"))
        storage.store_to_mailbox(self.alice, raw_message(
            "b@x.com", "alice@example.com", "Second", "hello two"))

    def test_imap_flow(self):
        from mail.imap.server import handle_client
        with ThreadedServer(handle_client) as srv:
            M = imaplib.IMAP4("127.0.0.1", srv.port)
            self.assertIn("IMAP4REV1", [c.upper() for c in M.capabilities])
            M.login("alice@example.com", "correcthorse42")
            typ, data = M.select("INBOX")
            self.assertEqual(typ, "OK")
            self.assertEqual(int(data[0]), 2)
            typ, data = M.search(None, "ALL")
            self.assertEqual(data[0].split(), [b"1", b"2"])
            typ, data = M.fetch("1", "(RFC822 FLAGS)")
            self.assertIn(b"hello one", data[0][1])
            M.store("1", "+FLAGS", r"(\Seen)")
            typ, data = M.search(None, "UNSEEN")
            self.assertEqual(data[0].split(), [b"2"])
            M.append("INBOX", r"(\Seen)", None, raw_message(
                "c@x.com", "alice@example.com", "Third", "appended"))
            typ, data = M.select("INBOX")
            self.assertEqual(int(data[0]), 3)
            M.logout()

    def test_imap_xoauth2(self):
        from mail.imap.server import handle_client
        token = ApiToken.objects.create(mailbox=self.alice, name="x")
        with ThreadedServer(handle_client) as srv:
            M = imaplib.IMAP4("127.0.0.1", srv.port)
            xo = f"user=alice@example.com\x01auth=Bearer {token.key}\x01\x01".encode()
            M.authenticate("XOAUTH2", lambda _: xo)
            typ, _ = M.select("INBOX")
            self.assertEqual(typ, "OK")
            M.logout()


@skip_sqlite
class Pop3Tests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        for s in ("One", "Two"):
            storage.store_to_mailbox(self.alice, raw_message(
                "a@x.com", "alice@example.com", s, f"body {s}"))

    def test_pop3_flow(self):
        from mail.pop3.server import handle_client
        with ThreadedServer(handle_client) as srv:
            P = poplib.POP3("127.0.0.1", srv.port, timeout=5)
            P.user("alice@example.com")
            P.pass_("correcthorse42")
            count, _size = P.stat()
            self.assertEqual(count, 2)
            _resp, lines, _ = P.retr(1)
            self.assertTrue(any(b"body One" in ln for ln in lines))
            P.dele(2)
            P.quit()
        self.assertEqual(
            Message.objects.get(mailbox=self.alice, subject="Two").folder, "TRASH")


@skip_sqlite
class SmtpXoauth2Tests(TransactionTestCase):
    reset_sequences = True

    def test_smtp_xoauth2(self):
        import socket

        from mail.smtp.handler import Authenticator, MailHandler, XOAuthController
        alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        token = ApiToken.objects.create(mailbox=alice, name="x")

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        ctrl = XOAuthController(MailHandler(), hostname="127.0.0.1", port=port,
                               authenticator=Authenticator(), auth_required=True,
                               auth_require_tls=False)
        ctrl.start()
        time.sleep(0.3)
        try:
            s = smtplib.SMTP("127.0.0.1", port, timeout=5)
            s.ehlo("test")
            blob = base64.b64encode(
                f"user=alice@example.com\x01auth=Bearer {token.key}\x01\x01".encode()).decode()
            code, _resp = s.docmd("AUTH", "XOAUTH2 " + blob)
            self.assertEqual(code, 235)
            s.quit()
        finally:
            # The XOAUTH2 validation opened a DB connection in the controller's
            # event-loop thread; close it so the test DB can be dropped.
            def _close():
                from django.db import connections
                connections.close_all()
            try:
                ctrl.loop.call_soon_threadsafe(_close)
                time.sleep(0.3)
            except Exception:  # noqa: BLE001
                pass
            ctrl.stop()
