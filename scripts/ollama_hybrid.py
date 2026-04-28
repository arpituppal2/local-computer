"""Routes prompts to local Ollama or the MLX backend for heavy tasks.

For MODEL_HEAVY (qwen3:14b) calls, MLX via Apple's Metal framework gives
~30-40% better tokens/sec on M-series chips compared to Ollama's llama.cpp.
Falls back to Ollama if mlx-lm is not installed.
"""
from __future__ import annotations
import logging

# ---------------------------------------------------------------------------
# MLX path (heavy tasks only)
# ---------------------------------------------------------------------------

def _mlx_available() -> bool:
    try:
        import mlx_lm  # noqa: F401
        return True
    except ImportError:
        return False


_MLX_READY = _mlx_available()


def _call_mlx(prompt: str, model_path: str, max_tokens: int = 2048) -> str:
    """Run inference via mlx-lm (Apple Metal). model_path is a HuggingFace repo id."""
    from mlx_lm import load, generate  # type: ignore
    model, tokenizer = load(model_path)
    response = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
    return response.strip()


# ---------------------------------------------------------------------------
# Ollama path (default for all other tasks)
# ---------------------------------------------------------------------------

API_URL = "http://127.0.0.1:11434/api/generate"


def _call_ollama(prompt: str, model: str, max_tokens: int = 512) -> str:
    import httpx
    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "num_predict": max_tokens,
            "num_ctx": 2048,
        },
    }
    chunks: list[str] = []
    import json
    with httpx.stream("POST", API_URL, json=payload, timeout=120) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            chunks.append(data.get("response", ""))
            if data.get("done"):
                break
    return "".join(chunks).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# HuggingFace repo id used when routing to MLX
_MLX_HEAVY_MODEL = "mlx-community/Qwen2.5-14B-Instruct-4bit"


def send_prompt(
    prompt: str,
    model: str = "qwen3:8b",
    use_mlx_for_heavy: bool = True,
    max_tokens: int = 1024,
) -> str:
    """Dispatch a prompt to MLX (if heavy + available) or Ollama.

    Args:
        prompt: the input text.
        model: Ollama model tag. If '14b' in model and MLX is available,
               the call is routed to MLX for faster Metal-accelerated inference.
        use_mlx_for_heavy: set False to force Ollama even for 14b models.
        max_tokens: generation limit.
    """
    is_heavy = "14b" in model
    if is_heavy and use_mlx_for_heavy and _MLX_READY:
        logging.info(f"[hybrid] Routing heavy model → MLX ({_MLX_HEAVY_MODEL})")
        try:
            return _call_mlx(prompt, _MLX_HEAVY_MODEL, max_tokens=max_tokens)
        except Exception as e:
            logging.warning(f"[hybrid] MLX failed ({e}), falling back to Ollama")

    return _call_ollama(prompt, model, max_tokens=max_tokens)
