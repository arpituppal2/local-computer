"""Handles obvious actions like cookie banners before calling the LLM."""
from __future__ import annotations


def obvious_action(goal: str, state: dict) -> dict | None:
    for t in state.get("targets", []):
        text = (t.get("text", "") or "").lower()
        if text in ["accept all", "accept cookies", "i accept", "agree",
                    "allow all cookies", "ok"]:
            return {
                "action": "click",
                "target": {"target_id": t["target_id"], "text": t["text"]},
                "reason": "Accept cookies banner.",
                "source": "candidate_policy",
            }
    return None
