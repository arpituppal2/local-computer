"""Runtime policy switches that keep local model use explicit.

The project can run in two very different modes:

* model-free mode: dashboard, plugin status, uploads, workspace inspection
* local model mode: Ollama-backed planning, synthesis, embeddings, and routing

Local model mode is intentionally opt-in so opening the dashboard or running a
status command cannot accidentally load a model into RAM/VRAM.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from scripts.os_profile import detect_os

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PATH = ROOT / "configs" / "runtime.json"

TRUTHY = {"1", "true", "yes", "on", "allow", "enabled"}
FALSY = {"0", "false", "no", "off", "deny", "disabled"}
INTELLIGENCE_LEVELS = {"xlow", "low", "medium", "high", "xhigh", "max"}


def _load_runtime() -> dict[str, Any]:
    try:
        return json.loads(RUNTIME_PATH.read_text())
    except Exception:
        return {}


def update_runtime(updates: dict[str, Any]) -> dict[str, Any]:
    runtime = _load_runtime()
    runtime.update(updates)
    RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_PATH.write_text(json.dumps(runtime, indent=2) + "\n")
    return runtime


def env_flag(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False
    return None


def local_models_allowed() -> bool:
    """Return True only when local inference has been explicitly enabled."""
    env_value = env_flag("LOCAL_COMPUTER_ALLOW_MODELS")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("allow_local_models", False))


def external_ai_allowed() -> bool:
    """Return True only when browser-based cloud AI chatbots are explicitly enabled."""
    env_value = env_flag("LOCAL_COMPUTER_ALLOW_EXTERNAL_AI")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("allow_external_ai", False))


def cloud_workers_allowed() -> bool:
    """Return True only when remote worker dispatch is explicitly enabled."""
    env_value = env_flag("LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("allow_cloud_workers", False))


def auto_select_models() -> bool:
    env_value = env_flag("LOCAL_COMPUTER_AUTO_SELECT_MODELS")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("auto_select_models", True))


def auto_install_python() -> bool:
    env_value = env_flag("LOCAL_COMPUTER_AUTO_INSTALL_PYTHON")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("auto_install_python", True))


def auto_install_ollama() -> bool:
    env_value = env_flag("LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("auto_install_ollama", False))


def auto_install_models() -> bool:
    env_value = env_flag("LOCAL_COMPUTER_AUTO_INSTALL_MODELS")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("auto_install_models", False))


def max_ram_gb() -> float | None:
    raw = os.getenv("LOCAL_COMPUTER_MAX_RAM_GB")
    if raw is None:
        raw = _load_runtime().get("max_ram_gb")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def max_gpu_percent() -> float:
    raw = os.getenv("LOCAL_COMPUTER_MAX_GPU_PERCENT")
    if raw is None:
        raw = _load_runtime().get("max_gpu_percent", 90)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 90.0
    return max(50.0, min(99.0, value))


def intelligence_level() -> str:
    raw = os.getenv("LOCAL_COMPUTER_INTELLIGENCE_LEVEL")
    if raw is None:
        raw = _load_runtime().get("intelligence_level", "medium")
    value = str(raw or "medium").strip().lower()
    return value if value in INTELLIGENCE_LEVELS else "medium"


def learn_step_by_step() -> bool:
    env_value = env_flag("LOCAL_COMPUTER_LEARN_STEP_BY_STEP")
    if env_value is not None:
        return env_value
    return bool(_load_runtime().get("learn_step_by_step", False))


def skip_model_validation() -> bool:
    """Return True when startup should avoid even lightweight Ollama checks."""
    env_value = env_flag("LOCAL_COMPUTER_SKIP_MODEL_VALIDATE")
    if env_value is not None:
        return env_value
    return not local_models_allowed()


def workspace_root() -> Path:
    """Resolve the folder Locus should inhabit."""
    raw = os.getenv("LOCAL_COMPUTER_WORKSPACE") or _load_runtime().get("workspace_root")
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return Path.cwd().resolve()


def runtime_summary() -> dict[str, Any]:
    workspace = workspace_root()
    os_profile = detect_os()
    return {
        "os": os_profile.to_dict(),
        "supported_os": os_profile.supported,
        "allow_local_models": local_models_allowed(),
        "allow_external_ai": external_ai_allowed(),
        "allow_cloud_workers": cloud_workers_allowed(),
        "auto_select_models": auto_select_models(),
        "auto_install_python": auto_install_python(),
        "auto_install_ollama": auto_install_ollama(),
        "auto_install_models": auto_install_models(),
        "max_ram_gb": max_ram_gb(),
        "max_gpu_percent": max_gpu_percent(),
        "intelligence_level": intelligence_level(),
        "learn_step_by_step": learn_step_by_step(),
        "skip_model_validation": skip_model_validation(),
        "workspace_root": str(workspace),
        "workspace_exists": workspace.exists(),
    }
