"""Search-page action helpers — merged redundant functions, domain-based result filtering (fixes #44-45)."""
from __future__ import annotations
import logging

def _url(state: dict) -> str:
    return (state.get("url") or "").lower().rstrip("/")

def is_search_page(state: dict) -> bool:
    url = _url(state)
    return (
        "bing.com/search" in url
        or "google.com/search" in url
        or "google.com/?q=" in url
        or url == "https://www.google.com"
    )

def _targets(state: dict) -> list[dict]:
    return state.get("targets") or state.get("candidate_targets") or []

def _search_action(goal: str, state: dict, timeout: int = 8000) -> dict | None:
    url = _url(state)
    targets = _targets(state)
    boxes = [t for t in targets if "input:search" in str(t.get("kind", "")).lower()]
    if not boxes:
        boxes = [t for t in targets
                 if "input" in str(t.get("kind", "")).lower()
                 and any(k in str(t.get("text", "")).lower() for k in ["search", "search the web"])]
    if not boxes:
        return None
    box = boxes[0]
    return {
        "action": "batch",
        "reason": f"Fill search box and press Enter.",
        "source": "search_controller",
        "actions": [
            {"action": "type", "text": goal},
            {"action": "press", "value": "Return"},
        ],
    }

def bing_home_query_action(goal: str, state: dict) -> dict | None:
    url = _url(state)
    if url != "https://www.bing.com/":
        return None
    return _search_action(goal, state)

def first_query_action(goal: str, state: dict) -> dict | None:
    if not is_search_page(state):
        return None
    return _search_action(goal, state)

def follow_result_action(state: dict, skip_domains: list[str] | None = None) -> dict | None:
    skip_domains = skip_domains or ["bing.com", "microsoft.com"]
    targets = _targets(state)
    for t in targets:
        href = t.get("href") or ""
        if any(d in href for d in skip_domains):
            continue
        return {
            "action": "batch",
            "reason": "Follow first search result.",
            "source": "search_controller",
            "actions": [
                {"action": "click", "target": t},
            ],
        }
    return None
