"""Rewrites search queries for corroboration passes."""
from __future__ import annotations


def rewrite_query(goal: str, state: dict, memory) -> str:
    goal_l  = (goal or "").lower()
    title   = (state.get("title")        or "").strip()
    visible = (state.get("visible_text") or "").strip()

    if any(x in goal_l for x in ["cross reference", "corroborate", "verify"]):
        if title:
            return f"{title} Reuters AP BBC"
        if visible:
            seed = " ".join(visible.split()[:12])
            return f"{seed} Reuters AP BBC"

    if any(x in goal_l for x in ["news", "headline", "article"]):
        return goal.strip()

    return goal.strip()
