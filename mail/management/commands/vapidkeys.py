"""Generate a VAPID keypair for Web Push and print the env vars to set."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "Generate a VAPID (Web Push) keypair."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())

        priv_raw = key.private_numbers().private_value.to_bytes(32, "big")
        pub = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)

        self.stdout.write(self.style.SUCCESS("VAPID keypair generated. Add to .env:\n"))
        self.stdout.write(f"VAPID_PRIVATE_KEY={_b64(priv_raw)}")
        self.stdout.write(f"VAPID_PUBLIC_KEY={_b64(pub)}")
        self.stdout.write("VAPID_SUBJECT=mailto:postmaster@yourdomain")
