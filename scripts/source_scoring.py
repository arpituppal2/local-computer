"""Source scoring with learned quality adjustments (fixes #35-36)."""
from __future__ import annotations
from urllib.parse import urlparse

HIGH_TRUST = {
    "nature.com": 7, "science.org": 7, "nejm.org": 7, "thelancet.com": 7,
    "arxiv.org": 6, "pubmed.ncbi.nlm.nih.gov": 7, "scholar.google.com": 6,
    "reuters.com": 6, "apnews.com": 6, "bbc.com": 5, "nytimes.com": 5,
    "theguardian.com": 5, "wikipedia.org": 4, "gov": 6,
}
LOW_TRUST = {"reddit.com": 2, "twitter.com": 2, "x.com": 2, "pinterest.com": 1}

_learned: dict[str, float] = {}


def domain_of(url: str) -> str:
    """Return the hostname of a URL, or empty string on failure."""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def score_source(url: str, title: str = "", body: str = "") -> int:
    domain = domain_of(url)

    score = 3
    for key, val in HIGH_TRUST.items():
        if domain.endswith(key):
            score = val
            break
    for key, val in LOW_TRUST.items():
        if domain.endswith(key):
            score = val
            break

    penalties = 0
    title_lower = (title or "").lower()
    if any(w in title_lower for w in ("live updates", "breaking", "just in")):
        penalties += 1
    if len(body or "") < 300:
        penalties += 1
    if not body or body.strip() == "":
        penalties += 2

    learned_delta = _learned.get(domain, 0.0)
    final = max(1, min(7, score - penalties + int(learned_delta)))
    return final


def record_quality(url: str, was_useful: bool):
    domain = domain_of(url)
    delta = 0.3 if was_useful else -0.2
    _learned[domain] = _learned.get(domain, 0.0) + delta
