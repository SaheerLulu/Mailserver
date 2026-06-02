import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import delivery, search
from .forms import ComposeForm, SettingsForm
from .models import Label, Message, OutboundMessage

CATEGORIES = [
    Message.Category.PRIMARY, Message.Category.SOCIAL,
    Message.Category.PROMOTIONS, Message.Category.UPDATES, Message.Category.FORUMS,
]

log = logging.getLogger("mail")

FOLDERS = [
    Message.Folder.INBOX, Message.Folder.SENT, Message.Folder.DRAFTS,
    Message.Folder.JUNK, Message.Folder.ARCHIVE, Message.Folder.TRASH,
]


def _sidebar(user, current=None):
    folder_list = []
    for f in FOLDERS:
        qs = user.messages.filter(folder=f)
        folder_list.append({
            "name": f,
            "label": f.capitalize(),
            "unread": qs.filter(is_read=False).count() if f != Message.Folder.SENT else 0,
        })
    now = timezone.now()
    return {
        "folder_list": folder_list,
        "current_folder": current,
        "label_list": user.labels.all(),
        "snoozed_count": user.messages.filter(snooze_until__gt=now).count(),
        "scheduled_count": OutboundMessage.objects.filter(
            sender_mailbox=user, scheduled=True,
            status=OutboundMessage.Status.QUEUED, next_attempt__gt=now).count(),
    }


def _threaded(qs):
    """Collapse a message queryset (ordered newest-first) into conversations."""
    seen, items = {}, []
    for m in qs:
        if m.thread_id and m.thread_id in seen:
            seen[m.thread_id]["count"] += 1
            if not m.is_read:
                seen[m.thread_id]["unread"] = True
            continue
        entry = {"msg": m, "count": 1, "unread": not m.is_read}
        if m.thread_id:
            seen[m.thread_id] = entry
        items.append(entry)
    return items


@login_required
def mailbox_view(request, folder="INBOX"):
    folder = folder.upper()
    if folder not in Message.Folder.values:
        raise Http404("Unknown folder")
    qs = request.user.messages.filter(folder=folder).prefetch_related("labels")

    tabs = None
    category = None
    if folder == Message.Folder.INBOX:
        # Hide snoozed + muted conversations from the inbox (Gmail behaviour).
        now = timezone.now()
        qs = qs.filter(Q(snooze_until__isnull=True) | Q(snooze_until__lte=now))
        qs = qs.filter(is_muted=False)
        category = request.GET.get("category", Message.Category.PRIMARY).upper()
        if category in Message.Category.values:
            qs = qs.filter(category=category)
        tabs = [{"name": c, "label": c.capitalize(),
                 "unread": request.user.messages.filter(
                     folder=folder, category=c, is_read=False,
                     is_muted=False).count()}
                for c in CATEGORIES]

    context = {
        "folder": folder,
        "heading": folder.capitalize(),
        "threads": _threaded(qs),
        "tabs": tabs,
        "current_category": category,
        "selectable": True,
        **_sidebar(request.user, folder),
    }
    return render(request, "mail/mailbox.html", context)


@login_required
def search_view(request):
    q = request.GET.get("q", "").strip()
    threads = []
    if q:
        query, folder = search.build_query(q)
        qs = request.user.messages.filter(query)
        if folder:
            qs = qs.filter(folder=folder)
        else:
            qs = qs.exclude(folder=Message.Folder.TRASH)
        threads = _threaded(qs.distinct().prefetch_related("labels"))
    context = {"folder": None, "heading": f"Search: {q}" if q else "Search",
               "threads": threads, "query": q, "is_search": True,
               "selectable": True, **_sidebar(request.user)}
    return render(request, "mail/mailbox.html", context)


@login_required
def snoozed_view(request):
    now = timezone.now()
    qs = (request.user.messages.filter(snooze_until__gt=now)
          .order_by("snooze_until").prefetch_related("labels"))
    return render(request, "mail/mailbox.html",
                  {"folder": None, "heading": "💤 Snoozed", "threads": _threaded(qs),
                   "selectable": True, **_sidebar(request.user)})


@login_required
def scheduled_view(request):
    now = timezone.now()
    scheduled = (OutboundMessage.objects
                 .filter(sender_mailbox=request.user, scheduled=True,
                         status=OutboundMessage.Status.QUEUED, next_attempt__gt=now)
                 .order_by("next_attempt"))
    return render(request, "mail/scheduled.html",
                  {"scheduled": scheduled, "heading": "🕒 Scheduled",
                   **_sidebar(request.user)})


