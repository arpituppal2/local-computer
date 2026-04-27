#!/usr/bin/env python3
"""
scripts/navigation_agent.py
Primary Perplexity-style research loop.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

# ── repo root ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.critic_agent import critique
from scripts.ollama_client import (
    MODEL_ACTOR,
    MODEL_HEAVY,
    MODEL_PLANNER,
    call,
    call_json,
)
from scripts.observer import observe
from scripts.executor import execute
from playwright.sync_api import sync_playwright
from scripts.memory import Memory
from scripts.event_logger import EventLogger
from scripts.claim_extractor import extract_claims
from scripts.source_scoring import score_source, domain_of
from scripts.claim_cluster import cluster_claims
from scripts.long_term_memory import (
    should_read,
    read_relevant,
    manage_memory,
)

# ── runtime config ─────────────────────────────────────────────────────────
RT_FILE = ROOT / "configs" / "runtime.json"
RUNTIME = json.loads(RT_FILE.read_text()) if RT_FILE.exists() else {}

OUT_DIR = ROOT / RUNTIME.get("outputs_dir", "outputs")
LOG_DIR = ROOT / RUNTIME.get("logs_dir", "logs")
MAX_STEPS = RUNTIME.get("max_steps_per_stage", 25)

SEARCH_BASE = "https://www.bing.com/search?q="

HIGH_SCORE_THRESH = 4
STUCK_THRESHOLD   = 3
EVIDENCE_GOAL_BASE = 6

# Goals that never need web browsing — matched as substrings (case-insensitive)
_LOCAL_KEYWORDS = (
    "test", "hello", "ping", "echo", "debug", "check",
    "calculate", "compute", "convert", "summarize this",
    "write a", "generate a", "create a", "list the",
    "what is 2", "what is 1",
)


def _needs_web(goal: str) -> bool:
    """Return False for goals that clearly don't need a browser."""
    g = goal.lower().strip()
    if any(k in g for k in _LOCAL_KEYWORDS):
        return False
    # Ask the planner model to classify — lightweight call
    verdict = call_json(
        f"Does answering this goal require browsing the web or searching for "
        f"current information?\nGoal: {goal}\n"
        f"Reply with JSON: {{\"needs_web\": true}} or {{\"needs_web\": false}}",
        model=MODEL_PLANNER,
    )
    return bool((verdict or {}).get("needs_web", True))


# ── adaptive evidence goal ─────────────────────────────────────────────────
def adaptive_evidence_goal(memory: Memory) -> int:
    base  = EVIDENCE_GOAL_BASE
    bonus = len(set(e.get("source_domain", "") for e in memory.evidence))
    return base + max(0, 3 - bonus)


# ════════════════════════════════════════════════════════════════════════
# ACTION DECISION MODEL
# ════════════════════════════════════════════════════════════════════════
def decide_action(goal: str, state: dict, memory: Memory, step: int) -> dict:
    targets = state.get("candidate_targets") or []

    targets_brief = [
        f"[{t['target_id']}] {t['kind']} {t['text'][:80]}"
        for t in targets[:30]
    ]

    recent_actions = [
        f"{r['action'].get('action')} -> {r['result'].get('ok')}"
        for r in list(memory.recent_actions)[-8:]
    ]

    recent_failures = [
        f"{r['action'].get('action')} {r['action'].get('target', {}).get('text','')[:40]}"
        for r in list(memory.recent_failures)[-4:]
    ]

    prior_block = ""
    if memory.prior_context:
        prior_block = f"\nPRIOR CONTEXT FROM MEMORY:\n{memory.prior_context[:800]}\n"

    prompt = f"""
You are a research agent.

GOAL: {goal}
{prior_block}
STATE:
URL: {state.get('url')}
TITLE: {state.get('title')}
TEXT:
{(state.get('visible_text') or '')[:1500]}

TARGETS:
{chr(10).join(targets_brief)}

RECENT ACTIONS:
{chr(10).join(recent_actions)}

RECENT FAILURES:
{chr(10).join(recent_failures)}

RULES:
- use search, navigate, click, fill, press, scroll, go_back, finish
- avoid repeating failures
- return JSON only
"""

    return call_json(prompt, model=MODEL_ACTOR) or {}


# ════════════════════════════════════════════════════════════════════════
# PAGE PROCESSING
# ════════════════════════════════════════════════════════════════════════
def _already_visited(url: str, memory: Memory) -> bool:
    """Return True if this URL is already in the evidence store."""
    return any(e.get("url") == url for e in memory.evidence)


def process_page(state: dict, memory: Memory, log: EventLogger) -> int:
    text = state.get("visible_text", "")
    url  = state.get("url", "")

    if len(text) < 200:
        return 0

    if _already_visited(url, memory):
        log.log("page_skip", url=url, reason="already_visited")
        return 0

    score  = score_source(url, state.get("title", ""), text)
    claims = extract_claims(state.get("title", ""), url, text)

    if not claims:
        return 0

    memory.add_evidence({
        "url":           url,
        "title":         state.get("title", ""),
        "score":         score,
        "source_domain": domain_of(url),
        "claims":        claims,
    })

    log.log("evidence", url=url, score=score, claim_count=len(claims))
    return len(claims)


