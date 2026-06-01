"""
Data model for the mail server.

  Domain          — a mail domain we're authoritative for (+ its DKIM key)
  Mailbox         — a user account / mailbox (also the Django auth user)
  Alias           — address forwarding, including "@domain" catch-alls
  Label           — a user-defined tag that can be applied to messages
  Filter          — a server-side rule applied to incoming mail
  Message         — a stored message belonging to a mailbox, filed in a folder
  Attachment      — a file extracted from a message
  OutboundMessage — an item in the persistent outbound delivery queue
"""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import MailboxManager


class Domain(models.Model):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    dkim_selector = models.CharField(max_length=63, blank=True, default="mail")
    dkim_private_key = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def has_dkim(self) -> bool:
        return bool(self.dkim_private_key.strip())


class Mailbox(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    domain = models.ForeignKey(
        Domain, on_delete=models.CASCADE, related_name="mailboxes",
        null=True, blank=True,
    )
    full_name = models.CharField(max_length=255, blank=True)
    quota_bytes = models.BigIntegerField(default=0)  # 0 == unlimited
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Per-mailbox preferences.
    signature = models.TextField(blank=True)
    spam_threshold = models.FloatField(
        default=5.0, help_text="Messages scoring at/above this go to Junk")
    vacation_enabled = models.BooleanField(default=False)
    vacation_subject = models.CharField(max_length=255, blank=True,
                                        default="Out of office")
    vacation_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    objects = MailboxManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]
        verbose_name_plural = "mailboxes"

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()
            if not self.domain_id and "@" in self.email:
                domain_name = self.email.split("@", 1)[1]
                self.domain, _ = Domain.objects.get_or_create(name=domain_name)
        super().save(*args, **kwargs)

    @property
    def local_part(self) -> str:
        return self.email.split("@", 1)[0]

    @property
    def used_bytes(self) -> int:
        return self.messages.aggregate(total=models.Sum("size"))["total"] or 0


class Alias(models.Model):
    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="aliases")
    # Full address (info@example.com) or a catch-all (@example.com).
    source = models.CharField(max_length=255)
    # A local mailbox address or any remote address.
    destination = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("source", "destination")
        ordering = ["source"]
        verbose_name_plural = "aliases"

    def __str__(self):
        return f"{self.source} → {self.destination}"


class Label(models.Model):
    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE, related_name="labels")
    name = models.CharField(max_length=64)
    color = models.CharField(max_length=7, default="#6b7280")  # hex
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("mailbox", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Filter(models.Model):
    """A server-side rule: when a condition matches incoming mail, act on it."""

    class Field(models.TextChoices):
        FROM = "FROM", "From"
        TO = "TO", "To/Cc"
        SUBJECT = "SUBJECT", "Subject"
        BODY = "BODY", "Body"
        ANY = "ANY", "Anywhere"

    class Match(models.TextChoices):
        CONTAINS = "CONTAINS", "contains"
        EQUALS = "EQUALS", "equals"
        STARTSWITH = "STARTSWITH", "starts with"
        REGEX = "REGEX", "matches regex"

    class Action(models.TextChoices):
        MOVE = "MOVE", "Move to folder"
        LABEL = "LABEL", "Apply label"
        READ = "READ", "Mark as read"
        STAR = "STAR", "Flag/star"
        SPAM = "SPAM", "Mark as spam (Junk)"
        DELETE = "DELETE", "Move to Trash"

    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE, related_name="filters")
    name = models.CharField(max_length=128, blank=True)
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    field = models.CharField(max_length=16, choices=Field.choices, default=Field.FROM)
    match = models.CharField(max_length=16, choices=Match.choices, default=Match.CONTAINS)
    value = models.CharField(max_length=512)

    action = models.CharField(max_length=16, choices=Action.choices, default=Action.MOVE)
    action_arg = models.CharField(max_length=128, blank=True,
                                  help_text="Folder name or label name")

    class Meta:
        ordering = ["priority", "id"]

    def __str__(self):
        return self.name or f"{self.field} {self.match} {self.value!r}"


class Message(models.Model):
    class Folder(models.TextChoices):
        INBOX = "INBOX", "Inbox"
        SENT = "SENT", "Sent"
        DRAFTS = "DRAFTS", "Drafts"
        JUNK = "JUNK", "Junk"
        TRASH = "TRASH", "Trash"
        ARCHIVE = "ARCHIVE", "Archive"

    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE, related_name="messages")
    folder = models.CharField(max_length=16, choices=Folder.choices, default=Folder.INBOX)
    labels = models.ManyToManyField(Label, blank=True, related_name="messages")

    message_id = models.CharField(max_length=998, blank=True, db_index=True)
    in_reply_to = models.CharField(max_length=998, blank=True)
    references = models.TextField(blank=True)
    # Conversation grouping key.
    thread_id = models.CharField(max_length=255, blank=True, db_index=True)

    from_addr = models.CharField(max_length=998, blank=True)
    to_addrs = models.TextField(blank=True)
    cc_addrs = models.TextField(blank=True)
    subject = models.CharField(max_length=998, blank=True)
    date = models.DateTimeField(default=timezone.now)

    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    eml = models.FileField(upload_to="messages/%Y/%m/", blank=True)
    size = models.PositiveBigIntegerField(default=0)

    spam_score = models.FloatField(default=0.0)
    is_spam = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    is_flagged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["mailbox", "folder", "-date"]),
            models.Index(fields=["mailbox", "thread_id"]),
        ]

    def __str__(self):
        return f"[{self.folder}] {self.subject or '(no subject)'}"

    @property
    def preview(self) -> str:
        text = (self.body_text or "").strip()
        return (text[:140] + "…") if len(text) > 140 else text


class Attachment(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    file = models.FileField(upload_to="attachments/%Y/%m/")

    def __str__(self):
        return self.filename


class OutboundMessage(models.Model):
    """An item in the persistent outbound delivery queue (with retries)."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    sender_mailbox = models.ForeignKey(
        Mailbox, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="outbound",
    )
    mail_from = models.CharField(max_length=998, blank=True)
    recipients = models.TextField(help_text="Comma-separated envelope recipients")
    raw = models.FileField(upload_to="queue/%Y/%m/")

    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=10)
    last_error = models.TextField(blank=True)
    next_attempt = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_attempt"]

    def __str__(self):
        return f"{self.status} → {self.recipients} ({self.attempts} tries)"

    @property
    def recipient_list(self):
        return [r.strip() for r in self.recipients.split(",") if r.strip()]
