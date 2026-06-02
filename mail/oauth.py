"""OAuth2 bearer tokens for mail.

- A small OAuth2 token endpoint (``/oauth/token``, Resource Owner Password
  Credentials grant) that issues an ``ApiToken`` as the access token.
- Helpers to validate a bearer token and the XOAUTH2 SASL exchange used by
  IMAP/SMTP clients (Gmail-style ``user=…^Aauth=Bearer <token>^A^A``).

A full authorization-code server (consent screens, client registry, refresh
tokens) is intentionally out of scope; this covers programmatic + mail-client
token auth.
"""
import base64
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import storage
from .models import ApiToken

log = logging.getLogger("mail")


def validate_bearer(token: str):
    if not token:
        return None
    row = ApiToken.objects.filter(key=token).select_related("mailbox").first()
    if row and row.mailbox.is_active:
        ApiToken.objects.filter(pk=row.pk).update(last_used=timezone.now())
        return row.mailbox
    return None


def xoauth2_validate(decoded: str):
    """Validate a decoded XOAUTH2 SASL string. Returns the mailbox or None.

    Format: ``user=<email>\\x01auth=Bearer <token>\\x01\\x01``
    """
    fields = {}
    for part in decoded.split("\x01"):
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
    user = (fields.get("user") or "").lower()
    auth = fields.get("auth", "")
    token = auth.split(" ", 1)[1].strip() if " " in auth else ""
    mailbox = validate_bearer(token)
    if mailbox and (not user or mailbox.email == user):
        return mailbox
    return None


def xoauth2_from_b64(b64: str):
    try:
        decoded = base64.b64decode(b64).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    return xoauth2_validate(decoded)


@csrf_exempt
def token(request):
    """OAuth2 token endpoint (password grant)."""
    if request.method != "POST":
        return JsonResponse({"error": "invalid_request"}, status=405)
    if request.POST.get("grant_type") != "password":
        return JsonResponse({"error": "unsupported_grant_type"}, status=400)
    mailbox = storage.authenticate(request.POST.get("username", ""),
                                   request.POST.get("password", ""))
    if not mailbox:
        return JsonResponse({"error": "invalid_grant"}, status=400)
    tok = ApiToken.objects.create(mailbox=mailbox, name="oauth")
    return JsonResponse({"access_token": tok.key, "token_type": "Bearer",
                         "scope": "mail"})
