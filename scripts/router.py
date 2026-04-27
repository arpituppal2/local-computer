"""Goal routing with word-boundary matching, goal-encoded fallback (fixes #37-38)."""
from __future__ import annotations
import re

_APP_ROUTES = [
    (r'\bdocs?\b',      "https://docs.google.com"),
    (r'\bdrive\b',      "https://drive.google.com"),
    (r'\bcalendar\b',   "https://calendar.google.com"),
    (r'\byoutube\b',    "https://youtube.com"),
    (r'\bprose\b',      "prose"),
]

def route_goal(goal: str) -> dict:
    g = (goal or "").lower().strip()
    if not g:
        return {"mode": "browse", "url": "https://www.bing.com", "answer": ""}

    for pattern, dest in _APP_ROUTES:
        if re.search(pattern, g):
            return {"mode": "workflow", "url": dest, "answer": ""}

    # Goal-encoded Bing fallback for workflow mode
    encoded = goal.replace(" ", "+")
    return {"mode": "workflow", "url": f"https://www.bing.com/search?q={encoded}", "answer": ""}
