"""A simple Bayesian spam classifier (Paul Graham style), backed by the DB.

Tokens are learned from messages the user (or autolearn) marks as spam/ham via
``train``. ``probability`` returns P(spam) in [0, 1]; ``score_points`` maps that
to a contribution for the overall spam score. With an empty corpus it stays
neutral, so it never hurts a fresh install.
"""
import logging
import math
import re

from django.db import transaction
from django.db.models import F

from .models import SpamCorpus, SpamToken

log = logging.getLogger("mail")

_TOKEN_RE = re.compile(r"[a-zA-Z0-9$!\-_'.]{2,}")
MIN_CORPUS = 20          # need this many learned messages before trusting Bayes
MAX_TOKENS = 5000


def tokenize(text: str) -> set[str]:
    tokens = set()
    for raw in _TOKEN_RE.findall(text or ""):
        tok = raw.lower().strip(".'")
        if 2 <= len(tok) <= 64:
            tokens.add(tok)
    return tokens


def features(parsed: dict) -> set[str]:
    """Build the token set used for both training and scoring."""
    text = " ".join([
        parsed.get("subject", ""), parsed.get("from_addr", ""),
        parsed.get("body_text", ""),
    ])
    toks = tokenize(text)
    frm = parsed.get("from_addr", "")
    if "@" in frm:
        toks.add("from:" + frm.split("@")[-1].strip(">").lower())
    return set(list(toks)[:MAX_TOKENS])


@transaction.atomic
def train(parsed: dict, is_spam: bool):
    corpus = SpamCorpus.get()
    for tok in features(parsed):
        obj, _ = SpamToken.objects.get_or_create(token=tok)
        if is_spam:
            SpamToken.objects.filter(pk=obj.pk).update(spam=F("spam") + 1)
        else:
            SpamToken.objects.filter(pk=obj.pk).update(ham=F("ham") + 1)
    if is_spam:
        corpus.spam_messages = F("spam_messages") + 1
    else:
        corpus.ham_messages = F("ham_messages") + 1
    corpus.save()


def _token_spamminess(tok: SpamToken, n_spam: int, n_ham: int) -> float | None:
    # Probability this token indicates spam, à la Paul Graham, with smoothing.
    if tok.spam + tok.ham < 1:
        return None
    s = min(1.0, (tok.spam * 2) / max(1, n_spam))   # weight spam hits
    h = min(1.0, tok.ham / max(1, n_ham))
    if s + h == 0:
        return None
    p = s / (s + h)
    return min(0.99, max(0.01, p))


def probability(parsed: dict) -> float:
    corpus = SpamCorpus.get()
    n_spam, n_ham = corpus.spam_messages, corpus.ham_messages
    if n_spam + n_ham < MIN_CORPUS:
        return 0.5  # not enough data — stay neutral

    toks = features(parsed)
    rows = {t.token: t for t in SpamToken.objects.filter(token__in=toks)}
    # Use the 15 most "interesting" tokens (farthest from 0.5).
    probs = []
    for tok in toks:
        row = rows.get(tok)
        if not row:
            continue
        p = _token_spamminess(row, n_spam, n_ham)
        if p is not None:
            probs.append(p)
    if not probs:
        return 0.5
    probs.sort(key=lambda p: abs(p - 0.5), reverse=True)
    probs = probs[:15]

    # Combine: P = ∏p / (∏p + ∏(1-p)), computed in log space for stability.
    ln_p = sum(math.log(p) for p in probs)
    ln_np = sum(math.log(1 - p) for p in probs)
    return math.exp(ln_p) / (math.exp(ln_p) + math.exp(ln_np))


def score_points(parsed: dict) -> tuple[float, str | None]:
    """Map P(spam) to a spam-score contribution."""
    p = probability(parsed)
    if p >= 0.95:
        return 4.0, f"bayes={p:.2f}"
    if p >= 0.8:
        return 2.0, f"bayes={p:.2f}"
    if p <= 0.05:
        return -2.0, f"bayes={p:.2f}"
    if p <= 0.2:
        return -1.0, f"bayes={p:.2f}"
    return 0.0, None
