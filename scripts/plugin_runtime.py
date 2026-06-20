"""Executable built-in plugin tools.

The registry in plugin_manager.py is declarative; this module is the trusted
runtime for the built-in plugins. Third-party plugin manifests are discoverable,
but only tools implemented here are executed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.plugin_manager import get_plugin, list_plugins, tool_policy
from scripts.runtime_policy import ROOT, local_models_allowed, workspace_root
from scripts.upload_store import UPLOAD_DIR, list_uploads, save_uploads

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "outputs", "logs", "uploads"}
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
    ".csv",
    ".tsv",
    ".env",
}
MAX_READ_CHARS = 80_000
MAX_PREVIEW_CHARS = 80_000
MAX_PREVIEW_DIFF_LINES = 180
MAX_PREVIEW_DIFF_CHARS = 20_000
BROWSER_STATE_PATH = Path.home() / ".local-computer" / "browser_state.json"
MODEL_LAUNCH_BINS = {
    "llama-cli",
    "llama-server",
    "llamafile",
    "ollama",
}
MODEL_LAUNCH_MODULES = {
    "mlx_lm.generate",
    "mlx_lm.server",
    "llama_cpp.server",
}


class ToolError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonable_error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "type": exc.__class__.__name__}


def _safe_path(raw: str | None, *, base: Path | None = None, must_exist: bool = False) -> Path:
    base = (base or workspace_root()).resolve()
    raw = str(raw or ".").strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if base != path and base not in path.parents:
        raise ToolError(f"path escapes workspace: {path}")
    if must_exist and not path.exists():
        raise ToolError(f"path does not exist: {path}")
    return path


def _relative(path: Path, root: Path | None = None) -> str:
    root = root or workspace_root()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _is_text_path(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {".gitignore", ".env.example"}


def _truncate_text(value: str, limit: int = 2_000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n[truncated {len(value) - limit} chars]"


def _preview_diff(old: str, new: str, *, fromfile: str, tofile: str) -> tuple[str, bool]:
    diff_lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    truncated = False
    if len(diff_lines) > MAX_PREVIEW_DIFF_LINES:
        diff_lines = diff_lines[:MAX_PREVIEW_DIFF_LINES]
        truncated = True
    diff = "\n".join(diff_lines)
    if len(diff) > MAX_PREVIEW_DIFF_CHARS:
        diff = diff[:MAX_PREVIEW_DIFF_CHARS].rstrip()
        truncated = True
    if truncated:
        diff += "\n[diff truncated]"
    return diff, truncated


def _iter_files(root: Path, limit: int = 500, suffixes: set[str] | None = None) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if _is_skipped(path):
            continue
        if not path.is_file():
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        files.append(path)
        if len(files) >= limit:
            break
    return files


def _run_command(command: list[str], cwd: Path, timeout: float = 20.0) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-20_000:],
            "stderr": result.stderr[-8_000:],
            "command": command,
            "cwd": str(cwd),
        }
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(exc), "command": command, "cwd": str(cwd)}


def _basename(token: str) -> str:
    return Path(str(token)).name


def _command_tokens(args_or_command: dict[str, Any] | list[str] | str | None) -> list[str]:
    if isinstance(args_or_command, dict):
        raw = args_or_command.get("command")
    else:
        raw = args_or_command
    if isinstance(raw, list):
        return [str(part) for part in raw]
    try:
        return shlex.split(str(raw or ""))
    except ValueError:
        return [str(raw or "")]


def classify_shell_command(args_or_command: dict[str, Any] | list[str] | str | None) -> dict[str, Any]:
    tokens = _command_tokens(args_or_command)
    lowered = [token.lower() for token in tokens]
    base_tokens = [_basename(token).lower() for token in tokens]
    text = " ".join(lowered)
    warnings: list[str] = []
    category = "shell"
    severity = "normal"
    blocked = False
    requires_approval = False
    reason = ""

    if "ollama" in base_tokens or re.search(r"\bollama\s+(run|serve|create)\b", text):
        index = base_tokens.index("ollama") if "ollama" in base_tokens else -1
        subcommand = lowered[index + 1] if index >= 0 and index + 1 < len(lowered) else ""
        if not subcommand:
            match = re.search(r"\bollama\s+(run|serve|create)\b", text)
            subcommand = match.group(1) if match else ""
        if subcommand in {"run", "serve", "create"}:
            category = "model_launch"
            severity = "blocked" if not local_models_allowed() else "high"
            reason = f"`ollama {subcommand}` can load a local model into RAM/GPU."
    elif any(token in MODEL_LAUNCH_BINS - {"ollama"} for token in base_tokens):
        category = "model_launch"
        severity = "blocked" if not local_models_allowed() else "high"
        reason = "This command appears to launch a local model runtime."
    elif any(module in lowered for module in MODEL_LAUNCH_MODULES) or any(module in text for module in MODEL_LAUNCH_MODULES):
        category = "model_launch"
        severity = "blocked" if not local_models_allowed() else "high"
        reason = "This command appears to launch a local MLX or llama.cpp model runtime."
    elif "scripts/orchestrator.py" in text or "orchestrator.py" in base_tokens:
        category = "model_launch"
        severity = "blocked" if not local_models_allowed() else "high"
        reason = "The research orchestrator can trigger local model inference."

    if category == "model_launch" and not local_models_allowed():
        blocked = True
        warnings.append("Local models are disabled, so Locus will not start or load a model.")

    if any(token in {"sudo", "su"} for token in base_tokens):
        severity = "high" if severity == "normal" else severity
        category = "privileged" if category == "shell" else category
        warnings.append("This command requests elevated privileges.")
    rm_recursive = any(flag.startswith("-") and ("r" in flag or flag == "--recursive") for flag in lowered)
    rm_force = any(flag.startswith("-") and ("f" in flag or flag == "--force") for flag in lowered)
    if "rm" in base_tokens and rm_recursive and rm_force:
        severity = "high" if severity == "normal" else severity
        category = "destructive" if category == "shell" else category
        warnings.append("This command can recursively delete files.")
    if any(token in {"npm", "pnpm", "yarn", "pip", "pip3", "brew"} for token in base_tokens) and any(
        token in {"install", "add", "upgrade"} for token in lowered
    ):
        severity = "medium" if severity == "normal" else severity
        category = "installer" if category == "shell" else category
        warnings.append("This command may install or update software.")
    if any(token in {"serve", "server", "dev", "watch"} for token in lowered) or "http.server" in lowered:
        severity = "medium" if severity == "normal" else severity
        category = "long_running" if category == "shell" else category
        warnings.append("This command may keep running until stopped.")

    if severity in {"medium", "high"}:
        requires_approval = True

    detail = reason or "No specific shell safety issue detected."
    return {
        "category": category,
        "severity": severity,
        "blocked": blocked,
        "requires_approval": requires_approval,
        "approval_reason": "Shell safety review required." if requires_approval else "",
        "detail": detail,
        "warnings": warnings,
        "tokens": tokens[:20],
        "local_models_allowed": local_models_allowed(),
    }


def tool_metadata(plugin_id: str, tool_name: str) -> dict[str, Any]:
    plugin = get_plugin(plugin_id)
    implemented = tool_name in IMPLEMENTED_TOOLS.get(plugin_id, {})
    if not plugin:
        return {
            "plugin": plugin_id,
            "tool": tool_name,
            "name": tool_name,
            "risk": "unknown",
            "policy": "ask",
            "blocked": False,
            "description": "Unknown plugin tool.",
            "enabled": False,
            "implemented": implemented,
            "requires_approval": implemented,
        }

    manifest = next((item for item in plugin.tools if item.get("name") == tool_name), None)
    risk = str((manifest or {}).get("risk") or "unknown")
    policy = tool_policy(plugin_id, tool_name, risk)
    return {
        "plugin": plugin_id,
        "tool": tool_name,
        "name": tool_name,
        "risk": risk,
        "policy": policy,
        "blocked": policy == "block",
        "description": str((manifest or {}).get("description") or "Plugin tool."),
        "enabled": bool(plugin.enabled),
        "implemented": implemented,
        "requires_approval": bool(plugin.enabled and implemented and policy == "ask"),
    }


def tool_requires_approval(plugin_id: str, tool_name: str) -> bool:
    return bool(tool_metadata(plugin_id, tool_name).get("requires_approval"))


def tool_catalog() -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    for plugin in list_plugins(enabled_only=True):
        implemented = sorted(IMPLEMENTED_TOOLS.get(plugin.id, {}).keys())
        tools = []
        for item in plugin.tools:
            tool = dict(item)
            meta = tool_metadata(plugin.id, str(tool.get("name") or ""))
            tool["requires_approval"] = bool(meta.get("requires_approval"))
            tool["policy"] = str(meta.get("policy") or "allow")
            tool["blocked"] = bool(meta.get("blocked"))
            tools.append(tool)
        catalog[plugin.id] = {
            "name": plugin.name,
            "description": plugin.description,
            "tools": tools,
            "capabilities": plugin.capabilities,
            "implemented": implemented,
        }
    return catalog


def preview_tool(plugin_id: str, tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a non-executing preview for a tool approval sheet."""
    args = args or {}
    meta = tool_metadata(plugin_id, tool_name)
    preview: dict[str, Any] = {
        "ok": True,
        "plugin": plugin_id,
        "tool": tool_name,
        "kind": "generic",
        "summary": str(meta.get("description") or f"Run {plugin_id}.{tool_name}"),
        "warnings": [],
        "details": {"policy": meta.get("policy"), "risk": meta.get("risk")},
        "diff": "",
    }

    try:
        if plugin_id == "filesystem" and tool_name == "write_file":
            path = _safe_path(args.get("path"))
            content = str(args.get("content", ""))
            append = bool(args.get("append", False))
            content_bytes = len(content.encode("utf-8"))
            exists = path.exists()
            relative = _relative(path)
            warnings: list[str] = []
            details: dict[str, Any] = {
                "path": relative,
                "mode": "append" if append else "replace",
                "incoming_bytes": content_bytes,
                "backup": bool(args.get("backup", True)) and exists and not append,
                "exists": exists,
            }
            diff = ""

            if exists and path.is_dir():
                warnings.append("Target is a directory; the write would fail unless the path is changed.")
                summary = f"Attempt to write to directory {relative}"
            elif exists and path.is_file():
                old_size = path.stat().st_size
                details["existing_bytes"] = old_size
                summary = (
                    f"Append {content_bytes} bytes to {relative}"
                    if append
                    else f"Replace {relative} ({old_size} -> {content_bytes} bytes)"
                )
                if _is_text_path(path) and old_size <= MAX_PREVIEW_CHARS:
                    old = path.read_text(errors="replace")
                    new = old + content if append else content
                    diff, truncated = _preview_diff(old, new, fromfile=f"a/{relative}", tofile=f"b/{relative}")
                    if truncated:
                        warnings.append("Diff preview was truncated.")
                elif not _is_text_path(path):
                    warnings.append("Existing file is not text-like; diff omitted.")
                else:
                    warnings.append("Existing file is large; diff omitted.")
            else:
                summary = f"Create {relative} ({content_bytes} bytes)"
                if len(content) <= MAX_PREVIEW_CHARS:
                    diff, truncated = _preview_diff("", content, fromfile="/dev/null", tofile=f"b/{relative}")
                    if truncated:
                        warnings.append("Diff preview was truncated.")
                else:
                    warnings.append("New content is large; diff omitted.")

            preview.update(
                {
                    "kind": "file_write",
                    "summary": summary,
                    "warnings": warnings,
                    "details": details,
                    "diff": diff,
                }
            )
            return preview

        if plugin_id == "email" and tool_name == "draft_email":
            body = str(args.get("body") or "")
            to = str(args.get("to") or "").strip()
            subject = str(args.get("subject") or "").strip()
            preview.update(
                {
                    "kind": "email_draft",
                    "summary": f"Create local email draft to {to or 'unspecified recipient'}",
                    "warnings": ["Nothing will be sent. Locus only writes a local draft file."],
                    "details": {
                        "to": to or "unspecified",
                        "subject": subject or "(no subject)",
                        "body_preview": _truncate_text(body, 1_200),
                        "body_bytes": len(body.encode("utf-8")),
                        "sent": False,
                    },
                }
            )
            return preview

        if plugin_id == "shell" and tool_name == "run_command":
            shell_safety = classify_shell_command(args)
            command = args.get("command")
            if isinstance(command, list):
                command_text = " ".join(shlex.quote(str(part)) for part in command)
            else:
                command_text = str(command or "")
            preview.update(
                {
                    "kind": "shell_command",
                    "summary": f"Run shell command: {command_text or '(empty command)'}",
                    "warnings": shell_safety.get("warnings") or [],
                    "details": {
                        "cwd": str(args.get("cwd") or "."),
                        "timeout_seconds": args.get("timeout", 20),
                        "category": shell_safety.get("category"),
                        "severity": shell_safety.get("severity"),
                        "detail": shell_safety.get("detail"),
                        "blocked": shell_safety.get("blocked"),
                    },
                    "shell_safety": shell_safety,
                }
            )
            return preview

        if plugin_id == "uploads" and tool_name == "save_upload":
            files = args.get("files")
            if not isinstance(files, list):
                files = [args]
            total = 0
            names = []
            for item in files:
                if not isinstance(item, dict):
                    continue
                total += int(item.get("size") or 0)
                names.append(str(item.get("name") or "unnamed"))
            preview.update(
                {
                    "kind": "upload_save",
                    "summary": f"Store {len(names)} uploaded file(s) locally",
                    "details": {"files": names[:20], "total_bytes": total, "upload_dir": str(UPLOAD_DIR)},
                }
            )
            return preview

        return preview
    except Exception as exc:
        preview.update(
            {
                "ok": False,
                "summary": f"Could not build preview for {plugin_id}.{tool_name}",
                "warnings": [str(exc)],
                "details": {"error": str(exc), "type": exc.__class__.__name__},
            }
        )
        return preview


