"""aiosmtpd handler + authenticator bridging SMTP to the Django ORM."""
import logging

from aiosmtpd.smtp import AuthResult, LoginPassword
from asgiref.sync import sync_to_async
from django.conf import settings

from mail import storage

log = logging.getLogger("mail")


class MailHandler:
    """Accepts inbound mail (port 25) and authenticated submissions (587)."""

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        # Inbound (unauthenticated) mail may only go to local recipients;
        # authenticated clients may relay anywhere.
        if not session.authenticated:
            if not await sync_to_async(storage.is_deliverable)(address):
                return "550 5.1.1 <%s>: Recipient address rejected: User unknown" % address
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        data = envelope.content
        if len(data) > settings.MAX_MESSAGE_SIZE:
            return "552 5.3.4 Message size exceeds limit"
        try:
            if session.authenticated:
                await sync_to_async(storage.handle_submission)(
                    session.auth_login, envelope.mail_from,
                    list(envelope.rcpt_tos), data,
                )
            else:
                peer_ip = session.peer[0] if session.peer else ""
                helo = getattr(session, "host_name", "") or ""
                # Greylisting: ask senders to retry on first sighting.
                from mail import greylist
                if await sync_to_async(greylist.should_defer)(
                        peer_ip, envelope.mail_from, list(envelope.rcpt_tos)):
                    return "451 4.7.1 Greylisted, please retry shortly"
                await sync_to_async(storage.handle_inbound)(
                    list(envelope.rcpt_tos), data, peer_ip,
                    envelope.mail_from, helo,
                )
        except Exception:  # noqa: BLE001
            log.exception("Error processing message")
            return "451 4.3.0 Temporary failure processing message"
        return "250 2.0.0 Message accepted for delivery"


class Authenticator:
    """Validates SMTP AUTH (LOGIN/PLAIN) against mailbox passwords.

    Called synchronously by aiosmtpd. The ORM access here relies on
    ``DJANGO_ALLOW_ASYNC_UNSAFE`` being set by the runsmtp command.
    """

    def __call__(self, server, session, envelope, mechanism, auth_data):
        if not isinstance(auth_data, LoginPassword):
            return AuthResult(success=False, handled=False)
        email = auth_data.login.decode("utf-8", "replace")
        password = auth_data.password.decode("utf-8", "replace")
        mailbox = storage.authenticate(email, password)
        if mailbox is None:
            log.info("AUTH failed for %s", email)
            return AuthResult(success=False)
        session.auth_login = mailbox.email
        return AuthResult(success=True)
