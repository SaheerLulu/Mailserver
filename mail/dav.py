"""A pragmatic CardDAV + CalDAV server (subset of RFC 6352 / 4791).

Supports the flows real clients use to sync: discovery (current-user-principal,
*-home-set), collection PROPFIND with getctag/getetag, addressbook/calendar
multiget + query REPORTs, and GET/PUT/DELETE of individual .vcf/.ics resources.
Authentication is HTTP Basic against the mailbox password.

Not a complete implementation (no sync-collection token, scheduling, free/busy),
but enough for Contacts/Calendar apps on desktop and mobile to read and write.
"""
import base64
import re
from xml.sax.saxutils import escape

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import ics, storage
from .models import Calendar, Contact, Event

BASE = "/dav"
NS = ('xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav" '
      'xmlns:cal="urn:ietf:params:xml:ns:caldav" '
      'xmlns:cs="http://calendarserver.org/ns/"')


# --- auth -------------------------------------------------------------------
def _authenticate(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except Exception:  # noqa: BLE001
            return None
        return storage.authenticate(user, pw)
    return None


def _unauth():
    resp = HttpResponse("Unauthorized", status=401)
    resp["WWW-Authenticate"] = 'Basic realm="mail"'
    return resp


# --- XML helpers ------------------------------------------------------------
def _multistatus(responses: list[str]) -> HttpResponse:
    body = (f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<d:multistatus {NS}>{"".join(responses)}</d:multistatus>')
    resp = HttpResponse(body, status=207, content_type='application/xml; charset=utf-8')
    return resp


def _response(href: str, props: str, status="HTTP/1.1 200 OK") -> str:
    return (f"<d:response><d:href>{escape(href)}</d:href>"
            f"<d:propstat><d:prop>{props}</d:prop>"
            f"<d:status>{status}</d:status></d:propstat></d:response>")


# --- collection ctags -------------------------------------------------------
def _ctag(qs) -> str:
    latest = qs.order_by("-updated_at").values_list("updated_at", flat=True).first()
    return f'"{int(latest.timestamp())}-{qs.count()}"' if latest else '"empty"'


# --- main entry -------------------------------------------------------------
@csrf_exempt
def dav(request, path=""):
    mailbox = _authenticate(request)
    if not mailbox:
        return _unauth()

    method = request.method.upper()
    if method == "OPTIONS":
        resp = HttpResponse(status=200)
        resp["DAV"] = "1, 2, 3, addressbook, calendar-access"
        resp["Allow"] = "OPTIONS, GET, PUT, DELETE, PROPFIND, REPORT, MKCOL"
        return resp

    parts = [p for p in path.strip("/").split("/") if p]
    email = mailbox.email

    try:
        if not parts or parts[0] == "principals":
            return _discovery(request, mailbox, parts, method)
        if parts[0] == email:
            parts = parts[1:]
            if not parts:  # the home collection
                return _discovery(request, mailbox, parts, method)
            if parts[0] in ("addressbook", "contacts"):
                return _carddav(request, mailbox, parts[1:], method)
            if parts[0] in ("calendars", "calendar"):
                return _caldav(request, mailbox, parts[1:], method)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("mail").exception("DAV error")
        return HttpResponse(status=500)
    return HttpResponse(status=404)


def _discovery(request, mailbox, parts, method):
    if method != "PROPFIND":
        return HttpResponse(status=405)
    email = mailbox.email
    props = (
        f"<d:current-user-principal><d:href>{BASE}/principals/{email}/</d:href></d:current-user-principal>"
        f"<d:principal-URL><d:href>{BASE}/principals/{email}/</d:href></d:principal-URL>"
        f"<card:addressbook-home-set><d:href>{BASE}/{email}/</d:href></card:addressbook-home-set>"
        f"<cal:calendar-home-set><d:href>{BASE}/{email}/calendars/</d:href></cal:calendar-home-set>"
        "<d:resourcetype><d:collection/><d:principal/></d:resourcetype>"
        f"<d:displayname>{escape(email)}</d:displayname>"
    )
    return _multistatus([_response(request.path, props)])


# --- CardDAV ----------------------------------------------------------------
def _carddav(request, mailbox, rest, method):
    contacts = mailbox.contacts
    if not rest:  # the addressbook collection
        if method == "PROPFIND":
            depth = request.META.get("HTTP_DEPTH", "0")
            coll_props = (
                "<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>"
                "<d:displayname>Contacts</d:displayname>"
                f"<cs:getctag>{_ctag(contacts)}</cs:getctag>"
            )
            responses = [_response(f"{BASE}/{mailbox.email}/addressbook/", coll_props)]
            if depth != "0":
                for c in contacts.all():
                    responses.append(_response(
                        f"{BASE}/{mailbox.email}/addressbook/{c.uid}.vcf",
                        f"<d:getetag>{c.etag}</d:getetag>"
                        "<d:getcontenttype>text/vcard</d:getcontenttype>"))
            return _multistatus(responses)
        if method == "REPORT":
            return _carddav_report(request, mailbox)
        return HttpResponse(status=405)

    # a single card resource
    uid = rest[0][:-4] if rest[0].endswith(".vcf") else rest[0]
    if method == "GET":
        c = contacts.filter(uid=uid).first()
        if not c:
            return HttpResponse(status=404)
        resp = HttpResponse(ics.to_vcard(c), content_type="text/vcard; charset=utf-8")
        resp["ETag"] = c.etag
        return resp
    if method == "PUT":
        data = ics.parse_vcard(request.body.decode("utf-8", "replace"))
        email = data["email"] or f"{uid}@local"
        c, created = Contact.objects.update_or_create(
            mailbox=mailbox, email=email,
            defaults={"uid": uid, "name": data["name"],
                      "organization": data["organization"], "phone": data["phone"]})
        resp = HttpResponse(status=201 if created else 204)
        resp["ETag"] = c.etag
        return resp
    if method == "DELETE":
        contacts.filter(uid=uid).delete()
        return HttpResponse(status=204)
    return HttpResponse(status=405)


def _carddav_report(request, mailbox):
    hrefs = re.findall(r"<[^>]*href>([^<]+)</", request.body.decode("utf-8", "replace"), re.I)
    qs = mailbox.contacts.all()
    if hrefs:
        uids = [h.rstrip("/").split("/")[-1].removesuffix(".vcf") for h in hrefs]
        qs = qs.filter(uid__in=uids)
    responses = []
    for c in qs:
        data = escape(ics.to_vcard(c))
        responses.append(_response(
            f"{BASE}/{mailbox.email}/addressbook/{c.uid}.vcf",
            f"<d:getetag>{c.etag}</d:getetag><card:address-data>{data}</card:address-data>"))
    return _multistatus(responses)


# --- CalDAV -----------------------------------------------------------------
def _get_calendar(mailbox, slug):
    cal, _ = Calendar.objects.get_or_create(mailbox=mailbox, slug=slug or "default")
    return cal


def _caldav(request, mailbox, rest, method):
    if not rest:  # calendar-home: list calendars
        if method == "PROPFIND":
            cal = _get_calendar(mailbox, "default")
            home_props = "<d:resourcetype><d:collection/></d:resourcetype>"
            responses = [_response(f"{BASE}/{mailbox.email}/calendars/", home_props)]
            responses.append(_response(
                f"{BASE}/{mailbox.email}/calendars/{cal.slug}/",
                "<d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>"
                f"<d:displayname>{escape(cal.name)}</d:displayname>"
                f"<cs:getctag>{_ctag(Event.objects.filter(calendar=cal))}</cs:getctag>"
                '<cal:supported-calendar-component-set><cal:comp name="VEVENT"/>'
                "</cal:supported-calendar-component-set>"))
            return _multistatus(responses)
        return HttpResponse(status=405)

    cal = _get_calendar(mailbox, rest[0])
    events = Event.objects.filter(calendar=cal)

    if len(rest) == 1:  # the calendar collection
        if method == "PROPFIND":
            depth = request.META.get("HTTP_DEPTH", "0")
            coll = (
                "<d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>"
                f"<d:displayname>{escape(cal.name)}</d:displayname>"
                f"<cs:getctag>{_ctag(events)}</cs:getctag>")
            responses = [_response(f"{BASE}/{mailbox.email}/calendars/{cal.slug}/", coll)]
            if depth != "0":
                for e in events:
                    responses.append(_response(
                        f"{BASE}/{mailbox.email}/calendars/{cal.slug}/{e.uid}.ics",
                        f"<d:getetag>{e.etag}</d:getetag>"
                        "<d:getcontenttype>text/calendar</d:getcontenttype>"))
            return _multistatus(responses)
        if method == "REPORT":
            return _caldav_report(request, mailbox, cal)
        return HttpResponse(status=405)

    # a single event resource
    uid = rest[1][:-4] if rest[1].endswith(".ics") else rest[1]
    if method == "GET":
        e = events.filter(uid=uid).first()
        if not e:
            return HttpResponse(status=404)
        resp = HttpResponse(ics.to_ical(e), content_type="text/calendar; charset=utf-8")
        resp["ETag"] = e.etag
        return resp
    if method == "PUT":
        data = ics.parse_ical(request.body.decode("utf-8", "replace"))
        e = events.filter(uid=uid).first()
        defaults = {"summary": data["summary"], "description": data["description"],
                    "location": data["location"], "dtstart": data["dtstart"],
                    "dtend": data["dtend"], "all_day": data["all_day"],
                    "rrule": data["rrule"]}
        if e:
            for k, v in defaults.items():
                setattr(e, k, v)
            e.sequence += 1
            e.save()
            created = False
        else:
            e = Event.objects.create(calendar=cal, uid=uid, **defaults)
            created = True
        resp = HttpResponse(status=201 if created else 204)
        resp["ETag"] = e.etag
        return resp
    if method == "DELETE":
        events.filter(uid=uid).delete()
        return HttpResponse(status=204)
    return HttpResponse(status=405)


def _caldav_report(request, mailbox, cal):
    body = request.body.decode("utf-8", "replace")
    hrefs = re.findall(r"<[^>]*href>([^<]+)</", body, re.I)
    qs = Event.objects.filter(calendar=cal)
    if hrefs:
        uids = [h.rstrip("/").split("/")[-1].removesuffix(".ics") for h in hrefs]
        qs = qs.filter(uid__in=uids)
    responses = []
    for e in qs:
        data = escape(ics.to_ical(e))
        responses.append(_response(
            f"{BASE}/{mailbox.email}/calendars/{cal.slug}/{e.uid}.ics",
            f"<d:getetag>{e.etag}</d:getetag><cal:calendar-data>{data}</cal:calendar-data>"))
    return _multistatus(responses)
