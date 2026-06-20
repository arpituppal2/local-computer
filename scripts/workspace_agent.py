"""Model-free workspace agent.

This is the dashboard fallback when local models are disabled. It does not try
to be a full LLM agent; it exposes useful deterministic capabilities so the app
can still inspect the workspace, plugins, uploads, connectors, and model plan.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.model_selector import recommend_models
from scripts.plugin_manager import plugins_for_goal, registry_snapshot
from scripts.plugin_runtime import classify_shell_command, execute_tool, preview_tool, render_tool_result, tool_catalog, tool_metadata
from scripts.run_history import store_run, store_tool_event
from scripts.runtime_policy import runtime_summary, workspace_root
from scripts.upload_store import list_uploads
from scripts.workspace_planner import ToolStep, plan_workspace_task, plan_to_json

Emit = Callable[[dict[str, Any]], Awaitable[None] | None]

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "outputs", "logs"}
TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".sh",
}


async def _emit(callback: Emit | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


def _run(cmd: list[str], cwd: Path, timeout: float = 8.0) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(exc)}


def _iter_workspace_files(root: Path, limit: int = 120) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
            if len(files) >= limit:
                break
    return files


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _workspace_summary(root: Path) -> str:
    files = _iter_workspace_files(root, limit=80)
    suffix_counts: dict[str, int] = {}
    for path in files:
        suffix_counts[path.suffix or "[none]"] = suffix_counts.get(path.suffix or "[none]", 0) + 1
    top_types = ", ".join(f"{suffix}: {count}" for suffix, count in sorted(suffix_counts.items())[:8]) or "no files"
    shown = "\n".join(f"- `{_relative(path, root)}`" for path in files[:30])
    extra = f"\n\nShowing 30 of at least {len(files)} files." if len(files) > 30 else ""
    return f"Workspace: `{root}`\n\nFile types: {top_types}\n\n{shown or 'No files found.'}{extra}"


def _plugin_summary(goal: str) -> str:
    snapshot = registry_snapshot()
    matches = plugins_for_goal(goal)
    lines = [
        f"Plugins enabled: {snapshot['enabled_count']} / {snapshot['total_count']}",
        "",
        "| Plugin | Status | Capabilities |",
        "| --- | --- | --- |",
    ]
    for plugin in snapshot["plugins"]:
        status = plugin["status"]["detail"]
        caps = ", ".join(plugin.get("capabilities", [])[:4])
        lines.append(f"| `{plugin['id']}` | {status} | {caps} |")
    if matches:
        lines.append("")
        lines.append("Best matches for this task: " + ", ".join(f"`{plugin.id}`" for plugin in matches))
    return "\n".join(lines)


def _model_summary() -> str:
    rec = recommend_models()
    hardware = rec["hardware"]
    budget = rec["resource_budget"]
    lines = [
        "Local model recommendation only; no models were downloaded or run.",
        "",
        f"Hardware tier: `{rec['tier']}`",
        f"RAM: `{hardware['ram_gb']:.1f} GB`; CPU: `{hardware['cpu_brand']}`; logical cores: `{hardware['logical_cores']}`",
        f"RAM budget: max `{budget['max_ram_gb']:.1f} GB`; usable for models `{budget['usable_for_models_gb']:.1f} GB`; reserved `{budget['reserved_system_gb']:.1f} GB`",
        (
            f"Available RAM now: `{budget['available_ram_gb']:.1f} GB` "
            f"(`{budget.get('memory_pressure', 'unknown')}`"
            f"{', pressure-adjusted' if budget.get('pressure_adjusted') else ''})"
            if budget.get("available_ram_gb") is not None
            else "Available RAM now: `unknown`"
        ),
        f"GPU cap: `{budget.get('gpu_limit_pct', 90):.0f}%`",
        "",
        "| Role | Recommended model |",
        "| --- | --- |",
    ]
    for role, model in rec["roles"].items():
        lines.append(f"| `{role}` | `{model}` |")
    lines.append("")
    lines.append("Download commands when local inference is allowed:")
    for item in rec["pull_plan"]:
        lines.append(f"- `{item['command']}`")
    if rec["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in rec["warnings"])
    return "\n".join(lines)


def _uploads_summary() -> str:
    uploads = list_uploads()
    if not uploads:
        return "No uploaded files are stored yet."
    lines = ["Recent uploads:", "", "| File | Size | Path |", "| --- | ---: | --- |"]
    for item in uploads[:12]:
        lines.append(f"| `{item['name']}` | {item['size']} | `{item['path']}` |")
    return "\n".join(lines)


def _git_status(root: Path) -> str:
    status = _run(["git", "status", "--short"], root)
    if not status["ok"]:
        return f"`{root}` is not a git repository, or git status failed.\n\n```text\n{status['stderr']}\n```"
    return "Git status:\n\n```text\n" + (status["stdout"] or "clean") + "\n```"


def _parse_path(query: str) -> str | None:
    quoted = re.search(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", query)
    if quoted:
        return next(group for group in quoted.groups() if group)
    match = re.search(r"\b(?:read|open|show)\s+([A-Za-z0-9_./~-]+)", query, re.IGNORECASE)
    return match.group(1) if match else None


def _read_file(root: Path, query: str) -> str:
    raw = _parse_path(query)
    if not raw:
        return "Tell me which file to read, for example: `read README.md`."
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    if not path.exists() or not path.is_file():
        return f"File not found: `{path}`"
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return f"`{path}` exists, but I only preview text-like files in model-free mode."
    content = path.read_text(errors="replace")
    if len(content) > 12000:
        content = content[:12000] + "\n\n[truncated]"
    return f"`{path}`:\n\n```text\n{content}\n```"


def _search_query(query: str) -> str | None:
    quoted = re.search(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", query)
    if quoted:
        return next(group for group in quoted.groups() if group)
    match = re.search(r"\bsearch(?:\s+for)?\s+(.+)$", query, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _search_workspace(root: Path, query: str) -> str:
    needle = _search_query(query)
    if not needle:
        return "Tell me what to search for, for example: `search for plugin_manager`."
    hits: list[str] = []
    lowered = needle.lower()
    for path in _iter_workspace_files(root, limit=600):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
                if lowered in line.lower():
                    hits.append(f"- `{_relative(path, root)}:{lineno}` {line.strip()[:180]}")
                    break
        except Exception:
            continue
        if len(hits) >= 40:
            break
    if not hits:
        return f"No text hits found for `{needle}` under `{root}`."
    return f"Search hits for `{needle}`:\n\n" + "\n".join(hits)


def _conversation_summary(conversation_context: dict[str, Any] | None) -> str:
    if not conversation_context:
        return "No local conversation history is available yet."
    summary = str(conversation_context.get("summary") or "").strip()
    recent = conversation_context.get("recent_messages") if isinstance(conversation_context.get("recent_messages"), list) else []
    lines = [
        "Local conversation context",
        "",
        f"Messages stored: `{conversation_context.get('message_count', 0)}`",
        f"Compressed context active: `{'yes' if conversation_context.get('compression_active') else 'no'}`",
        f"Estimated active context: `{conversation_context.get('estimated_context_tokens', 0)}` tokens",
    ]
    if summary:
        lines.extend(["", "Compressed summary:", "", summary])
    if recent:
        lines.extend(["", "Recent messages:", ""])
        for item in recent[-8:]:
            role = "User" if item.get("role") == "user" else "Locus"
            preview = str(item.get("preview") or item.get("content") or "").strip()
            lines.append(f"- {role}: {preview}")
    return "\n".join(lines)


def _default_answer(query: str, root: Path, conversation_context: dict[str, Any] | None = None) -> str:
    policy = runtime_summary()
    matches = plugins_for_goal(query)
    match_text = ", ".join(f"`{plugin.id}`" for plugin in matches) or "none"
    catalog = tool_catalog()
    implemented = []
    for plugin_id, item in catalog.items():
        tools = ", ".join(item.get("implemented", []))
        if tools:
            implemented.append(f"- `{plugin_id}`: {tools}")
    context_note = ""
    if conversation_context and conversation_context.get("message_count"):
        context_note = (
            "\n\nConversation context: "
            f"{conversation_context.get('message_count')} stored message(s), "
            f"{conversation_context.get('context_message_count', conversation_context.get('active_message_count'))} in active context."
        )
    return (
        "Local models are disabled, so I handled this without Ollama inference.\n\n"
        "I can run deterministic plugin tools now: files, text search, git, shell commands, uploads, connector status, "
        "and email draft creation. For precise control use `@tool plugin.tool {\"arg\":\"value\"}`.\n\n"
        f"Workspace: `{root}`\n\n"
        f"Matched plugins: {match_text}\n\n"
        "Implemented tools:\n"
        f"{chr(10).join(implemented)}"
        f"{context_note}\n\n"
        f"Runtime policy:\n\n```json\n{json.dumps(policy, indent=2)}\n```"
    )


def _render_plan(plan: list[ToolStep]) -> str:
    if not plan:
        return ""
    lines = ["Tool plan:", ""]
    for idx, step in enumerate(plan, start=1):
        args = json.dumps(step.args, ensure_ascii=False)
        lines.append(f"{idx}. `{step.plugin}.{step.tool}` `{args}`")
        lines.append(f"   Reason: {step.reason}")
    return "\n".join(lines)


def _render_plan_only(query: str, plan: list[ToolStep]) -> str:
    if not plan:
        return (
            "Plan Mode\n\n"
            "No executable local tool plan was found. Locus would answer with its deterministic workspace fallback.\n\n"
            f"Task: `{query}`"
        )
    lines = ["Plan Mode", "", "No tools were executed.", ""]
    for idx, step in enumerate(plan, start=1):
        args = json.dumps(step.args, ensure_ascii=False)
        lines.append(f"{idx}. `{step.plugin}.{step.tool}` `{args}`")
        lines.append(f"   Reason: {step.reason}")
    return "\n".join(lines)


def _approval_payload(step: ToolStep) -> dict[str, Any]:
    meta = tool_metadata(step.plugin, step.tool)
    payload = {**meta, "args": step.args, "reason": step.reason}
    payload["preview"] = preview_tool(step.plugin, step.tool, step.args)
    if step.plugin == "shell" and step.tool == "run_command":
        payload["shell_safety"] = classify_shell_command(step.args)
    return payload


def _approval_steps(plan: list[ToolStep]) -> list[dict[str, Any]]:
    return [
        payload
        for payload in (_approval_payload(step) for step in plan)
        if payload.get("requires_approval")
        or (isinstance(payload.get("shell_safety"), dict) and payload["shell_safety"].get("requires_approval"))
    ]


def _blocked_steps(plan: list[ToolStep]) -> list[dict[str, Any]]:
    return [
        payload
        for payload in (_approval_payload(step) for step in plan)
        if payload.get("blocked") or (isinstance(payload.get("shell_safety"), dict) and payload["shell_safety"].get("blocked"))
    ]


def _render_approval_answer(plan: list[ToolStep], approvals: list[dict[str, Any]]) -> str:
    lines = [
        _render_plan(plan),
        "Approval required before running high-impact local tools.",
        "",
        "Locus has not executed these tools:",
    ]
    for item in approvals:
        args = json.dumps(item.get("args") or {}, ensure_ascii=False)
        lines.append(f"- `{item.get('plugin')}.{item.get('tool')}` ({item.get('risk', 'unknown')}) `{args}`")
    lines.append("")
    lines.append("Use the approval sheet to run the tool once.")
    return "\n".join(line for line in lines if line is not None)


def _render_blocked_answer(plan: list[ToolStep], blocked: list[dict[str, Any]]) -> str:
    safety_blocked = any(isinstance(item.get("shell_safety"), dict) and item["shell_safety"].get("blocked") for item in blocked)
    lines = [
        _render_plan(plan),
        "Blocked by local safety policy." if safety_blocked else "Blocked by local permission policy.",
        "",
        "Locus did not execute these tools:",
    ]
    for item in blocked:
        args = json.dumps(item.get("args") or {}, ensure_ascii=False)
        lines.append(f"- `{item.get('plugin')}.{item.get('tool')}` ({item.get('risk', 'unknown')}) `{args}`")
    lines.append("")
    if safety_blocked:
        lines.append("This command is blocked while the relevant safety setting is off.")
    else:
        lines.append("Change the tool policy in Plugin Center if you want to allow this action.")
    return "\n".join(line for line in lines if line is not None)


async def _execute_plan(
    plan: list[ToolStep],
    emit_event: Emit | None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for step in plan:
        await _emit(
            emit_event,
            {
                "type": "tool",
                "data": {
                    "state": "running",
                    "plugin": step.plugin,
                    "tool": step.tool,
                    "args": step.args,
                    "reason": step.reason,
                },
            },
        )
        result = await asyncio.to_thread(execute_tool, step.plugin, step.tool, step.args)
        results.append(result)
        meta = tool_metadata(step.plugin, step.tool)
        try:
            await asyncio.to_thread(
                store_tool_event,
                step.plugin,
                step.tool,
                "completed",
                args=step.args,
                risk=str(meta.get("risk") or "unknown"),
                ok=bool(result.get("ok")),
                reason=step.reason,
                result=result,
            )
        except Exception:
            pass
        await _emit(
            emit_event,
            {
                "type": "tool",
                "data": {
                    "state": "done" if result.get("ok") else "error",
                    "plugin": step.plugin,
                    "tool": step.tool,
                    "args": step.args,
                    "reason": step.reason,
                    "ok": bool(result.get("ok")),
                    "error": result.get("error") or result.get("stderr"),
                },
            },
        )
    return results


def _render_tool_answer(plan: list[ToolStep], results: list[dict[str, Any]]) -> str:
    sections = [_render_plan(plan), "Tool output:"]
    sections.extend(render_tool_result(result) for result in results)
    return "\n\n".join(section for section in sections if section.strip())


async def run_workspace_query(
    query: str,
    uploads: list[dict[str, Any]] | None = None,
    emit_event: Emit | None = None,
    plan_only: bool = False,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = workspace_root()
    normalized = query.lower()

    await _emit(emit_event, {"type": "thinking", "data": "Using model-free workspace mode"})
    if conversation_context and conversation_context.get("message_count"):
        mode = "compressed" if conversation_context.get("compression_active") else "recent"
        await _emit(
            emit_event,
            {
                "type": "thinking",
                "data": (
                    f"Loaded {mode} conversation context: "
                    f"{conversation_context.get('message_count')} stored message(s), "
                    f"{conversation_context.get('estimated_context_tokens', 0)} estimated tokens"
                ),
            },
        )

    if uploads:
        await _emit(emit_event, {"type": "thinking", "data": f"Attached {len(uploads)} uploaded file(s)"})

    plan = plan_workspace_task(query, uploads=uploads or [])
    if plan_only:
        await _emit(emit_event, {"type": "thinking", "data": f"Plan mode inspected {len(plan)} local tool step(s)"})
        answer = _render_plan_only(query, plan)
    elif plan:
        await _emit(emit_event, {"type": "thinking", "data": f"Planned {len(plan)} plugin tool step(s)"})
        blocked = _blocked_steps(plan)
        approvals = _approval_steps(plan)
        if blocked:
            await _emit(emit_event, {"type": "thinking", "data": "Blocked by local tool permission policy"})
            for item in blocked:
                try:
                    await asyncio.to_thread(
                        store_tool_event,
                        str(item.get("plugin") or ""),
                        str(item.get("tool") or ""),
                        "blocked",
                        args=item.get("args") if isinstance(item.get("args"), dict) else {},
                        risk=str(item.get("risk") or "unknown"),
                        ok=False,
                        reason=str(item.get("reason") or "blocked by local permission policy"),
                    )
                except Exception:
                    pass
                await _emit(
                    emit_event,
                    {
                        "type": "tool",
                        "data": {
                            "state": "error",
                            "plugin": item.get("plugin"),
                            "tool": item.get("tool"),
                            "ok": False,
                            "error": "tool is blocked by local permission policy",
                        },
                    },
                )
            answer = _render_blocked_answer(plan, blocked)
        elif approvals:
            await _emit(emit_event, {"type": "thinking", "data": "Waiting for approval before high-impact local action"})
            for item in approvals:
                try:
                    await asyncio.to_thread(
                        store_tool_event,
                        str(item.get("plugin") or ""),
                        str(item.get("tool") or ""),
                        "approval_required",
                        args=item.get("args") if isinstance(item.get("args"), dict) else {},
                        risk=str(item.get("risk") or "unknown"),
                        reason=str(item.get("reason") or ""),
                    )
                except Exception:
                    pass
                await _emit(emit_event, {"type": "tool_approval_required", "data": item})
            answer = _render_approval_answer(plan, approvals)
        else:
            results = await _execute_plan(plan, emit_event)
            answer = _render_tool_answer(plan, results)
    if not plan and not plan_only:
        if re.search(r"\b(conversation|chat history|previous chat|what did we discuss|context summary)\b", normalized):
            answer = _conversation_summary(conversation_context)
        elif re.search(r"\b(models?|hardware|ram|gpu|cpu|download)\b", normalized):
            answer = _model_summary()
        elif "upload" in normalized or "attachment" in normalized:
            answer = _uploads_summary()
        elif normalized.startswith(("workspace", "repo summary")):
            answer = _workspace_summary(root)
        else:
            answer = _default_answer(query, root, conversation_context)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    result = {
        "query": query,
        "answer": answer,
        "sources": [],
        "plugins": [plugin.to_dict() for plugin in plugins_for_goal(query)],
        "uploads": uploads or [],
        "plan": plan_to_json(plan_workspace_task(query, uploads=uploads or [])),
        "elapsed_ms": elapsed_ms,
        "mode": "workspace",
    }
    try:
        result["run_id"] = store_run(query, "workspace", result)
    except Exception:
        result["run_id"] = None

    await _emit(emit_event, {"type": "token", "data": answer})
    await _emit(emit_event, {"type": "done", "data": {"elapsed_ms": elapsed_ms, "sources_used": 0, "run_id": result.get("run_id")}})

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a model-free workspace query")
    parser.add_argument("query", nargs="*", help="Query to handle")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args()
    query = " ".join(args.query).strip() or "workspace summary"
    result = asyncio.run(run_workspace_query(query))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["answer"])


if __name__ == "__main__":
    main()