def list_files(args: dict[str, Any]) -> dict[str, Any]:
    root = _safe_path(args.get("path", "."), must_exist=True)
    if not root.is_dir():
        root = root.parent
    limit = max(1, min(int(args.get("limit", 80)), 500))
    files = _iter_files(root, limit=limit)
    payload = [
        {
            "path": _relative(path),
            "size": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        }
        for path in files
    ]
    return {"ok": True, "root": str(root), "count": len(payload), "files": payload}


def read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _safe_path(args.get("path"), must_exist=True)
    if not path.is_file():
        raise ToolError(f"not a file: {path}")
    if not _is_text_path(path):
        raise ToolError(f"refusing to preview non-text file in model-free mode: {path.name}")
    max_chars = max(1_000, min(int(args.get("max_chars", 12_000)), MAX_READ_CHARS))
    content = path.read_text(errors="replace")
    truncated = len(content) > max_chars
    return {
        "ok": True,
        "path": str(path),
        "relative_path": _relative(path),
        "content": content[:max_chars],
        "truncated": truncated,
        "size": path.stat().st_size,
    }


def write_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _safe_path(args.get("path"))
    content = str(args.get("content", ""))
    append = bool(args.get("append", False))
    make_backup = bool(args.get("backup", True))
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: str | None = None
    if path.exists() and make_backup and not append:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_suffix(path.suffix + f".bak-{stamp}")
        backup.write_bytes(path.read_bytes())
        backup_path = str(backup)
    if append:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
    else:
        path.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "relative_path": _relative(path),
        "bytes": len(content.encode("utf-8")),
        "appended": append,
        "backup_path": backup_path,
    }


