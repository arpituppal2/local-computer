#!/usr/bin/env python3
"""
scripts/long_term_memory.py

Persistent long-term memory with full CRUD:
  add      – append a new entry
  update   – merge new facts into an existing related entry
  replace  – overwrite an existing entry with contradicting info
  delete   – remove an entry that is stale / no longer true
  skip     – do nothing (query was one-off / nothing novel)

Decision flow
─────────────
READ phase (before research):
  1. LLM decides: "does memory contain relevant prior context?"
  2. If yes → surface the top-N relevant entries to the agent
  3. If the LLM is uncertain → ask the user

WRITE phase (after research):
  1. LLM decides: add / update / replace / delete / skip + which entries to touch
  2. If the LLM is uncertain → ask the user
  3. If action targets an existing entry → ask the user to confirm before mutating

All user prompts go to stdout/stdin so they surface in the dashboard terminal
or in the native app window's attached terminal.

Memory file format (human-readable, no entry limit):

  [2026-04-27T01:00:00Z] ID:a1b2c3  GOAL: research topic
  KEY_FACTS:
  • fact 1
  • fact 2
  DOMAINS_VISITED: example.com, nature.com
  TAGS: tag1, tag2
  ---
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.memory import Memory

from scripts.ollama_client import MODEL_PLANNER, call_json

MEMORY_FILE = Path(__file__).parent.parent / "memory" / "agent_memory.txt"
MEMORY_FILE.parent.mkdir(exist_ok=True)

MAX_CONTEXT_CHARS = 6000   # chars surfaced to the agent per session
SEP = "---"


# ════════════════════════════════════════════════════════════════════════════
# LOW-LEVEL ENTRY HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _read_raw() -> str:
    if not MEMORY_FILE.exists():
        return ""
    return MEMORY_FILE.read_text(encoding="utf-8")


def _write_raw(raw: str) -> None:
    MEMORY_FILE.write_text(raw, encoding="utf-8")


def _entries(raw: str) -> list[str]:
    """Split on ---, drop blank/header chunks."""
    return [e.strip() for e in raw.split(SEP) if e.strip() and not e.strip().startswith("#")]


def _reassemble(entries: list[str]) -> str:
    header = "# local-computer long-term memory\n"
    if entries:
        return header + f"\n{SEP}\n".join(entries) + f"\n{SEP}\n"
    return header


def _entry_id(entry: str) -> str | None:
    """Extract the short ID from 'ID:xxxx' in an entry header."""
    m = re.search(r"ID:([a-f0-9]{6})", entry)
    return m.group(1) if m else None


def _new_id() -> str:
    return uuid.uuid4().hex[:6]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _score_entry(entry: str, goal_words: set[str]) -> int:
    words = set(re.findall(r"\w+", entry.lower()))
    return len(goal_words & words)


# ════════════════════════════════════════════════════════════════════════════
# USER PROMPT HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _ask_user(question: str, choices: list[str], default: str) -> str:
    """
    Print a question and a numbered menu to stdout, read a reply from stdin.
    Falls back to `default` if stdin is not a TTY (e.g. headless CI).
    """
    if not sys.stdin.isatty():
        print(f"[memory] Non-interactive mode — defaulting to '{default}' for: {question}",
              flush=True)
        return default

    print(f"\n\033[1;36m[local-computer memory]\033[0m {question}", flush=True)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    print(f"  (Enter number, or press Enter for default: '{default}'): ", end="", flush=True)

    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        return default

    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    return default


def _confirm(question: str) -> bool:
    """Yes/no prompt; defaults to yes in non-interactive mode."""
    answer = _ask_user(question, ["Yes", "No"], "Yes")
    return answer.lower().startswith("y")


# ════════════════════════════════════════════════════════════════════════════
# READ PHASE
# ════════════════════════════════════════════════════════════════════════════

def should_read(goal: str) -> bool:
    """
    Ask the LLM if memory is worth consulting for this goal.
    If the LLM is uncertain, ask the user.
    """
    if not MEMORY_FILE.exists() or MEMORY_FILE.stat().st_size < 10:
        return False

    snippet = _read_raw()[-2000:]
    prompt = f"""
