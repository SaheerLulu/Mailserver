"""Spam scoring, Bayes, greylisting, DNSBL, filters and threading."""
import datetime

from django.test import TestCase
from django.utils import timezone

from mail import bayes, greylist, parsing, spam, storage
from mail.models import Filter, GreylistEntry, Mailbox, Message

from .helpers import raw_message


class SpamHeuristicTests(TestCase):
    def test_spammy_vs_clean(self):
        spammy = parsing.parse_message(raw_message(
            "x@y.com", "a@example.com", "FREE MONEY WINNER!!!",
            "you have won a lottery, click here now"))
        clean = parsing.parse_message(raw_message(
            "friend@y.com", "a@example.com", "Lunch?", "grab lunch tomorrow?"))
        self.assertGreaterEqual(spam.heuristic_score(spammy)[0], 5)
        self.assertLess(spam.heuristic_score(clean)[0], 2)


class BayesTests(TestCase):
    def test_learns_to_separate(self):
        for _ in range(12):
            bayes.train({"subject": "cheap viagra pills", "from_addr": "x@spam.ru",
                         "body_text": "buy cheap viagra now lottery winner"}, True)
            bayes.train({"subject": "lunch tomorrow", "from_addr": "joe@work.com",
                         "body_text": "are we still on for lunch tomorrow team"}, False)
        p_spam = bayes.probability({"subject": "cheap viagra", "from_addr": "x@spam.ru",
                                    "body_text": "buy cheap viagra lottery"})
        p_ham = bayes.probability({"subject": "lunch", "from_addr": "joe@work.com",
                                   "body_text": "lunch tomorrow team"})
        self.assertGreater(p_spam, 0.8)
        self.assertLess(p_ham, 0.2)


class GreylistTests(TestCase):
    def test_defer_then_accept(self):
        self.assertTrue(greylist.should_defer("203.0.113.9", "s@ext.com", ["a@example.com"]))
        GreylistEntry.objects.update(
            first_seen=timezone.now() - datetime.timedelta(seconds=120))
        self.assertFalse(greylist.should_defer("203.0.113.9", "s@ext.com", ["a@example.com"]))

    def test_no_ip_never_defers(self):
        self.assertFalse(greylist.should_defer("", "s@ext.com", ["a@example.com"]))


class DnsblTests(TestCase):
    def test_listed_ip_scores(self):
        import dns.resolver
        from mail import dnsbl
        orig = dnsbl.dns.resolver.resolve
        dnsbl.dns.resolver.resolve = lambda name, rt: ["127.0.0.2"]
        try:
            points, zones = dnsbl.check("203.0.113.9")
        finally:
            dnsbl.dns.resolver.resolve = orig
        self.assertGreater(points, 0)
        self.assertTrue(zones)


class FilterAndThreadTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self.bob = Mailbox.objects.create_user("bob@example.com", "batterystaple99")

    def test_filters_move_and_label(self):
        Filter.objects.create(mailbox=self.alice, field="FROM", match="CONTAINS",
                              value="newsletter@", action="LABEL", action_arg="News")
        Filter.objects.create(mailbox=self.alice, field="SUBJECT", match="CONTAINS",
                              value="[invoice]", action="MOVE", action_arg="ARCHIVE")
        storage.handle_inbound(["alice@example.com"], raw_message(
            "newsletter@shop.com", "alice@example.com", "[invoice] May", "x"))
        m = Message.objects.get(mailbox=self.alice, subject="[invoice] May")
        self.assertEqual(m.folder, "ARCHIVE")
        self.assertTrue(m.labels.filter(name="News").exists())

    def test_threading_groups_replies(self):
        storage.handle_inbound(["bob@example.com"], raw_message(
            "c@x.com", "bob@example.com", "Project", "kickoff",
            "Message-ID: <m1@x.com>\r\n"))
        storage.handle_inbound(["bob@example.com"], raw_message(
            "d@x.com", "bob@example.com", "Re: Project", "reply",
            "Message-ID: <m2@x.com>\r\nIn-Reply-To: <m1@x.com>\r\n"))
        t1 = Message.objects.get(message_id="<m1@x.com>", mailbox=self.bob)
        t2 = Message.objects.get(message_id="<m2@x.com>", mailbox=self.bob)
        self.assertTrue(t1.thread_id)
        self.assertEqual(t1.thread_id, t2.thread_id)


class SpfTests(TestCase):
    def test_mechanisms(self):
        orig = spam._txt_records
        spam._txt_records = lambda d: {
            "good.test": ["v=spf1 ip4:203.0.113.0/24 -all"],
            "inc.test": ["v=spf1 include:good.test ~all"],
        }.get(d, [])
        try:
            self.assertEqual(spam.check_spf("203.0.113.7", "a@good.test"), "pass")
            self.assertEqual(spam.check_spf("198.51.100.9", "a@good.test"), "fail")
            self.assertEqual(spam.check_spf("203.0.113.7", "a@inc.test"), "pass")
            self.assertEqual(spam.check_spf("8.8.8.8", "a@inc.test"), "softfail")
        finally:
            spam._txt_records = orig
