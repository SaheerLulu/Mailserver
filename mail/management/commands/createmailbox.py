"""Create or update a mailbox from the command line."""
import getpass

from django.core.management.base import BaseCommand, CommandError

from mail.models import Mailbox


class Command(BaseCommand):
    help = "Create (or update the password of) a mailbox."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--password", default=None,
                            help="Set non-interactively (otherwise prompted)")
        parser.add_argument("--name", default="", help="Display name")
        parser.add_argument("--quota-mb", type=int, default=0,
                            help="Quota in MB (0 = unlimited)")

    def handle(self, *args, **options):
        email = options["email"].lower()
        if "@" not in email:
            raise CommandError("email must be a full address like user@example.com")

        password = options["password"]
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Confirm: "):
                raise CommandError("passwords did not match")

        quota_bytes = options["quota_mb"] * 1024 * 1024
        mailbox, created = Mailbox.objects.get_or_create(email=email)
        mailbox.full_name = options["name"] or mailbox.full_name
        mailbox.quota_bytes = quota_bytes
        mailbox.is_active = True
        mailbox.set_password(password)
        mailbox.save()

        verb = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"✓ mailbox {email} {verb}"))
