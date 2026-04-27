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
from scripts.observer import read as observe
from scripts.executor import execute
from playwright.sync_api import sync_playwright
from scripts.memory import Memory
from scripts.event_logger import EventLogger
from scripts.claim_extractor import extract_claims
from scripts.source_scoring import score_source, domain_of
from scripts.claim_cluster import cluster_claims

# ── runtime config ─────────────────────────────────────────────────────────
RT_FILE = ROOT / "configs" / "runtime.json"
RUNTIME = json.loads(RT_FILE.read_text()) if RT_FILE.exists() else {}

OUT_DIR = ROOT / RUNTIME.get("outputs_dir", "outputs")
LOG_DIR = ROOT / RUNTIME.get("logs_dir", "logs")
MAX_STEPS = RUNTIME.get("max_steps_per_stage", 25)

SEARCH_BASE = "https://www.bing.com/search?q="

HIGH_SCORE_THRESH = 4
STUCK_THRESHOLD = 3
EVIDENCE_GOAL_BASE = 6


# ── adaptive evidence goal ────────────────────────────────────────────────
def adaptive_evidence_goal(memory: Memory) -> int:
    base = EVIDENCE_GOAL_BASE
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

    prompt = f"""
You are a research agent.

GOAL: {goal}

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
def process_page(state, memory, log):
    text = state.get("visible_text", "")
    if len(text) < 200:
        return 0

    score = score_source(state["url"], state.get("title", ""), text)
    claims = extract_claims(state.get("title", ""), state["url"], text)

    if not claims:
        return 0

    memory.add_evidence({
        "url": state["url"],
        "title": state.get("title", ""),
        "score": score,
        "source_domain": domain_of(state["url"]),
        "claims": claims,
    })

    log.log("evidence", url=state["url"], score=score, claim_count=len(claims))
    return len(claims)


def enough_evidence(memory: Memory) -> bool:
    if len(memory.evidence) < 5:
        return False

    trusted = sum(1 for e in memory.evidence if e.get("score", 0) >= HIGH_SCORE_THRESH)
    domains = len(set(e.get("source_domain") for e in memory.evidence))
    return trusted >= 2 and domains >= 2


# ════════════════════════════════════════════════════════════════════════
# STAGE LOOP
# ════════════════════════════════════════════════════════════════════════
def run_stage(page, context, goal, stage, memory, log):
    stage_goal = stage.get("goal", goal)
    max_steps = min(stage.get("max_steps", 20), MAX_STEPS)

    log.log("stage_start", goal=stage_goal)

    for step in range(max_steps):

        state = observe(page)

        # ── CRITIC (self-healing layer) ─────────────────────────────
        crit = critique(stage_goal, memory, state)
        log.log("critique", **crit)

        if crit.get("is_stuck"):
            q = crit.get("suggested_query") or stage_goal
            action = {"action": "navigate", "value": SEARCH_BASE + q.replace(" ", "+")}
            result = execute(page, context, action)
            memory.record_action(action, result)
            continue

        # ── extract knowledge ────────────────────────────────────────
        process_page(state, memory, log)

        # ── stop condition ───────────────────────────────────────────
        if len(memory.evidence) >= adaptive_evidence_goal(memory) and enough_evidence(memory):
            log.log("done", evidence=len(memory.evidence))
            return True

        # ── decide action ────────────────────────────────────────────
        raw = decide_action(stage_goal, state, memory, step)
        action_type = raw.get("action", "scroll")

        if action_type == "search":
            q = raw.get("value", stage_goal)
            action = {"action": "navigate", "value": SEARCH_BASE + q.replace(" ", "+")}
        elif action_type == "finish":
            return True
        else:
            action = raw

        result = execute(page, context, action)
        memory.record_action(action, result)

        if not result.get("ok"):
            log.log("action_failed", action=action_type)

        time.sleep(0.5)

    return enough_evidence(memory)


# ════════════════════════════════════════════════════════════════════════
# BROWSER — always Playwright Chromium, never Arc
# ════════════════════════════════════════════════════════════════════════
def get_browser_and_page(p):
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    return browser, ctx, ctx.new_page(), "chromium"


# ════════════════════════════════════════════════════════════════════════
# ENTRY
# ════════════════════════════════════════════════════════════════════════
def main():
    mission = json.loads(Path(sys.argv[1]).read_text())

    log = EventLogger(OUT_DIR)
    memory = Memory()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx, page, mode = get_browser_and_page(p)

        page.goto(mission.get("start_url", "https://bing.com"))

        for stage in mission.get("stages", []):
            if run_stage(page, ctx, mission["mission_name"], stage, memory, log):
                break

        clusters = cluster_claims(memory.evidence) if memory.evidence else []

        if memory.evidence:
            result = call(
                f"Synthesize:\n{clusters}",
                model=MODEL_HEAVY
            )
        else:
            result = "# No evidence collected"

        Path(OUT_DIR).mkdir(exist_ok=True)
        out = OUT_DIR / "result.md"
        out.write_text(result)

        print(result)


if __name__ == "__main__":
    main()
