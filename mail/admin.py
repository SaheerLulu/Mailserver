from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import (Alias, Attachment, Domain, Filter, Label, Mailbox,
                     Message, OutboundMessage)


class MailboxCreationForm(BaseUserCreationForm):
    class Meta:
        model = Mailbox
        fields = ("email", "full_name", "quota_bytes")


class MailboxChangeForm(UserChangeForm):
    class Meta:
        model = Mailbox
        fields = "__all__"


@admin.register(Mailbox)
class MailboxAdmin(UserAdmin):
    add_form = MailboxCreationForm
    form = MailboxChangeForm
    model = Mailbox
    ordering = ("email",)
    list_display = ("email", "domain", "is_active", "is_staff", "quota_bytes")
    list_filter = ("is_active", "is_staff", "is_superuser", "domain")
    search_fields = ("email", "full_name")
    readonly_fields = ("last_login",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "domain", "quota_bytes", "signature")}),
        ("Spam", {"fields": ("spam_threshold",)}),
        ("Vacation auto-reply", {"fields": ("vacation_enabled", "vacation_subject",
                                            "vacation_message")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser",
                                    "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "quota_bytes",
                       "usable_password", "password1", "password2"),
        }),
    )


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "dkim_selector", "has_dkim", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at",)


@admin.register(Alias)
class AliasAdmin(admin.ModelAdmin):
    list_display = ("source", "destination", "domain", "is_active")
    list_filter = ("is_active", "domain")
    search_fields = ("source", "destination")


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ("filename", "content_type", "size", "file")
    can_delete = False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "mailbox", "folder", "from_addr", "date",
                    "spam_score", "is_spam", "is_read")
    list_filter = ("folder", "is_spam", "is_read", "is_flagged")
    search_fields = ("subject", "from_addr", "to_addrs", "message_id", "body_text")
    date_hierarchy = "date"
    inlines = [AttachmentInline]
    readonly_fields = ("created_at", "size", "message_id", "in_reply_to",
                       "thread_id", "spam_score")


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ("name", "mailbox", "color")
    search_fields = ("name",)
    list_filter = ("mailbox",)


@admin.register(Filter)
class FilterAdmin(admin.ModelAdmin):
    list_display = ("__str__", "mailbox", "priority", "action", "action_arg", "is_active")
    list_filter = ("mailbox", "is_active", "action")
    list_editable = ("priority", "is_active")


@admin.register(OutboundMessage)
class OutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("pk", "status", "mail_from", "recipients", "attempts",
                    "next_attempt", "updated_at")
    list_filter = ("status",)
    search_fields = ("mail_from", "recipients")
    readonly_fields = ("created_at", "updated_at", "attempts", "last_error")

    actions = ["requeue"]

    @admin.action(description="Requeue selected (retry now)")
    def requeue(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(status=OutboundMessage.Status.QUEUED,
                                  next_attempt=timezone.now(), attempts=0)
        self.message_user(request, f"{updated} message(s) requeued.")
