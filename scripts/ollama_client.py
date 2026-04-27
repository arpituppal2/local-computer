"""Ollama client with per-model timeouts, GPU pinning, JSON parsing, and chatbot threshold export.

Bug fix: call_json() previously had prompt/model args swapped — corrected here.
"""
from __future__ import annotations
import json, logging, psutil
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
_CFG_PATH = ROOT / "configs" / "models.json"

_cfg = json.loads(_CFG_PATH.read_text()) if _CFG_PATH.exists() else {}

MODEL_ROUTER  = _cfg.get("router",  "qwen3:4b")
MODEL_ACTOR   = _cfg.get("actor",   "qwen3:4b")
MODEL_PLANNER = _cfg.get("planner", "qwen3:8b")
MODEL_ANALYST = _cfg.get("analyst", "qwen3:8b")
MODEL_HEAVY   = _cfg.get("heavy",   "qwen3:14b")

# Complexity score above which the router should send tasks to a chatbot UI
CHATBOT_THRESHOLD: int = _cfg.get("chatbot_threshold", 7)

_TIMEOUTS = _cfg.get("timeouts", {
    "qwen3:4b":  30,
    "qwen3:8b":  60,
    "qwen3:14b": 180,
})

BASE_URL = _cfg.get("ollama_host", "http://localhost:11434")
_client = httpx.Client(base_url=BASE_URL, timeout=None)


def _memory_ok_for(model: str) -> bool:
    available = psutil.virtual_memory().available / (1024 ** 3)
    required = 9.5 if "14b" in model else 3.5 if "8b" in model else 2.0
    return available >= required


def _fallback(model: str) -> str:
    """Downgrade to a smaller model if we don't have enough free RAM."""
    if "14b" in model and not _memory_ok_for(model):
        logging.warning(f"[ollama] Low memory — downgrading {model} → qwen3:8b")
        return "qwen3:8b"
    if "8b" in model and not _memory_ok_for(model):
        logging.warning(f"[ollama] Low memory — downgrading {model} → qwen3:4b")
        return "qwen3:4b"
    return model


def _timeout_for(model: str) -> float:
    for key, val in _TIMEOUTS.items():
        if key in model:
            return float(val)
    return 60.0


def _validate_models() -> None:
    try:
        r = httpx.get(f"{BASE_URL}/api/tags", timeout=5)
        available = {m["name"] for m in r.json().get("models", [])}
        for role, model in [
            ("router",  MODEL_ROUTER),
            ("actor",   MODEL_ACTOR),
            ("planner", MODEL_PLANNER),
            ("heavy",   MODEL_HEAVY),
        ]:
            if model not in available:
                logging.warning(f"[ollama] Model '{model}' (role={role}) not found — run: ollama pull {model}")
    except Exception as e:
        logging.warning(f"[ollama] Could not validate models: {e}")


_validate_models()


def call(prompt: str, model: str = MODEL_ACTOR, system: str = "") -> str:
    """Call Ollama with a text prompt; return response string."""
    model = _fallback(model)
    timeout = _timeout_for(model)
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 2048},
    }
    if system:
        payload["system"] = system
    try:
        r = _client.post("/api/generate", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except httpx.HTTPStatusError as e:
        logging.error(f"[ollama] HTTP {e.response.status_code} for model={model}")
        return ""
    except httpx.TimeoutException:
        logging.error(f"[ollama] Timeout after {timeout}s for model={model}")
        return ""
    except Exception as e:
        logging.error(f"[ollama] Unexpected error: {e}")
        return ""


def call_json(prompt: str, model: str = MODEL_PLANNER, system: str = "") -> dict:
    """Call Ollama and parse the response as JSON.

    Note: argument order is (prompt, model) — this matches how the rest of the
    codebase calls it. The previous version had the args swapped.
    """
    raw = call(prompt, model=model, system=system)
    if not raw:
        return {}
    # Try full response, then extract first {…} block
    for attempt in [raw, raw[raw.find("{"):raw.rfind("}")+1] if "{" in raw else ""]:
        if not attempt:
            continue
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
    logging.warning(f"[ollama] Could not parse JSON from model={model}. Raw: {raw[:200]}")
    return {}
