#!/usr/bin/env python3
"""
scripts/navigation_agent.py
Primary Perplexity-style research loop.
Usage: python scripts/navigation_agent.py auto_mission.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

# ── repo root on sys.path ─────────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ollama_client import (
    MODEL_ACTOR,
    MODEL_ANALYST,
    MODEL_HEAVY,
    MODEL_PLANNER,
    call,
    call_json,
)
from scripts.observer      import read as observe
from scripts.executor      import execute
from scripts.agent_memory  import Memory
from scripts.event_logger  import EventLogger
from scripts.claim_extractor  import extract_claims
from scripts.source_scoring   import score_source, domain_of
from scripts.claim_cluster    import cluster_claims

# ── constants ─────────────────────────────────────────────────────────────────────────────────
RT_FILE   = ROOT / "configs" / "runtime.json"
RUNTIME   = json.loads(RT_FILE.read_text()) if RT_FILE.exists() else {}
OUT_DIR   = ROOT / RUNTIME.get("outputs_dir", "outputs")
LOG_DIR   = ROOT / RUNTIME.get("logs_dir",    "logs")
ARC_PORT  = RUNTIME.get("arc_debug_port", 9222)
MAX_STEPS = RUNTIME.get("max_steps_per_stage", 25)

EVIDENCE_GOAL     = 6      # min evidence items before considering "enough"
HIGH_SCORE_THRESH = 4      # source score >= this counts as trusted
STUCK_THRESHOLD   = 3      # same state N times -> force a new search
SEARCH_BASE       = "https://www.bing.com/search?q="


# ═══════════════════════════════════════════════════════════════════════════════
# ACTION POLICY  (qwen3:4b — fast)
# ═══════════════════════════════════════════════════════════════════════════════
_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning":   {"type": "string"},
        "action":      {"type": "string",
                        "enum": ["navigate","click","fill","press","scroll",
                                  "go_back","search","finish"]},
        "target_id":   {"type": ["integer", "null"]},
        "value":       {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["reasoning", "action", "explanation"],
}

def decide_action(goal: str, state: dict, memory: Memory, step: int) -> dict:
    targets_brief = [
        f"[{t['target_id']}] {t['kind']} \"{t['text'][:80]}\"" + (f" -> {t['href'][:80]}" if t.get("href") else "")
        for t in (state.get("candidate_targets") or [])[:30]
    ]
    recent_actions = [
        f"{r['action'].get('action','?')} -> {'OK' if r['result'].get('ok') else 'FAIL: ' + r['result'].get('error','')}"
        for r in list(memory.recent_actions)[-8:]
    ]
    recent_failures = [
        f"{r['action'].get('action','?')} target={r['action'].get('target',{}).get('target_id')} \"{r['action'].get('target',{}).get('text','')[:60]}\""
        for r in list(memory.recent_failures)[-4:]
    ]
    evidence_summary = f"{len(memory.evidence)} items collected so far."

    prompt = f"""You are a Perplexity-style research agent browsing the web to fulfil this goal:

GOAL: {goal}

CURRENT STATE (step {step}):
  URL: {state.get('url','?')}
  Title: {state.get('title','?')}
  Visible text (first 1800 chars):
{(state.get('visible_text') or '')[:1800]}

INTERACTIVE TARGETS (id, kind, text, href):
{chr(10).join(targets_brief) or 'none'}

RECENT ACTIONS:
{chr(10).join(recent_actions) or 'none'}
RECENT FAILURES (avoid repeating):
{chr(10).join(recent_failures) or 'none'}

EVIDENCE STATUS: {evidence_summary}

