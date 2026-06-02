from django.contrib.auth.base_user import BaseUserManager


class MailboxManager(BaseUserManager):
    """Manager for the Mailbox user model (keyed by email address)."""

    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("A mailbox must have an email address")
        email = self.normalize_email(email).lower()
        mailbox = self.model(email=email, **extra)
        mailbox.set_password(password)
        mailbox.save(using=self._db)
        return mailbox

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self._create(email, password, **extra)
