"""Virus scanning via a ClamAV daemon (clamd) over TCP using INSTREAM.

No third-party dependency — speaks the clamd wire protocol directly. Fails
*open* (treats mail as clean) if the scanner is unreachable, logging a warning,
so a clamd outage never blocks mail flow. Configure with CLAMAV_HOST/PORT.
"""
import logging
import socket
import struct

from django.conf import settings

log = logging.getLogger("mail")

HOST = getattr(settings, "CLAMAV_HOST", "")
PORT = int(getattr(settings, "CLAMAV_PORT", 3310))
CHUNK = 8192


def enabled() -> bool:
    return bool(HOST)


def scan(raw: bytes) -> tuple[bool, str | None]:
    """Return ``(clean, signature)``. ``clean`` is True when no virus is found."""
    if not HOST:
        return True, None
    try:
        with socket.create_connection((HOST, PORT), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(raw), CHUNK):
                chunk = raw[i:i + CHUNK]
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))  # zero-length chunk = end
            resp = b""
            while not resp.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                resp += part
        text = resp.rstrip(b"\0").decode("utf-8", "replace").strip()
        if text.endswith("OK"):
            return True, None
        if "FOUND" in text:
            signature = text.split(":", 1)[-1].replace("FOUND", "").strip()
            log.warning("ClamAV found virus: %s", signature)
            return False, signature
        log.warning("ClamAV unexpected response: %s", text)
        return True, None
    except Exception as exc:  # noqa: BLE001 - fail open
        log.warning("ClamAV scan skipped (scanner unreachable): %s", exc)
        return True, None