You are a meta-reasoning agent managing persistent long-term memory.

Current goal: "{goal}"

Tail of memory file:
{snippet}

Decide whether reading the full memory would give the agent useful prior context.
Return JSON with ONE of:
  {{"read": true}}   — memory is clearly relevant
  {{"read": false}}  — memory is clearly irrelevant / goal is one-off
  {{"read": "ask"}}  — you are not sure; the user should decide
"""
    result = call_json(prompt, model=MODEL_PLANNER) or {}
    decision = result.get("read", False)

    if decision == "ask":
        return _confirm(
            f"I'm not sure if past memory is relevant to goal: '{goal}'\n"
            "  Should I read from long-term memory before starting?"
        )
    return bool(decision)


def read_relevant(goal: str) -> str:
    """
    Return the entries most relevant to `goal`, scored by keyword overlap.
    No entry limit — returns as many as fit within MAX_CONTEXT_CHARS.
    """
    raw = _read_raw()
    if not raw:
        return ""

    goal_words = set(re.findall(r"\w+", goal.lower())) - {"the", "a", "an", "of", "to", "is", "in"}
    entries = _entries(raw)

    scored = sorted(
        ((score, e) for e in entries if (score := _score_entry(e, goal_words)) > 0),
        key=lambda x: x[0],
        reverse=True,
    )

    collected, total = [], 0
    for _, entry in scored:
        if total + len(entry) > MAX_CONTEXT_CHARS:
            break
        collected.append(entry)
        total += len(entry)

    if not collected and entries:
        collected = [entries[-1]]  # fallback: most recent

    return f"\n{SEP}\n".join(collected)


# ════════════════════════════════════════════════════════════════════════════
# WRITE PHASE — LLM DECISION
# ════════════════════════════════════════════════════════════════════════════

def _decide_write_action(goal: str, summary: str, entries: list[str]) -> dict:
    """
    Ask the LLM what memory operation to perform after a research session.

    Returns a dict like:
      {"action": "add",     "tags": ["ml", "python"]}
      {"action": "update",  "target_id": "a1b2c3", "tags": [...]}
      {"action": "replace", "target_id": "a1b2c3", "tags": [...]}
      {"action": "delete",  "target_id": "a1b2c3"}
      {"action": "skip"}
      {"action": "ask"}     — agent is uncertain
    """
    # Show only IDs + first line of each entry to keep prompt tight
    index_lines = []
    for e in entries:
        eid = _entry_id(e) or "??????"
        first = e.splitlines()[0][:100] if e.splitlines() else ""
        index_lines.append(f"  ID:{eid}  {first}")
    index_str = "\n".join(index_lines) if index_lines else "  (memory is empty)"

    prompt = f"""
You are managing a persistent long-term memory file for an AI research agent.

The agent just finished goal: "{goal}"

Summary of findings:
{summary[:1500]}

Existing memory entries (ID + first line):
{index_str}

Decide the best memory operation. Return JSON with exactly one of:

  {{"action": "skip"}}                                         — nothing novel / one-off query
  {{"action": "add",     "tags": ["tag1", "tag2"]}}           — brand new knowledge
  {{"action": "update",  "target_id": "xxxx", "tags": [...]}} — new facts extend an existing entry
  {{"action": "replace", "target_id": "xxxx", "tags": [...]}} — new findings contradict / supersede entry
  {{"action": "delete",  "target_id": "xxxx"}}               — existing entry is now stale/wrong
  {{"action": "ask"}}                                          — you are genuinely uncertain

