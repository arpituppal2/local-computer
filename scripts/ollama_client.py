"""Ollama client with sensible defaults for Apple Silicon unified memory."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable

import httpx

from scripts.model_selector import effective_models_config
from scripts.runtime_policy import local_models_allowed, skip_model_validation

ROOT = Path(__file__).resolve().parent.parent
_cfg = effective_models_config()

MODEL_ORCHESTRATOR = _cfg.get("orchestrator", "qwen2.5:14b")
MODEL_PLANNER = _cfg.get("planner", "qwen2.5:14b")
MODEL_NAVIGATOR = _cfg.get("navigator", "qwen2.5:3b")
MODEL_EXECUTOR = _cfg.get("executor", "qwen2.5:3b")
MODEL_SYNTHESIZER = _cfg.get("synthesizer", "qwen2.5:14b")
MODEL_CRITIC = _cfg.get("critic", "qwen2.5:3b")
MODEL_MEMORY = _cfg.get("memory", "nomic-embed-text")
MODEL_ROUTER = _cfg.get("router", "qwen2.5:3b")

# Backward-compatible aliases used by older modules.
MODEL_ACTOR = MODEL_NAVIGATOR
MODEL_ANALYST = MODEL_SYNTHESIZER
MODEL_HEAVY = MODEL_SYNTHESIZER

CHATBOT_THRESHOLD = int(_cfg.get("chatbot_threshold", 8))

BASE_URL = _cfg.get("ollama_host", "http://localhost:11434")
_TIMEOUTS = _cfg.get(
    "timeouts",
    {
        "qwen2.5:3b": 45,
        "qwen2.5:14b": 120,
        "llama3.1:70b": 420,
        "nomic-embed-text": 45,
    },
)

DEFAULT_OLLAMA_OPTIONS = {
    "num_ctx": 4096,
    "num_thread": 8,
    "num_gpu": 999,
}
DEFAULT_OLLAMA_OPTIONS.update(_cfg.get("ollama_options", {}))

_sync_client = httpx.Client(base_url=BASE_URL, timeout=None)


def _timeout_for(model: str) -> float:
    for key, timeout in _TIMEOUTS.items():
        if key in model:
            return float(timeout)
    return 90.0


def _merge_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_OLLAMA_OPTIONS)
    if options:
        merged.update(options)
    return merged


def _extract_stream_text(payload: dict[str, Any]) -> str:
    if "response" in payload:
        return str(payload.get("response", ""))
    msg = payload.get("message")
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    return ""


def call(prompt: str, model: str = MODEL_NAVIGATOR, system: str = "", options: dict[str, Any] | None = None) -> str:
    if not local_models_allowed():
        logging.warning("[ollama] Local model use is disabled; skipping generate call")
        return ""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": _merge_options(options),
    }
    if system:
        payload["system"] = system

    timeout = _timeout_for(model)
    chunks: list[str] = []
    try:
        with _sync_client.stream("POST", "/api/generate", json=payload, timeout=timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunks.append(_extract_stream_text(data))
                if data.get("done"):
                    break
    except Exception as exc:
        logging.error(f"[ollama] generate failed for model={model}: {exc}")
        return ""
    return "".join(chunks).strip()


def stream_chat(
    messages: list[dict[str, str]],
    model: str = MODEL_SYNTHESIZER,
    options: dict[str, Any] | None = None,
) -> Iterable[str]:
    if not local_models_allowed():
        logging.warning("[ollama] Local model use is disabled; skipping chat stream")
        return
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": _merge_options(options),
    }

    timeout = _timeout_for(model)
    try:
        with _sync_client.stream("POST", "/api/chat", json=payload, timeout=timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = _extract_stream_text(data)
                if token:
                    yield token
                if data.get("done"):
                    break
    except Exception as exc:
        logging.error(f"[ollama] chat stream failed for model={model}: {exc}")


def chat(
    messages: list[dict[str, str]],
    model: str = MODEL_SYNTHESIZER,
    options: dict[str, Any] | None = None,
) -> str:
    return "".join(stream_chat(messages, model=model, options=options)).strip()


def call_json(
    prompt: str,
    model: str = MODEL_PLANNER,
    system: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = call(prompt, model=model, system=system, options=options)
    if not raw:
        return {}

    candidates = [raw]
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(raw[first:last + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            continue

    logging.warning(f"[ollama] Could not parse JSON response from model={model}")
    return {}


def embed_text(
    text: str,
    model: str = MODEL_MEMORY,
    options: dict[str, Any] | None = None,
) -> list[float]:
    if not local_models_allowed():
        return []
    embed_payload = {
        "model": model,
        "input": text,
        "options": _merge_options(options),
    }
    timeout = _timeout_for(model)
    try:
        resp = _sync_client.post("/api/embed", json=embed_payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        embeddings = body.get("embeddings")
        emb = embeddings[0] if isinstance(embeddings, list) and embeddings else body.get("embedding", [])
        return [float(x) for x in emb]
    except Exception as exc:
        logging.warning(f"[ollama] /api/embed failed for model={model}, trying legacy endpoint: {exc}")
    legacy_payload = {
        "model": model,
        "prompt": text,
        "options": _merge_options(options),
    }
    try:
        resp = _sync_client.post("/api/embeddings", json=legacy_payload, timeout=timeout)
        resp.raise_for_status()
        emb = resp.json().get("embedding", [])
        return [float(x) for x in emb]
    except Exception as exc:
        logging.error(f"[ollama] embedding failed for model={model}: {exc}")
        return []


async def async_call(
    prompt: str,
    model: str = MODEL_NAVIGATOR,
    system: str = "",
    options: dict[str, Any] | None = None,
) -> str:
    return await asyncio.to_thread(call, prompt, model, system, options)


async def async_call_json(
    prompt: str,
    model: str = MODEL_PLANNER,
    system: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(call_json, prompt, model, system, options)


async def async_embed_text(
    text: str,
    model: str = MODEL_MEMORY,
    options: dict[str, Any] | None = None,
) -> list[float]:
    return await asyncio.to_thread(embed_text, text, model, options)


async def async_stream_chat(
    messages: list[dict[str, str]],
    model: str = MODEL_SYNTHESIZER,
    options: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    if not local_models_allowed():
        logging.warning("[ollama] Local model use is disabled; skipping async chat stream")
        return
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": _merge_options(options),
    }
    timeout = _timeout_for(model)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=None) as client:
        try:
            async with client.stream("POST", "/api/chat", json=payload, timeout=timeout) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = _extract_stream_text(data)
                    if token:
                        yield token
                    if data.get("done"):
                        break
        except Exception as exc:
            logging.error(f"[ollama] async chat stream failed for model={model}: {exc}")


def _validate_models() -> None:
    if skip_model_validation():
        logging.info("[ollama] Model validation skipped by runtime policy")
        return
    try:
        resp = httpx.get(f"{BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        available = {m.get("name") for m in resp.json().get("models", [])}
        for model in {
            MODEL_ORCHESTRATOR,
            MODEL_PLANNER,
            MODEL_NAVIGATOR,
            MODEL_EXECUTOR,
            MODEL_SYNTHESIZER,
            MODEL_CRITIC,
            MODEL_MEMORY,
            MODEL_ROUTER,
        }:
            if model not in available:
                logging.warning(f"[ollama] Model '{model}' not found — run: ollama pull {model}")
    except Exception as exc:
        logging.warning(f"[ollama] Could not validate local models: {exc}")


_validate_models()