@login_required
@require_POST
def cancel_scheduled(request, pk):
    om = get_object_or_404(OutboundMessage, pk=pk, sender_mailbox=request.user,
                           scheduled=True, status=OutboundMessage.Status.QUEUED)
    om.delete()
    messages.info(request, "Scheduled message cancelled.")
    return redirect("scheduled")


@login_required
def label_view(request, pk):
    label = get_object_or_404(request.user.labels, pk=pk)
    qs = label.messages.filter(mailbox=request.user).prefetch_related("labels")
    context = {"folder": None, "heading": f"🏷 {label.name}",
               "threads": _threaded(qs), **_sidebar(request.user)}
    return render(request, "mail/mailbox.html", context)


@login_required
def message_view(request, pk):
    message = get_object_or_404(
        request.user.messages.prefetch_related("labels", "attachments"), pk=pk)
    if not message.is_read:
        message.is_read = True
        message.save(update_fields=["is_read"])
    # Show the whole conversation.
    if message.thread_id:
        thread = (request.user.messages.filter(thread_id=message.thread_id)
                  .order_by("date").prefetch_related("attachments"))
    else:
        thread = [message]
    context = {"message": message, "thread": thread,
               "all_labels": request.user.labels.all(), **_sidebar(request.user)}
    return render(request, "mail/message.html", context)


@login_required
@require_POST
def message_action(request, pk):
    message = get_object_or_404(request.user.messages, pk=pk)
    action = request.POST.get("action", "")
    redirect_to = request.POST.get("next") or "mailbox"

    if action == "delete":
        message.delete()
        messages.info(request, "Message permanently deleted.")
        return redirect("mailbox_folder", folder=Message.Folder.TRASH)
    elif action == "trash":
        message.folder = Message.Folder.TRASH
        message.save(update_fields=["folder"])
    elif action == "spam":
        message.folder = Message.Folder.JUNK
        message.is_spam = True
        message.save(update_fields=["folder", "is_spam"])
        _train(message, is_spam=True)
    elif action == "notspam":
        message.folder = Message.Folder.INBOX
        message.is_spam = False
        message.save(update_fields=["folder", "is_spam"])
        _train(message, is_spam=False)
    elif action == "flag":
        message.is_flagged = not message.is_flagged
        message.save(update_fields=["is_flagged"])
    elif action == "unread":
        message.is_read = False
        message.save(update_fields=["is_read"])
    elif action == "important":
        message.is_important = not message.is_important
        message.save(update_fields=["is_important"])
    elif action in ("mute", "unmute"):
        message.is_muted = (action == "mute")
        message.save(update_fields=["is_muted"])
    elif action == "snooze":
        when = _parse_dt(request.POST.get("until"))
        if when:
            # Snooze the whole conversation.
            request.user.messages.filter(thread_id=message.thread_id).update(
                snooze_until=when) if message.thread_id else None
            message.snooze_until = when
            message.save(update_fields=["snooze_until"])
            messages.info(request, f"Snoozed until {when:%Y-%m-%d %H:%M}.")
        return redirect(redirect_to)
    elif action == "unsnooze":
        message.snooze_until = None
        message.save(update_fields=["snooze_until"])
    elif action == "label":
        label = get_object_or_404(request.user.labels, pk=request.POST.get("label_id"))
        if message.labels.filter(pk=label.pk).exists():
            message.labels.remove(label)
        else:
            message.labels.add(label)
    elif action in Message.Folder.values:
        message.folder = action
        message.save(update_fields=["folder"])

    return redirect(redirect_to)


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


@login_required
@require_POST
def bulk_action(request):
    """Apply an action to many selected messages at once."""
    ids = request.POST.getlist("ids")
    action = request.POST.get("action", "")
    qs = request.user.messages.filter(pk__in=ids)
    if action == "read":
        qs.update(is_read=True)
    elif action == "unread":
        qs.update(is_read=False)
    elif action == "star":
        qs.update(is_flagged=True)
    elif action == "archive":
        qs.update(folder=Message.Folder.ARCHIVE)
    elif action == "trash":
        qs.update(folder=Message.Folder.TRASH)
    elif action == "spam":
        qs.update(folder=Message.Folder.JUNK, is_spam=True)
    elif action == "delete":
        qs.delete()
    elif action == "important":
        qs.update(is_important=True)
    messages.info(request, f"Updated {len(ids)} message(s).")
    return redirect(request.POST.get("next") or "mailbox")


@login_required
@require_POST
def mark_all_read(request, folder):
    folder = folder.upper()
    if folder in Message.Folder.values:
        request.user.messages.filter(folder=folder, is_read=False).update(is_read=True)
        messages.info(request, f"Marked all read in {folder.capitalize()}.")
    return redirect("mailbox_folder", folder=folder)