RULES:
- If you have enough evidence (>={EVIDENCE_GOAL} items) AND can answer the goal -> use "finish".
- Prefer clicking search-result links or article links over re-searching.
- Use "search" to start a new Bing query (set value = query string).
- Use "navigate" to go directly to a URL (set value = full URL).
- Use "fill" + "press" to type into search boxes.
- Use "scroll" to see more content (value = pixel amount as string, e.g. "900").
- Use "go_back" if a page was useless.
- NEVER repeat an action that already failed.
- Output ONLY a JSON object with: reasoning, action, target_id (int or null), value, explanation.
"""
    return call_json(prompt, model=MODEL_ACTOR) or {}


# ═══════════════════════════════════════════════════════════════════════════════
# SYNTHESIS  (deepseek-r1:14b — thorough)
# ═══════════════════════════════════════════════════════════════════════════════
def synthesize(goal: str, clusters: list[dict], evidence: list[dict], log: EventLogger) -> str:
    log.log("synthesis_start", model=MODEL_HEAVY, evidence_items=len(evidence),
            cluster_count=len(clusters))

    lines = []
    for i, c in enumerate(clusters[:20], 1):
        domains = ", ".join(c.get("domains", [])[:4])
        lines.append(f"{i}. [{c['source_count']} sources | {domains}] {c['representative_claim']}")
    digest = "\n".join(lines) or "(no evidence collected)"

    contradictions = [
        c for c in clusters
        if c.get("source_count", 1) > 1 and _has_contradiction(c)
    ]
    contra_block = ""
    if contradictions:
        contra_lines = [f"- {cx['representative_claim']}" for cx in contradictions[:5]]
        contra_block = "\n\n**⚠ Conflicting signals detected:**\n" + "\n".join(contra_lines)

    prompt = f"""You are a research analyst. Synthesize the following evidence to fully answer the user's research goal.

GOAL: {goal}

EVIDENCE DIGEST (clustered, ranked by source breadth):
{digest}
{contra_block}

OUTPUT FORMAT (strict Markdown):
# [Concise answer title]

## Summary
[2-4 sentence executive summary]

## Key Findings
[Bullet points from the evidence, each citing domain(s) where seen]

## Contradictions / Caveats
[If any; otherwise omit section]

## Sources
[List unique domains encountered]

Be factual, concise, and cite domains inline like (reuters.com).
"""
    return call(prompt, model=MODEL_HEAVY, timeout=180)


def _has_contradiction(cluster: dict) -> bool:
    texts = [item.get("claim","").lower() for item in cluster.get("items", [])]
    pos = sum(1 for t in texts if any(w in t for w in ("increased","rose","gained","up","higher","approved")))
    neg = sum(1 for t in texts if any(w in t for w in ("decreased","fell","lost","down","lower","rejected")))
    return pos > 0 and neg > 0


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH QUERY REFINEMENT  (qwen3:14b)
# ═══════════════════════════════════════════════════════════════════════════════
def refine_query(goal: str, stage_goal: str, memory: Memory, failed_query: str) -> str:
    visited = list(memory.visited_urls.keys())[:12]
    prompt = f"""A browser research agent is trying to answer this goal:
