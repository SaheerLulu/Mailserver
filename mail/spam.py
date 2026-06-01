"""Inbound spam scoring.

Combines deterministic content heuristics (always available) with best-effort
authentication checks — DKIM verification (``dkimpy``) and a lightweight SPF
evaluator (``dnspython``). Network checks degrade gracefully to a neutral
result when DNS is unavailable, so scoring never blocks delivery.

A higher score means more spammy. Each mailbox has its own threshold
(``Mailbox.spam_threshold``, default 5.0) above which mail is filed in Junk.
"""
import ipaddress
import logging
import re

import dkim as dkimpy
import dns.resolver

log = logging.getLogger("mail")

SPAMMY_WORDS = re.compile(
    r"\b(viagra|cialis|lottery|winner|free money|nigerian prince|bitcoin doubler|"
    r"act now|risk[- ]free|wire transfer|crypto giveaway|you have won|click here now)\b",
    re.IGNORECASE,
)


def heuristic_score(parsed: dict) -> tuple[float, list[str]]:
    """Content-based score; fully deterministic and offline."""
    score = 0.0
    reasons = []
    subject = parsed.get("subject", "") or ""
    body = parsed.get("body_text", "") or ""
    from_addr = parsed.get("from_addr", "") or ""

    if not from_addr:
        score += 3.0
        reasons.append("missing From")
    if not subject:
        score += 0.5
        reasons.append("empty subject")

    letters = [c for c in subject if c.isalpha()]
    if len(letters) >= 8 and all(c.isupper() for c in letters):
        score += 2.0
        reasons.append("shouting subject")

    if subject.count("!") >= 3:
        score += 1.0
        reasons.append("exclamation spam")

    matches = SPAMMY_WORDS.findall(f"{subject} {body}")
    if matches:
        score += min(4.0, 1.5 * len(matches))
        reasons.append(f"spam phrases: {', '.join(sorted(set(m.lower() for m in matches)))}")

    if parsed.get("body_html") and not body.strip():
        score += 1.0
        reasons.append("html-only body")

    if "$$$" in subject or "$$$" in body:
        score += 1.5
        reasons.append("money symbols")

    return score, reasons


def verify_dkim(raw: bytes) -> str:
    """Return 'pass' / 'fail' / 'none' (best-effort; needs DNS)."""
    if not raw or b"dkim-signature" not in raw[:8192].lower():
        return "none"
    try:
        return "pass" if dkimpy.verify(raw) else "fail"
    except Exception as exc:  # noqa: BLE001 - DNS errors etc.
        log.debug("DKIM verify inconclusive: %s", exc)
        return "none"


# --- Minimal SPF evaluator --------------------------------------------------
_QUALIFIERS = {"+": "pass", "-": "fail", "~": "softfail", "?": "neutral"}


def _txt_records(domain):
    answers = dns.resolver.resolve(domain, "TXT")
    out = []
    for rdata in answers:
        out.append(b"".join(rdata.strings).decode("utf-8", "replace"))
    return out


def _ip_in(ip, network):
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False


def check_spf(ip: str, mail_from: str, helo: str = "", _depth: int = 0) -> str:
    """Evaluate SPF for (ip, sender). Returns pass/fail/softfail/neutral/none."""
    domain = (mail_from.split("@", 1)[1] if "@" in (mail_from or "") else helo).lower()
    if not ip or not domain or _depth > 5:
        return "none"
    try:
        record = next((t for t in _txt_records(domain) if t.lower().startswith("v=spf1")), None)
    except Exception:  # noqa: BLE001
        return "none" if _depth == 0 else "neutral"
    if not record:
        return "none"

    for token in record.split()[1:]:
        qualifier = _QUALIFIERS.get(token[0], "pass")
        mech = token.lstrip("+-~?")
        try:
            if mech == "all":
                return qualifier
            if mech.startswith("ip4:") or mech.startswith("ip6:"):
                if _ip_in(ip, mech.split(":", 1)[1]):
                    return qualifier
            elif mech.startswith("include:"):
                if check_spf(ip, "x@" + mech.split(":", 1)[1], helo, _depth + 1) == "pass":
                    return qualifier
            elif mech == "a" or mech.startswith("a:"):
                host = mech.split(":", 1)[1] if ":" in mech else domain
                for rtype in ("A", "AAAA"):
                    try:
                        if any(_ip_in(ip, str(r)) for r in dns.resolver.resolve(host, rtype)):
                            return qualifier
                    except Exception:  # noqa: BLE001
                        pass
            elif mech == "mx" or mech.startswith("mx:"):
                host = mech.split(":", 1)[1] if ":" in mech else domain
                try:
                    for mx in dns.resolver.resolve(host, "MX"):
                        exch = str(mx.exchange).rstrip(".")
                        for rtype in ("A", "AAAA"):
                            try:
                                if any(_ip_in(ip, str(r)) for r in dns.resolver.resolve(exch, rtype)):
                                    return qualifier
                            except Exception:  # noqa: BLE001
                                pass
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            continue
    return "neutral"


def score_message(parsed: dict, raw: bytes, peer_ip: str = "",
                  mail_from: str = "", helo: str = "") -> tuple[float, list[str]]:
    """Total spam score + human-readable reasons."""
    score, reasons = heuristic_score(parsed)

    dkim_result = verify_dkim(raw)
    if dkim_result == "pass":
        score -= 1.0
        reasons.append("dkim=pass")
    elif dkim_result == "fail":
        score += 3.0
        reasons.append("dkim=fail")

    spf_result = check_spf(peer_ip, mail_from, helo) if peer_ip else "none"
    if spf_result == "pass":
        score -= 0.5
        reasons.append("spf=pass")
    elif spf_result in ("fail", "softfail"):
        score += 2.0
        reasons.append(f"spf={spf_result}")

    # Bayesian classifier (neutral until trained).
    from . import bayes
    bayes_points, bayes_reason = bayes.score_points(parsed)
    score += bayes_points
    if bayes_reason:
        reasons.append(bayes_reason)

    # DNS blocklists for the connecting IP.
    if peer_ip:
        from . import dnsbl
        rbl_points, zones = dnsbl.check(peer_ip)
        if rbl_points:
            score += rbl_points
            reasons.append("rbl: " + ", ".join(zones))

    return max(0.0, round(score, 2)), reasons