@login_required
def autocomplete(request):
    """Recipient autocomplete from the address book + recent correspondents."""
    term = request.GET.get("q", "").strip().lower()
    contacts = request.user.contacts.all()
    if term:
        contacts = contacts.filter(Q(email__icontains=term) | Q(name__icontains=term))
    results = [{"email": c.email, "name": c.name} for c in contacts[:10]]
    return JsonResponse({"results": results})


def _train(message, is_spam):
    """Feed a message into the Bayesian classifier (best-effort)."""
    from . import bayes
    try:
        bayes.train({"subject": message.subject, "from_addr": message.from_addr,
                     "body_text": message.body_text}, is_spam=is_spam)
    except Exception:  # noqa: BLE001
        log.exception("Bayes training failed")


@login_required
@require_POST
def label_create(request):
    name = request.POST.get("name", "").strip()
    color = request.POST.get("color", "#6b7280").strip() or "#6b7280"
    if name:
        Label.objects.get_or_create(mailbox=request.user, name=name[:64],
                                    defaults={"color": color[:7]})
    return redirect(request.POST.get("next") or "mailbox")


def _sig(user):
    return f"\n\n-- \n{user.signature}" if user.signature else ""


def _compose_initial(request):
    """Build compose initial data for reply / reply-all / forward / draft."""
    user = request.user
    sig = _sig(user)

    draft_pk = request.GET.get("draft")
    if draft_pk:
        d = user.messages.filter(pk=draft_pk, folder=Message.Folder.DRAFTS).first()
        if d:
            return {"to": d.to_addrs, "cc": d.cc_addrs, "subject": d.subject,
                    "body": d.body_text, "draft_id": d.pk,
                    "in_reply_to": d.in_reply_to, "references": d.references}

    src_pk = request.GET.get("reply") or request.GET.get("replyall") or request.GET.get("forward")
    if not src_pk:
        return {"body": sig} if sig else {}
    o = user.messages.filter(pk=src_pk).first()
    if not o:
        return {"body": sig} if sig else {}

    quoted = "\n".join(f"> {ln}" for ln in o.body_text.splitlines())
    attribution = f"\n\nOn {o.date:%Y-%m-%d %H:%M}, {o.from_addr} wrote:\n{quoted}"
    refs = (o.references + " " + o.message_id).strip()

    if "forward" in request.GET:
        subject = o.subject if o.subject.lower().startswith("fwd:") else f"Fwd: {o.subject}"
        fwd = (f"\n\n---------- Forwarded message ----------\nFrom: {o.from_addr}\n"
               f"Date: {o.date:%Y-%m-%d %H:%M}\nSubject: {o.subject}\n"
               f"To: {o.to_addrs}\n\n{o.body_text}")
        return {"subject": subject, "body": sig + fwd}

    subject = o.subject if o.subject.lower().startswith("re:") else f"Re: {o.subject}"
    initial = {"to": o.from_addr, "subject": subject, "in_reply_to": o.message_id,
               "references": refs, "body": sig + attribution}
    if "replyall" in request.GET:
        others = [a for a in (o.to_addrs + ", " + o.cc_addrs).split(",")
                  if a.strip() and user.email not in a.lower()
                  and o.from_addr not in a]
        initial["cc"] = ", ".join(a.strip() for a in others)
    return initial


def _save_draft(user, data):
    draft_id = data.get("draft_id")
    fields = dict(folder=Message.Folder.DRAFTS, to_addrs=data.get("to", ""),
                  cc_addrs=data.get("cc", ""), subject=data.get("subject", ""),
                  body_text=data.get("body", ""), from_addr=user.email,
                  in_reply_to=data.get("in_reply_to", ""),
                  references=data.get("references", ""), is_read=True)
    if draft_id:
        Message.objects.filter(pk=draft_id, mailbox=user,
                               folder=Message.Folder.DRAFTS).update(**fields)
        return Message.objects.filter(pk=draft_id).first()
    return Message.objects.create(mailbox=user, date=timezone.now(), **fields)


@login_required
def compose_view(request):
    if request.method == "POST":
        intent = request.POST.get("intent", "send")
        require = intent == "send"
        form = ComposeForm(request.POST, require_recipient=require)
        if form.is_valid():
            data = form.cleaned_data
            if intent == "draft":
                _save_draft(request.user, data)
                messages.success(request, "Draft saved.")
                return redirect("mailbox_folder", folder=Message.Folder.DRAFTS)
            try:
                if intent == "schedule" and data.get("send_at"):
                    pass  # send_at already in cleaned_data
                deferred = delivery.send_from_webmail(request.user, data)
                # Sending a draft removes it.
                if data.get("draft_id"):
                    Message.objects.filter(pk=data["draft_id"], mailbox=request.user,
                                           folder=Message.Folder.DRAFTS).delete()
                if deferred:
                    messages.success(request, "Message scheduled.")
                    return redirect("scheduled")
                messages.success(request, "Message sent.")
                return redirect("mailbox_folder", folder=Message.Folder.SENT)
            except Exception:  # noqa: BLE001
                log.exception("Sending failed")
                messages.error(request, "Sending failed — see server logs.")
    else:
        form = ComposeForm(initial=_compose_initial(request))

    return render(request, "mail/compose.html", {"form": form, **_sidebar(request.user)})


