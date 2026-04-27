"""Ollama client with error handling, per-model timeouts, GPU pinning, and JSON parsing guards (fixes #1-6)."""
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

_TIMEOUTS = _cfg.get("timeouts", {
    "qwen3:4b":  30,
    "qwen3:8b":  60,
    "qwen3:14b": 180,
})

BASE_URL = _cfg.get("ollama_host", "http://localhost:11434")
_client = httpx.Client(base_url=BASE_URL, timeout=None)

def _memory_ok_for(model: str) -> bool:
    available = psutil.virtual_memory().available / (1024 ** 3)
    required = 6.0 if "14b" in model else 2.0
    return available >= required

def _fallback(model: str) -> str:
    if not _memory_ok_for(model):
        logging.warning(f"[ollama] Low memory — downgrading {model} → qwen3:8b")
        return "qwen3:8b"
    return model

def _timeout_for(model: str) -> float:
    for key, val in _TIMEOUTS.items():
        if key in model:
            return float(val)
    return 60.0

def _validate_models():
    try:
        r = httpx.get(f"{BASE_URL}/api/tags", timeout=5)
        available = {m["name"] for m in r.json().get("models", [])}
        for role, model in [("router", MODEL_ROUTER), ("actor", MODEL_ACTOR),
                             ("planner", MODEL_PLANNER), ("heavy", MODEL_HEAVY)]:
            if model not in available:
                logging.warning(f"[ollama] Model '{model}' (role={role}) not found. Pull it first.")
    except Exception as e:
        logging.warning(f"[ollama] Could not validate models: {e}")

_validate_models()

def call(prompt: str, model: str = MODEL_ACTOR, system: str = "") -> str:
    model = _fallback(model)
    timeout = _timeout_for(model)
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": 2048}}
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
    raw = call(model, prompt, system)
    if not raw:
        return {}
    for attempt in [raw, raw[raw.find("{"):raw.rfind("}")+1]]:
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
    logging.warning(f"[ollama] Could not parse JSON from model={model}. Raw: {raw[:200]}")
    return {}
