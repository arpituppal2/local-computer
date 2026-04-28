"""Specialist agent roles — Perplexity Computer parity.

Roles:
  ResearcherAgent  — web search, page reading, claim extraction
  AnalystAgent     — evidence synthesis and cross-referencing
  CoderAgent       — writes + runs Python; returns stdout as findings
  WriterAgent      — drafts structured markdown answer/report
  BrowserAgent     — multi-step browser automation with re-observe loop
  FileAgent        — reads/writes local files
"""
from __future__ import annotations
import logging
import time
from pathlib import Path
from typing import Any

from scripts.ollama_client import call, call_json, MODEL_ACTOR, MODEL_ANALYST, MODEL_HEAVY


# ════════════════════════════════════════════════════════════════════════════
# Base
# ════════════════════════════════════════════════════════════════════════════

class _BaseAgent:
    role: str = "base"

    def run(self, task: dict[str, Any], page=None, context=None, memory=None) -> dict[str, Any]:
        raise NotImplementedError


# ════════════════════════════════════════════════════════════════════════════
# Researcher
# ════════════════════════════════════════════════════════════════════════════

class ResearcherAgent(_BaseAgent):
    role = "researcher"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        from scripts.navigation_agent import run_stage
        from scripts.event_logger import EventLogger
        ROOT = Path(__file__).resolve().parent.parent
        log = EventLogger(ROOT / "outputs")

        if page is None or context is None:
            logging.warning("[ResearcherAgent] no browser page — falling back to Ollama call")
            answer = call(task["goal"], model=MODEL_ACTOR)
            return {"role": self.role, "findings": answer, "status": "done"}

        done = run_stage(page, context, task["goal"], task, memory, log)
        findings = "\n".join(
            c for e in (memory.evidence if memory else []) for c in e.get("claims", [])
        )
        return {"role": self.role, "findings": findings, "status": "done" if done else "partial"}


# ════════════════════════════════════════════════════════════════════════════
# Analyst
# ════════════════════════════════════════════════════════════════════════════

class AnalystAgent(_BaseAgent):
    role = "analyst"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        evidence_text = ""
        if memory and memory.evidence:
            evidence_text = "\n\n".join(
                f"Source: {e.get('url')}\nClaims: " + "\n".join(e.get("claims", []))
                for e in memory.evidence[:12]
            )

        prompt = (
            f"TASK: {task['goal']}\n\n"
            f"EVIDENCE:\n{evidence_text[:3000]}\n\n"
            "Synthesize the evidence above into a concise, factual answer. "
            "Highlight contradictions. Cite source URLs where relevant."
        )
        answer = call(prompt, model=MODEL_ANALYST)
        return {"role": self.role, "findings": answer, "status": "done"}


# ════════════════════════════════════════════════════════════════════════════
# Coder
# ════════════════════════════════════════════════════════════════════════════

class CoderAgent(_BaseAgent):
    role = "coder"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        from scripts.executor import execute

        prompt = (
            f"Write a concise Python script to accomplish this task:\n{task['goal']}\n\n"
            "Return ONLY the Python code, no prose or markdown fences."
        )
        code = call(prompt, model=MODEL_ACTOR)

        if page is not None and context is not None:
            result = execute(page, context, {"action": "run_code", "code": code})
        else:
            import subprocess, sys, tempfile, textwrap
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(textwrap.dedent(code)); tmp = f.name
            proc = subprocess.run([sys.executable, tmp], capture_output=True, text=True, timeout=30)
            result = {"ok": proc.returncode == 0, "stdout": proc.stdout[:4000], "stderr": proc.stderr[:2000]}

        findings = result.get("stdout") or result.get("error", "[no output]")
        return {"role": self.role, "findings": findings, "code": code, "status": "done" if result["ok"] else "error"}


# ════════════════════════════════════════════════════════════════════════════
# Writer
# ════════════════════════════════════════════════════════════════════════════

