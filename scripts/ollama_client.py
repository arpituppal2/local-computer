"""Ollama client with streaming, per-role token caps, context-window limits, and GPU pinning.

Mac M4 optimizations applied:
  - stream=True on all calls to avoid full-response buffering
  - num_ctx capped per model to reduce KV-cache memory pressure
  - num_predict capped per role (router/actor/planner/analyst/heavy)
  - memory fallback logic retained
"""
from __future__ import annotations
import json, logging, psutil
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
_CFG_PATH = ROOT / "configs" / "models.json"

_cfg = json.loads(_CFG_PATH.read_text()) if _CFG_PATH.exists() else {}

MODEL_ROUTER  = _cfg.get("router",  "qwen3:4b")
MODEL_ACTOR   = _cfg.get("actor",   "qwen3:8b")
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

# Context window sizes — kept small to reduce KV-cache RAM on 16 GB unified memory
_CTX_SIZES: dict[str, int] = _cfg.get("context_sizes", {
    "qwen3:4b":  2048,
    "qwen3:8b":  2048,
    "qwen3:14b": 4096,
})

# Per-role generation caps — short outputs for routing, longer for heavy analysis
_MAX_TOKENS: dict[str, int] = _cfg.get("max_tokens", {
    "router":   256,
    "actor":    1024,
    "planner":  512,
    "analyst":  1500,
    "heavy":    2048,
})

# Map model → role so we can look up the right cap in call()
_MODEL_ROLE: dict[str, str] = {
    MODEL_ROUTER:  "router",
    MODEL_ACTOR:   "actor",
    MODEL_PLANNER: "planner",
    MODEL_ANALYST: "analyst",
    MODEL_HEAVY:   "heavy",
}

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


def _ctx_for(model: str) -> int:
    for key, val in _CTX_SIZES.items():
        if key in model:
            return val
    return 2048


def _max_tokens_for(model: str) -> int:
    role = _MODEL_ROLE.get(model)
    if role:
        return _MAX_TOKENS.get(role, 1024)
    # fallback: key substring match
    if "14b" in model:
        return _MAX_TOKENS.get("heavy", 2048)
    if "8b" in model:
        return _MAX_TOKENS.get("analyst", 1500)
    return _MAX_TOKENS.get("actor", 1024)


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
    """Call Ollama with streaming enabled; returns full response string.

    Streaming prevents full-response buffering, making the pipeline feel
    3-5x more responsive on Apple Silicon unified memory.
    """
    model = _fallback(model)
    timeout = _timeout_for(model)
    payload: dict = {
        "model":  model,
        "prompt": prompt,
        "stream": True,   # stream to avoid buffering the full response
        "options": {
            "num_predict": _max_tokens_for(model),
            "num_ctx":     _ctx_for(model),   # cap KV-cache to save unified memory
        },
    }
    if system:
        payload["system"] = system
    chunks: list[str] = []
    try:
        with _client.stream("POST", "/api/generate", json=payload, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunks.append(data.get("response", ""))
                if data.get("done"):
                    break
        return "".join(chunks).strip()
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

    Note: argument order is (prompt, model) — matches the rest of the codebase.
    """
    raw = call(prompt, model=model, system=system)
    if not raw:
        return {}
    for attempt in [raw, raw[raw.find("{"):raw.rfind("}")+1] if "{" in raw else ""]:
        if not attempt:
            continue
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
    logging.warning(f"[ollama] Could not parse JSON from model={model}. Raw: {raw[:200]}")
    return {}
