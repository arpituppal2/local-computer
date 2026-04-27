#!/usr/bin/env python3
"""
scripts/memory.py
In-session working memory for the research agent.
"""
from __future__ import annotations

from collections import deque
from typing import Any


class Memory:
    def __init__(self, maxlen: int = 50):
        self.evidence: list[dict] = []
        self.recent_actions: deque = deque(maxlen=maxlen)
        self.recent_failures: deque = deque(maxlen=maxlen)
        self._prior_context: str = ""

    # ── evidence ────────────────────────────────────────────────────
    def add_evidence(self, item: dict) -> None:
        self.evidence.append(item)

    # ── actions ─────────────────────────────────────────────────────
    def record_action(self, action: dict, result: dict) -> None:
        entry = {"action": action, "result": result}
        self.recent_actions.append(entry)
        if not result.get("ok"):
            self.recent_failures.append(entry)

    # ── long-term memory injection ──────────────────────────────────
    def inject_prior_context(self, text: str) -> None:
        """Store text retrieved from the long-term memory file."""
        self._prior_context = text

    @property
    def prior_context(self) -> str:
        return self._prior_context

    # ── misc ─────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        return {
            "evidence_count": len(self.evidence),
            "action_count": len(self.recent_actions),
            "failure_count": len(self.recent_failures),
            "has_prior_context": bool(self._prior_context),
        }
