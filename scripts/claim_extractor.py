"""Claim extraction with deduplication, smart truncation, and conservative fallback (fixes #7-10)."""
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

def extract_claims(url: str, text: str, memory) -> list[str]:
    if url in memory.visited_urls:
        return []

    excerpt = _smart_excerpt(text or "")
    if not excerpt.strip():
        return []

    prompt = (
        f"Extract up to 8 specific, verifiable factual claims from this web page text.\n"
        f"Return JSON: {{\"claims\": [\"claim1\", \"claim2\", ...]}}\n"
        f"Only include claims that are concrete and falsifiable. Skip navigation text, ads, or vague statements.\n\n"
        f"URL: {url}\n\nTEXT:\n{excerpt}"
    )
    result = call_json(MODEL_ANALYST, prompt)
    claims = result.get("claims", [])

    if not isinstance(claims, list) or not claims:
        logging.info(f"[extractor] LLM returned no claims, using fallback for {url}")
        claims = _cheap_fallback(text or "")

    return [str(c).strip() for c in claims if str(c).strip()]
