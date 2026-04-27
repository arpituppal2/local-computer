"""Subagent dispatcher: local Ollama | AI chatbot UI | cloud worker.

Routing priority:
  1. If task is marked chatbot_mode or complexity >= threshold → chatbot UI
  2. If local RAM allows → run_locally via Ollama
  3. Cloud worker (if configured) → cloud HTTP POST
  4. Fallback → local Ollama regardless
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

import psutil

ROOT = Path(__file__).resolve().parent.parent

_MODELS_PATH = ROOT / "configs" / "models.json"
_MODELS = json.loads(_MODELS_PATH.read_text()) if _MODELS_PATH.exists() else {}

MODEL_PLANNER = _MODELS.get("planner", "qwen3:8b")
MODEL_HEAVY   = _MODELS.get("heavy",   "qwen3:14b")

# If task complexity_score >= this, route to chatbot UI instead of local 14b
CHATBOT_THRESHOLD = _MODELS.get("chatbot_threshold", 7)

# Max simultaneous local Ollama subagents
# 3 × qwen3:4b ≈ 4.5 GB; leaves headroom for Chromium + planner
MAX_LOCAL_PARALLEL = _MODELS.get("max_local_parallel", 3)

CLOUD_BACKENDS = {
    "cloud_run":    {"check_cmd": "gcloud config get-value project"},
    "railway":      {"check_cmd": "railway whoami"},
    "huggingface":  {"check_cmd": "huggingface-cli whoami"},
    "render":       {"check_cmd": "which render"},
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _available_ram_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)


def _ram_ok_for_heavy() -> bool:
    """Is there enough free RAM to run qwen3:14b alongside Chromium?"""
    # 14b Q4 ≈ 8 GB; Chromium ≈ 0.5 GB; keep 1 GB headroom → need ~9.5 GB free
    return _available_ram_gb() >= 9.5


def _backend_available(name: str) -> bool:
    import subprocess
    cmd = CLOUD_BACKENDS[name]["check_cmd"]
    try:
        subprocess.run(cmd.split(), capture_output=True, check=True, timeout=5)
        return True
    except Exception:
        return False


# ── Core dispatch functions ────────────────────────────────────────────────────

def _run_locally(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run a subagent task via local Ollama."""
    from scripts.ollama_client import call_json
    goal = task.get("goal", "")
    prompt = f"Complete this research task and return JSON with 'findings' key:\n{goal}"
    result = call_json(prompt, model=MODEL_PLANNER)
    return {"status": "done", "goal": goal, "output": result, "source": "local_ollama"}


def _run_via_chatbot(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run a subagent task by querying a cloud AI chatbot UI via Playwright."""
    from scripts.ai_chatbot_subagent import chatbot_query, pick_best_backend
    goal = task.get("goal", "")
    backend = task.get("chatbot_backend") or pick_best_backend(goal)
    logging.info(f"[subagents] chatbot dispatch → {backend}: {goal[:80]}")
    result = chatbot_query(goal, backend=backend)
    return {
        "status": "done" if result["success"] else "error",
        "goal": goal,
        "output": {"findings": result["response"]},
        "source": f"chatbot:{backend}",
        "error": result.get("error", ""),
    }


def _run_cloud_worker(task: Dict[str, Any], worker_url: str) -> Dict[str, Any]:
    """POST a task to a deployed cloud worker endpoint."""
    import httpx
    goal = task.get("goal", "")
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(worker_url, json=task)
            resp.raise_for_status()
            return {"status": "done", "goal": goal, "output": resp.json(), "source": "cloud_worker"}
    except Exception as e:
        logging.warning(f"[subagents] Cloud worker failed ({e}), falling back to local")
        return _run_locally(task)


# ── Smart dispatch ─────────────────────────────────────────────────────────────

def dispatch(task: Dict[str, Any]) -> Dict[str, Any]:
    """Route a single task to the best available execution venue.

    Task dict keys:
      goal (str)             — what the agent should do
      complexity (int)       — 0-10; if >= chatbot_threshold → chatbot
      chatbot_mode (bool)    — force chatbot routing
      chatbot_backend (str)  — override backend (gemini|chatgpt|claude|copilot|perplexity)
      worker_url (str)       — if set, try cloud HTTP worker first
      local_only (bool)      — skip chatbot/cloud even if RAM is tight
    """
    goal = task.get("goal", "")
    complexity = int(task.get("complexity", 0))
    force_chatbot = bool(task.get("chatbot_mode", False))
    local_only = bool(task.get("local_only", False))
    worker_url = task.get("worker_url", "")

    # 1. Explicit chatbot flag OR complexity too high for local heavy model
    if not local_only and (force_chatbot or complexity >= CHATBOT_THRESHOLD):
        result = _run_via_chatbot(task)
        if result["status"] == "done":
            return result
        logging.warning(f"[subagents] chatbot failed for '{goal[:60]}', falling back to local")

    # 2. Cloud worker (if configured and available)
    if not local_only and worker_url:
        return _run_cloud_worker(task, worker_url)

    # 3. Local heavy model — but only if we have the RAM
    if not _ram_ok_for_heavy() and "heavy" in task.get("preferred_model", ""):
        logging.warning("[subagents] Insufficient RAM for heavy model; using planner instead")

    return _run_locally(task)


# ── Parallel batch dispatch ────────────────────────────────────────────────────

def run_parallel_subagents(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run a list of tasks in parallel, respecting MAX_LOCAL_PARALLEL for Ollama jobs.

    Chatbot tasks are NOT subject to the local semaphore (they open separate browser tabs).
    """
    if not tasks:
        return []

    results: List[Dict[str, Any]] = [{}] * len(tasks)
    local_sem = threading.Semaphore(MAX_LOCAL_PARALLEL)

    def _run(idx: int, task: Dict[str, Any]) -> None:
        is_chatbot = (
            task.get("chatbot_mode")
            or int(task.get("complexity", 0)) >= CHATBOT_THRESHOLD
        )
        if is_chatbot:
            results[idx] = dispatch(task)
        else:
            with local_sem:
                results[idx] = dispatch(task)

    threads = [
        threading.Thread(target=_run, args=(i, t), daemon=True)
        for i, t in enumerate(tasks)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    return results


# ── Backward-compatible pick_subagent ─────────────────────────────────────────

def pick_subagent(goal: str, state: dict, history: list) -> str:
    """Returns a routing label: 'chatbot' | 'search' | 'workflow' | 'browse'."""
    g = (goal or "").lower()
    url = (state.get("url") or "").lower()

    # Force chatbot for complex reasoning tasks
    if any(x in g for x in [
        "ask gemini", "use claude", "ask chatgpt", "ask copilot", "via chatgpt",
        "ask perplexity", "deep analysis", "synthesize", "explain in depth",
    ]):
        return "chatbot"

    if any(x in g for x in ["calendar", "docs", "drive", "gmail", "youtube",
                            "prose", "notion", "sheet", "slides"]):
        return "workflow"
    if any(x in url for x in ["calendar.google.com", "docs.google.com",
                              "drive.google.com", "mail.google.com", "youtube.com"]):
        return "workflow"
    if any(x in g for x in ["search", "look up", "find", "latest", "news",
                            "price", "who is", "what is"]):
        return "search"
    if any(x in url for x in ["bing.com", "google.com/search", "duckduckgo.com"]):
        return "search"
    return "browse"
