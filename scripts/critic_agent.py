"""Critic agent — uses MODEL_ACTOR by default, escalates to MODEL_PLANNER only when stuck (fixes #39-40)."""
from __future__ import annotations
import logging
from scripts.ollama_client import call_json, MODEL_ACTOR, MODEL_PLANNER

def critique(step_history: list, current_url: str, evidence_count: int, model=None) -> dict:
    if model is None:
        recent_urls = [s.get("url", "") for s in step_history[-5:]]
        is_cycling = len(set(recent_urls)) <= 2 and len(recent_urls) >= 4
        model = MODEL_PLANNER if is_cycling else MODEL_ACTOR

    prompt = (
        f"Evaluate agent progress. Current URL: {current_url}. "
        f"Evidence collected: {evidence_count}. "
        f"Last {min(5,len(step_history))} actions: {step_history[-5:]}.

"
        f"Return JSON: {{\"is_stuck\": bool, \"fix_strategy\": \"search_refine|navigate_reset|continue|replan\", \"reason\": \"...\"}}"
    )
    result = call_json(model, prompt)
    if not result:
        return {"is_stuck": False, "fix_strategy": "continue", "reason": "no critique"}
    return result
