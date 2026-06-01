"""Run the SMTP services: inbound MTA (:25) and authenticated submission (:587)."""
import os
import signal
import ssl
import threading

from django.conf import settings
from django.core.management.base import BaseCommand

# The SMTP authenticator touches the ORM from inside aiosmtpd's event loop
# thread. Opt into that explicitly before anything imports the handler.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")


class Command(BaseCommand):
    help = "Run the inbound and submission SMTP listeners."

    def handle(self, *args, **options):
        from aiosmtpd.controller import Controller

        from mail.smtp.handler import Authenticator, MailHandler, XOAuthController

        handler = MailHandler()

        tls_context = None
        if settings.SMTP_TLS_CERT and settings.SMTP_TLS_KEY:
            tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            tls_context.load_cert_chain(settings.SMTP_TLS_CERT, settings.SMTP_TLS_KEY)

        ident = f"{settings.MAIL_SERVER_HOSTNAME} ESMTP"

        inbound = Controller(
            handler,
            hostname="0.0.0.0",
            port=settings.SMTP_INBOUND_PORT,
            ident=ident,
            data_size_limit=settings.MAX_MESSAGE_SIZE,
            tls_context=tls_context,
        )
        submission = XOAuthController(
            handler,
            hostname="0.0.0.0",
            port=settings.SMTP_SUBMISSION_PORT,
            ident=ident,
            data_size_limit=settings.MAX_MESSAGE_SIZE,
            authenticator=Authenticator(),
            auth_required=True,
            auth_require_tls=bool(tls_context),
            tls_context=tls_context,
        )

        inbound.start()
        submission.start()
        self.stdout.write(self.style.SUCCESS(
            f"SMTP inbound  : 0.0.0.0:{settings.SMTP_INBOUND_PORT}\n"
            f"SMTP submission: 0.0.0.0:{settings.SMTP_SUBMISSION_PORT} "
            f"(AUTH required, STARTTLS {'enabled' if tls_context else 'disabled'})"
        ))

        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            stop.wait()
        finally:
            self.stdout.write("Shutting down SMTP listeners…")
            inbound.stop()
            submission.stop()
