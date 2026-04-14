"""Single Ollama wrapper — import this everywhere, never call the API directly."""
from __future__ import annotations
import json, os, urllib.request
from pathlib import Path

_CFG = Path(__file__).parent.parent / "configs" / "models.json"
_models: dict = json.loads(_CFG.read_text()) if _CFG.exists() else {}

OLLAMA_HOST  = _models.get("ollama_host") or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL_ROUTER  = _models.get("router",  "qwen3:4b")
MODEL_PLANNER = _models.get("planner", "qwen3:14b")
MODEL_ACTOR   = _models.get("actor",   "qwen3:4b")
MODEL_ANALYST = _models.get("analyst", "qwen3:14b")
MODEL_HEAVY   = _models.get("heavy",   "deepseek-r1:14b")


def call(prompt: str, model: str = MODEL_ACTOR, fmt: str | None = None, timeout: int = 90) -> str:
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if fmt:
        payload["format"] = fmt
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    return body.get("response", "").strip()


def call_json(prompt: str, model: str = MODEL_PLANNER, timeout: int = 90) -> dict:
    raw = call(prompt, model=model, fmt="json", timeout=timeout)
    try:
        return json.loads(raw)
    except Exception:
        return {}
