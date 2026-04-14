"""Extracts factual claims from article text via Ollama structured output."""
from __future__ import annotations

import json
import os
import re
import urllib.request

OLLAMA_HOST  = os.environ.get("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")

CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim":      {"type": "string"},
                    "entities":   {"type": "array", "items": {"type": "string"}},
                    "time":       {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["claim"],
            },
        },
    },
    "required": ["claims"],
}


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def cheap_claims(text: str) -> list[dict]:
    """Regex fallback when LLM is unavailable."""
    text  = (text or "").strip()
    if not text:
        return []
    sents = re.split(r'(?<=[.!?])\s+', text)
    out   = []
    keywords = ["said", "according to", "reported", "announced", "confirmed",
                "filed", "won", "killed", "injured"]
    for s in sents[:12]:
        ss = " ".join(s.split()).strip()
        if len(ss) < 40:
            continue
        if any(x in ss.lower() for x in keywords):
            out.append({"claim": ss[:300], "entities": [], "time": "", "confidence": 0.45})
    return out[:6]


def extract_claims(title: str, url: str, text: str) -> list[dict]:
    excerpt = (text or "")[:5000]
    if not excerpt.strip():
        return []

    prompt = f"""Extract concise factual claims from this article text.

Title: {title}
URL: {url}

Text:
{excerpt}

Rules:
- Return JSON only.
- Extract 3 to 8 factual claims.
- Keep each claim under 220 characters.
- Prefer concrete events, numbers, dates, names, outcomes.
- Do not include opinion or stylistic commentary.""".strip()

    payload = {
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "format":  CLAIM_SCHEMA,
        "options": {"temperature": 0.1},
    }
    try:
        body   = _post_json(f"{OLLAMA_HOST}/api/generate", payload)
        raw    = body.get("response", "{}").strip()
        parsed = json.loads(raw)
        claims = parsed.get("claims", [])
        if isinstance(claims, list) and claims:
            return claims[:8]
    except Exception:
        pass

    return cheap_claims(excerpt)
