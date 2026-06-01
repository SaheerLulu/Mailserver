"""
Data model for the mail server.

  Domain    — a mail domain this server is authoritative for (+ its DKIM key)
  Mailbox   — a user account / mailbox (also the Django auth user)
  Alias     — address forwarding, including "@domain" catch-alls
  Message   — a stored message belonging to a mailbox, filed in a folder
  Attachment— a file extracted from a message
"""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import MailboxManager


class Domain(models.Model):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    dkim_selector = models.CharField(max_length=63, blank=True, default="mail")
    # PEM-encoded RSA private key used to sign outbound mail for this domain.
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
    # 0 == unlimited.
    quota_bytes = models.BigIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
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
        # Normalise and keep the domain FK in sync with the address.
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

    message_id = models.CharField(max_length=998, blank=True, db_index=True)
    in_reply_to = models.CharField(max_length=998, blank=True)
    from_addr = models.CharField(max_length=998, blank=True)
    to_addrs = models.TextField(blank=True)
    cc_addrs = models.TextField(blank=True)
    subject = models.CharField(max_length=998, blank=True)
    date = models.DateTimeField(default=timezone.now)

    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    # Full RFC 5322 source, kept for fidelity / download.
    eml = models.FileField(upload_to="messages/%Y/%m/", blank=True)
    size = models.PositiveBigIntegerField(default=0)

    is_read = models.BooleanField(default=False)
    is_flagged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["mailbox", "folder", "-date"]),
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
