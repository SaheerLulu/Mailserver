"""Gmail-style features: search operators, categories, drafts, reply-all/forward,
scheduled send, undo send, snooze, bulk actions, mute/important, autocomplete."""
from datetime import timedelta

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from mail import categorize, delivery, search, storage
from mail.models import Contact, Mailbox, Message, OutboundMessage

from .helpers import raw_message


class SearchOperatorTests(TestCase):
    def setUp(self):
        self.u = Mailbox.objects.create_user("a@example.com", "correcthorse42")

    def test_operators(self):
        q, folder = search.build_query("from:bob subject:report is:unread")
        self.assertIsNone(folder)
        q2, folder2 = search.build_query("in:sent has:attachment")
        self.assertEqual(folder2, "SENT")
        # exercise against real rows
        storage.store_to_mailbox(self.u, raw_message("bob@x.com", "a@example.com",
                                                     "Monthly report", "body"))
        qs = self.u.messages.filter(search.build_query("from:bob subject:report")[0])
        self.assertEqual(qs.count(), 1)
        self.assertEqual(self.u.messages.filter(
            search.build_query("from:nobody")[0]).count(), 0)


class CategorizeTests(TestCase):
    def test_buckets(self):
        social = categorize.categorize({"from_addr": "x@facebook.com", "subject": "hi", "body_text": ""})
        promo = categorize.categorize({"from_addr": "x@shop.com", "subject": "50% off SALE", "body_text": "unsubscribe"})
        update = categorize.categorize({"from_addr": "no-reply@bank.com", "subject": "Your receipt", "body_text": "order confirmed"})
        primary = categorize.categorize({"from_addr": "friend@example.org", "subject": "lunch", "body_text": "tomorrow?"})
        self.assertEqual(social, Message.Category.SOCIAL)
        self.assertEqual(promo, Message.Category.PROMOTIONS)
        self.assertEqual(update, Message.Category.UPDATES)
        self.assertEqual(primary, Message.Category.PRIMARY)

    def test_inbound_sets_category(self):
        u = Mailbox.objects.create_user("a@example.com", "correcthorse42")
        storage.handle_inbound(["a@example.com"], raw_message(
            "deals@shop.com", "a@example.com", "Huge SALE 70% off", "unsubscribe here"))
        m = u.messages.get()
        self.assertEqual(m.category, Message.Category.PROMOTIONS)


class WebFeatureTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self.bob = Mailbox.objects.create_user("bob@example.com", "batterystaple99")
        self.orig = storage.store_to_mailbox(self.alice, raw_message(
            "carol@x.com", "alice@example.com", "Project", "let's chat",
            "Message-ID: <p1@x.com>\r\n"))
        self.c = Client()
        self.c.force_login(self.alice)

    def test_inbox_category_tabs(self):
        r = self.c.get("/?category=PRIMARY")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Promotions")  # tab present

    def test_save_and_send_draft(self):
        r = self.c.post("/compose/", {"intent": "draft", "to": "bob@example.com",
                                      "subject": "WIP", "body": "draft body"})
        self.assertEqual(r.status_code, 302)
        draft = Message.objects.get(mailbox=self.alice, folder="DRAFTS", subject="WIP")
        # send the draft
        r = self.c.post("/compose/", {"intent": "send", "to": "bob@example.com",
                                      "subject": "WIP", "body": "draft body",
                                      "draft_id": draft.pk})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Message.objects.filter(pk=draft.pk).exists())  # draft removed
        self.assertTrue(self.bob.messages.filter(folder="INBOX", subject="WIP").exists())

    def test_reply_all_and_forward_initial(self):
        r = self.c.get(f"/compose/?replyall={self.orig.pk}")
        self.assertContains(r, "carol@x.com")          # To = original sender
        self.assertContains(r, "Re: Project")
        r = self.c.get(f"/compose/?forward={self.orig.pk}")
        self.assertContains(r, "Fwd: Project")
        self.assertContains(r, "Forwarded message")

    def test_bulk_archive(self):
        ids = [self.orig.pk]
        r = self.c.post("/bulk/", {"ids": ids, "action": "archive", "next": "/"})
        self.assertEqual(r.status_code, 302)
        self.orig.refresh_from_db()
        self.assertEqual(self.orig.folder, "ARCHIVE")

    def test_mark_all_read(self):
        self.assertFalse(self.orig.is_read)
        self.c.post("/folder/INBOX/read-all/")
        self.orig.refresh_from_db()
        self.assertTrue(self.orig.is_read)

    def test_mute_and_important(self):
        self.c.post(f"/message/{self.orig.pk}/action/", {"action": "important", "next": "/"})
        self.c.post(f"/message/{self.orig.pk}/action/", {"action": "mute", "next": "/"})
        self.orig.refresh_from_db()
        self.assertTrue(self.orig.is_important)
        self.assertTrue(self.orig.is_muted)
        # muted conversations drop out of the inbox listing
        r = self.c.get("/")
        self.assertNotContains(r, "let's chat")

    def test_snooze_hides_then_returns(self):
        until = (timezone.now() + timedelta(hours=1)).isoformat(timespec="minutes")
        self.c.post(f"/message/{self.orig.pk}/action/",
                    {"action": "snooze", "until": until, "next": "/"})
        self.orig.refresh_from_db()
        self.assertIsNotNone(self.orig.snooze_until)
        self.assertNotContains(self.c.get("/"), "Project")     # hidden while snoozed
        self.assertContains(self.c.get("/snoozed/"), "Project")  # shown in Snoozed
        # move snooze into the past and run the worker tick
        Message.objects.filter(pk=self.orig.pk).update(
            snooze_until=timezone.now() - timedelta(minutes=1))
        call_command("runqueue", "--once")
        self.orig.refresh_from_db()
        self.assertIsNone(self.orig.snooze_until)
        self.assertEqual(self.orig.folder, "INBOX")

    def test_autocomplete(self):
        Contact.objects.create(mailbox=self.alice, name="Bob", email="bob@example.com")
        r = self.c.get("/autocomplete/?q=bob")
        self.assertEqual(r.json()["results"][0]["email"], "bob@example.com")


class ScheduledSendTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self.bob = Mailbox.objects.create_user("bob@example.com", "batterystaple99")

    def test_scheduled_send_finalizes_when_due(self):
        future = timezone.now() + timedelta(hours=2)
        om = delivery.send_from_webmail(self.alice, {
            "to": "bob@example.com", "subject": "Later", "body": "scheduled",
            "send_at": future.isoformat(timespec="minutes")})
        self.assertIsNotNone(om)
        self.assertTrue(om.scheduled)
        # nothing delivered yet (future)
        self.assertFalse(self.bob.messages.exists())
        # make it due, run the worker
        OutboundMessage.objects.filter(pk=om.pk).update(next_attempt=timezone.now())
        call_command("runqueue", "--once")
        self.assertTrue(self.bob.messages.filter(folder="INBOX", subject="Later").exists())
        self.assertTrue(self.alice.messages.filter(folder="SENT", subject="Later").exists())
        om.refresh_from_db()
        self.assertEqual(om.status, "SENT")

    def test_undo_send_window(self):
        om = delivery.send_from_webmail(self.alice, {
            "to": "bob@example.com", "subject": "Oops", "body": "x",
            "undo_seconds": 30})
        self.assertIsNotNone(om)
        self.assertTrue(om.scheduled)
        self.assertGreater(om.next_attempt, timezone.now())
        # user hits undo -> cancel
        self.c = Client(); self.c.force_login(self.alice)
        r = self.c.post(f"/scheduled/{om.pk}/cancel/")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(OutboundMessage.objects.filter(pk=om.pk).exists())
        self.assertFalse(self.bob.messages.exists())
