"""Run the IMAP server."""
import asyncio
import os
import ssl

from django.conf import settings
from django.core.management.base import BaseCommand

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")


class Command(BaseCommand):
    help = "Run the IMAP4rev1 server."

    def handle(self, *args, **options):
        from mail.imap.server import handle_client

        ssl_ctx = None
        if settings.SMTP_TLS_CERT and settings.SMTP_TLS_KEY:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(settings.SMTP_TLS_CERT, settings.SMTP_TLS_KEY)

        async def main():
            server = await asyncio.start_server(
                handle_client, "0.0.0.0", settings.IMAP_PORT)
            extra = []
            if ssl_ctx:
                tls = await asyncio.start_server(
                    handle_client, "0.0.0.0", 993, ssl=ssl_ctx)
                extra.append(tls)
            self.stdout.write(self.style.SUCCESS(
                f"IMAP on 0.0.0.0:{settings.IMAP_PORT}"
                + (" + IMAPS on 0.0.0.0:993" if ssl_ctx else "")))
            async with server:
                await server.serve_forever()

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            self.stdout.write("IMAP server stopped")
