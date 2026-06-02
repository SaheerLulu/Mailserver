"""Outbound delivery worker: drains the OutboundMessage queue with retries."""
import signal
import threading
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from mail import delivery
from mail.models import OutboundMessage


def backoff_seconds(attempts: int) -> int:
    """Exponential backoff, capped at 6 hours."""
    return min(6 * 3600, 60 * (2 ** max(0, attempts - 1)))


class Command(BaseCommand):
    help = "Process the outbound mail queue, retrying failed deliveries."

    def add_arguments(self, parser):
        parser.add_argument("--poll", type=float, default=20.0,
                            help="Seconds between queue polls")
        parser.add_argument("--batch", type=int, default=20,
                            help="Max messages processed per poll")
        parser.add_argument("--once", action="store_true",
                            help="Process due messages once and exit")

    def handle(self, *args, **options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        self.stdout.write(self.style.SUCCESS("Outbound queue worker started"))

        while not stop.is_set():
            processed = self._drain(options["batch"])
            if options["once"]:
                break
            if processed == 0:
                stop.wait(options["poll"])

    def _drain(self, batch: int) -> int:
        now = timezone.now()
        # Claim a batch atomically so multiple queue workers can run in
        # parallel (HA) without delivering the same message twice.
        with transaction.atomic():
            qs = (OutboundMessage.objects
                  .filter(status=OutboundMessage.Status.QUEUED, next_attempt__lte=now)
                  .order_by("next_attempt"))
            if connection.features.has_select_for_update_skip_locked:
                qs = qs.select_for_update(skip_locked=True)
            claimed = list(qs[:batch].values_list("id", flat=True))
            OutboundMessage.objects.filter(id__in=claimed).update(
                next_attempt=now + timedelta(hours=1))  # lease while we work
        due = OutboundMessage.objects.filter(id__in=claimed)

        count = 0
        for om in due:
            count += 1
            try:
                raw = om.raw.read()
            except (OSError, ValueError) as exc:
                om.status = OutboundMessage.Status.FAILED
                om.last_error = f"cannot read queued body: {exc}"
                om.save(update_fields=["status", "last_error", "updated_at"])
                continue

            ok, error = delivery.attempt_delivery(om.mail_from, om.recipient_list, raw)
            om.attempts += 1
            if ok:
                om.status = OutboundMessage.Status.SENT
                om.last_error = ""
                self.stdout.write(f"sent #{om.pk} → {om.recipients}")
            elif om.attempts >= om.max_attempts:
                om.status = OutboundMessage.Status.FAILED
                om.last_error = error
                self.stderr.write(f"FAILED #{om.pk} → {om.recipients}: {error}")
            else:
                om.last_error = error
                om.next_attempt = now + timedelta(seconds=backoff_seconds(om.attempts))
                self.stdout.write(
                    f"deferred #{om.pk} (try {om.attempts}/{om.max_attempts}): {error}")
            om.save()
        return count
