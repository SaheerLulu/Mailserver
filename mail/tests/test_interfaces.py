"""Webmail, REST API, OAuth2, CalDAV/CardDAV, notifications and ICS."""
import base64
import json

from django.test import Client, TestCase

from mail import ics, notify, oauth, storage
from mail.models import (ApiToken, Calendar, Contact, Event, Label, Mailbox,
                         Message, PushSubscription)

from .helpers import raw_message


class WebmailTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        storage.store_to_mailbox(self.alice, raw_message(
            "x@y.com", "alice@example.com", "Hello", "world"))
        self.c = Client()
        self.c.force_login(self.alice)

    def test_pages_render(self):
        for url in ["/", "/folder/SENT/", "/folder/JUNK/", "/compose/",
                    "/settings/", "/contacts/", "/calendar/", "/search/?q=hello"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)

    def test_message_and_actions(self):
        m = self.alice.messages.first()
        self.assertEqual(self.c.get(f"/message/{m.pk}/").status_code, 200)
        self.assertEqual(self.c.get(f"/message/{m.pk}/raw/").status_code, 200)

    def test_labels_and_settings(self):
        self.c.post("/labels/create/", {"name": "Important", "next": "/"})
        lbl = Label.objects.get(mailbox=self.alice, name="Important")
        m = self.alice.messages.first()
        self.c.post(f"/message/{m.pk}/action/",
                    {"action": "label", "label_id": lbl.pk, "next": "/"})
        self.assertTrue(m.labels.filter(pk=lbl.pk).exists())
        self.c.post("/settings/", {"full_name": "Alice", "signature": "Cheers",
                                   "spam_threshold": "5.0", "vacation_subject": "x",
                                   "webhook_url": ""})
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.signature, "Cheers")

    def test_compose_send_local(self):
        Mailbox.objects.create_user("bob@example.com", "batterystaple99")
        r = self.c.post("/compose/", {"to": "bob@example.com", "subject": "Hey",
                                      "body": "hi", "cc": "", "in_reply_to": ""})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Message.objects.filter(folder="SENT", subject="Hey").exists())

    def test_calendar_add(self):
        r = self.c.post("/calendar/", {"summary": "Lunch", "dtstart": "2026-06-02T12:00"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Event.objects.filter(summary="Lunch").exists())


class ApiTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        storage.store_to_mailbox(self.alice, raw_message(
            "x@y.com", "alice@example.com", "Hello", "world"))
        self.token = ApiToken.objects.create(mailbox=self.alice, name="t")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.token.key}"}
        self.c = Client()

    def test_requires_auth(self):
        self.assertEqual(self.c.get("/api/folders/").status_code, 401)

    def test_folders_messages_action(self):
        self.assertEqual(self.c.get("/api/folders/", **self.auth).status_code, 200)
        r = self.c.get("/api/messages/?folder=INBOX", **self.auth)
        self.assertEqual(r.status_code, 200)
        mid = r.json()["messages"][0]["id"]
        r = self.c.post(f"/api/messages/{mid}/action/", data=json.dumps({"action": "flag"}),
                        content_type="application/json", **self.auth)
        self.assertTrue(r.json()["is_flagged"])

    def test_contacts(self):
        r = self.c.post("/api/contacts/", data=json.dumps({"email": "b@x.com", "name": "B"}),
                        content_type="application/json", **self.auth)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.c.get("/api/contacts/", **self.auth).json()["contacts"][0]["email"],
                         "b@x.com")


class OAuthTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self.c = Client()

    def test_password_grant(self):
        r = self.c.post("/oauth/token", {"grant_type": "password",
                        "username": "alice@example.com", "password": "correcthorse42"})
        self.assertEqual(r.status_code, 200)
        token = r.json()["access_token"]
        self.assertEqual(oauth.validate_bearer(token), self.alice)
        self.assertEqual(
            self.c.get("/api/folders/", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200)

    def test_bad_password(self):
        r = self.c.post("/oauth/token", {"grant_type": "password",
                        "username": "alice@example.com", "password": "x"})
        self.assertEqual(r.status_code, 400)


class DavTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")
        self.basic = "Basic " + base64.b64encode(b"alice@example.com:correcthorse42").decode()
        self.c = Client()

    def _auth(self):
        return {"HTTP_AUTHORIZATION": self.basic}

    def test_carddav_roundtrip(self):
        card = (b"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Carol\r\n"
                b"EMAIL:carol@x.com\r\nEND:VCARD\r\n")
        r = self.c.generic("PUT", "/dav/alice@example.com/addressbook/c1.vcf", card,
                           content_type="text/vcard", **self._auth())
        self.assertIn(r.status_code, (201, 204))
        self.assertTrue(Contact.objects.filter(email="carol@x.com").exists())
        r = self.c.generic("GET", "/dav/alice@example.com/addressbook/c1.vcf", **self._auth())
        self.assertIn(b"carol@x.com", r.content)
        r = self.c.generic("PROPFIND", "/dav/alice@example.com/addressbook/", b"",
                           HTTP_DEPTH="1", **self._auth())
        self.assertEqual(r.status_code, 207)
        r = self.c.generic("DELETE", "/dav/alice@example.com/addressbook/c1.vcf", **self._auth())
        self.assertEqual(r.status_code, 204)

    def test_caldav_put_get(self):
        ev = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:e1\r\n"
              b"SUMMARY:Standup\r\nDTSTART:20260601T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        r = self.c.generic("PUT", "/dav/alice@example.com/calendars/default/e1.ics", ev,
                           content_type="text/calendar", **self._auth())
        self.assertIn(r.status_code, (201, 204))
        self.assertEqual(Event.objects.get(uid="e1").summary, "Standup")
        r = self.c.generic("GET", "/dav/alice@example.com/calendars/default/e1.ics", **self._auth())
        self.assertIn(b"Standup", r.content)

    def test_requires_auth(self):
        self.assertEqual(
            self.c.generic("PROPFIND", "/dav/alice@example.com/addressbook/", b"").status_code, 401)


class IcsTests(TestCase):
    def test_vcard_roundtrip(self):
        c = Contact(mailbox_id=1, name="Bob Jones", email="bob@x.com",
                    organization="Acme", phone="123", uid="u1")
        parsed = ics.parse_vcard(ics.to_vcard(c))
        self.assertEqual(parsed["name"], "Bob Jones")
        self.assertEqual(parsed["email"], "bob@x.com")
        self.assertEqual(parsed["organization"], "Acme")


class NotifyTests(TestCase):
    def setUp(self):
        self.alice = Mailbox.objects.create_user("alice@example.com", "correcthorse42")

    def test_webhook_fires_on_inbound(self):
        self.alice.webhook_url = "https://hook.test/x"
        self.alice.save()
        fired = {}

        def fake_urlopen(req, timeout=5):
            fired["url"] = req.full_url
            fired["body"] = json.loads(req.data)

            class R:
                def close(self):
                    pass
            return R()

        orig = notify.urllib.request.urlopen
        notify.urllib.request.urlopen = fake_urlopen
        try:
            storage.handle_inbound(["alice@example.com"], raw_message(
                "ext@y.com", "alice@example.com", "ping", "hi"), mail_from="ext@y.com")
        finally:
            notify.urllib.request.urlopen = orig
        self.assertEqual(fired.get("url"), "https://hook.test/x")
        self.assertEqual(fired["body"]["event"], "new_mail")

    def test_push_subscribe(self):
        c = Client()
        c.force_login(self.alice)
        sub = {"endpoint": "https://push.test/abc", "keys": {"p256dh": "k", "auth": "a"}}
        r = c.post("/push/subscribe/", data=json.dumps(sub), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(PushSubscription.objects.filter(endpoint="https://push.test/abc").exists())
        self.assertEqual(c.get("/sw.js").status_code, 200)
