"""A small token-authenticated JSON REST API.

Authenticate with a bearer token (create one in the admin or via
`manage.py apitoken <email>`):

    Authorization: Bearer <token>

Endpoints:
    GET  /api/folders/
    GET  /api/messages/?folder=INBOX&q=term&limit=50
    GET  /api/messages/<id>/
    POST /api/messages/<id>/action/   {"action": "read|trash|spam|flag|..."}
    POST /api/send/                   {"to": [...], "cc": [...], "subject", "body"}
    GET  /api/contacts/   POST /api/contacts/   {"name","email",...}
"""
import functools
import json
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import delivery
from .models import ApiToken, Contact, Message

log = logging.getLogger("mail")


def _authenticate(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = (ApiToken.objects.filter(key=auth[7:].strip())
             .select_related("mailbox").first())
    if token and token.mailbox.is_active:
        ApiToken.objects.filter(pk=token.pk).update(last_used=timezone.now())
        return token.mailbox
    return None


def api(*methods):
    """Decorator: token auth + method check + JSON error handling."""
    def outer(view):
        @csrf_exempt
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return JsonResponse({"error": "method not allowed"}, status=405)
            mailbox = _authenticate(request)
            if not mailbox:
                return JsonResponse({"error": "unauthorized"}, status=401)
            request.api_mailbox = mailbox
            try:
                return view(request, *args, **kwargs)
            except Exception:  # noqa: BLE001
                log.exception("API error")
                return JsonResponse({"error": "server error"}, status=500)
        return wrapper
    return outer


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


def _msg_summary(m: Message) -> dict:
    return {
        "id": m.id, "folder": m.folder, "thread_id": m.thread_id,
        "from": m.from_addr, "to": m.to_addrs, "subject": m.subject,
        "date": m.date.isoformat(), "is_read": m.is_read,
        "is_flagged": m.is_flagged, "is_spam": m.is_spam,
        "spam_score": m.spam_score,
        "labels": list(m.labels.values_list("name", flat=True)),
    }


@api("GET")
def folders(request):
    mb = request.api_mailbox
    data = []
    for f in Message.Folder.values:
        qs = mb.messages.filter(folder=f)
        data.append({"folder": f, "total": qs.count(),
                     "unread": qs.filter(is_read=False).count()})
    return JsonResponse({"folders": data})


@api("GET")
def messages(request):
    mb = request.api_mailbox
    qs = mb.messages.all().prefetch_related("labels")
    folder = request.GET.get("folder")
    if folder:
        qs = qs.filter(folder=folder.upper())
    q = request.GET.get("q")
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(subject__icontains=q) | Q(body_text__icontains=q)
                       | Q(from_addr__icontains=q))
    try:
        limit = min(200, int(request.GET.get("limit", 50)))
    except ValueError:
        limit = 50
    return JsonResponse({"messages": [_msg_summary(m) for m in qs[:limit]]})


@api("GET")
def message_detail(request, pk):
    m = request.api_mailbox.messages.filter(pk=pk).prefetch_related("attachments").first()
    if not m:
        return JsonResponse({"error": "not found"}, status=404)
    if not m.is_read:
        m.is_read = True
        m.save(update_fields=["is_read"])
    data = _msg_summary(m)
    data.update({
        "cc": m.cc_addrs, "in_reply_to": m.in_reply_to,
        "body_text": m.body_text, "body_html": m.body_html,
        "attachments": [{"id": a.id, "filename": a.filename,
                         "content_type": a.content_type, "size": a.size}
                        for a in m.attachments.all()],
    })
    return JsonResponse(data)


@api("POST")
def message_action(request, pk):
    m = request.api_mailbox.messages.filter(pk=pk).first()
    if not m:
        return JsonResponse({"error": "not found"}, status=404)
    action = _body(request).get("action", "")
    if action == "read":
        m.is_read = True
    elif action == "unread":
        m.is_read = False
    elif action == "flag":
        m.is_flagged = not m.is_flagged
    elif action == "trash":
        m.folder = Message.Folder.TRASH
    elif action == "spam":
        m.folder, m.is_spam = Message.Folder.JUNK, True
    elif action == "notspam":
        m.folder, m.is_spam = Message.Folder.INBOX, False
    elif action.upper() in Message.Folder.values:
        m.folder = action.upper()
    else:
        return JsonResponse({"error": "unknown action"}, status=400)
    m.save()
    return JsonResponse(_msg_summary(m))


@api("POST")
def send(request):
    body = _body(request)
    to = body.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not to:
        return JsonResponse({"error": "no recipients"}, status=400)
    cc = body.get("cc") or []
    if isinstance(cc, str):
        cc = [cc]
    delivery.send_from_webmail(request.api_mailbox, {
        "to": ", ".join(to), "cc": ", ".join(cc),
        "subject": body.get("subject", ""), "body": body.get("body", ""),
        "in_reply_to": body.get("in_reply_to", ""),
    })
    return JsonResponse({"status": "queued"})


@api("GET", "POST")
def contacts(request):
    mb = request.api_mailbox
    if request.method == "POST":
        body = _body(request)
        email = (body.get("email") or "").strip().lower()
        if not email:
            return JsonResponse({"error": "email required"}, status=400)
        contact, _ = Contact.objects.update_or_create(
            mailbox=mb, email=email,
            defaults={"name": body.get("name", ""),
                      "organization": body.get("organization", ""),
                      "phone": body.get("phone", "")})
        return JsonResponse({"id": contact.id, "email": contact.email,
                             "name": contact.name}, status=201)
    return JsonResponse({"contacts": [
        {"id": c.id, "name": c.name, "email": c.email,
         "organization": c.organization, "phone": c.phone}
        for c in mb.contacts.all()]})
