"""New-mail notifications: outbound webhooks and Web Push (VAPID).

- Webhook: if a mailbox has ``webhook_url`` set, POST a small JSON payload.
- Web Push: deliver to every browser/mobile PushSubscription the mailbox has
  registered (requires VAPID keys; see settings). Dead subscriptions (404/410)
  are pruned.

Both are best-effort and never raise into the delivery path.
"""
import json
import logging
import urllib.request

from django.conf import settings

log = logging.getLogger("mail")


def _payload(mailbox, message) -> dict:
    return {
        "event": "new_mail",
        "mailbox": mailbox.email,
        "message": {
            "id": message.id, "from": message.from_addr,
            "subject": message.subject, "date": message.date.isoformat(),
            "folder": message.folder,
        },
    }


def notify_new_mail(mailbox, message):
    try:
        _webhook(mailbox, message)
    except Exception:  # noqa: BLE001
        log.warning("webhook notify failed", exc_info=True)
    try:
        _webpush(mailbox, message)
    except Exception:  # noqa: BLE001
        log.warning("web push notify failed", exc_info=True)


def _webhook(mailbox, message):
    if not mailbox.webhook_url:
        return
    data = json.dumps(_payload(mailbox, message)).encode()
    req = urllib.request.Request(
        mailbox.webhook_url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "django-mailserver"})
    urllib.request.urlopen(req, timeout=5).close()
    log.info("webhook delivered to %s", mailbox.webhook_url)


def _webpush(mailbox, message):
    if not settings.VAPID_PRIVATE_KEY:
        return
    subs = list(mailbox.push_subs.all())
    if not subs:
        return
    from pywebpush import WebPushException, webpush
    data = json.dumps({"title": f"New mail from {message.from_addr}",
                       "body": message.subject or "(no subject)",
                       "id": message.id})
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint,
                                   "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=data,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT})
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                sub.delete()
            else:
                log.warning("web push to %s failed: %s", sub.endpoint[:40], exc)
