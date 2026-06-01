"""Run the POP3 server."""
import asyncio
import os

from django.conf import settings
from django.core.management.base import BaseCommand

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")


class Command(BaseCommand):
    help = "Run the POP3 server."

    def handle(self, *args, **options):
        from mail.pop3.server import handle_client

        async def main():
            server = await asyncio.start_server(
                handle_client, "0.0.0.0", settings.POP3_PORT)
            self.stdout.write(self.style.SUCCESS(
                f"POP3 on 0.0.0.0:{settings.POP3_PORT}"))
            async with server:
                await server.serve_forever()

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            self.stdout.write("POP3 server stopped")
