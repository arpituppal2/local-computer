"""Critic agent — uses MODEL_ACTOR by default, escalates to MODEL_PLANNER only when stuck."""
from __future__ import annotations
import logging
from scripts.ollama_client import call_json, MODEL_ACTOR, MODEL_PLANNER


def critique(goal: str, memory, state: dict, model: str | None = None) -> dict:
    """
    Evaluate whether the agent is stuck and suggest a recovery strategy.

    Parameters match the call-site in navigation_agent.py:
        critique(stage_goal, memory, state)

    Returns dict with keys:
        is_stuck        bool
        fix_strategy    "search_refine" | "navigate_reset" | "continue" | "replan"
        suggested_query str | None
        reason          str
    """
    recent_actions = list(memory.recent_actions)[-8:]
    recent_failures = list(memory.recent_failures)[-4:]
    evidence_count = len(memory.evidence)
    current_url = state.get("url", "")

    # Auto-escalate to planner when URL cycling is detected
    if model is None:
        recent_urls = [r["action"].get("value", "") for r in recent_actions
                       if r["action"].get("action") == "navigate"]
        is_cycling = len(set(recent_urls)) <= 2 and len(recent_urls) >= 4
        model = MODEL_PLANNER if is_cycling else MODEL_ACTOR

    action_summary = [
        f"{r['action'].get('action')} -> ok={r['result'].get('ok')}"
        for r in recent_actions
    ]
    failure_summary = [
        f"{r['action'].get('action')} {r['action'].get('value', '')[:60]}"
        for r in recent_failures
    ]

    prompt = (
        f"Evaluate agent progress.\n"
        f"Goal: {goal}\n"
        f"Current URL: {current_url}\n"
        f"Evidence collected: {evidence_count}\n"
        f"Recent actions: {action_summary}\n"
        f"Recent failures: {failure_summary}\n\n"
        f"Return JSON: {{\"is_stuck\": bool, "
        f"\"fix_strategy\": \"search_refine|navigate_reset|continue|replan\", "
        f"\"suggested_query\": \"...\"|null, "
        f"\"reason\": \"...\"}}"
    )

    result = call_json(prompt, model=model)
    if not result:
        return {"is_stuck": False, "fix_strategy": "continue",
                "suggested_query": None, "reason": "no critique"}
    return result
