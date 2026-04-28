#!/usr/bin/env python3
"""
scripts/navigation_agent.py
Primary research loop.

Search strategy (fast-path first):
  1. DuckDuckGo JSON API  — no browser, instant (~200 ms)
  2. If a result URL is worth visiting, open it in Playwright for full text
  3. Fall back to Bing browser search only if the API returns nothing
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.critic_agent       import critique
from scripts.search_api         import search as api_search, search_to_text
from scripts.ollama_client      import (
    MODEL_ACTOR, MODEL_HEAVY, MODEL_PLANNER,
    call, call_json,
)
from scripts.observer           import observe
from scripts.executor           import execute
from playwright.sync_api        import sync_playwright
from scripts.memory             import Memory
from scripts.event_logger       import EventLogger
from scripts.claim_extractor    import extract_claims
from scripts.source_scoring     import score_source, domain_of
from scripts.claim_cluster      import cluster_claims
from scripts.long_term_memory   import should_read, read_relevant, manage_memory

RT_FILE  = ROOT / "configs" / "runtime.json"
RUNTIME  = json.loads(RT_FILE.read_text()) if RT_FILE.exists() else {}

OUT_DIR  = ROOT / RUNTIME.get("outputs_dir", "outputs")
LOG_DIR  = ROOT / RUNTIME.get("logs_dir", "logs")
MAX_STEPS = RUNTIME.get("max_steps_per_stage", 25)

SEARCH_BASE      = "https://www.bing.com/search?q="
HIGH_SCORE_THRESH = 4
STUCK_THRESHOLD   = 3
EVIDENCE_GOAL_BASE = 6

_LOCAL_KEYWORDS = (
    "test", "hello", "ping", "echo", "debug", "check",
    "calculate", "compute", "convert", "summarize this",
    "write a", "generate a", "create a", "list the",
    "what is 2", "what is 1",
)


def _needs_web(goal: str) -> bool:
    g = goal.lower().strip()
    if any(k in g for k in _LOCAL_KEYWORDS):
        return False
    verdict = call_json(
        f"Does answering this goal require browsing the web or searching for "
        f"current information?\nGoal: {goal}\n"
        f"Reply with JSON: {{\"needs_web\": true}} or {{\"needs_web\": false}}",
        model=MODEL_PLANNER,
    )
    return bool((verdict or {}).get("needs_web", True))


def adaptive_evidence_goal(memory: Memory) -> int:
    base  = EVIDENCE_GOAL_BASE
    bonus = len(set(e.get("source_domain", "") for e in memory.evidence))
    return base + max(0, 3 - bonus)


# ════════════════════════════════════════════════════════════════════════
# FAST-PATH: API search → harvest claims without opening a browser page
# ════════════════════════════════════════════════════════════════════════

def _harvest_from_api(query: str, memory: Memory, log: EventLogger) -> int:
    """
    Run a DuckDuckGo API search and add snippet-level evidence to memory.
    Returns the number of new claims added.
    """
    hits = api_search(query, max_results=10)
    new_claims = 0
    for hit in hits:
        url = hit["url"]
        if any(e.get("url") == url for e in memory.evidence):
            continue
        snippet = hit["snippet"]
        if len(snippet) < 40:
            continue
        claims = extract_claims(hit["title"], url, snippet)
        if not claims:
            claims = [snippet[:300]]
        score = score_source(url, hit["title"], snippet)
        memory.add_evidence({
            "url":           url,
            "title":         hit["title"],
            "score":         score,
            "source_domain": domain_of(url),
            "claims":        claims,
        })
        new_claims += len(claims)
        log.log("api_evidence", url=url, score=score, claims=len(claims))
    return new_claims


# ════════════════════════════════════════════════════════════════════════
# ACTION DECISION MODEL  (browser fallback path)
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
# PAGE PROCESSING  (for browser-visited URLs)
# ════════════════════════════════════════════════════════════════════════

def _already_visited(url: str, memory: Memory) -> bool:
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
    goal = adaptive_evidence_goal(memory)
    if len(memory.evidence) < goal:
        return False
    trusted = sum(1 for e in memory.evidence if e.get("score", 0) >= HIGH_SCORE_THRESH)
    domains  = len(set(e.get("source_domain") for e in memory.evidence))
    return trusted >= 2 and domains >= 2


# ════════════════════════════════════════════════════════════════════════
# STAGE LOOP  — API-first, browser as fallback
# ════════════════════════════════════════════════════════════════════════

def run_stage(
    page, context, goal: str, stage: dict, memory: Memory, log: EventLogger
) -> bool:
    stage_goal = stage.get("goal", goal)
    max_steps  = min(stage.get("max_steps", 20), MAX_STEPS)

    log.log("stage_start", goal=stage_goal)

    # ── FAST PATH: try the API first ─────────────────────────────────────
    api_claims = _harvest_from_api(stage_goal, memory, log)
    log.log("api_search_done", new_claims=api_claims, query=stage_goal)

    if enough_evidence(memory):
        log.log("done_via_api", evidence=len(memory.evidence))
        return True

    # ── BROWSER FALLBACK: only if API didn't give enough ─────────────────
    # Lock the page so the user can't interfere during automation
    try:
        page.evaluate("""
            () => {
                document.__agentLock = true;
                document.addEventListener('click',  e => { if (document.__agentLock) e.stopImmediatePropagation(); }, true);
                document.addEventListener('keydown', e => { if (document.__agentLock) e.stopImmediatePropagation(); }, true);
            }
        """)
    except Exception:
        pass

    for step in range(max_steps):
        state = observe(page)

        # CRITIC
        crit = critique(stage_goal, memory, state)
        log.log("critique", **crit)

        if crit.get("is_stuck"):
            q = crit.get("suggested_query") or stage_goal
            # Try API first when stuck, before opening a new browser page
            new_claims = _harvest_from_api(q, memory, log)
            if new_claims > 0 and enough_evidence(memory):
                return True
            action = {"action": "navigate", "value": SEARCH_BASE + q.replace(" ", "+")}
            result = execute(page, context, action)
            memory.record_action(action, result)
            continue

        process_page(state, memory, log)

        if enough_evidence(memory):
            log.log("done", evidence=len(memory.evidence))
            return True

        raw         = decide_action(stage_goal, state, memory, step)
        action_type = raw.get("action", "scroll")

        if action_type == "search":
            q = raw.get("value", stage_goal)
            # Use API first, only fall back to browser Bing if needed
            new_claims = _harvest_from_api(q, memory, log)
            if new_claims > 0:
                action = {"action": "noop"}  # API handled it
                result = {"ok": True}
            else:
                action = {"action": "navigate", "value": SEARCH_BASE + q.replace(" ", "+")}
                result = execute(page, context, action)
            memory.record_action(action, result)
        elif action_type == "finish":
            return True
        else:
            action = raw
            result = execute(page, context, action)
            memory.record_action(action, result)
            if not result.get("ok"):
                log.log("action_failed", action=action_type)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

    return enough_evidence(memory)


# ════════════════════════════════════════════════════════════════════════
# BROWSER setup  — locked viewport, no user interaction during runs
# ════════════════════════════════════════════════════════════════════════

def get_browser_and_page(p):
    browser = p.chromium.launch(
        headless=False,
        args=[
            "--disable-extensions",
            "--disable-plugins",
        ],
    )
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        # Overlay a banner so user sees the agent is in control
        extra_http_headers={"X-Agent-Running": "1"},
    )
    page = ctx.new_page()

    # Inject a visual banner and input-lock overlay on every page load
    ctx.add_init_script("""
        (() => {
            const banner = document.createElement('div');
            banner.id = 'agent-banner';
            banner.style.cssText = [
                'position:fixed', 'top:0', 'left:0', 'width:100%', 'z-index:2147483647',
                'background:#1a1a2e', 'color:#e2e8f0', 'font:600 13px/36px system-ui',
                'text-align:center', 'letter-spacing:.04em', 'pointer-events:none',
                'user-select:none', 'padding:0 12px',
            ].join(';');
            banner.textContent = '\u26a1 Agent running — hands off the keyboard 🤤';
            const inject = () => {
                if (!document.getElementById('agent-banner')) {
                    document.body?.prepend(banner.cloneNode(true));
                }
            };
            document.addEventListener('DOMContentLoaded', inject);
            setTimeout(inject, 300);

            // Block user mouse/keyboard during agent run
            ['click','mousedown','keydown','keypress','keyup'].forEach(evt => {
                document.addEventListener(evt, e => {
                    if (document.__agentLock) {
                        e.stopImmediatePropagation();
                        e.preventDefault();
                    }
                }, true);
            });
            document.__agentLock = true;
        })();
    """)

    return browser, ctx, page, "chromium"


# ════════════════════════════════════════════════════════════════════════
# ENTRY  (called by orchestrator)
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

    if should_read(goal):
        prior = read_relevant(goal)
        if prior:
            log.log("ltm_read", chars=len(prior))
            memory.inject_prior_context(prior)

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

    # ── API-only fast path: if we can satisfy the goal without a browser ──
    fast_memory = Memory()
    if memory.prior_context:
        fast_memory.inject_prior_context(memory.prior_context)

    fast_claims = _harvest_from_api(goal, fast_memory, EventLogger(OUT_DIR))
    if enough_evidence(fast_memory):
        log.log("api_only_sufficient", claims=fast_claims)
        # Merge fast evidence into main memory
        for e in fast_memory.evidence:
            memory.add_evidence(e)

        clusters = cluster_claims(memory.evidence)
        result = call(f"Synthesize:\n{clusters}", model=MODEL_HEAVY)
        Path(OUT_DIR).mkdir(exist_ok=True)
        (OUT_DIR / "result.md").write_text(result)
        manage_memory(goal, memory, result)
        print(result)
        return result

    # ── merge what we got from API, then open browser for the rest ────────
    for e in fast_memory.evidence:
        memory.add_evidence(e)

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
        (OUT_DIR / "result.md").write_text(result)
        manage_memory(goal, memory, result)
        log.log("ltm_manage_done", goal=goal)
        print(result)
        return result


def main():
    mission = json.loads(Path(sys.argv[1]).read_text())
    run_mission(mission)


if __name__ == "__main__":
    main()
