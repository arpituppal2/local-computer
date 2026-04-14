"""Domain trust + text quality scoring for evidence items."""
from __future__ import annotations
from urllib.parse import urlparse

HIGH_TRUST = {
    "reuters.com":    5,
    "apnews.com":     5,
    "bbc.com":        4,
    "nytimes.com":    4,
    "wsj.com":        4,
    "npr.org":        4,
    "theguardian.com":4,
    "bloomberg.com":  4,
    "cnn.com":        3,
    "foxnews.com":    3,
    "abcnews.go.com": 3,
    "cbsnews.com":    3,
    "nbcnews.com":    3,
}


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def score_source(url: str, title: str = "", text: str = "") -> int:
    d     = domain_of(url)
    score = HIGH_TRUST.get(d, 1)
    tl    = (title or "").lower()
    body  = (text  or "").lower()
    if any(x in tl   for x in ["live updates", "breaking"]):
        score += 1
    if len(body) > 1200:
        score += 1
    if any(x in body for x in ["according to", "officials said", "statement",
                                 "court filing", "report"]):
        score += 1
    return min(score, 7)