Rules:
- Prefer "update" over "add" when an entry on the same topic already exists.
- Use "replace" only when facts directly contradict the old entry.
- "delete" is for clearly outdated entries (e.g. a version number that changed).
- "skip" for searches like "what time is it" or "calculate 2+2".
- Return ONLY valid JSON, nothing else.
"""
    return call_json(prompt, model=MODEL_PLANNER) or {"action": "skip"}


# ════════════════════════════════════════════════════════════════════════════
# WRITE OPERATIONS
# ════════════════════════════════════════════════════════════════════════════

def _build_entry(goal: str, memory: "Memory", summary: str,
                 tags: list[str], entry_id: str | None = None) -> str:
    domains = sorted({e.get("source_domain", "") for e in memory.evidence
                      if e.get("source_domain")})
    lines = [l.strip() for l in summary.splitlines() if l.strip()]
    facts = lines[:10]
    eid = entry_id or _new_id()
    ts = _timestamp()
    tag_str = ", ".join(tags) if tags else "general"
    return (
        f"[{ts}] ID:{eid}  GOAL: {goal}\n"
        f"KEY_FACTS:\n"
        + "\n".join(f"  • {f}" for f in facts)
        + f"\nDOMAINS_VISITED: {', '.join(domains) or 'none'}\n"
        f"TAGS: {tag_str}\n"
    )


def _do_add(entries: list[str], goal: str, memory: "Memory",
            summary: str, tags: list[str]) -> None:
    new_entry = _build_entry(goal, memory, summary, tags)
    entries.append(new_entry)
    _write_raw(_reassemble(entries))
    print(f"[memory] ✚ Added new entry (ID:{_entry_id(new_entry)})", flush=True)


def _do_update(entries: list[str], target_id: str, goal: str,
               memory: "Memory", summary: str, tags: list[str]) -> bool:
    for i, e in enumerate(entries):
        if _entry_id(e) == target_id:
            # Merge: keep old facts, append new ones, refresh timestamp
            old_facts = re.findall(r"•\s+(.+)", e)
            new_lines = [l.strip() for l in summary.splitlines() if l.strip()]
            merged_facts = list(dict.fromkeys(old_facts + new_lines[:10]))  # dedup
            domains = sorted({e.get("source_domain", "") for e in memory.evidence
                               if e.get("source_domain")})
            tag_str = ", ".join(tags) if tags else "general"
            ts = _timestamp()
            updated = (
                f"[{ts}] ID:{target_id}  GOAL: {goal}\n"
                f"KEY_FACTS:\n"
                + "\n".join(f"  • {f}" for f in merged_facts)
                + f"\nDOMAINS_VISITED: {', '.join(domains) or 'none'}\n"
                f"TAGS: {tag_str}\n"
            )
            entries[i] = updated
            _write_raw(_reassemble(entries))
            print(f"[memory] ↑ Updated entry ID:{target_id}", flush=True)
            return True
    print(f"[memory] ⚠ Update target ID:{target_id} not found — adding as new.", flush=True)
    _do_add(entries, goal, memory, summary, tags)
    return False


def _do_replace(entries: list[str], target_id: str, goal: str,
                memory: "Memory", summary: str, tags: list[str]) -> bool:
    for i, e in enumerate(entries):
        if _entry_id(e) == target_id:
            new_entry = _build_entry(goal, memory, summary, tags, entry_id=target_id)
            entries[i] = new_entry
            _write_raw(_reassemble(entries))
            print(f"[memory] ⟳ Replaced entry ID:{target_id}", flush=True)
            return True
    print(f"[memory] ⚠ Replace target ID:{target_id} not found — adding as new.", flush=True)
    _do_add(entries, goal, memory, summary, tags)
    return False


def _do_delete(entries: list[str], target_id: str) -> bool:
    before = len(entries)
    entries[:] = [e for e in entries if _entry_id(e) != target_id]
    if len(entries) < before:
        _write_raw(_reassemble(entries))
        print(f"[memory] ✕ Deleted entry ID:{target_id}", flush=True)
        return True
    print(f"[memory] ⚠ Delete target ID:{target_id} not found.", flush=True)
    return False


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC WRITE ENTRYPOINT
# ════════════════════════════════════════════════════════════════════════════

def manage_memory(goal: str, memory: "Memory", summary: str) -> None:
    """
    Main write-phase entrypoint called from navigation_agent.py after synthesis.

    1. Ask the LLM what operation to perform.
    2. If uncertain ("ask"), prompt the user interactively.
    3. For any destructive operation (replace / delete), confirm with the user.
    """
    if not summary or len(summary) < 50:
        return

    raw = _read_raw()
    entries = _entries(raw)

    decision = _decide_write_action(goal, summary, entries)
    action = decision.get("action", "skip")
    target_id = decision.get("target_id")
    tags = decision.get("tags", [])

    # ── agent is uncertain: ask the user ──────────────────────────────────
    if action == "ask":
        action = _ask_user(
            f"I'm not sure how to update memory for goal: '{goal}'\n"
            f"  What should I do?",
            choices=["add", "update existing entry", "replace existing entry",
                     "delete an entry", "skip (do nothing)"],
            default="skip",
        ).split()[0].lower()   # grab first word: "add", "update", "replace", "delete", "skip"

        # If they picked a mutating action that needs a target, ask which ID
        if action in ("update", "replace", "delete") and not target_id:
            if entries:
                print("\n[memory] Existing entries:", flush=True)
                for e in entries:
                    eid = _entry_id(e) or "??????"
                    first = e.splitlines()[0][:120] if e.splitlines() else ""
                    print(f"  ID:{eid}  {first}")
                print("  Enter ID to target (or press Enter to skip): ", end="", flush=True)
                try:
                    target_id = input().strip() or None
                except (EOFError, KeyboardInterrupt):
                    target_id = None
            if not target_id:
                action = "add"  # graceful fallback

    # ── execute operation ─────────────────────────────────────────────────
    if action == "skip":
        print("[memory] No memory changes.", flush=True)
        return

    if action == "add":
        _do_add(entries, goal, memory, summary, tags)

    elif action in ("update", "update existing entry"):
        if not target_id:
            print("[memory] ⚠ No target_id for update — adding as new.", flush=True)
            _do_add(entries, goal, memory, summary, tags)
        else:
            _do_update(entries, target_id, goal, memory, summary, tags)

    elif action in ("replace", "replace existing entry"):
        if not target_id:
            print("[memory] ⚠ No target_id for replace — adding as new.", flush=True)
            _do_add(entries, goal, memory, summary, tags)
        else:
            # Confirm before overwriting
            target_entry = next((e for e in entries if _entry_id(e) == target_id), "")
            preview = target_entry[:300].strip()
            if _confirm(
                f"Replace existing entry ID:{target_id}?\n"
                f"  Preview: {preview}\n"
                f"  Confirm replace?"
            ):
                _do_replace(entries, target_id, goal, memory, summary, tags)
            else:
                print("[memory] Replace cancelled — adding as new entry instead.", flush=True)
                _do_add(entries, goal, memory, summary, tags)

    elif action in ("delete", "delete an entry"):
        if not target_id:
            print("[memory] ⚠ No target_id for delete — skipping.", flush=True)
            return
        target_entry = next((e for e in entries if _entry_id(e) == target_id), "")
        preview = target_entry[:300].strip()
        if _confirm(
            f"Permanently delete entry ID:{target_id}?\n"
            f"  Preview: {preview}\n"
            f"  This cannot be undone. Confirm?"
        ):
            _do_delete(entries, target_id)
        else:
            print("[memory] Delete cancelled.", flush=True)

    else:
        print(f"[memory] Unknown action '{action}' — skipping.", flush=True)


# ════════════════════════════════════════════════════════════════════════════
# LEGACY SHIMS  (keeps navigation_agent.py import-compatible)
# ════════════════════════════════════════════════════════════════════════════

def should_write(goal: str, summary: str) -> bool:
    """Deprecated shim — manage_memory() now owns the full write decision."""
    return bool(summary and len(summary) >= 50)


def write_entry(goal: str, memory: "Memory", summary: str) -> None:
    """Deprecated shim — delegates to manage_memory()."""
    manage_memory(goal, memory, summary)
