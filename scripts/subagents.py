"""Subagent dispatcher with local parallel inference and cloud offload."""
from __future__ import annotations
import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent

# Load config
_MODELS_PATH = ROOT / "configs" / "models.json"
_MODELS = json.loads(_MODELS_PATH.read_text()) if _MODELS_PATH.exists() else {}

MODEL_PLANNER = _MODELS.get("planner", "qwen3:8b")
MODEL_HEAVY  = _MODELS.get("heavy", "qwen3:14b")

# Cloud backend definitions (free tiers)
CLOUD_BACKENDS = {
    "cloud_run":  {"check_cmd": "gcloud config get-value project", "url": "https://REGION-PROJECT_ID.REGION.r.appspot.com"},
    "railway":   {"check_cmd": "railway whoami", "url": "https://PROJECT_ID.railway.app"},
    "huggingface": {"check_cmd": "which huggingface-cli", "url": "https://huggingface.co/spaces/PROJECT_ID"},
    "render":    {"check_cmd": "which render", "url": "https://PROJECT_ID.onrender.com"},
}

MAX_LOCAL_SUBAGENTS = 3  # 3x qwen3:4b (~1.5GB each) = 4.5GB; leaves 10GB for 8b planner + Chromium


def _backend_available(name: str) -> bool:
    cmd = CLOUD_BACKENDS[name]["check_cmd"]
    try:
        subprocess.run(cmd.split(), capture_output=True, check=True, timeout=5)
        return True
    except Exception:
        return False


def _run_locally(task: dict) -> dict:
    goal = task.get("goal", "")
    model = MODEL_PLANNER
    prompt = f"Complete this research task and return JSON with 'findings' key:\n{goal}"
    from scripts.ollama_client import call_json
    result = call_json(model, prompt)
    return {"status": "done", "goal": goal, "output": result}


def dispatch_cloud_subagent(task: dict, backend: str = "cloud_run") -> dict:
    if not _backend_available(backend):
        logging.warning(f"[subagents] {backend} CLI not found — running locally")
        return _run_locally(task)

    logging.info(f"[subagents] Dispatching task to {backend}: {task.get('goal', '')[:60]}")
    # In full implementation: POST payload to deployed worker URL
    # For now, fall back to local
    return _run_locally(task)


def run_parallel_subagents(tasks: List[dict], backend: str = "local") -> List[dict]:
    if not tasks:
        return []

    results = [{}] * len(tasks)
    semaphore = threading.Semaphore(MAX_LOCAL_SUBAGENTS)

    def _run(idx: int, task: dict):
        with semaphore:
            results[idx] = dispatch_cloud_subagent(task, backend)

    threads = [threading.Thread(target=_run, args=(i, t), daemon=True)
               for i, t in enumerate(tasks)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def pick_subagent(goal: str, state: dict, history: List[dict]) -> str:
    g = (goal or "").lower()
    url = (state.get("url") or "").lower()

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