class WriterAgent(_BaseAgent):
    role = "writer"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        context_text = ""
        if memory and memory.evidence:
            context_text = "\n\n".join(
                f"[{e.get('url')}]\n" + "\n".join(e.get("claims", []))
                for e in memory.evidence[:10]
            )

        prompt = (
            f"TASK: {task['goal']}\n\n"
            f"RESEARCH CONTEXT:\n{context_text[:3000]}\n\n"
            "Write a clear, well-structured markdown response. "
            "Use headers, bullet points, and code blocks as appropriate. "
            "Be concise and factual."
        )
        answer = call(prompt, model=MODEL_HEAVY)
        return {"role": self.role, "findings": answer, "status": "done"}


# ════════════════════════════════════════════════════════════════════════════
# Browser (multi-step re-observe loop)
# ════════════════════════════════════════════════════════════════════════════

# Actions the LLM can emit
_VALID_ACTIONS = {
    "click", "fill", "goto", "wait", "scroll",
    "press", "select", "hover", "done",
}

# Keywords that signal the page is waiting for the user to sign in
_LOGIN_SIGNALS = [
    "sign in", "log in", "login", "sign into", "google accounts",
    "accounts.google.com", "enter your email", "enter your password",
    "forgot password", "create account", "use your google account",
]


class BrowserAgent(_BaseAgent):
    """Multi-step browser automation.

    Runs a tight observe → plan → execute loop so the LLM can react
    to page state after every action (navigation, dialogs, login walls, etc.).
    Automatically surfaces login walls to the UI permission system.
    """
    role = "browser"
    MAX_STEPS = 30          # hard cap per task
    WAIT_AFTER_NAV = 1.2    # seconds to let page settle after goto/click

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        if page is None:
            return {"role": self.role, "findings": "", "status": "error", "error": "no page"}

        from scripts.observer import observe
        from scripts.executor import execute

        goal        = task["goal"]
        step        = 0
        history     = []   # brief log of what happened so LLM has context
        findings    = ""

        while step < self.MAX_STEPS:
            step += 1

            # ── 1. Observe ────────────────────────────────────────────────
            try:
                state = observe(page)
            except Exception as e:
                logging.warning(f"[BrowserAgent] observe failed: {e}")
                break

            url          = state.get("url", "")
            visible_text = state.get("visible_text", "")[:1000]
            targets      = state.get("candidate_targets", [])[:24]

            # ── 2. Check for login wall ───────────────────────────────────
            combined = (url + " " + visible_text).lower()
            on_login_page = any(sig in combined for sig in _LOGIN_SIGNALS)

            if on_login_page and memory is not None:
                # Surface to the UI — user can provide creds or take over
                handled = memory.request_login(url, page)
                if handled:
                    creds = memory.get_login_creds()
                    if creds.get("email") or creds.get("username"):
                        # Try to fill the form automatically
                        _autofill_login(page, creds)
                    # Re-observe after login attempt
                    time.sleep(self.WAIT_AFTER_NAV)
                    continue
                # User denied — stop trying to log in
                findings = "[login required but denied by user]"
                break

            # ── 3. Build prompt with full page state ──────────────────────
            targets_str = "\n".join(
                f"[{t['target_id']}] {t['kind']} '{t['text'][:55]}' @({t['x']},{t['y']})"
                for t in targets
            )
            history_str = "\n".join(history[-6:])  # last 6 steps for context

            prompt = (
                f"You are a browser automation agent completing a multi-step task.\n"
                f"GOAL: {goal}\n\n"
                f"STEP: {step}/{self.MAX_STEPS}\n"
                f"CURRENT URL: {url}\n"
                f"PAGE TEXT (truncated):\n{visible_text}\n\n"
                f"INTERACTIVE ELEMENTS:\n{targets_str}\n\n"
                f"RECENT ACTIONS:\n{history_str}\n\n"
                "Decide the SINGLE next action to get closer to the goal.\n"
                "If the goal is complete, return {\"action\": \"done\", \"findings\": \"<summary>\"}\n"
                "Otherwise return ONE of:\n"
                '  {"action": "goto",   "url": "https://..."}\n'
                '  {"action": "click",  "x": 120, "y": 340}\n'
                '  {"action": "fill",   "selector": "css-selector", "value": "text"}\n'
                '  {"action": "press",  "key": "Enter"}\n'
                '  {"action": "wait",   "ms": 1500}\n'
                "Return ONLY raw JSON, no prose."
            )

            # ── 4. Plan (one action at a time) ───────────────────────────
            raw = call_json(prompt, model=MODEL_ACTOR)
            if not raw or not isinstance(raw, dict):
                logging.warning(f"[BrowserAgent] invalid LLM response at step {step}: {raw}")
                break

            action = raw.get("action", "")
            if action not in _VALID_ACTIONS:
                logging.warning(f"[BrowserAgent] unknown action '{action}' — stopping")
                break

            # ── 5. Done signal ────────────────────────────────────────────
            if action == "done":
                findings = raw.get("findings", f"Task completed after {step} steps.")
                break

            # ── 6. Execute ────────────────────────────────────────────────
            result = execute(page, context, raw)
            ok     = result.get("ok", False)
            history.append(f"Step {step}: {action} → {'ok' if ok else 'FAILED: '+str(result.get('error',''))}")

            if not ok:
                logging.warning(f"[BrowserAgent] step {step} failed: {result}")
                # Don't hard-stop — let LLM try to recover on next observe

            # ── 7. Wait for page to settle after navigation ───────────────
            if action in ("goto", "click", "press"):
                time.sleep(self.WAIT_AFTER_NAV)

        if not findings:
            findings = f"Browser task ran {step} steps. Last URL: {page.url}"

        return {"role": self.role, "findings": findings, "status": "done"}


