from __future__ import annotations
import json, re, urllib.request
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.ollama_client import MODEL_ANALYST, OLLAMA_HOST

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
        }
    },
    "required": ["claims"],
}


def _cheap(text: str) -> list[dict]:
    out = []
    for s in re.split(r"[?.!;]", text)[:12]:
        ss = " ".join(s.split()).strip()
        if len(ss) < 40:
            continue
        if any(x in ss.lower() for x in ("said","according to","reported","announced","confirmed","filed")):
            out.append({"claim": ss[:300], "entities": [], "time": "", "confidence": 0.45})
    return out[:6]


def extract_claims(title: str, url: str, text: str) -> list[dict]:
    excerpt = (text or "")[:5000]
    if not excerpt.strip():
        return []
    prompt = (
        f"Extract concise factual claims.\nTitle: {title}\nURL: {url}\nText: {excerpt}\n"
        "Rules: Return JSON only. 3-8 claims, each <220 chars. Prefer concrete events, numbers, dates. No opinions."
    )
    payload = {"model": MODEL_ANALYST, "prompt": prompt, "stream": False,
               "format": CLAIM_SCHEMA, "options": {"temperature": 0.1}}
    try:
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
        claims = json.loads(body.get("response","").strip()).get("claims",[])
        if isinstance(claims, list) and claims:
            return claims[:8]
    except Exception:
        pass
    return _cheap(excerpt)