def search_text(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("search query is required")
    root = _safe_path(args.get("path", "."), must_exist=True)
    if not root.is_dir():
        root = root.parent
    limit = max(1, min(int(args.get("limit", 40)), 200))
    case_sensitive = bool(args.get("case_sensitive", False))
    needle = query if case_sensitive else query.lower()
    hits: list[dict[str, Any]] = []
    for path in _iter_files(root, limit=1200):
        if not _is_text_path(path):
            continue
        try:
            for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    hits.append({"path": _relative(path), "line": lineno, "text": line.strip()[:240]})
                    break
        except Exception:
            continue
        if len(hits) >= limit:
            break
    return {"ok": True, "query": query, "count": len(hits), "hits": hits}


def run_shell(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("command")
    if isinstance(raw, list):
        command = [str(part) for part in raw]
    else:
        command = shlex.split(str(raw or ""))
    if not command:
        raise ToolError("command is required")
    timeout = max(1, min(float(args.get("timeout", 20)), 120))
    cwd = _safe_path(args.get("cwd", "."), must_exist=True)
    if cwd.is_file():
        cwd = cwd.parent
    shell_safety = classify_shell_command(command)
    if shell_safety.get("blocked"):
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": shell_safety.get("detail") or "Command blocked by shell safety policy.",
            "command": command,
            "cwd": str(cwd),
            "shell_safety": shell_safety,
        }
    result = _run_command(command, cwd, timeout=timeout)
    result["shell_safety"] = shell_safety
    return result


def git_status(args: dict[str, Any]) -> dict[str, Any]:
    root = _safe_path(args.get("cwd", "."), must_exist=True)
    return _run_command(["git", "status", "--short"], root, timeout=10)


def git_diff(args: dict[str, Any]) -> dict[str, Any]:
    root = _safe_path(args.get("cwd", "."), must_exist=True)
    command = ["git", "diff", "--", str(args["path"])] if args.get("path") else ["git", "diff"]
    return _run_command(command, root, timeout=15)


def git_log(args: dict[str, Any]) -> dict[str, Any]:
    root = _safe_path(args.get("cwd", "."), must_exist=True)
    limit = max(1, min(int(args.get("limit", 8)), 30))
    return _run_command(["git", "log", f"--max-count={limit}", "--oneline", "--decorate"], root, timeout=10)


def connector_status(args: dict[str, Any]) -> dict[str, Any]:
    plugin_id = str(args.get("plugin") or args.get("plugin_id") or "").strip()
    if plugin_id:
        plugin = get_plugin(plugin_id)
        if not plugin:
            raise ToolError(f"unknown plugin: {plugin_id}")
        return {"ok": True, "plugins": [plugin.to_dict()]}
    return {"ok": True, "plugins": [plugin.to_dict() for plugin in list_plugins(enabled_only=True)]}


def list_uploads_tool(args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit", 20)), 100))
    return {"ok": True, "uploads": list_uploads(limit=limit)}


def save_upload(args: dict[str, Any]) -> dict[str, Any]:
    files = args.get("files")
    if not isinstance(files, list):
        files = [args]
    return {"ok": True, "uploads": save_uploads(files)}


def read_upload(args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("path") or args.get("name") or "").strip()
    uploads = list_uploads(limit=200)
    selected = None
    for item in uploads:
        if target in {item.get("path"), item.get("name")} or Path(str(item.get("path"))).name == target:
            selected = item
            break
    if selected is None:
        raise ToolError(f"upload not found: {target}")
    path = Path(str(selected["path"])).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root != path and upload_root not in path.parents:
        raise ToolError("upload path escapes upload directory")
    if not _is_text_path(path):
        raise ToolError(f"upload is not a text-like file: {selected.get('name')}")
    max_chars = max(1_000, min(int(args.get("max_chars", 12_000)), MAX_READ_CHARS))
    content = path.read_text(errors="replace")
    return {
        "ok": True,
        "upload": selected,
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
    }


def draft_email(args: dict[str, Any]) -> dict[str, Any]:
    to = str(args.get("to") or "").strip()
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "").strip()
    if not subject and not body:
        raise ToolError("subject or body is required")
    drafts_dir = ROOT / "outputs" / "email_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    payload = {"to": to, "subject": subject, "body": body, "created_at": _utc_now(), "sent": False}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return {"ok": True, "draft_path": str(path), "draft": payload}


def search_mail(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("mail search query is required")
    mail_root = Path.home() / "Library" / "Mail"
    if not mail_root.exists():
        return {
            "ok": False,
            "error": "local Mail store was not found; configure email credentials or use a browser mail workflow",
            "query": query,
            "hits": [],
        }
    limit = max(1, min(int(args.get("limit", 20)), 80))
    hits: list[dict[str, Any]] = []
    needle = query.lower()
    for path in sorted(mail_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".emlx", ".txt"}:
            continue
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        lowered = text.lower()
        if needle not in lowered:
            continue
        excerpt_index = max(0, lowered.find(needle) - 120)
        hits.append({"path": str(path), "excerpt": text[excerpt_index : excerpt_index + 300].replace("\n", " ")})
        if len(hits) >= limit:
            break
    return {"ok": True, "query": query, "hits": hits}


def _github_cli_context(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    number = str(args.get("number") or args.get(kind) or args.get("id") or "").strip()
    repo = str(args.get("repo") or "").strip()
    if not number:
        raise ToolError(f"GitHub {kind} number is required")
    command = ["gh", kind, "view", number, "--json", "number,title,state,author,url,body"]
    if repo:
        command.extend(["--repo", repo])
    result = _run_command(command, workspace_root(), timeout=20)
    if not result.get("ok"):
        result["error"] = result.get("stderr") or f"gh {kind} view failed"
        return result
    try:
        payload = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {"ok": True, kind: payload, "command": command}


def github_issue_context(args: dict[str, Any]) -> dict[str, Any]:
    return _github_cli_context("issue", args)


def github_pr_context(args: dict[str, Any]) -> dict[str, Any]:
    return _github_cli_context("pr", args)


def _browser_state() -> dict[str, Any]:
    try:
        data = json.loads(BROWSER_STATE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_browser_state(state: dict[str, Any]) -> None:
    BROWSER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BROWSER_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _browser_url(args: dict[str, Any], *, required: bool = True) -> str:
    url = str(args.get("url") or args.get("page") or "").strip()
    if not url:
        url = str(_browser_state().get("url") or "").strip()
    if required and not url:
        raise ToolError("url is required until a browser page has been opened")
    return url


def _browser_text(page: Any, limit: int = 12_000) -> str:
    try:
        text = page.locator("body").inner_text(timeout=3_000)
    except Exception:
        text = page.content()
    return _truncate_text(str(text or ""), limit)


def _run_browser(args: dict[str, Any], action: Any) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise ToolError(f"Playwright is not installed: {exc}") from exc

    timeout_ms = max(2_000, min(int(args.get("timeout_ms", 15_000)), 60_000))
    headless = bool(args.get("headless", True))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            return action(page, timeout_ms)
        finally:
            browser.close()


def browser_open_page(args: dict[str, Any]) -> dict[str, Any]:
    url = _browser_url(args)

    def action(page: Any, timeout_ms: int) -> dict[str, Any]:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        current = page.url
        _write_browser_state({"url": current, "updated_at": _utc_now()})
        return {
            "ok": True,
            "url": current,
            "title": page.title(),
            "text": _browser_text(page, int(args.get("max_chars", 4_000))),
        }

    return _run_browser(args, action)


def browser_extract_text(args: dict[str, Any]) -> dict[str, Any]:
    url = _browser_url(args)

    def action(page: Any, timeout_ms: int) -> dict[str, Any]:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        current = page.url
        _write_browser_state({"url": current, "updated_at": _utc_now()})
        return {
            "ok": True,
            "url": current,
            "title": page.title(),
            "text": _browser_text(page, int(args.get("max_chars", 12_000))),
        }

    return _run_browser(args, action)


def browser_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    url = _browser_url(args)
    out_dir = ROOT / "outputs" / "browser_screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".png")

    def action(page: Any, timeout_ms: int) -> dict[str, Any]:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.screenshot(path=str(path), full_page=bool(args.get("full_page", True)))
        current = page.url
        _write_browser_state({"url": current, "updated_at": _utc_now()})
        return {
            "ok": True,
            "url": current,
            "title": page.title(),
            "path": str(path),
            "bytes": path.stat().st_size,
        }

    return _run_browser(args, action)


def browser_click(args: dict[str, Any]) -> dict[str, Any]:
    url = _browser_url(args)
    selector = str(args.get("selector") or "").strip()
    text = str(args.get("text") or args.get("target") or "").strip()
    if not selector and not text:
        raise ToolError("selector or text is required")

    def action(page: Any, timeout_ms: int) -> dict[str, Any]:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if selector:
            locator = page.locator(selector).first
        else:
            locator = page.get_by_text(text, exact=False).first
        locator.click(timeout=timeout_ms)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        current = page.url
        _write_browser_state({"url": current, "updated_at": _utc_now()})
        return {
            "ok": True,
            "url": current,
            "title": page.title(),
            "text": _browser_text(page, int(args.get("max_chars", 4_000))),
        }

    return _run_browser(args, action)


def browser_fill(args: dict[str, Any]) -> dict[str, Any]:
    url = _browser_url(args)
    value = str(args.get("value") or args.get("text") or "")
    selector = str(args.get("selector") or "").strip()
    label = str(args.get("label") or "").strip()
    placeholder = str(args.get("placeholder") or "").strip()
    if not selector and not label and not placeholder:
        raise ToolError("selector, label, or placeholder is required")

    def action(page: Any, timeout_ms: int) -> dict[str, Any]:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if selector:
            locator = page.locator(selector).first
        elif label:
            locator = page.get_by_label(label, exact=False).first
        else:
            locator = page.get_by_placeholder(placeholder, exact=False).first
        locator.fill(value, timeout=timeout_ms)
        if bool(args.get("submit", False)):
            locator.press("Enter", timeout=timeout_ms)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass
        current = page.url
        _write_browser_state({"url": current, "updated_at": _utc_now()})
        return {
            "ok": True,
            "url": current,
            "title": page.title(),
            "text": _browser_text(page, int(args.get("max_chars", 4_000))),
        }

    return _run_browser(args, action)


def memory_list_recent(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.long_term_memory import list_recent_queries

    limit = max(1, min(int(args.get("limit", 5)), 50))
    return {"ok": True, "memory": list_recent_queries(limit=limit)}


def memory_store(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.long_term_memory import store_query_answer

    query = str(args.get("query") or "").strip()
    answer = str(args.get("answer") or "").strip()
    sources = args.get("sources") if isinstance(args.get("sources"), list) else []
    memory_id = store_query_answer(query, answer, sources=sources)
    return {"ok": memory_id >= 0, "id": memory_id}


def memory_retrieve(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.long_term_memory import retrieve_relevant_answers

    query = str(args.get("query") or "").strip()
    top_k = max(1, min(int(args.get("top_k", 3)), 20))
    return {"ok": True, "matches": retrieve_relevant_answers(query, top_k=top_k)}


def model_recommendation_tool(args: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import replace

    from scripts.hardware_profile import GPUInfo, detect_hardware
    from scripts.model_selector import recommend_models

    profile = detect_hardware()
    updates: dict[str, Any] = {}
    if args.get("simulate_ram_gb") is not None:
        updates["ram_gb"] = float(args.get("simulate_ram_gb"))
    if args.get("simulate_os_family") in {"macos", "windows"}:
        os_family = str(args.get("simulate_os_family"))
        updates["os_family"] = os_family
        updates["os"] = "Windows" if os_family == "windows" else "Darwin"
        updates["supported_os"] = True
        updates["arch"] = "AMD64" if os_family == "windows" else "arm64"
    if args.get("simulate_gpu_vram_gb") not in (None, "") or args.get("simulate_gpu_name"):
        name = str(args.get("simulate_gpu_name") or ("NVIDIA GPU" if args.get("simulate_os_family") == "windows" else "Apple GPU"))
        vendor = "nvidia" if "nvidia" in name.lower() or "rtx" in name.lower() else "apple" if args.get("simulate_os_family") == "macos" else ""
        memory = float(args["simulate_gpu_vram_gb"]) if args.get("simulate_gpu_vram_gb") not in (None, "") else None
        updates["gpus"] = [GPUInfo(name=name, vendor=vendor, memory_gb=memory)]
    if updates:
        profile = replace(profile, **updates)
    recommendation = recommend_models(
        profile=profile,
        max_ram_override_gb=float(args["max_ram_gb"]) if args.get("max_ram_gb") not in (None, "") else None,
        available_ram_override_gb=(
            float(args["simulate_available_ram_gb"]) if args.get("simulate_available_ram_gb") not in (None, "") else None
        ),
    )
    return {"ok": True, "recommendation": recommendation}


def plan_task_tool(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.workspace_planner import plan_to_json, plan_workspace_task

    query = str(args.get("query") or args.get("task") or "").strip()
    uploads = args.get("uploads") if isinstance(args.get("uploads"), list) else []
    return {"ok": True, "query": query, "plan": plan_to_json(plan_workspace_task(query, uploads=uploads))}


def automation_list(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.automation_store import list_automations

    return {
        "ok": True,
        "automations": list_automations(
            limit=max(1, min(int(args.get("limit", 100)), 500)),
            include_disabled=bool(args.get("include_disabled", True)),
        ),
    }


def automation_create(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.automation_store import create_automation

    automation = create_automation(
        name=str(args.get("name") or "").strip(),
        prompt=str(args.get("prompt") or args.get("query") or "").strip(),
        schedule=str(args.get("schedule") or args.get("rrule") or "").strip(),
        workspace=str(args.get("workspace") or workspace_root()),
        enabled=bool(args.get("enabled", True)),
        metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
    )
    return {"ok": True, "automation": automation}


def automation_update(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.automation_store import update_automation

    automation_id = str(args.get("id") or args.get("automation_id") or "").strip()
    updates = {key: value for key, value in args.items() if key not in {"id", "automation_id"}}
    return {"ok": True, "automation": update_automation(automation_id, updates)}


def automation_delete(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.automation_store import delete_automation

    automation_id = str(args.get("id") or args.get("automation_id") or "").strip()
    return {"ok": True, **delete_automation(automation_id)}


def automation_run_due(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    from scripts.automation_runner import run_due

    results = asyncio.run(run_due(limit=max(1, min(int(args.get("limit", 20)), 100))))
    return {"ok": True, "ran": len(results), "results": results}


def workspace_index_tool(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.workspace_index import build_workspace_index, load_cached_index

    use_cache = bool(args.get("cached", False))
    index = load_cached_index() if use_cache else None
    if index is None:
        index = build_workspace_index(write_cache=True)
    return {"ok": True, "index": index}


def workspace_brief_tool(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.workspace_index import build_workspace_index, load_cached_index, workspace_brief

    index = load_cached_index() if bool(args.get("cached", False)) else None
    if index is None:
        index = build_workspace_index(write_cache=True)
    return {"ok": True, "index": index, "brief": workspace_brief(index)}


def todo_report(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.workspace_index import build_workspace_index, load_cached_index

    index = load_cached_index() if bool(args.get("cached", False)) else None
    if index is None:
        index = build_workspace_index(write_cache=True)
    return {"ok": True, "todos": index.get("todos", []), "indexed_at": index.get("indexed_at"), "root": index.get("root")}


def health_report_tool(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.workspace_index import build_workspace_index, load_cached_index, workspace_health_report

    index = load_cached_index() if bool(args.get("cached", False)) else None
    if index is None:
        index = build_workspace_index(write_cache=True)
    return {"ok": True, "index": index, "report": workspace_health_report(index)}


def run_history_tool(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.run_history import list_runs

    limit = max(1, min(int(args.get("limit", 20)), 100))
    return {"ok": True, "runs": list_runs(limit=limit)}


def tool_audit_tool(args: dict[str, Any]) -> dict[str, Any]:
    from scripts.run_history import list_tool_events

    limit = max(1, min(int(args.get("limit", 30)), 100))
    return {"ok": True, "events": list_tool_events(limit=limit)}


def plugin_diagnostics_tool(args: dict[str, Any]) -> dict[str, Any]:
    include_disabled = bool(args.get("include_disabled", True))
    plugins = list_plugins(enabled_only=not include_disabled)
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    summary = {
        "plugins": len(plugins),
        "enabled_plugins": 0,
        "declared_tools": 0,
        "implemented_declared_tools": 0,
        "pending_declared_tools": 0,
        "runtime_only_tools": 0,
        "connectors_ready": 0,
        "connectors_needing_setup": 0,
        "policies": {"allow": 0, "ask": 0, "block": 0},
    }

    for plugin in plugins:
        if plugin.enabled:
            summary["enabled_plugins"] += 1
        declared_tools = [str(tool.get("name") or "") for tool in plugin.tools if str(tool.get("name") or "")]
        implemented = set(IMPLEMENTED_TOOLS.get(plugin.id, {}).keys())
        declared_set = set(declared_tools)
        pending = sorted(declared_set - implemented)
        runtime_only = sorted(implemented - declared_set)
        tool_rows = []
        for item in plugin.tools:
            name = str(item.get("name") or "")
            if not name:
                continue
            meta = tool_metadata(plugin.id, name)
            policy = str(meta.get("policy") or "ask")
            if policy in summary["policies"]:
                summary["policies"][policy] += 1
            tool_rows.append(
                {
                    "name": name,
                    "risk": meta.get("risk"),
                    "policy": policy,
                    "implemented": bool(meta.get("implemented")),
                    "blocked": bool(meta.get("blocked")),
                    "requires_approval": bool(meta.get("requires_approval")),
                    "description": meta.get("description"),
                }
            )

        status = plugin.to_dict().get("status", {})
        if plugin.connector:
            if status.get("configured"):
                summary["connectors_ready"] += 1
            else:
                summary["connectors_needing_setup"] += 1

        summary["declared_tools"] += len(declared_tools)
        summary["implemented_declared_tools"] += len(declared_set & implemented)
        summary["pending_declared_tools"] += len(pending)
        summary["runtime_only_tools"] += len(runtime_only)

        health = "ready"
        if pending:
            health = "partial"
            warnings.append(f"{plugin.id}: {len(pending)} declared tool(s) are not implemented locally.")
        if runtime_only:
            warnings.append(f"{plugin.id}: {len(runtime_only)} runtime tool(s) are implemented but not declared in the manifest.")
        if plugin.connector and not status.get("configured"):
            health = "needs_setup" if health == "ready" else health
            warnings.append(f"{plugin.id}: connector needs setup ({status.get('detail', 'not configured')}).")
        if not plugin.enabled:
            health = "disabled"

        diagnostics.append(
            {
                "id": plugin.id,
                "name": plugin.name,
                "enabled": plugin.enabled,
                "health": health,
                "description": plugin.description,
                "path": plugin.path,
                "status": status,
                "declared_tools": declared_tools,
                "implemented_tools": sorted(implemented),
                "pending_tools": pending,
                "runtime_only_tools": runtime_only,
                "tools": tool_rows,
            }
        )

    return {"ok": True, "summary": summary, "plugins": diagnostics, "warnings": warnings[:80]}


IMPLEMENTED_TOOLS: dict[str, dict[str, Any]] = {
    "automations": {
        "list_automations": automation_list,
        "create_automation": automation_create,
        "update_automation": automation_update,
        "delete_automation": automation_delete,
        "run_due_automations": automation_run_due,
    },
    "filesystem": {
        "list_files": list_files,
        "read_file": read_file,
        "write_file": write_file,
        "search_text": search_text,
    },
    "shell": {"run_command": run_shell},
    "git": {"git_status": git_status, "git_diff": git_diff, "git_log": git_log},
    "github": {
        "connector_status": connector_status,
        "github_issue_context": github_issue_context,
        "github_pr_context": github_pr_context,
    },
    "email": {"connector_status": connector_status, "draft_email": draft_email, "search_mail": search_mail},
    "google_drive": {"connector_status": connector_status},
    "uploads": {"save_upload": save_upload, "list_uploads": list_uploads_tool, "read_upload": read_upload},
    "memory": {
        "connector_status": connector_status,
        "list_recent_queries": memory_list_recent,
        "retrieve_relevant_answers": memory_retrieve,
        "store_query_answer": memory_store,
    },
    "browser": {
        "connector_status": connector_status,
        "open_page": browser_open_page,
        "click": browser_click,
        "fill": browser_fill,
        "extract_text": browser_extract_text,
        "screenshot": browser_screenshot,
    },
    "workspace": {
        "connector_status": connector_status,
        "workspace_index": workspace_index_tool,
        "workspace_brief": workspace_brief_tool,
        "health_report": health_report_tool,
        "todo_report": todo_report,
        "plugin_diagnostics": plugin_diagnostics_tool,
        "model_recommendation": model_recommendation_tool,
        "plan_task": plan_task_tool,
        "run_history": run_history_tool,
        "tool_audit": tool_audit_tool,
    },
}


def execute_tool(plugin_id: str, tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    plugin = get_plugin(plugin_id)
    if not plugin or not plugin.enabled:
        return {"ok": False, "plugin": plugin_id, "tool": tool_name, "error": "plugin is not installed or enabled"}
    fn = IMPLEMENTED_TOOLS.get(plugin_id, {}).get(tool_name)
    if fn is None:
        return {"ok": False, "plugin": plugin_id, "tool": tool_name, "error": "tool is declared but not implemented locally"}
    meta = tool_metadata(plugin_id, tool_name)
    if meta.get("blocked"):
        return {
            "ok": False,
            "plugin": plugin_id,
            "tool": tool_name,
            "error": "tool is blocked by local permission policy",
            "policy": "block",
        }
    try:
        result = fn(args)
        result.setdefault("ok", True)
        result["plugin"] = plugin_id
        result["tool"] = tool_name
        return result
    except Exception as exc:
        result = _jsonable_error(exc)
        result["plugin"] = plugin_id
        result["tool"] = tool_name
        return result


def render_tool_result(result: dict[str, Any]) -> str:
    plugin = result.get("plugin", "")
    tool = result.get("tool", "")
    if not result.get("ok"):
        return f"`{plugin}.{tool}` failed: {result.get('error') or result.get('stderr') or 'unknown error'}"

    if tool == "list_files":
        lines = [f"`{plugin}.{tool}` found {result.get('count', 0)} files:"]
        lines.extend(f"- `{item['path']}` ({item['size']} bytes)" for item in result.get("files", [])[:80])
        return "\n".join(lines)
    if tool in {"read_file", "read_upload"}:
        label = result.get("relative_path") or result.get("upload", {}).get("name") or result.get("path")
        content = result.get("content", "")
        suffix = "\n\n[truncated]" if result.get("truncated") else ""
        return f"`{label}`:\n\n```text\n{content}{suffix}\n```"
    if tool == "search_text":
        hits = result.get("hits", [])
        if not hits:
            return f"No text hits found for `{result.get('query')}`."
        lines = [f"`{plugin}.{tool}` found {len(hits)} hit(s) for `{result.get('query')}`:"]
        lines.extend(f"- `{hit['path']}:{hit['line']}` {hit['text']}" for hit in hits)
        return "\n".join(lines)
    if tool in {"run_command", "git_status", "git_diff", "git_log"}:
        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        code = result.get("returncode")
        body = stdout if stdout else stderr if stderr else "[no output]"
        return f"`{plugin}.{tool}` exited with `{code}`:\n\n```text\n{body.strip()}\n```"
    if tool == "connector_status":
        plugins = result.get("plugins") or []
        if not plugins and isinstance(result.get("plugin"), dict):
            plugins = [result["plugin"]]
        lines = ["Connector status:", "", "| Plugin | Status | Detail |", "| --- | --- | --- |"]
        for item in plugins:
            status = item.get("status", {})
            lines.append(f"| `{item.get('id')}` | `{status.get('kind', 'local')}` | {status.get('detail', '')} |")
        return "\n".join(lines)
    if tool == "list_uploads":
        uploads = result.get("uploads", [])
        if not uploads:
            return "No uploaded files are stored yet."
        lines = ["Uploads:", "", "| File | Size | Path |", "| --- | ---: | --- |"]
        lines.extend(f"| `{item['name']}` | {item['size']} | `{item['path']}` |" for item in uploads)
        return "\n".join(lines)
    if tool == "draft_email":
        return f"Email draft saved at `{result.get('draft_path')}`. Nothing was sent."
    if plugin == "browser" and tool in {"open_page", "click", "fill", "extract_text"}:
        text = result.get("text") or ""
        return (
            f"Browser page: `{result.get('title') or '[untitled]'}`\n\n"
            f"URL: `{result.get('url')}`\n\n"
            f"```text\n{text}\n```"
        )
    if plugin == "browser" and tool == "screenshot":
        return (
            f"Browser screenshot saved at `{result.get('path')}` "
            f"({result.get('bytes', 0)} bytes) for `{result.get('url')}`."
        )
    if plugin == "memory" and tool == "list_recent_queries":
        memory = result.get("memory", [])
        if not memory:
            return "No local memory entries yet."
        lines = ["Recent local memory:", "", "| Time | Query |", "| --- | --- |"]
        for item in memory:
            lines.append(f"| `{item.get('created_at')}` | {str(item.get('query') or '')[:120]} |")
        return "\n".join(lines)
    if plugin == "memory" and tool == "retrieve_relevant_answers":
        matches = result.get("matches", [])
        if not matches:
            return "No relevant local memory entries found."
        lines = ["Relevant local memory:"]
        for item in matches:
            lines.append(
                f"- `{item.get('created_at')}` score `{float(item.get('similarity') or 0):.2f}`: "
                f"{str(item.get('query') or '')[:120]}"
            )
        return "\n".join(lines)
    if plugin == "memory" and tool == "store_query_answer":
        return f"Stored local memory entry `{result.get('id')}`."
    if plugin == "automations" and tool == "list_automations":
        automations = result.get("automations", [])
        if not automations:
            return "No local automations are defined yet."
        lines = ["Local automations:", "", "| ID | Enabled | Schedule | Prompt |", "| --- | --- | --- | --- |"]
        for item in automations:
            lines.append(
                f"| `{item.get('id')}` | `{bool(item.get('enabled', True))}` | "
                f"`{item.get('schedule')}` | {str(item.get('prompt') or '')[:100]} |"
            )
        return "\n".join(lines)
    if plugin == "automations" and tool == "create_automation":
        item = result.get("automation", {})
        return f"Created local automation `{item.get('id')}` on `{item.get('schedule')}`."
    if plugin == "automations" and tool == "update_automation":
        item = result.get("automation", {})
        return f"Updated local automation `{item.get('id')}`."
    if plugin == "automations" and tool == "delete_automation":
        return f"Deleted local automation `{result.get('id')}`."
    if plugin == "automations" and tool == "run_due_automations":
        ran = int(result.get("ran") or 0)
        if ran == 0:
            return "No due local automations to run."
        lines = [f"Ran `{ran}` local automation(s):"]
        for item in result.get("results", [])[:20]:
            automation = item.get("automation", {})
            lines.append(f"- `{automation.get('id')}` {'ok' if item.get('ok') else 'failed'}")
        return "\n".join(lines)
    if tool == "write_file":
        backup = f"\nBackup: `{result['backup_path']}`" if result.get("backup_path") else ""
        return f"Wrote `{result.get('relative_path')}` ({result.get('bytes')} bytes).{backup}"
    if tool == "workspace_brief":
        return result.get("brief", "Workspace brief unavailable.")
    if tool == "health_report":
        return result.get("report", "Workspace health report unavailable.")
    if tool == "workspace_index":
        index = result.get("index", {})
        lines = [
            f"Indexed workspace `{index.get('root')}`.",
            f"Files: `{index.get('file_count')}`; languages: "
            + ", ".join(f"{name} {count}" for name, count in list((index.get("languages") or {}).items())[:6]),
        ]
        cache_path = Path.home() / ".local-computer" / "workspaces" / f"{index.get('workspace_id')}.json"
        lines.append(f"Cache: `{cache_path}`")
        return "\n".join(lines)
    if tool == "todo_report":
        todos = result.get("todos", [])
        if not todos:
            return "No TODO/FIXME/HACK markers found."
        lines = [f"TODO/FIXME markers ({len(todos)} indexed):"]
        lines.extend(f"- `{item['path']}:{item['line']}` {item['tag']}: {item['text']}" for item in todos[:80])
        return "\n".join(lines)
    if tool == "plugin_diagnostics":
        summary = result.get("summary", {})
        lines = [
            "Plugin Diagnostics",
            "",
            f"- Plugins: `{summary.get('enabled_plugins', 0)}` enabled / `{summary.get('plugins', 0)}` installed",
            f"- Tools: `{summary.get('implemented_declared_tools', 0)}` implemented / `{summary.get('declared_tools', 0)}` declared",
            f"- Pending declared tools: `{summary.get('pending_declared_tools', 0)}`",
            f"- Runtime-only tools: `{summary.get('runtime_only_tools', 0)}`",
            f"- Connectors: `{summary.get('connectors_ready', 0)}` ready / `{summary.get('connectors_needing_setup', 0)}` need setup",
            "",
            "| Plugin | Health | Implemented | Pending | Connector |",
            "| --- | --- | ---: | ---: | --- |",
        ]
        for plugin_item in result.get("plugins", []):
            status = plugin_item.get("status") or {}
            lines.append(
                f"| `{plugin_item.get('id')}` | `{plugin_item.get('health')}` | "
                f"{len(plugin_item.get('implemented_tools') or [])} | {len(plugin_item.get('pending_tools') or [])} | "
                f"{status.get('detail', 'local capability')} |"
            )
        warnings = result.get("warnings") or []
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in warnings[:20])
        return "\n".join(lines)
    if tool == "model_recommendation":
        rec = result.get("recommendation", {})
        hardware = rec.get("hardware", {})
        budget = rec.get("resource_budget", {})
        acceleration = rec.get("gpu_acceleration", {})
        lines = [
            "Local Model Recommendation",
            "",
            "No models were downloaded or run.",
            "",
            f"- Tier: `{rec.get('tier', 'unknown')}`",
            f"- RAM: `{float(hardware.get('ram_gb') or 0):.1f} GB`",
            f"- Budget: max `{float(budget.get('max_ram_gb') or 0):.1f} GB`; usable `{float(budget.get('usable_for_models_gb') or 0):.1f} GB`",
            f"- GPU cap: `{float(budget.get('gpu_limit_pct') or 90):.0f}%`",
            f"- Acceleration: `{acceleration.get('tier') or acceleration.get('kind') or 'unknown'}`",
            "",
            "| Role | Model |",
            "| --- | --- |",
        ]
        for role, model in (rec.get("roles") or {}).items():
            lines.append(f"| `{role}` | `{model}` |")
        pull_plan = rec.get("pull_plan") or []
        if pull_plan:
            lines.extend(["", "Download plan:"])
            lines.extend(f"- `{item.get('command')}`" for item in pull_plan)
        warnings = rec.get("warnings") or []
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in warnings)
        return "\n".join(lines)
    if tool == "plan_task":
        plan = result.get("plan") or []
        if not plan:
            return f"Plan Mode\n\nNo executable local tool plan was found for `{result.get('query')}`."
        lines = ["Plan Mode", "", "No tools were executed.", ""]
        for idx, step in enumerate(plan, start=1):
            lines.append(f"{idx}. `{step.get('plugin')}.{step.get('tool')}` `{json.dumps(step.get('args') or {}, ensure_ascii=False)}`")
            lines.append(f"   Reason: {step.get('reason')}")
        return "\n".join(lines)
    if tool == "run_history":
        runs = result.get("runs", [])
        if not runs:
            return "No Locus runs have been recorded for this workspace yet."
        lines = ["Recent runs:", "", "| ID | Time | Mode | Query |", "| ---: | --- | --- | --- |"]
        for run in runs:
            lines.append(f"| {run['id']} | `{run['created_at']}` | `{run['mode']}` | {run['query'][:100]} |")
        return "\n".join(lines)
    if tool == "tool_audit":
        events = result.get("events", [])
        if not events:
            return "No local tool audit events have been recorded for this workspace yet."
        lines = ["Recent tool audit events:", "", "| Time | State | Tool | Risk | Result |", "| --- | --- | --- | --- | --- |"]
        for event in events[:80]:
            ok = event.get("ok")
            status = "n/a" if ok is None else "ok" if ok else "failed"
            lines.append(
                f"| `{event.get('created_at')}` | `{event.get('state')}` | "
                f"`{event.get('plugin')}.{event.get('tool')}` | `{event.get('risk') or 'read'}` | {status} |"
            )
        return "\n".join(lines)
    return f"`{plugin}.{tool}` result:\n\n```json\n{json.dumps(result, indent=2)}\n```"


TOOL_DIRECTIVE_RE = re.compile(r"^@tool\s+([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)(?:\s+(.+))?$", re.DOTALL)


def parse_tool_directive(text: str) -> tuple[str, str, dict[str, Any]] | None:
    match = TOOL_DIRECTIVE_RE.match((text or "").strip())
    if not match:
        return None
    plugin_id, tool_name, raw_args = match.groups()
    if not raw_args:
        return plugin_id, tool_name, {}
    try:
        parsed = json.loads(raw_args)
        if isinstance(parsed, dict):
            return plugin_id, tool_name, parsed
    except json.JSONDecodeError:
        pass
    return plugin_id, tool_name, {"value": raw_args}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a built-in Locus plugin tool")
    parser.add_argument("tool", nargs="?", help="Tool name like filesystem.search_text")
    parser.add_argument("args", nargs="?", default="{}", help="JSON arguments")
    parser.add_argument("--catalog", action="store_true", help="Print executable tool catalog")
    args = parser.parse_args()

    if args.catalog:
        print(json.dumps(tool_catalog(), indent=2))
        return
    if not args.tool or "." not in args.tool:
        raise SystemExit("Expected tool name like filesystem.search_text")
    plugin_id, tool_name = args.tool.split(".", 1)
    payload = json.loads(args.args)
    print(json.dumps(execute_tool(plugin_id, tool_name, payload), indent=2))


if __name__ == "__main__":
    main()