def _autofill_login(page, creds: dict):
    """Best-effort form fill for common login selectors."""
    from scripts.executor import execute
    email = creds.get("email") or creds.get("username", "")
    pw    = creds.get("password", "")

    EMAIL_SELECTORS = [
        'input[type="email"]', 'input[name="email"]',
        'input[name="username"]', 'input[name="identifier"]',
        'input[autocomplete="username"]', 'input[autocomplete="email"]',
    ]
    PW_SELECTORS = [
        'input[type="password"]', 'input[name="password"]',
        'input[autocomplete="current-password"]',
    ]

    for sel in EMAIL_SELECTORS:
        r = execute(page, None, {"action": "fill", "selector": sel, "value": email})
        if r.get("ok"):
            break

    execute(page, None, {"action": "press", "key": "Enter"})
    time.sleep(0.8)

    for sel in PW_SELECTORS:
        r = execute(page, None, {"action": "fill", "selector": sel, "value": pw})
        if r.get("ok"):
            break

    execute(page, None, {"action": "press", "key": "Enter"})
    time.sleep(1.5)


# ════════════════════════════════════════════════════════════════════════════
# File
# ════════════════════════════════════════════════════════════════════════════

class FileAgent(_BaseAgent):
    role = "file"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        from scripts.executor import execute

        prompt = (
            f"You are a file-system agent. Determine which file action to take.\n"
            f"TASK: {task['goal']}\n\n"
            'Return JSON: {"action": "read_file", "value": "/path/to/file"} '
            'or {"action": "write_file", "path": "/path/to/file", "content": "..."}'
        )
        act = call_json(prompt, model=MODEL_ACTOR) or {}

        if page is not None:
            result = execute(page, context, act)
        else:
            from pathlib import Path as _P
            if act.get("action") == "read_file":
                try:
                    content = _P(act["value"]).expanduser().read_text(errors="replace")
                    result = {"ok": True, "content": content[:8000]}
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
            elif act.get("action") == "write_file":
                try:
                    p = _P(act["path"]).expanduser()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(act.get("content", ""))
                    result = {"ok": True, "path": str(p)}
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
            else:
                result = {"ok": False, "error": "unrecognised file action"}

        findings = result.get("content") or result.get("path") or result.get("error", "")
        return {"role": self.role, "findings": findings, "status": "done" if result["ok"] else "error"}


# ════════════════════════════════════════════════════════════════════════════
# Registry
# ════════════════════════════════════════════════════════════════════════════

_REGISTRY: dict[str, type[_BaseAgent]] = {
    "researcher": ResearcherAgent,
    "analyst":    AnalystAgent,
    "coder":      CoderAgent,
    "writer":     WriterAgent,
    "browser":    BrowserAgent,
    "file":       FileAgent,
}


def get_agent(role: str) -> _BaseAgent:
    cls = _REGISTRY.get(role.lower(), ResearcherAgent)
    return cls()
