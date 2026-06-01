"""Core routing, delivery, DKIM and the outbound queue."""
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from mail import delivery, dkimtools, parsing, storage
from mail.models import Alias, Mailbox, Message, OutboundMessage

from .helpers import raw_message


class RoutingTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self.bob = Mailbox.objects.create_user("bob@example.com", "batterystaple99")
        Alias.objects.create(domain=self.alice.domain, source="info@example.com",
                             destination="alice@example.com")

    def test_domain_autolink(self):
        self.assertEqual(self.alice.domain.name, "example.com")

    def test_authentication(self):
        self.assertEqual(storage.authenticate("alice@example.com", "correcthorse42"),
                         self.alice)
        self.assertIsNone(storage.authenticate("alice@example.com", "nope"))

    def test_deliverability_and_aliases(self):
        self.assertTrue(storage.is_deliverable("alice@example.com"))
        self.assertTrue(storage.is_deliverable("info@example.com"))
        self.assertFalse(storage.is_deliverable("nobody@elsewhere.org"))
        self.assertEqual(storage.resolve_recipients("info@example.com"),
                         [("local", self.alice)])

    def test_inbound_delivery(self):
        storage.handle_inbound(["bob@example.com"],
                               raw_message("x@y.com", "bob@example.com", "Hi", "hello"))
        self.assertEqual(self.bob.messages.filter(folder="INBOX").count(), 1)

    def test_submission_local(self):
        delivery.send_from_webmail(self.alice, {
            "to": "bob@example.com", "subject": "Local", "body": "hey"})
        self.assertTrue(self.alice.messages.filter(folder="SENT", subject="Local").exists())
        self.assertTrue(self.bob.messages.filter(folder="INBOX", subject="Local").exists())


class DkimTests(TestCase):
    def test_keygen_and_sign(self):
        priv, pub = dkimtools.generate_keypair(1024)
        name, value = dkimtools.dns_record("mail", "example.com", pub)
        self.assertEqual(name, "mail._domainkey.example.com")
        self.assertTrue(value.startswith("v=DKIM1;"))
        raw = raw_message("a@example.com", "b@x.com", "Hi", "body")
        signed = dkimtools.sign(raw, "example.com", "mail", priv)
        self.assertTrue(signed.lower().startswith(b"dkim-signature:"))

    def test_parse(self):
        parsed = parsing.parse_message(
            raw_message("a@example.com", "b@x.com", "Subject here", "the body"))
        self.assertEqual(parsed["from_addr"], "a@example.com")
        self.assertEqual(parsed["subject"], "Subject here")
        self.assertIn("the body", parsed["body_text"])


class QueueTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self._orig = delivery.attempt_delivery

    def tearDown(self):
        delivery.attempt_delivery = self._orig

    def test_remote_is_queued_and_sent(self):
        delivery.attempt_delivery = lambda mf, rc, raw: (True, "")
        delivery.send_outbound(self.alice, self.alice.email, ["ext@remote.test"],
                               raw_message("alice@example.com", "ext@remote.test", "Hi", "b"))
        om = OutboundMessage.objects.latest("id")
        self.assertEqual(om.status, "QUEUED")
        call_command("runqueue", "--once")
        om.refresh_from_db()
        self.assertEqual(om.status, "SENT")

    def test_retry_then_fail(self):
        delivery.attempt_delivery = lambda mf, rc, raw: (False, "boom")
        om = delivery.enqueue(self.alice, self.alice.email, ["x@remote.test"], b"raw")
        call_command("runqueue", "--once")
        om.refresh_from_db()
        self.assertEqual(om.status, "QUEUED")
        self.assertEqual(om.attempts, 1)
        om.next_attempt = timezone.now()
        om.max_attempts = 1
        om.save()
        call_command("runqueue", "--once")
        om.refresh_from_db()
        self.assertEqual(om.status, "FAILED")
