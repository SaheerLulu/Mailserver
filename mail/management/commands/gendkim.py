"""Generate a DKIM key for a domain and print the DNS record to publish."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mail import dkimtools
from mail.models import Domain


class Command(BaseCommand):
    help = "Generate (or rotate) a DKIM key for a domain."

    def add_arguments(self, parser):
        parser.add_argument("domain")
        parser.add_argument("--selector", default=None,
                            help=f"DKIM selector (default: {settings.DKIM_SELECTOR})")
        parser.add_argument("--bits", type=int, default=2048)
        parser.add_argument("--force", action="store_true",
                            help="Overwrite an existing key")

    def handle(self, *args, **options):
        name = options["domain"].lower()
        selector = options["selector"] or settings.DKIM_SELECTOR

        domain, _ = Domain.objects.get_or_create(name=name)
        if domain.has_dkim and not options["force"]:
            raise CommandError(
                f"{name} already has a DKIM key; pass --force to rotate it.")

        private_pem, public_b64 = dkimtools.generate_keypair(options["bits"])
        domain.dkim_private_key = private_pem
        domain.dkim_selector = selector
        domain.save(update_fields=["dkim_private_key", "dkim_selector"])

        record_name, record_value = dkimtools.dns_record(selector, name, public_b64)
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ DKIM key stored for {name} (selector: {selector})\n"))
        self.stdout.write("Publish this DNS TXT record:\n")
        self.stdout.write("-" * 70)
        self.stdout.write(f"  Name : {record_name}")
        self.stdout.write(f"  Type : TXT")
        self.stdout.write(f"  Value: {record_value}")
        self.stdout.write("-" * 70)