def enough_evidence(memory: Memory) -> bool:
    """True when we have enough trusted, diverse evidence to stop."""
    goal = adaptive_evidence_goal(memory)
    if len(memory.evidence) < goal:
        return False

    trusted = sum(1 for e in memory.evidence if e.get("score", 0) >= HIGH_SCORE_THRESH)
    domains  = len(set(e.get("source_domain") for e in memory.evidence))
    return trusted >= 2 and domains >= 2


# ════════════════════════════════════════════════════════════════════════
# STAGE LOOP
# ════════════════════════════════════════════════════════════════════════
def run_stage(page, context, goal: str, stage: dict, memory: Memory, log: EventLogger) -> bool:
    stage_goal = stage.get("goal", goal)
    max_steps  = min(stage.get("max_steps", 20), MAX_STEPS)

    log.log("stage_start", goal=stage_goal)

    for step in range(max_steps):

        state = observe(page)

        # ── CRITIC (self-healing layer) ─────────────────────────────
        crit = critique(stage_goal, memory, state)
        log.log("critique", **crit)

        if crit.get("is_stuck"):
            q      = crit.get("suggested_query") or stage_goal
            action = {"action": "navigate", "value": SEARCH_BASE + q.replace(" ", "+")}
            result = execute(page, context, action)
            memory.record_action(action, result)
            continue

        # ── extract knowledge ────────────────────────────────────────
        process_page(state, memory, log)

        # ── stop condition ───────────────────────────────────────────
        if enough_evidence(memory):
            log.log("done", evidence=len(memory.evidence))
            return True

        # ── decide action ────────────────────────────────────────────
        raw         = decide_action(stage_goal, state, memory, step)
        action_type = raw.get("action", "scroll")

        if action_type == "search":
            q      = raw.get("value", stage_goal)
            action = {"action": "navigate", "value": SEARCH_BASE + q.replace(" ", "+")}
        elif action_type == "finish":
            return True
        else:
            action = raw

        result = execute(page, context, action)
        memory.record_action(action, result)

        if not result.get("ok"):
            log.log("action_failed", action=action_type)

        # Adaptive load wait (max 3 s)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

    return enough_evidence(memory)


# ════════════════════════════════════════════════════════════════════════
# BROWSER — always Playwright Chromium, never Arc
# ════════════════════════════════════════════════════════════════════════
def get_browser_and_page(p):
    browser = p.chromium.launch(headless=False)
    ctx     = browser.new_context()
    return browser, ctx, ctx.new_page(), "chromium"


# ════════════════════════════════════════════════════════════════════════
# ENTRY (called by orchestrator)
# ════════════════════════════════════════════════════════════════════════
def run_mission(plan: dict, root: Path | None = None):
    global OUT_DIR, LOG_DIR
    if root:
        rt_file = root / "configs" / "runtime.json"
        rt = json.loads(rt_file.read_text()) if rt_file.exists() else {}
        OUT_DIR = root / rt.get("outputs_dir", "outputs")
        LOG_DIR = root / rt.get("logs_dir", "logs")

    goal   = plan.get("mission_name", "research")
    log    = EventLogger(OUT_DIR)
    memory = Memory()

    # ── long-term memory: read phase ──────────────────────────────────────
    if should_read(goal):
        prior = read_relevant(goal)
        if prior:
            log.log("ltm_read", chars=len(prior))
            memory.inject_prior_context(prior)

    # ── skip browser entirely for non-web goals ───────────────────────────
    if not _needs_web(goal):
        log.log("no_web", goal=goal)
        result = call(
            f"Answer the following directly without browsing:\n{goal}",
            model=MODEL_HEAVY,
        )
        Path(OUT_DIR).mkdir(exist_ok=True)
        (OUT_DIR / "result.md").write_text(result)
        manage_memory(goal, memory, result)
        print(result)
        return result

    with sync_playwright() as p:
        browser, ctx, page, mode = get_browser_and_page(p)

        start_url = plan.get("start_url") or (SEARCH_BASE + goal.replace(" ", "+"))
        page.goto(start_url)

        for stage in plan.get("stages", []):
            if run_stage(page, ctx, goal, stage, memory, log):
                break

        clusters = cluster_claims(memory.evidence) if memory.evidence else []

        result = (
            call(f"Synthesize:\n{clusters}", model=MODEL_HEAVY)
            if memory.evidence
            else "# No evidence collected"
        )

        Path(OUT_DIR).mkdir(exist_ok=True)
        out = OUT_DIR / "result.md"
        out.write_text(result)

        # ── long-term memory: smart write phase ───────────────────────────
        manage_memory(goal, memory, result)
        log.log("ltm_manage_done", goal=goal)

        print(result)
        return result


def main():
    mission = json.loads(Path(sys.argv[1]).read_text())
    run_mission(mission)


if __name__ == "__main__":
    main()
