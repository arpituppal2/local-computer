"""Simple memory + loop-escape helper (lighter alternative to agent_memory)."""
from __future__ import annotations
import hashlib


class Memory:
    def __init__(self):
        self.history = []
        self.repeated_same_state  = 0
        self.repeated_same_action = 0
        self.last_state_sig  = None
        self.last_action_sig = None
        self.mode_steps  = {}
        self.max_budgets = {"search": 12, "browse": 18, "workflow": 32, "respond": 5}

    def get_state_sig(self, state):
        content = f"{state.get('url','')}|{state.get('title','')}|{state.get('text','')[:250]}"
        return hashlib.md5(content.encode()).hexdigest()

    def record(self, state, action, success, mode):
        sig     = self.get_state_sig(state)
        act_sig = str(action)
        self.repeated_same_state  = self.repeated_same_state  + 1 if sig     == self.last_state_sig  else 0
        self.repeated_same_action = self.repeated_same_action + 1 if act_sig == self.last_action_sig else 0
        self.last_state_sig  = sig
        self.last_action_sig = act_sig
        self.mode_steps[mode] = self.mode_steps.get(mode, 0) + 1
        self.history.append({"state": state, "action": action, "success": success, "mode": mode})

    def is_stuck(self, mode):
        if self.repeated_same_state >= 4 or self.repeated_same_action >= 4:
            return True
        if self.mode_steps.get(mode, 0) > self.max_budgets.get(mode, 99):
            return True
        return False


def escape_action(state, memory) -> dict:
    url    = (state.get("url") or "").lower()
    recent = [str(h.get("action", {}).get("action", "")) for h in memory.history[-3:]]
    if "bing.com" in url or "google.com" in url:
        return {"action": "navigate", "value": "https://www.bing.com",
                "reason": "Hard reset search.", "source": "recovery"}
    if recent.count("go_back") >= 2:
        return {"action": "navigate", "value": "https://www.bing.com",
                "reason": "go_back loop detected.", "source": "recovery"}
    return {"action": "go_back", "reason": "Recover from loop.", "source": "recovery"}
