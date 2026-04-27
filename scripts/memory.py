"""Unified Memory class — replaces both memory.py and agent_memory.py APIs (fixes #22-24)."""
from __future__ import annotations
import hashlib, time
from collections import deque

class Memory:
    def __init__(self, max_history: int = 200):
        self.history: deque = deque(maxlen=max_history)
        self.visited_urls: set = set()
        self.evidence: list = []
        self.mode_steps: dict = {}
        self._state_hashes: deque = deque(maxlen=20)

    def record(self, action: str, url: str, result: str = ""):
        self.record_action(action, url, result)

    def record_action(self, action: str, url: str, result: str = ""):
        self.visited_urls.add(url)
        entry = {"action": action, "url": url, "result": result, "ts": time.time()}
        self.history.append(entry)
        self.mode_steps[action] = self.mode_steps.get(action, 0) + 1

    def is_stuck(self) -> bool:
        return self.stuck()

    def stuck(self) -> bool:
        if len(self.history) < 4:
            return False
        recent = list(self.history)[-4:]
        urls = [e.get("url", "") for e in recent]
        return len(set(urls)) <= 1

    def state_signature(self, url: str, text_snippet: str) -> str:
        raw = f"{url}::{text_snippet[:200]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def is_repeated_state(self, url: str, text_snippet: str) -> bool:
        sig = self.state_signature(url, text_snippet)
        if sig in self._state_hashes:
            return True
        self._state_hashes.append(sig)
        return False
