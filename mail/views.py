import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import delivery
from .forms import ComposeForm
from .models import Message

log = logging.getLogger("mail")

# Folders shown in the sidebar, in order.
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
    return {"folder_list": folder_list, "current_folder": current}


@login_required
def mailbox_view(request, folder="INBOX"):
    folder = folder.upper()
    if folder not in Message.Folder.values:
        raise Http404("Unknown folder")
    message_list = request.user.messages.filter(folder=folder)
    context = {"folder": folder, "message_list": message_list,
               **_sidebar(request.user, folder)}
    return render(request, "mail/mailbox.html", context)


@login_required
def message_view(request, pk):
    message = get_object_or_404(request.user.messages, pk=pk)
    if not message.is_read:
        message.is_read = True
        message.save(update_fields=["is_read"])
    context = {"message": message, **_sidebar(request.user)}
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
    elif action == "flag":
        message.is_flagged = not message.is_flagged
        message.save(update_fields=["is_flagged"])
    elif action == "unread":
        message.is_read = False
        message.save(update_fields=["is_read"])
    elif action in Message.Folder.values:
        message.folder = action
        message.save(update_fields=["folder"])

    return redirect(redirect_to)


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
            initial = {
                "to": original.from_addr,
                "subject": subject,
                "in_reply_to": original.message_id,
                "body": f"\n\nOn {original.date:%Y-%m-%d %H:%M}, "
                        f"{original.from_addr} wrote:\n{quoted}",
            }

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