GOAL: {goal}
Current stage: {stage_goal}
Previous query that didn't yield enough results: "{failed_query}"
Already visited URLs: {visited}
Suggest a single, improved Bing search query (plain string, no quotes needed).
Output ONLY the query string."""
    q = call(prompt, model=MODEL_PLANNER).strip().strip('"\'')
    return q or goal


# ═══════════════════════════════════════════════════════════════════════════════
# CLAIM PIPELINE  — observe -> extract -> score -> accumulate
# ═══════════════════════════════════════════════════════════════════════════════
def process_page(state: dict, memory: Memory, log: EventLogger) -> int:
    url   = state.get("url", "")
    title = state.get("title", "")
    text  = state.get("visible_text", "")
    if not text.strip() or len(text) < 200:
        return 0

    score  = score_source(url, title, text)
    claims = extract_claims(title, url, text)
    if not claims:
        return 0

    domain = domain_of(url)
    item   = {"url": url, "title": title, "score": score,
               "source_domain": domain, "claims": claims}
    memory.add_evidence(item)

    log.log("evidence", url=url, domain=domain, source_score=score,
            claim_count=len(claims),
            preview=claims[0].get("claim","")[:120] if claims else "")
    return len(claims)


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-CONFIDENCE CHECK
# ═══════════════════════════════════════════════════════════════════════════════
def enough_evidence(memory: Memory) -> bool:
    if len(memory.evidence) < EVIDENCE_GOAL:
        return False
    trusted = sum(1 for e in memory.evidence if e.get("score", 0) >= HIGH_SCORE_THRESH)
    unique_domains = len({e.get("source_domain","") for e in memory.evidence if e.get("source_domain")})
    return trusted >= 2 and unique_domains >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════
def run_stage(page, context, goal: str, stage: dict, memory: Memory, log: EventLogger) -> bool:
    stage_name = stage.get("name", "main")
    stage_goal = stage.get("goal", goal)
    max_steps  = min(int(stage.get("max_steps", 20)), MAX_STEPS)
    last_query = stage_goal

    log.log("stage_start", stage=stage_name, goal=stage_goal, max_steps=max_steps)

    for step in range(1, max_steps + 1):
        # ── observe ────────────────────────────────────────────────────────────────────
        state = observe(page)
        log.log("observe", step=step, stage=stage_name,
                url=state.get("url",""), title=state.get("title",""),
                targets=len(state.get("candidate_targets",[])))

        # ── stuck detection ───────────────────────────────────────────────────────────────────
        sig = memory.record_state(state)
        if memory.stuck(state, STUCK_THRESHOLD):
            log.log("stuck_detected", step=step, url=state.get("url",""), sig=sig)
            last_query = refine_query(goal, stage_goal, memory, last_query)
            log.log("new_query", query=last_query)
            result = execute(page, context, {"action": "navigate",
                                              "value": SEARCH_BASE + last_query.replace(" ", "+")})
            memory.record_action({"action": "navigate", "value": last_query}, result)
            continue

        # ── extract claims from this page ─────────────────────────────────────────────────
        n_claims = process_page(state, memory, log)
        if n_claims:
            log.log("claims_extracted", step=step, count=n_claims,
                    url=state.get("url",""))

        # ── re-cluster every 3 evidence items ───────────────────────────────────────────────
        if memory.evidence and len(memory.evidence) % 3 == 0:
            clusters = cluster_claims(memory.evidence)
            log.log("clusters", step=step, cluster_count=len(clusters),
                    clusters=[{
                        "representative_claim": c["representative_claim"][:120],
                        "source_count": c["source_count"],
                        "domains": c.get("domains", [])
                    } for c in clusters[:10]])

        # ── check if done ───────────────────────────────────────────────────────────────────
        if enough_evidence(memory):
            log.log("evidence_sufficient", step=step,
                    evidence_count=len(memory.evidence),
                    unique_domains=len({e.get("source_domain","") for e in memory.evidence}))
            return True

        # ── decide next action ──────────────────────────────────────────────────────────────────
        raw = decide_action(stage_goal, state, memory, step)
        action_type = (raw.get("action") or "scroll").lower()
        value       = str(raw.get("value") or "")
        target_id   = raw.get("target_id")
        explanation = raw.get("explanation") or ""

        log.log("decision", step=step, action=action_type, value=value[:120],
                target_id=target_id, explanation=explanation[:200],
                reasoning=(raw.get("reasoning") or "")[:300])

        # ── translate policy output to executor format ───────────────────────────────────────
        if action_type == "search":
            q = value or last_query
            last_query = q
            action = {"action": "navigate",
                      "value": SEARCH_BASE + q.replace(" ", "+")}
        elif action_type == "finish":
            return True
        elif action_type == "click":
            action = {"action": "click",
                      "target": {"target_id": target_id, "text": value}}
        elif action_type == "fill":
            action = {"action": "fill",
                      "target": {"target_id": target_id}, "value": value}
        elif action_type == "press":
            action = {"action": "press", "value": value or "Enter"}
        elif action_type == "scroll":
            action = {"action": "scroll", "value": value or "900"}
        elif action_type == "go_back":
            action = {"action": "go_back"}
        elif action_type == "navigate":
            action = {"action": "navigate", "value": value}
        else:
            action = {"action": "scroll", "value": "900"}

        result = execute(page, context, action)
        memory.record_action(action, result)

        if result.get("finish"):
            return True
        if not result.get("ok"):
            log.log("action_failed", step=step, action=action_type,
                    error=result.get("error","unknown"))

        time.sleep(0.6)

    log.log("stage_exhausted", stage=stage_name, steps_used=max_steps)
    return enough_evidence(memory)


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER SESSION  (Arc CDP first, headless Chromium fallback)
# ═══════════════════════════════════════════════════════════════════════════════
def get_browser_and_page(p):
    try:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{ARC_PORT}")
        ctx     = browser.contexts[0] if browser.contexts else browser.new_context()
        page    = ctx.new_page()
        return browser, ctx, page, "arc"
    except Exception:
        browser = p.chromium.launch(headless=False)
        ctx     = browser.new_context(viewport={"width": 1280, "height": 900})
        page    = ctx.new_page()
        return browser, ctx, page, "chromium"


# ═══════════════════════════════════════════════════════════════════════════════
# SAVE ARTIFACT
# ═══════════════════════════════════════════════════════════════════════════════
def save_artifact(mission_name: str, content: str, log: EventLogger) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", mission_name)[:48]
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"{safe_name}_{ts}.md"
    path.write_text(content, encoding="utf-8")
    log.log("artifact_saved", path=str(path), size_chars=len(content))
    print(f"\n{'='*60}\nArtifact saved -> {path}\n{'='*60}\n", flush=True)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: navigation_agent.py <mission.json>", file=sys.stderr)
        raise SystemExit(1)

    mission_path = Path(sys.argv[1])
    if not mission_path.is_absolute():
        mission_path = ROOT / mission_path
    mission = json.loads(mission_path.read_text())

    mission_name = mission.get("mission_name", "research")
    start_url    = mission.get("start_url", "https://www.bing.com")
    stages       = mission.get("stages") or [{"name": "main",
                                               "goal": mission_name,
                                               "max_steps": 20}]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log    = EventLogger(OUT_DIR)
    memory = Memory()

    log.log("mission_start", mission_name=mission_name, start_url=start_url,
            stage_count=len(stages))

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx, page, mode = get_browser_and_page(p)
        log.log("browser_ready", mode=mode)

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=14000)
        except Exception as e:
            log.log("nav_error", url=start_url, error=str(e))

        goal = stages[0].get("goal", mission_name)
        for stage in stages:
            done = run_stage(page, ctx, goal, stage, memory, log)
            if done:
                break

        clusters = cluster_claims(memory.evidence) if memory.evidence else []
        if clusters:
            log.log("clusters", cluster_count=len(clusters), final=True,
                    clusters=[{
                        "representative_claim": c["representative_claim"][:120],
                        "source_count": c["source_count"],
                        "domains": c.get("domains", [])
                    } for c in clusters[:15]])

        contradictions = [c for c in clusters if _has_contradiction(c)]
        if contradictions:
            log.log("contradictions", count=len(contradictions),
                    items=[{"claim": c["representative_claim"][:120],
                            "domains": c.get("domains",[])} for c in contradictions[:6]])

        if memory.evidence:
            markdown = synthesize(goal, clusters, memory.evidence, log)
        else:
            markdown = f"# {mission_name}\n\nNo evidence collected. Try a broader goal or check Ollama connectivity."
            log.log("warning", msg="no evidence collected")

        save_artifact(mission_name, markdown, log)

        log.log("mission_complete", evidence_count=len(memory.evidence),
                cluster_count=len(clusters), contradiction_count=len(contradictions))

        print(markdown[:2000], flush=True)

        try:
            if mode == "chromium":
                browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
