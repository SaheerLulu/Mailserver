import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import delivery
from .forms import ComposeForm, SettingsForm
from .models import Label, Message

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
    return {
        "folder_list": folder_list,
        "current_folder": current,
        "label_list": user.labels.all(),
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
    context = {
        "folder": folder,
        "heading": folder.capitalize(),
        "threads": _threaded(qs),
        **_sidebar(request.user, folder),
    }
    return render(request, "mail/mailbox.html", context)


@login_required
def search_view(request):
    q = request.GET.get("q", "").strip()
    threads = []
    if q:
        terms = q.split()
        query = Q()
        for t in terms:
            query &= (Q(subject__icontains=t) | Q(body_text__icontains=t)
                      | Q(from_addr__icontains=t) | Q(to_addrs__icontains=t))
        qs = (request.user.messages.filter(query)
              .exclude(folder=Message.Folder.TRASH).prefetch_related("labels"))
        threads = _threaded(qs)
    context = {"folder": None, "heading": f"Search: {q}" if q else "Search",
               "threads": threads, "query": q, "is_search": True,
               **_sidebar(request.user)}
    return render(request, "mail/mailbox.html", context)


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


@login_required
def compose_view(request):
    initial = {}
    reply_pk = request.GET.get("reply")
    if reply_pk:
        original = request.user.messages.filter(pk=reply_pk).first()
        if original:
            quoted = "\n".join(f"> {line}" for line in original.body_text.splitlines())
            subject = original.subject
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            sig = f"\n\n-- \n{request.user.signature}" if request.user.signature else ""
            initial = {
                "to": original.from_addr,
                "subject": subject,
                "in_reply_to": original.message_id,
                "body": f"{sig}\n\nOn {original.date:%Y-%m-%d %H:%M}, "
                        f"{original.from_addr} wrote:\n{quoted}",
            }
    elif request.user.signature:
        initial = {"body": f"\n\n-- \n{request.user.signature}"}

    if request.method == "POST":
        form = ComposeForm(request.POST)
        if form.is_valid():
            try:
                delivery.send_from_webmail(request.user, form.cleaned_data)
                messages.success(request, "Message sent.")
                return redirect("mailbox_folder", folder=Message.Folder.SENT)
            except Exception:  # noqa: BLE001
                log.exception("Sending failed")
                messages.error(request, "Sending failed — see server logs.")
    else:
        form = ComposeForm(initial=initial)

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
