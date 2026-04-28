"""Multi-agent task planner with upfront capability assessment.

Phase 1 — Capability Assessment
  The planner model inspects the goal and decides WHICH execution modes are
  needed before any tasks are scheduled. It returns:

    needs_browser  (bool)  — requires real Chromium control (click/fill/scroll)
    needs_response (bool)  — a plain Ollama text answer is sufficient
    needs_subagents(bool)  — task needs to be broken into parallel sub-workers
    needs_online   (bool)  — needs live web data but WITHOUT opening a new tab
                             (use search API / httpx fetch, not Playwright)
    needs_api      (bool)  — would benefit from a dedicated API (stub for now)
    confidence     (0-1)   — how certain the planner is about this assessment

Phase 2 — Task Decomposition
  Only after the capability plan is confirmed does the planner decompose the
  goal into a typed task DAG, annotating each task with the correct execution
  mode derived from Phase 1.

Roles:
  orchestrator  — decides the overall plan (this module)
  researcher    — web search + page reading (online, no new tab via search API)
  analyst       — synthesize / reason over gathered evidence
  coder         — write / run Python code
  writer        — produce final report / answer (headless, Ollama only)
  browser       — low-level click/fill/navigate actions (needs Chromium)
  file          — read/write local files
"""
from __future__ import annotations
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

_MODELS_PATH = ROOT / "configs" / "models.json"
_cfg = json.loads(_MODELS_PATH.read_text()) if _MODELS_PATH.exists() else {}

MODEL_PLANNER  = _cfg.get("planner", "qwen3:8b")
MODEL_HEAVY    = _cfg.get("heavy",   "qwen3:14b")
CHATBOT_THRESH = _cfg.get("chatbot_threshold", 7)

# ── Capability modes ──────────────────────────────────────────────────────────

@dataclass
class CapabilityPlan:
    """Upfront decision about what execution modes this goal needs."""
    needs_browser:   bool  = False  # Chromium control (click/fill/scroll)
    needs_response:  bool  = True   # Plain Ollama answer is sufficient
    needs_subagents: bool  = False  # Parallelise into multiple sub-workers
    needs_online:    bool  = False  # Live web data WITHOUT opening a new tab
    needs_api:       bool  = False  # Would use a dedicated API (stub for now)
    confidence:      float = 1.0    # 0.0 – 1.0 planner confidence
    reasoning:       str   = ""     # Short human-readable justification

    def any_external(self) -> bool:
        """True if this goal needs anything beyond a plain Ollama call."""
        return self.needs_browser or self.needs_online or self.needs_api

    def to_log(self) -> str:
        flags = []
        if self.needs_browser:   flags.append("browser")
        if self.needs_response:  flags.append("response")
        if self.needs_subagents: flags.append("subagents")
        if self.needs_online:    flags.append("online")
        if self.needs_api:       flags.append("api")
        return f"[{', '.join(flags)}] confidence={self.confidence:.2f} — {self.reasoning[:120]}"


# ── Role descriptions ─────────────────────────────────────────────────────────

ROLE_DESCRIPTIONS = {
    "researcher": "Fetch live web data without opening a new tab (search API / httpx).",
    "analyst":    "Reason over gathered evidence, cross-reference claims, and synthesize.",
    "coder":      "Write and execute Python code to compute, transform, or automate data.",
    "writer":     "Produce a well-structured final answer, report, or document.",
    "browser":    "Perform low-level browser interactions: click, fill, hover, drag, scroll.",
    "file":       "Read from or write to local files on the user's machine.",
}

_ROLE_LIST = "\n".join(f"  {k}: {v}" for k, v in ROLE_DESCRIPTIONS.items())

# Roles that never need a live browser page
HEADLESS_ROLES = {"writer", "analyst", "planner", "critic", "summarizer", "coder", "file"}
# Roles that need Chromium open
BROWSER_ROLES   = {"browser", "navigator", "actor"}
# Roles that fetch online data via API/httpx (no new tab)
ONLINE_ROLES    = {"researcher"}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _default_plan(goal: str) -> list[dict]:
    """Fallback single-task plan when the model returns garbage."""
    return [
        {
            "id":           _uid(),
            "role":         "writer",
            "goal":         goal,
            "depends_on":   [],
            "max_steps":    5,
            "chatbot_mode": False,
            "priority":     1,
            "exec_mode":    "response",
        }
    ]


# ── Phase 1: Capability Assessment ───────────────────────────────────────────

_CAPABILITY_PROMPT = """\
You are the capability planner for an autonomous computer agent running on a Mac
with 16GB RAM. Your job is to decide — BEFORE decomposing anything — which
execution modes a goal requires.

Available execution modes:
  browser   — needs real Chromium control (clicking, form filling, scrolling websites)
  response  — a direct Ollama text answer is sufficient (no external tools needed)
  subagents — goal is large enough to benefit from parallel sub-workers
  online    — needs live web data, but fetch it silently (search API / httpx, NO new tab)
  api       — would benefit from a dedicated third-party API (mark for future use)

Rules:
  - If the goal is a simple question, statement, or writing task → response only.
  - If the goal mentions clicking, logging in, filling forms, or navigating a site
    the user wants to see → browser.
  - If the goal needs current facts (prices, news, scores, weather, live data) but
    does NOT need to show the user a page → online (not browser).
  - Only set subagents=true if the goal has 3+ clearly independent sub-problems.
  - api is always false for now (stub).
  - confidence: 0.9+ if very clear, 0.6-0.9 if ambiguous, <0.6 if genuinely unclear.

GOAL: {goal}

Return ONLY valid JSON, no prose:
{{"needs_browser": false, "needs_response": true, "needs_subagents": false,
  "needs_online": false, "needs_api": false,
  "confidence": 0.95, "reasoning": "one sentence explanation"}}
"""


