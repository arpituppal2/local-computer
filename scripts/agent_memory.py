"""Loop memory: tracks states, actions, failures, evidence, visited URLs."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any


def _norm_text(s: str, limit: int = 1200) -> str:
    return " ".join((s or "").split())[:limit]


def _stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Memory:
    recent_states:   deque = field(default_factory=lambda: deque(maxlen=16))
    recent_actions:  deque = field(default_factory=lambda: deque(maxlen=30))
    recent_failures: deque = field(default_factory=lambda: deque(maxlen=30))
    visited_urls:    dict[str, int] = field(default_factory=dict)
    clicked_targets: dict[str, int] = field(default_factory=dict)
    evidence:        list[dict[str, Any]] = field(default_factory=list)

    def state_signature(self, state: dict[str, Any]) -> str:
        payload = {
            "url":   state.get("url", ""),
            "title": state.get("title", ""),
            "visible_text": _norm_text(state.get("visible_text", "")),
            "targets": [
                {
                    "kind": x.get("kind", ""),
                    "text": _norm_text(x.get("text", ""), 100),
                    "href": _norm_text(x.get("href", ""), 160),
                }
                for x in (state.get("candidate_targets") or [])[:12]
            ],
        }
        return _stable_hash(payload)

    def record_state(self, state: dict[str, Any]) -> str:
        sig = self.state_signature(state)
        self.recent_states.append(sig)
        url = (state.get("url") or "").strip()
        if url:
            self.visited_urls[url] = self.visited_urls.get(url, 0) + 1
        return sig

    def record_action(self, action: dict[str, Any], result: dict[str, Any]) -> None:
        self.recent_actions.append({"action": action, "result": result})
        if not result.get("ok", False):
            self.recent_failures.append({"action": action, "result": result})

    def seen_same_state_too_often(self, state: dict[str, Any], threshold: int = 3) -> bool:
        sig = self.state_signature(state)
        return sum(1 for x in self.recent_states if x == sig) >= threshold

    def failed_click_before(self, text: str) -> bool:
        t = (text or "").strip().lower()
        for item in self.recent_failures:
            act = item.get("action", {})
            if act.get("action") in {"click", "open_in_new_tab"}:
                target = act.get("target") or {}
                seen = (target.get("text") or "").strip().lower()
                if seen and seen == t:
                    return True
        return False

    def add_evidence(self, item: dict[str, Any]) -> None:
        self.evidence.append(item)

    def enough_evidence(self, minimum: int = 3) -> bool:
        return len(self.evidence) >= minimum
