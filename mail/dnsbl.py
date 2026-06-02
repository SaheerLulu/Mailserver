"""DNS blocklist (RBL) lookups for the connecting client IP. Best-effort."""
import logging

import dns.resolver

log = logging.getLogger("mail")

# Each listing adds this many spam points.
ZONES = ["zen.spamhaus.org", "bl.spamcop.net", "b.barracudacentral.org"]
POINTS_PER_HIT = 2.5


def _reversed_ipv4(ip: str) -> str | None:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(reversed(parts))


def check(ip: str) -> tuple[float, list[str]]:
    """Return (points, listing_zones) for ``ip``."""
    rev = _reversed_ipv4(ip) if ip else None
    if not rev:
        return 0.0, []  # only IPv4 supported here
    hits = []
    for zone in ZONES:
        try:
            dns.resolver.resolve(f"{rev}.{zone}", "A")
            hits.append(zone)
        except dns.resolver.NXDOMAIN:
            continue
        except Exception as exc:  # noqa: BLE001 - resolver/network issues
            log.debug("DNSBL %s lookup failed: %s", zone, exc)
            continue
    return POINTS_PER_HIT * len(hits), hits
