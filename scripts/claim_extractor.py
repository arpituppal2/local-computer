"""Claim extraction with URL deduplication, smart truncation, and conservative fallback."""
from __future__ import annotations
import re, logging
from scripts.ollama_client import call_json, MODEL_ANALYST

_SIGNAL = re.compile(
    r'\b(found|show|reveal|indicate|report|confirm|according to|study|research|data)\b',
    re.I
)


def _cheap_fallback(text: str) -> list[str]:
    sentences = re.split(r'[.!?;]\s+', text)
    claims = []
    for s in sentences:
        s = s.strip()
        words = s.split()
        if len(words) >= 10 and _SIGNAL.search(s):
            claims.append(s)
    return claims[:5]


def _smart_excerpt(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def extract_claims(title: str, url: str, text: str) -> list[str]:
    """
    Extract factual claims from a page.

    Signature matches the call-site in navigation_agent.py:
        extract_claims(state.get('title', ''), state['url'], text)

    URL-level deduplication is handled by navigation_agent.py (process_page
    checks memory.evidence for the URL before calling this function).
    """
    excerpt = _smart_excerpt(text or "")
    if not excerpt.strip():
        return []

    prompt = (
        f"Extract up to 8 specific, verifiable factual claims from this web page text.\n"
        f"Return JSON: {{\"claims\": [\"claim1\", \"claim2\", ...]}}\n"
        f"Only include claims that are concrete and falsifiable. "
        f"Skip navigation text, ads, or vague statements.\n\n"
        f"Title: {title}\nURL: {url}\n\nTEXT:\n{excerpt}"
    )
    result = call_json(prompt, model=MODEL_ANALYST)
    claims = result.get("claims", []) if isinstance(result, dict) else []

    if not isinstance(claims, list) or not claims:
        logging.info(f"[extractor] LLM returned no claims, using fallback for {url}")
        claims = _cheap_fallback(text or "")

    return [str(c).strip() for c in claims if str(c).strip()]