@login_required
def contacts_view(request):
    from .models import Contact
    if request.method == "POST" and request.POST.get("delete"):
        Contact.objects.filter(mailbox=request.user, pk=request.POST["delete"]).delete()
        return redirect("contacts")
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        if email:
            Contact.objects.update_or_create(
                mailbox=request.user, email=email,
                defaults={"name": request.POST.get("name", ""),
                          "organization": request.POST.get("organization", ""),
                          "phone": request.POST.get("phone", "")})
            messages.success(request, "Contact saved.")
        return redirect("contacts")
    return render(request, "mail/contacts.html",
                  {"contacts": request.user.contacts.all(), **_sidebar(request.user)})


@login_required
def calendar_view(request):
    from datetime import datetime
    from django.utils import timezone as tz
    from .models import Calendar, Event
    cal, _ = Calendar.objects.get_or_create(mailbox=request.user, slug="default")

    if request.method == "POST" and request.POST.get("delete"):
        Event.objects.filter(calendar=cal, pk=request.POST["delete"]).delete()
        return redirect("calendar")
    if request.method == "POST":
        import uuid
        summary = request.POST.get("summary", "").strip()

        def _parse(name, default=None):
            val = request.POST.get(name)
            if not val:
                return default
            try:
                return tz.make_aware(datetime.fromisoformat(val))
            except ValueError:
                return default
        start = _parse("dtstart")
        if summary and start:
            Event.objects.create(
                calendar=cal, uid=str(uuid.uuid4()), summary=summary,
                dtstart=start, dtend=_parse("dtend"),
                all_day=bool(request.POST.get("all_day")),
                location=request.POST.get("location", ""),
                description=request.POST.get("description", ""))
            messages.success(request, "Event added.")
        return redirect("calendar")

    from datetime import timedelta
    upcoming = cal.events.filter(dtstart__gte=tz.now() - timedelta(days=1))
    past = cal.events.filter(dtstart__lt=tz.now()).order_by("-dtstart")[:20]
    return render(request, "mail/calendar.html",
                  {"calendar": cal, "upcoming": upcoming, "past": past,
                   "caldav_url": f"/dav/{request.user.email}/calendars/default/",
                   **_sidebar(request.user)})


# --- Web Push -------------------------------------------------------------
def vapid_public_key(request):
    from django.conf import settings as s
    return JsonResponse({"publicKey": s.VAPID_PUBLIC_KEY})


@login_required
@require_POST
def push_subscribe(request):
    import json
    from .models import PushSubscription
    try:
        data = json.loads(request.body)
        keys = data["keys"]
        PushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={"mailbox": request.user, "p256dh": keys["p256dh"],
                      "auth": keys["auth"]})
    except (KeyError, ValueError):
        return JsonResponse({"error": "bad subscription"}, status=400)
    return JsonResponse({"status": "subscribed"})


def service_worker(request):
    """Served at /sw.js so it controls the whole origin scope."""
    js = """
self.addEventListener('push', function(event) {
  var data = {};
  try { data = event.data.json(); } catch (e) {}
  event.waitUntil(self.registration.showNotification(
    data.title || 'New mail', {body: data.body || '', tag: 'mail-' + (data.id || '')}));
});
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
"""
    return HttpResponse(js, content_type="application/javascript")


@login_required
def settings_view(request):
    if request.method == "POST":
        form = SettingsForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect("settings")
    else:
        form = SettingsForm(instance=request.user)
    return render(request, "mail/settings.html", {"form": form, **_sidebar(request.user)})


@login_required
def message_raw(request, pk):
    message = get_object_or_404(request.user.messages, pk=pk)
    if not message.eml:
        raise Http404("No source available")
    return FileResponse(message.eml.open("rb"), content_type="message/rfc822",
                        filename=f"message-{pk}.eml")


@login_required
def attachment_download(request, pk):
    from .models import Attachment
    attachment = get_object_or_404(
        Attachment, pk=pk, message__mailbox=request.user)
    return FileResponse(attachment.file.open("rb"),
                        content_type=attachment.content_type or "application/octet-stream",
                        as_attachment=True, filename=attachment.filename)
