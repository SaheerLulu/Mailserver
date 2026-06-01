"""DKIM key generation and message signing.

Key generation uses ``cryptography``; signing uses ``dkimpy``. Private keys are
stored per-domain in the database (Domain.dkim_private_key).
"""
import base64

import dkim as dkimpy
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Headers that get covered by the DKIM signature.
SIGNED_HEADERS = [b"from", b"to", b"cc", b"subject", b"date", b"message-id",
                  b"mime-version", b"content-type"]


def generate_keypair(bits: int = 2048):
    """Return ``(private_key_pem, public_key_b64)`` for a fresh RSA key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, base64.b64encode(public_der).decode()


def dns_record(selector: str, domain: str, public_key_b64: str):
    """Return ``(record_name, record_value)`` for the DKIM TXT record."""
    return (f"{selector}._domainkey.{domain}",
            f"v=DKIM1; k=rsa; p={public_key_b64}")


def sign(raw: bytes, domain: str, selector: str, private_key_pem: str) -> bytes:
    """Return ``raw`` with a prepended DKIM-Signature header."""
    present = {h.lower() for h in _header_names(raw)}
    include = [h for h in SIGNED_HEADERS if h in present]
    signature = dkimpy.sign(
        message=raw,
        selector=selector.encode(),
        domain=domain.encode(),
        privkey=private_key_pem.encode(),
        include_headers=include or [b"from"],
        canonicalize=(b"relaxed", b"relaxed"),
    )
    return signature + raw


def _header_names(raw: bytes):
    names = []
    for line in raw.split(b"\r\n" if b"\r\n" in raw[:2048] else b"\n"):
        if not line or line[:1] in (b" ", b"\t"):
            continue
        if line == b"":
            break
        if b":" in line:
            names.append(line.split(b":", 1)[0].strip().lower())
        else:
            break
    return names
