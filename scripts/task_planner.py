"""Multi-agent task planner — Perplexity Computer parity.

Decomposes a high-level goal into a DAG of typed tasks, each assigned
to a specialist agent role. Roles mirror Perplexity Computer’s internal
architecture:

  orchestrator  — decides the overall plan (this module)
  researcher    — web search + page reading
  analyst       — synthesize / reason over gathered evidence
  coder         — write / run Python code
  writer        — produce final report / answer
  browser       — low-level click/fill/navigate actions
  file          — read/write local files

Each task carries:
  id            — unique str
  role          — one of the roles above
  goal          — what this task must accomplish
  depends_on    — list of task ids that must finish first
  max_steps     — step budget for browser tasks
  chatbot_mode  — route to chatbot UI instead of local Ollama
  priority      — 1 (highest) – 5 (lowest)
"""
from __future__ import annotations
import json
import logging
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

_MODELS_PATH = ROOT / "configs" / "models.json"
_cfg = json.loads(_MODELS_PATH.read_text()) if _MODELS_PATH.exists() else {}

MODEL_PLANNER  = _cfg.get("planner", "qwen3:8b")
MODEL_HEAVY    = _cfg.get("heavy",   "qwen3:14b")
CHATBOT_THRESH = _cfg.get("chatbot_threshold", 7)

# ── Role descriptions sent to the planner model ──────────────────────────────
ROLE_DESCRIPTIONS = {
    "researcher": "Search the web, open URLs, and extract information from pages.",
    "analyst":    "Reason over gathered evidence, cross-reference claims, and synthesize.",
    "coder":      "Write and execute Python code to compute, transform, or automate data.",
    "writer":     "Produce a well-structured final answer, report, or document.",
    "browser":    "Perform low-level browser interactions: click, fill, hover, drag, scroll.",
    "file":       "Read from or write to local files on the user’s machine.",
}

_ROLE_LIST = ", ".join(f"{k} ({v})" for k, v in ROLE_DESCRIPTIONS.items())


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _default_plan(goal: str) -> list[dict]:
    """Fallback single-task plan when the model returns garbage."""
    return [
        {
            "id":           _uid(),
            "role":         "researcher",
            "goal":         goal,
            "depends_on":   [],
            "max_steps":    20,
            "chatbot_mode": False,
            "priority":     1,
        }
    ]


def build_task_graph(goal: str) -> list[dict[str, Any]]:
    """Ask the planner model to decompose `goal` into a task DAG.

    Returns a list of task dicts, topologically ordered (dependents last).
    Always returns a usable list — falls back to a single researcher task.
    """
    from scripts.ollama_client import call_json
    from scripts.router import complexity_score

    score = complexity_score(goal)
    model = MODEL_HEAVY if score >= CHATBOT_THRESH - 1 else MODEL_PLANNER

    prompt = (
        f"You are a task planner for an autonomous computer agent.\n\n"
        f"GOAL: {goal}\n\n"
        f"Available agent roles:\n{_ROLE_LIST}\n\n"
        "Decompose the goal into 1-6 discrete tasks. Each task must be completable "
        "by exactly one role. Tasks may depend on earlier tasks.\n\n"
        "Return ONLY valid JSON (no prose):\n"
        '{"tasks": [{"id": "<short_id>", "role": "<role>", "goal": "<what to do>", '
        '"depends_on": ["<id>"], "max_steps": 15, "priority": 1}]}'
    )
    result = call_json(prompt, model=model)

    raw_tasks = (result or {}).get("tasks", [])
    if not raw_tasks or not isinstance(raw_tasks, list):
        logging.warning("[task_planner] model returned no tasks — using default plan")
        return _default_plan(goal)

    # Normalise and validate each task
    seen_ids: set[str] = set()
    tasks: list[dict] = []
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        tid  = str(t.get("id") or _uid())
        role = str(t.get("role", "researcher")).lower()
        if role not in ROLE_DESCRIPTIONS:
            role = "researcher"
        task_goal = str(t.get("goal", goal))[:500]
        deps = [str(d) for d in (t.get("depends_on") or []) if d in seen_ids]

        task: dict[str, Any] = {
            "id":           tid,
            "role":         role,
            "goal":         task_goal,
            "depends_on":   deps,
            "max_steps":    int(t.get("max_steps") or 15),
            "chatbot_mode": bool(t.get("chatbot_mode", False)),
            "priority":     int(t.get("priority") or 1),
        }
        seen_ids.add(tid)
        tasks.append(task)

    if not tasks:
        return _default_plan(goal)

    logging.info(f"[task_planner] {len(tasks)} task(s) planned for: {goal[:80]}")
    return tasks


def tasks_to_stages(tasks: list[dict]) -> list[dict]:
    """Convert a task graph into the stage-list format expected by orchestrator / navigation_agent."""
    return [
        {
            "stage":         t["id"],
            "goal":          t["goal"],
            "role":          t["role"],
            "max_steps":     t["max_steps"],
            "chatbot_mode":  t["chatbot_mode"],
            "depends_on":    t["depends_on"],
            "priority":      t["priority"],
        }
        for t in tasks
    ]
