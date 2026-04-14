"""Search-page specific action helpers (Bing / Google)."""
from __future__ import annotations


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


def bing_home_query_action(goal: str, state: dict) -> dict | None:
    url = _url(state)
    if url != "https://www.bing.com/":
        return None
    targets = _targets(state)
    boxes   = [t for t in targets if "input:search" in str(t.get("kind", "")).lower()]
    if not boxes:
        boxes = [
            t for t in targets
            if "input" in str(t.get("kind", "")).lower()
            and any(k in str(t.get("text", "")).lower() for k in ["search", "search the web"])
        ]
    if not boxes:
        return None
    box = boxes[0]
    return {
        "action": "batch",
        "reason": "Bing homepage: fill search box and press Enter.",
        "source": "bing_home",
        "actions": [
            {"action": "type", "target_id": box.get("target_id"), "value": goal},
            {"action": "press", "value": "Enter"},
        ],
    }


def first_query_action(goal: str, state: dict) -> dict | None:
    if not is_search_page(state):
        return None
    targets = _targets(state)
    boxes   = [t for t in targets if "input:search" in str(t.get("kind", "")).lower()]
    if not boxes:
        boxes = [
            t for t in targets
            if "input" in str(t.get("kind", "")).lower()
            and any(k in str(t.get("text", "")).lower() for k in ["search", "query", "ask"])
        ]
    if not boxes:
        return None
    box = boxes[0]
    return {
        "action": "batch",
        "reason": "Search page: fill query and submit.",
        "source": "search_box",
        "actions": [
            {"action": "type", "target_id": box.get("target_id"), "value": goal},
            {"action": "press", "value": "Enter"},
        ],
    }


def follow_result_action(state: dict) -> dict | None:
    if not is_search_page(state):
        return None
    targets = _targets(state)
    for t in targets[:40]:
        txt = str(t.get("text", "")).strip().lower()
        if len(txt) > 18 and all(
            bad not in txt for bad in ["bing", "search", "microsoft", "feedback"]
        ):
            return {
                "action": "click",
                "reason": "Open first plausible search result.",
                "source": "search_result",
                "target_id": t.get("target_id"),
            }
    return None
