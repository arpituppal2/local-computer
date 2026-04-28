"""
scripts/search_api.py
Lightweight web search using the DuckDuckGo Instant Answer JSON API.
No API key required. Returns a list of {title, url, snippet} dicts.
Falls back to Bing browser search if the API returns nothing useful.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

_DDG_URL  = "https://api.duckduckgo.com/"
_BING_BASE = "https://www.bing.com/search?q="
_TIMEOUT  = 8.0


def search(query: str, max_results: int = 8) -> list[dict[str, str]]:
    """
    Return up to `max_results` web results for `query`.
    Uses DuckDuckGo's no-JS JSON API — instant, no browser needed.
    Falls back gracefully to an empty list if the request fails.
    """
    try:
        params = {
            "q":       query,
            "format":  "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        r = httpx.get(_DDG_URL, params=params, timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logging.warning(f"[search_api] DDG request failed: {e}")
        return []

    results: list[dict[str, str]] = []

    # AbstractURL / AbstractText — the main answer card
    if data.get("AbstractURL") and data.get("AbstractText"):
        results.append({
            "title":   data.get("Heading", query),
            "url":     data["AbstractURL"],
            "snippet": data["AbstractText"][:300],
        })

    # RelatedTopics — list of related results with URLs
    for topic in data.get("RelatedTopics", []):
        if len(results) >= max_results:
            break
        # topics can be nested groups
        if "Topics" in topic:
            for sub in topic["Topics"]:
                if len(results) >= max_results:
                    break
                url = sub.get("FirstURL", "")
                text = sub.get("Text", "")
                if url and text:
                    results.append({"title": text[:80], "url": url, "snippet": text[:300]})
        else:
            url = topic.get("FirstURL", "")
            text = topic.get("Text", "")
            if url and text:
                results.append({"title": text[:80], "url": url, "snippet": text[:300]})

    # Results[] — available on some queries
    for item in data.get("Results", []):
        if len(results) >= max_results:
            break
        url = item.get("FirstURL") or item.get("url", "")
        text = item.get("Text") or item.get("snippet", "")
        if url and text:
            results.append({"title": text[:80], "url": url, "snippet": text[:300]})

    if not results:
        logging.info(f"[search_api] DDG returned no results for '{query}'")

    return results[:max_results]


def search_to_text(query: str, max_results: int = 8) -> str:
    """Convenience: return results as a plain-text block for LLM prompts."""
    hits = search(query, max_results)
    if not hits:
        return f"No results found for: {query}"
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['title']}\n   {h['url']}\n   {h['snippet']}")
    return "\n\n".join(lines)
