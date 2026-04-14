from __future__ import annotations
import hashlib, json
from collections import deque
from dataclasses import dataclass, field
from typing import Any


def _norm(s: str, limit: int = 1200) -> str:
    return " ".join((s or "").split())[:limit]


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


@dataclass
class Memory:
    recent_states:   deque = field(default_factory=lambda: deque(maxlen=16))
    recent_actions:  deque = field(default_factory=lambda: deque(maxlen=30))
    recent_failures: deque = field(default_factory=lambda: deque(maxlen=30))
    visited_urls:    dict  = field(default_factory=dict)
    evidence:        list  = field(default_factory=list)

    def sig(self, state: dict) -> str:
        return _hash({"url": state.get("url",""), "title": state.get("title",""),
                      "text": _norm(state.get("visible_text",""))})

    def record_state(self, state: dict) -> str:
        s = self.sig(state)
        self.recent_states.append(s)
        url = (state.get("url") or "").strip()
        if url:
            self.visited_urls[url] = self.visited_urls.get(url, 0) + 1
        return s

    def record_action(self, action: dict, result: dict) -> None:
        self.recent_actions.append({"action": action, "result": result})
        if not result.get("ok"):
            self.recent_failures.append({"action": action, "result": result})

    def stuck(self, state: dict, threshold: int = 3) -> bool:
        return sum(1 for x in self.recent_states if x == self.sig(state)) >= threshold

    def add_evidence(self, item: dict) -> None:
        self.evidence.append(item)

    def enough_evidence(self, minimum: int = 3) -> bool:
        return len(self.evidence) >= minimum
