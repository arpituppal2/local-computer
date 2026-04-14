"""Picks which subagent mode to use based on goal + current URL."""
from __future__ import annotations


def pick_subagent(goal: str, state: dict, history: list[dict]) -> str:
    g   = (goal or "").lower()
    url = (state.get("url") or "").lower()

    if any(x in g for x in ["calendar", "docs", "drive", "gmail", "youtube",
                              "prose", "notion", "sheet", "slides"]):
        return "workflow"
    if any(x in url for x in ["calendar.google.com", "docs.google.com",
                               "drive.google.com", "mail.google.com", "youtube.com"]):
        return "workflow"
    if any(x in g for x in ["search", "look up", "find", "latest", "news",
                              "price", "who is", "what is"]):
        return "search"
    if any(x in url for x in ["bing.com", "google.com/search", "duckduckgo.com"]):
        return "search"
    return "browse"
