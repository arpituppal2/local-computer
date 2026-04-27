#!/usr/bin/env python3
"""
scripts/long_term_memory.py

Persistent long-term memory stored in memory/agent_memory.txt.

The agent decides autonomously when to read or write:
  - should_read(goal)  → ask the LLM "is prior context relevant?"
  - should_write(goal) → ask the LLM "did I learn anything worth saving?"

Memory file format  (human-readable, append-only):

  [2026-04-27T01:00:00]  GOAL: research topic here
  KEY_FACTS:
  • fact 1
  • fact 2
  DOMAINS_VISITED: example.com, other.org
  ---
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.memory import Memory

from scripts.ollama_client import MODEL_PLANNER, call_json

MEMORY_FILE = Path(__file__).parent.parent / "memory" / "agent_memory.txt"
MEMORY_FILE.parent.mkdir(exist_ok=True)

MAX_CONTEXT_CHARS = 4000   # how many chars to surface to the agent
MAX_FILE_ENTRIES  = 200    # trim oldest entries beyond this


# ── helpers ────────────────────────────────────────────────────────────────

def _read_raw() -> str:
    if not MEMORY_FILE.exists():
        return ""
    return MEMORY_FILE.read_text(encoding="utf-8")


def _entries(raw: str) -> list[str]:
    """Split on the --- separator, drop empty chunks."""
    return [e.strip() for e in raw.split("---") if e.strip()]


def _trim_if_needed(raw: str) -> str:
    entries = _entries(raw)
    if len(entries) <= MAX_FILE_ENTRIES:
        return raw
    kept = entries[-MAX_FILE_ENTRIES:]
    return "\n---\n".join(kept) + "\n---\n"


# ── public API ──────────────────────────────────────────────────────────────

def should_read(goal: str) -> bool:
    """
    Ask the LLM whether reading long-term memory is worthwhile for this goal.
    Falls back to False if the file is empty or the LLM is unavailable.
    """
    if not MEMORY_FILE.exists() or MEMORY_FILE.stat().st_size == 0:
        return False

    snippet = _read_raw()[-1500:]   # only show the tail for speed
    prompt = f"""
You are a meta-reasoning agent.

The user's current goal: "{goal}"

Below is the tail of the persistent memory file:
{snippet}

Decide: would reading this memory give useful prior context for the current goal?
Return JSON: {{"read": true}} or {{"read": false}}
Only return true if the memory is genuinely relevant."""

    result = call_json(prompt, model=MODEL_PLANNER) or {}
    return bool(result.get("read", False))


def read_relevant(goal: str) -> str:
    """
    Return the entries most likely relevant to `goal`.
    Simple keyword overlap scoring — no embedding needed.
    """
    raw = _read_raw()
    if not raw:
        return ""

    goal_words = set(re.findall(r"\w+", goal.lower()))
    entries = _entries(raw)

    scored = []
    for entry in entries:
        words = set(re.findall(r"\w+", entry.lower()))
        overlap = len(goal_words & words)
        scored.append((overlap, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [e for _, e in scored[:5] if _]   # top-5 non-zero-overlap entries
    top = [e for score, e in scored[:5] if score > 0]

    if not top:
        # fallback: just return the most recent entry
        top = [entries[-1]] if entries else []

    joined = "\n---\n".join(top)
    return joined[:MAX_CONTEXT_CHARS]


def should_write(goal: str, summary: str) -> bool:
    """
    Ask the LLM whether the session produced knowledge worth persisting.
    """
    if not summary or len(summary) < 50:
        return False

    prompt = f"""
You are a meta-reasoning agent.

After completing the goal "{goal}", the agent produced this summary:
{summary[:1200]}

Should these findings be saved to long-term memory for future sessions?
Return JSON: {{"write": true}} or {{"write": false}}
Only return true for novel, reusable facts — not one-off queries."""

    result = call_json(prompt, model=MODEL_PLANNER) or {}
    return bool(result.get("write", False))


def write_entry(goal: str, memory: "Memory", summary: str) -> None:
    """
    Append a structured entry to the memory file.
    """
    domains = list({e.get("source_domain", "") for e in memory.evidence if e.get("source_domain")})

    # Extract bullet-point key facts from the summary (first 8 lines with content)
    lines = [l.strip() for l in summary.splitlines() if l.strip()]
    facts = lines[:8]

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = (
        f"[{timestamp}]  GOAL: {goal}\n"
        f"KEY_FACTS:\n"
        + "\n".join(f"• {f}" for f in facts)
        + f"\nDOMAINS_VISITED: {', '.join(domains) or 'none'}\n"
        f"---\n"
    )

    raw = _read_raw()
    raw = _trim_if_needed(raw) + entry
    MEMORY_FILE.write_text(raw, encoding="utf-8")
