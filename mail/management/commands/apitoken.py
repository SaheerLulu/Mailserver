"""Create a REST API token for a mailbox."""
from django.core.management.base import BaseCommand, CommandError

from mail.models import ApiToken, Mailbox


class Command(BaseCommand):
    help = "Create an API token for a mailbox and print it."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="cli", help="Label for the token")

    def handle(self, *args, **options):
        mailbox = Mailbox.objects.filter(email=options["email"].lower()).first()
        if not mailbox:
            raise CommandError(f"no such mailbox: {options['email']}")
        token = ApiToken.objects.create(mailbox=mailbox, name=options["name"])
        self.stdout.write(self.style.SUCCESS("✓ API token created"))
        self.stdout.write(f"  {token.key}")
        self.stdout.write("Use it as:  Authorization: Bearer <token>")
