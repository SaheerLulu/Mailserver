from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import Alias, Attachment, Domain, Mailbox, Message


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
        ("Profile", {"fields": ("full_name", "domain", "quota_bytes")}),
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
    list_display = ("subject", "mailbox", "folder", "from_addr", "date", "is_read")
    list_filter = ("folder", "is_read", "is_flagged")
    search_fields = ("subject", "from_addr", "to_addrs", "message_id")
    date_hierarchy = "date"
    inlines = [AttachmentInline]
    readonly_fields = ("created_at", "size", "message_id", "in_reply_to")
