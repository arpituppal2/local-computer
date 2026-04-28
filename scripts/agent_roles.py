"""Specialist agent roles — Perplexity Computer parity.

Each role wraps the shared action primitives (observe/execute) with
role-specific prompting, tool sets, and decision logic so each agent
acts like a domain expert rather than a generic browser bot.

Roles:
  ResearcherAgent  — web search, page reading, claim extraction
  AnalystAgent     — evidence synthesis and cross-referencing
  CoderAgent       — writes + runs Python; returns stdout as findings
  WriterAgent      — drafts structured markdown answer/report
  BrowserAgent     — pure low-level browser automation (click/fill/drag)
  FileAgent        — reads/writes local files
"""
from __future__ import annotations
import logging
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
    """Drives the browser to search and extract information.

    Delegates to the existing navigation_agent stage loop so all
    existing evidence/memory plumbing is reused.
    """
    role = "researcher"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        from scripts.navigation_agent import run_stage
        from scripts.event_logger import EventLogger
        from pathlib import Path
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
    """Synthesizes evidence gathered by ResearcherAgent(s)."""
    role = "analyst"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        evidence_text = ""
        if memory and memory.evidence:
            evidence_text = "\n\n".join(
                f"Source: {e.get('url')}\n" + "\n".join(e.get("claims", []))
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
    """Writes Python code, executes it, and returns the output."""
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
    """Produces the final structured markdown answer."""
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
# Browser (pure automation)
# ════════════════════════════════════════════════════════════════════════════

class BrowserAgent(_BaseAgent):
    """Executes an LLM-planned sequence of low-level browser actions."""
    role = "browser"

    def run(self, task: dict, page=None, context=None, memory=None) -> dict:
        if page is None:
            return {"role": self.role, "findings": "", "status": "error", "error": "no page"}

        from scripts.observer import observe
        from scripts.executor import execute

        state   = observe(page)
        prompt  = (
            f"You are a browser automation agent.\n"
            f"TASK: {task['goal']}\n\n"
            f"CURRENT PAGE: {state['url']}\n"
            f"VISIBLE TEXT (truncated):\n{state['visible_text'][:800]}\n\n"
            f"INTERACTIVE ELEMENTS (first 20):\n"
            + "\n".join(
                f"[{t['target_id']}] {t['kind']} '{t['text'][:60]}' at ({t['x']},{t['y']})"
                for t in state["candidate_targets"][:20]
            ) +
            "\n\nReturn a JSON list of actions to perform: "
            '[{"action": "click", "x": 120, "y": 340}, ...] or '
            '[{"action": "fill", "selector": "input[name=q]", "value": "..."}]'
        )
        plan = call_json(prompt, model=MODEL_ACTOR) or {}
        actions = plan.get("actions", plan) if isinstance(plan, dict) else plan
        if not isinstance(actions, list):
            actions = []

        results = []
        for act in actions[:task.get("max_steps", 10)]:
            r = execute(page, context, act)
            results.append(r)
            if not r.get("ok"):
                logging.warning(f"[BrowserAgent] action failed: {r}")

        return {"role": self.role, "findings": str(results), "status": "done"}


# ════════════════════════════════════════════════════════════════════════════
# File
# ════════════════════════════════════════════════════════════════════════════

class FileAgent(_BaseAgent):
    """Reads or writes local files as directed by the task goal."""
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
    """Return an instantiated agent for the given role string."""
    cls = _REGISTRY.get(role.lower(), ResearcherAgent)
    return cls()