def assess_capabilities(goal: str) -> CapabilityPlan:
    """Phase 1 — ask the planner model what execution modes this goal needs."""
    from scripts.ollama_client import call_json
    from scripts.router import complexity_score

    score = complexity_score(goal)
    model = MODEL_HEAVY if score >= CHATBOT_THRESH - 1 else MODEL_PLANNER

    raw = call_json(_CAPABILITY_PROMPT.format(goal=goal), model=model)

    if not raw:
        # Fallback: simple heuristic
        g = goal.lower()
        browser_kw  = ["click", "fill", "login", "sign in", "go to", "open site",
                       "navigate to", "scroll", "download file", "take screenshot"]
        online_kw   = ["latest", "current", "news", "price", "weather", "score",
                       "today", "live", "search for", "look up", "find online"]
        needs_b = any(k in g for k in browser_kw)
        needs_o = any(k in g for k in online_kw) and not needs_b
        return CapabilityPlan(
            needs_browser=needs_b,
            needs_response=not (needs_b or needs_o),
            needs_subagents=False,
            needs_online=needs_o,
            needs_api=False,
            confidence=0.6,
            reasoning="heuristic fallback (model returned no JSON)",
        )

    plan = CapabilityPlan(
        needs_browser=bool(raw.get("needs_browser",   False)),
        needs_response=bool(raw.get("needs_response",  True)),
        needs_subagents=bool(raw.get("needs_subagents", False)),
        needs_online=bool(raw.get("needs_online",    False)),
        needs_api=bool(raw.get("needs_api",       False)),
        confidence=float(raw.get("confidence",     0.8)),
        reasoning=str(raw.get("reasoning",       "")),
    )
    logging.info(f"[task_planner] capability → {plan.to_log()}")
    return plan


# ── Phase 2: Task Decomposition ───────────────────────────────────────────────

_TASK_PROMPT = """\
You are a task planner for an autonomous computer agent.

GOAL: {goal}

Capability plan already decided:
  browser={needs_browser}, response={needs_response},
  subagents={needs_subagents}, online={needs_online}

Available agent roles:
{role_list}

Decompose the goal into 1-6 discrete tasks consistent with the capability plan.
- If response=true and browser=false and online=false → use writer role, 1 task.
- If online=true → use researcher for fetching, then analyst/writer for synthesis.
- If browser=true → use browser role for interactions, writer for output.
- Each task completed by exactly one role. Tasks may depend on earlier tasks.

Return ONLY valid JSON (no prose):
{{"tasks": [{{"id": "<short_id>", "role": "<role>", "goal": "<what to do>",
  "depends_on": ["<id>"], "max_steps": 10, "priority": 1,
  "exec_mode": "response|online|browser|file"}}]}}
"""


def build_task_graph(
    goal: str,
    cap: CapabilityPlan | None = None,
) -> list[dict[str, Any]]:
    """Phase 2 — decompose goal into a typed task DAG.

    If `cap` is not provided, assess_capabilities() is called first.
    Returns a topologically ordered list of task dicts.
    """
    from scripts.ollama_client import call_json
    from scripts.router import complexity_score

    if cap is None:
        cap = assess_capabilities(goal)

    score = complexity_score(goal)
    model = MODEL_HEAVY if score >= CHATBOT_THRESH - 1 else MODEL_PLANNER

    prompt = _TASK_PROMPT.format(
        goal=goal,
        needs_browser=cap.needs_browser,
        needs_response=cap.needs_response,
        needs_subagents=cap.needs_subagents,
        needs_online=cap.needs_online,
        role_list=_ROLE_LIST,
    )
    result = call_json(prompt, model=model)

    raw_tasks = (result or {}).get("tasks", [])
    if not raw_tasks or not isinstance(raw_tasks, list):
        logging.warning("[task_planner] model returned no tasks — using default plan")
        return _default_plan(goal)

    seen_ids: set[str] = set()
    tasks: list[dict] = []
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        tid  = str(t.get("id") or _uid())
        role = str(t.get("role", "writer")).lower()
        if role not in ROLE_DESCRIPTIONS:
            role = "writer"
        task_goal = str(t.get("goal", goal))[:500]
        deps = [str(d) for d in (t.get("depends_on") or []) if d in seen_ids]
        exec_mode = str(t.get("exec_mode", "response"))

        task: dict[str, Any] = {
            "id":           tid,
            "role":         role,
            "goal":         task_goal,
            "depends_on":   deps,
            "max_steps":    int(t.get("max_steps") or 10),
            "chatbot_mode": bool(t.get("chatbot_mode", False)),
            "priority":     int(t.get("priority") or 1),
            "exec_mode":    exec_mode,
        }
        seen_ids.add(tid)
        tasks.append(task)

    if not tasks:
        return _default_plan(goal)

    logging.info(f"[task_planner] {len(tasks)} task(s) planned for: {goal[:80]}")
    return tasks


def tasks_to_stages(tasks: list[dict]) -> list[dict]:
    """Convert a task graph into the stage-list format expected by orchestrator."""
    return [
        {
            "stage":        t["id"],
            "goal":         t["goal"],
            "role":         t["role"],
            "max_steps":    t["max_steps"],
            "chatbot_mode": t["chatbot_mode"],
            "depends_on":   t["depends_on"],
            "priority":     t["priority"],
            "exec_mode":    t.get("exec_mode", "response"),
        }
        for t in tasks
    ]
