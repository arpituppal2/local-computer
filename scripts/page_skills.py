"""Detects page type and provides contextual hints to the agent."""
from __future__ import annotations


def detect_page_type(state: dict) -> str:
    url     = (state.get("url")          or "").lower()
    title   = (state.get("title")        or "").lower()
    text    = (state.get("visible_text") or "").lower()
    targets = state.get("candidate_targets") or []

    if "bing.com/news" in url or ("bing.com" in url and "news" in title):
        return "news_results"
    if "bing.com/search" in url or ("bing.com" in url and len(targets) >= 5):
        return "search_results"

    article_markers = ["updated", "published", "author", "share", "comments", "newsletter"]
    if len(text) > 1200 and sum(1 for m in article_markers if m in text) >= 2:
        return "article"
    if any(x in text for x in ["ingredients", "directions", "prep time", "cook time", "servings"]):
        return "recipe"
    if any(x in text for x in ["docs", "api", "reference", "installation", "usage"]) \
            and len(targets) >= 5:
        return "docs"
    return "generic"


def skill_hints(page_type: str, goal: str) -> list[str]:
    hints = {
        "news_results":   [
            "Prefer opening top distinct headlines from different publishers.",
            "Avoid sign-in, opinion hubs, video pages, and category pages.",
        ],
        "search_results": [
            "Prefer the most goal-relevant organic result.",
            "Avoid ads, account links, image/video/search vertical links.",
        ],
        "article":        [
            "Extract page text now; do not keep navigating unless verification is needed.",
            "Capture source title and URL for evidence.",
        ],
        "recipe":         [
            "Prefer extracting ingredients, dietary markers, and cooking details.",
        ],
        "docs":           [
            "Prefer exact section links and API reference pages over marketing pages.",
        ],
        "generic":        [
            "Prefer visible relevant targets over manual URL guessing.",
        ],
    }
    return hints.get(page_type, hints["generic"])
