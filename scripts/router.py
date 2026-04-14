"""Routes a goal string to a mode + starting URL."""
from __future__ import annotations


def route_goal(goal: str) -> dict:
    g = (goal or "").lower().strip()
    if not g:
        return {"mode": "browse", "url": "https://www.bing.com", "answer": ""}

    if any(x in g for x in ["calendar", "gmail", "drive", "docs", "sheets",
                              "slides", "youtube", "prose", "notion"]):
        if "calendar" in g:
            return {"mode": "workflow", "url": "https://calendar.google.com/calendar/u/0/r", "answer": ""}
        if "youtube"  in g:
            return {"mode": "workflow", "url": "https://www.youtube.com", "answer": ""}
        if "docs"     in g:
            return {"mode": "workflow", "url": "https://docs.google.com/document", "answer": ""}
        if "drive"    in g:
            return {"mode": "workflow", "url": "https://drive.google.com", "answer": ""}
        if "prose"    in g:
            return {"mode": "workflow", "url": "https://prose.shreyashs.xyz", "answer": ""}
        return {"mode": "workflow", "url": "https://www.bing.com", "answer": ""}

    if any(x in g for x in ["who is", "what is", "latest", "news", "price",
                              "find", "search", "look up"]):
        return {"mode": "search", "url": "https://www.bing.com", "answer": ""}

    return {"mode": "browse", "url": "https://www.bing.com", "answer": ""}
