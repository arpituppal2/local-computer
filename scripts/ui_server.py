#!/usr/bin/env python3
"""WebSocket + static dashboard server for Locus."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import mimetypes
import os
import sys
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol, serve

from scripts.model_selector import recommend_models
from scripts.conversation_history import force_compress_session, get_conversation_snapshot, get_context_bundle, get_session, store_turn
from scripts.plugin_manager import autonomy_mode_detail, registry_snapshot, set_autonomy_mode, set_plugin_enabled, set_tool_policy
from scripts.plugin_runtime import classify_shell_command, execute_tool, preview_tool, render_tool_result, tool_catalog, tool_metadata
from scripts.resource_policy import resource_budget
from scripts.run_history import list_runs, list_tool_events, store_run, store_tool_event
from scripts.runtime_policy import INTELLIGENCE_LEVELS, local_models_allowed, runtime_summary, update_runtime
from scripts.setup_manager import (
    accessibility_status,
    approve_model_downloads,
    decline_model_downloads,
    full_disk_access_status,
    open_accessibility_settings,
    open_full_disk_access_settings,
    run_app_setup,
    setup_status,
)
from scripts.upload_store import list_uploads, save_uploads
from scripts.workspace_index import build_workspace_index, load_cached_index

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_HTML = ROOT / "dashboard" / "index.html"
ASSETS_DIR = ROOT / "assets"

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

CLIENTS: set[WebSocketServerProtocol] = set()
CLIENT_UPLOADS: dict[int, list[dict[str, Any]]] = {}
RUN_LOCK = asyncio.Lock()
SETUP_LOCK = asyncio.Lock()
LAST_RESULT: dict[str, Any] = {}


def _ci_mode() -> bool:
    return os.getenv("CI", "").strip().lower() in {"1", "true", "yes"} or os.getenv(
        "GITHUB_ACTIONS", ""
    ).strip().lower() == "true"


async def _setup_status(running: bool | None = None) -> dict[str, Any]:
    status = await asyncio.to_thread(setup_status, lightweight=_ci_mode())
    if running is not None:
        status = {**status, "running": running}
    return status


def _safety_summary() -> dict[str, Any]:
    runtime = runtime_summary()
    budget = resource_budget()
    try:
        model_recommendation = recommend_models()
    except Exception:
        model_recommendation = {}
    acceleration = model_recommendation.get("gpu_acceleration", {}) if isinstance(model_recommendation, dict) else {}
    plugins = registry_snapshot()
    catalog = tool_catalog()
    config = plugins.get("config", {})
    autonomy_mode = str(config.get("autonomy_mode") or "guided")

    enabled_plugins = [plugin for plugin in plugins.get("plugins", []) if plugin.get("enabled")]
    risk_counts: dict[str, int] = {}
    policy_counts = {"allow": 0, "ask": 0, "block": 0}
    pending_tools: list[dict[str, str]] = []
    approval_required = 0
    for plugin in enabled_plugins:
        catalog_item = (catalog.get(plugin.get("id", ""), {}) or {})
        implemented = set(catalog_item.get("implemented", []))
        catalog_tools = {tool.get("name"): tool for tool in catalog_item.get("tools", [])}
        for tool in plugin.get("tools", []):
            risk = str(tool.get("risk") or "read")
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            if tool.get("name") not in implemented:
                pending_tools.append({"plugin": str(plugin.get("id")), "tool": str(tool.get("name"))})
            catalog_tool = catalog_tools.get(tool.get("name")) or {}
            policy = str(catalog_tool.get("policy") or ("allow" if risk == "read" else "ask"))
            if policy in policy_counts:
                policy_counts[policy] += 1
            if catalog_tool.get("requires_approval"):
                approval_required += 1

    connectors = []
    for plugin in enabled_plugins:
        connector = plugin.get("connector")
        if connector:
            connectors.append(
                {
                    "plugin": plugin.get("id"),
                    "name": plugin.get("name"),
                    "kind": plugin.get("status", {}).get("kind"),
                    "configured": bool(plugin.get("status", {}).get("configured")),
                    "detail": plugin.get("status", {}).get("detail", ""),
                }
            )

    pressure = str(getattr(budget, "memory_pressure", "unknown") or "unknown")
    available = getattr(budget, "available_ram_gb", None)
    ram_detail = f"{budget.max_ram_gb:.1f} GB max; {budget.reserved_system_gb:.1f} GB reserved"
    if available is not None:
        ram_detail += f"; {available:.1f} GB available now ({pressure})"
    if getattr(budget, "pressure_adjusted", False):
        ram_detail += f"; adjusted from {budget.configured_max_ram_gb:.1f} GB"

    posture = [
        {
            "id": "os",
            "title": "Operating System",
            "state": "done" if runtime.get("supported_os") else "error",
            "detail": (runtime.get("os") or {}).get("name", "Unknown") + (" supported" if runtime.get("supported_os") else " unsupported"),
        },
        {
            "id": "local_models",
            "title": "Local Models",
            "state": "warning" if runtime.get("allow_local_models") else "done",
            "detail": "enabled; local RAM/GPU pressure possible" if runtime.get("allow_local_models") else "off; no model loaded by default",
        },
        {
            "id": "external_ai",
            "title": "External AI",
            "state": "warning" if runtime.get("allow_external_ai") else "done",
            "detail": "enabled" if runtime.get("allow_external_ai") else "off by default",
        },
        {
            "id": "cloud_workers",
            "title": "Cloud Workers",
            "state": "warning" if runtime.get("allow_cloud_workers") else "done",
            "detail": "enabled" if runtime.get("allow_cloud_workers") else "off by default",
        },
        {
            "id": "gpu_cap",
            "title": "GPU Cap",
            "state": "done",
            "detail": f"{budget.gpu_limit_pct:.0f}% maximum",
        },
        {
            "id": "acceleration",
            "title": "Acceleration",
            "state": "warning" if acceleration.get("kind") == "cpu" else "done",
            "detail": str(acceleration.get("tier") or acceleration.get("kind") or "local"),
        },
        {
            "id": "model_guard",
            "title": "Model Launch Guard",
            "state": "warning" if runtime.get("allow_local_models") else "done",
            "detail": "model launch commands allowed" if runtime.get("allow_local_models") else "blocks shell commands that start local models",
        },
        {
            "id": "ram_budget",
            "title": "RAM Budget",
            "state": "warning" if budget.low_ram_mode or pressure in {"critical", "high"} else "done",
            "detail": ram_detail,
        },
        {
            "id": "plugins",
            "title": "Plugins",
            "state": "done",
            "detail": f"{plugins.get('enabled_count', 0)} enabled / {plugins.get('total_count', 0)} installed",
        },
        {
            "id": "autonomy",
            "title": "Autonomy",
            "state": "warning" if autonomy_mode == "full_local" else "done",
            "detail": autonomy_mode.replace("_", " "),
        },
        {
            "id": "approvals",
            "title": "Tool Approvals",
            "state": "done",
            "detail": f"{approval_required} tool(s) ask-gated",
        },
        {
            "id": "policy",
            "title": "Permission Policy",
            "state": "warning" if policy_counts.get("block") else "done",
            "detail": f"{policy_counts['allow']} allow; {policy_counts['ask']} ask; {policy_counts['block']} blocked",
        },
    ]

    return {
        "runtime": runtime,
        "resource_budget": asdict(budget),
        "plugins": {
            "enabled_count": plugins.get("enabled_count", 0),
            "total_count": plugins.get("total_count", 0),
            "autonomy_mode": autonomy_mode,
            "autonomy_detail": autonomy_mode_detail(autonomy_mode),
            "risk_counts": risk_counts,
            "pending_tools": pending_tools[:40],
            "approval_required": approval_required,
            "policy_counts": policy_counts,
            "connectors": connectors,
        },
        "tool_audit": {"recent": list_tool_events(limit=8)},
        "posture": posture,
        "warnings": list(budget.warnings),
    }


def _voice_recommendation() -> dict[str, Any]:
    budget = resource_budget()
    total_ram = float(getattr(budget, "total_ram_gb", 0) or 0)
    max_ram = float(getattr(budget, "max_ram_gb", 0) or 0)
    low_ram = bool(getattr(budget, "low_ram_mode", False) or max_ram <= 8.5 or total_ram <= 8.5)
    if low_ram:
        stt_model = "whisper.cpp tiny.en"
        stt_ram = 0.35
        notes = "8 GB mode: use push-to-talk, tiny transcription, and system voices."
    elif max_ram <= 20:
        stt_model = "whisper.cpp base.en"
        stt_ram = 0.75
        notes = "Balanced mode: base transcription with one local task at a time."
    else:
        stt_model = "whisper.cpp small.en"
        stt_ram = 1.8
        notes = "High-memory mode: small transcription is acceptable when the user enables models."
    return {
        "enabled_by_default": False,
        "status": "preview",
        "transcription": {
            "recommended_model": stt_model,
            "estimated_ram_gb": stt_ram,
            "runs_only_when_enabled": True,
        },
        "speech": {
            "recommended_engine": "macOS system voice",
            "estimated_ram_gb": 0.15,
            "runs_only_when_voice_mode_is_active": True,
        },
        "resource_budget": asdict(budget),
        "notes": notes,
    }


async def _send_tool_audit(ws: WebSocketServerProtocol | None = None, *, limit: int = 12) -> None:
    payload = {"type": "tool_audit", "data": {"events": list_tool_events(limit=limit)}}
    if ws is None:
        await _broadcast(payload)
    else:
        await ws.send(json.dumps(payload, ensure_ascii=False))


async def _record_tool_event(**event: Any) -> None:
    try:
        await asyncio.to_thread(store_tool_event, **event)
    except Exception:
        logging.exception("Tool audit logging failed")


async def _broadcast(message: dict[str, Any]) -> None:
    if not CLIENTS:
        return
    payload = json.dumps(message, ensure_ascii=False)
    stale: list[WebSocketServerProtocol] = []
    for client in CLIENTS:
        try:
            await client.send(payload)
        except ConnectionClosed:
            stale.append(client)
    for client in stale:
        CLIENTS.discard(client)


async def _send_memory_snapshot(ws: WebSocketServerProtocol) -> None:
    try:
        from scripts.long_term_memory import list_recent_queries

        recent = list_recent_queries(limit=5)
    except Exception:
        recent = []
    await ws.send(json.dumps({"type": "memory", "data": recent}, ensure_ascii=False))


async def _send_conversation_snapshot(ws: WebSocketServerProtocol | None = None) -> None:
    try:
        snapshot = await asyncio.to_thread(get_conversation_snapshot)
    except Exception as exc:
        logging.exception("Conversation snapshot failed")
        snapshot = {"ok": False, "error": str(exc), "sessions": [], "context": {}}
    payload = {"type": "conversation", "data": snapshot}
    if ws is None:
        await _broadcast(payload)
    else:
        await ws.send(json.dumps(payload, ensure_ascii=False))


async def _send_setup_status(ws: WebSocketServerProtocol) -> None:
    await ws.send(json.dumps({"type": "setup_status", "data": await _setup_status()}, ensure_ascii=False))


async def _run_setup_flow() -> None:
    if SETUP_LOCK.locked():
        await _broadcast({"type": "setup_log", "data": {"state": "running", "detail": "setup already running"}})
        return

    async with SETUP_LOCK:
        loop = asyncio.get_running_loop()

        def emit(event: dict[str, Any]) -> None:
            future = asyncio.run_coroutine_threadsafe(
                _broadcast({"type": "setup_step", "data": event}),
                loop,
            )
            future.result(timeout=10)

        await _broadcast({"type": "setup_status", "data": await _setup_status(running=True)})
        try:
            status = await asyncio.to_thread(run_app_setup, emit)
            await _broadcast({"type": "setup_done", "data": status})
            await _broadcast({"type": "setup_status", "data": {**status, "running": False}})
        except Exception as exc:
            logging.exception("Setup failed")
            await _broadcast({"type": "setup_error", "data": {"error": str(exc)}})
            await _broadcast({"type": "setup_status", "data": await _setup_status(running=False)})


async def _run_query(
    query: str,
    uploads: list[dict[str, Any]] | None = None,
    *,
    plan_only: bool = False,
    intelligence_level: str = "medium",
    learn_step_by_step: bool = False,
) -> None:
    global LAST_RESULT

    async with RUN_LOCK:
        await _broadcast({"type": "status", "data": {"state": "running"}})
        thinking_lines: list[str] = []
        conversation_context: dict[str, Any] = {"message_count": 0, "active_message_count": 0, "estimated_context_tokens": 0}

        async def _emit(event: dict[str, Any]) -> None:
            if event.get("type") == "thinking":
                thinking_lines.append(str(event.get("data") or ""))
            await _broadcast(event)

        try:
            try:
                conversation_context = await asyncio.to_thread(get_context_bundle)
            except Exception as exc:
                logging.exception("Conversation context load failed")
                thinking_lines.append(f"Conversation context unavailable: {exc}")
                await _broadcast({"type": "thinking", "data": f"Conversation context unavailable: {exc}"})
            intelligence_line = (
                f"Intelligence: {intelligence_level}"
                f"{' · Learn Step-by-Step on' if learn_step_by_step else ''}"
            )
            thinking_lines.append(intelligence_line)
            await _broadcast(
                {
                    "type": "thinking",
                    "data": intelligence_line,
                }
            )
            if conversation_context.get("message_count"):
                context_line = (
                    "Conversation context ready: "
                    f"{conversation_context.get('message_count')} stored message(s), "
                    f"{conversation_context.get('active_message_count')} active after compression"
                )
                thinking_lines.append(context_line)
                await _broadcast({"type": "thinking", "data": context_line})
            if plan_only:
                from scripts.workspace_agent import run_workspace_query

                result = await run_workspace_query(
                    query,
                    uploads=uploads or [],
                    emit_event=_emit,
                    plan_only=True,
                    conversation_context=conversation_context,
                )
            elif local_models_allowed():
                from scripts.long_term_memory import get_cached_answer
                from scripts.orchestrator import run_research_query

                cached = get_cached_answer(query)
                if cached:
                    await _broadcast({"type": "thinking", "data": "Found cached answer, refreshing with live research"})
                research_query = query
                if conversation_context.get("context_text"):
                    research_query = (
                        "Local conversation context, already compressed when needed:\n"
                        f"{conversation_context['context_text']}\n\n"
                        "Current user request:\n"
                        f"{query}"
                    )
                result = await run_research_query(research_query, emit_event=_emit)
                if isinstance(result, dict):
                    result["query"] = query
                try:
                    result["run_id"] = store_run(query, "research", result)
                except Exception:
                    result["run_id"] = None
            else:
                from scripts.workspace_agent import run_workspace_query

                result = await run_workspace_query(
                    query,
                    uploads=uploads or [],
                    emit_event=_emit,
                    conversation_context=conversation_context,
                )
            LAST_RESULT = result
            await _broadcast({"type": "status", "data": {"state": "idle"}})
            try:
                answer = str(result.get("answer") or "") if isinstance(result, dict) else str(result)
                sources = result.get("sources") if isinstance(result, dict) and isinstance(result.get("sources"), list) else []
                conversation = await asyncio.to_thread(
                    store_turn,
                    query,
                    answer,
                    thinking=thinking_lines,
                    sources=sources,
                    metadata={
                        "run_id": result.get("run_id") if isinstance(result, dict) else None,
                        "mode": result.get("mode") if isinstance(result, dict) else "unknown",
                        "plan_only": plan_only,
                        "intelligence_level": intelligence_level,
                        "learn_step_by_step": learn_step_by_step,
                        "uploads": len(uploads or []),
                    },
                )
                await _broadcast({"type": "conversation", "data": conversation})
                if conversation.get("compressed_now"):
                    await _broadcast(
                        {
                            "type": "thinking",
                            "data": "Compressed older conversation context locally; recent turns stay active.",
                        }
                    )
            except Exception:
                logging.exception("Conversation history store failed")
            try:
                from scripts.long_term_memory import list_recent_queries

                memory = list_recent_queries(limit=5)
            except Exception:
                memory = []
            await _broadcast({"type": "memory", "data": memory})
            await _send_tool_audit()
        except Exception as exc:
            logging.exception("Query execution failed")
            await _broadcast({"type": "status", "data": {"state": "error"}})
            await _broadcast({"type": "thinking", "data": f"Error: {exc}"})
            await _broadcast({"type": "done", "data": {"elapsed_ms": 0, "sources_used": 0}})


async def _handle_ws(ws: WebSocketServerProtocol, path: str) -> None:
    if path not in ("/", "/stream"):
        await ws.close(code=1008, reason="Unsupported endpoint")
        return

    CLIENTS.add(ws)
    try:
        try:
            await ws.send(json.dumps({"type": "status", "data": {"state": "idle"}}, ensure_ascii=False))
            await _send_memory_snapshot(ws)
            await _send_conversation_snapshot(ws)
            await _send_setup_status(ws)
        except ConnectionClosed:
            return

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = str(msg.get("type", "")).strip().lower()
            if msg_type == "query":
                payload = msg.get("data", "")
                uploads = CLIENT_UPLOADS.get(id(ws), [])
                if isinstance(payload, dict):
                    query = str(payload.get("query") or payload.get("text") or "").strip()
                    plan_only = bool(payload.get("plan_only", False))
                    intelligence_level = str(payload.get("intelligence_level") or "medium").strip().lower()
                    if intelligence_level not in INTELLIGENCE_LEVELS:
                        intelligence_level = "medium"
                    learn_step_by_step = bool(payload.get("learn_step_by_step", False))
                    payload_uploads = payload.get("uploads")
                    if isinstance(payload_uploads, list):
                        uploads = payload_uploads
                else:
                    query = str(payload).strip()
                    plan_only = False
                    intelligence_level = "medium"
                    learn_step_by_step = False
                if not query:
                    await ws.send(json.dumps({"type": "thinking", "data": "Query cannot be empty"}))
                    continue
                asyncio.create_task(
                    _run_query(
                        query,
                        uploads=uploads,
                        plan_only=plan_only,
                        intelligence_level=intelligence_level,
                        learn_step_by_step=learn_step_by_step,
                    )
                )
            elif msg_type == "upload":
                payload = msg.get("data") or {}
                files = payload.get("files") if isinstance(payload, dict) else []
                if not isinstance(files, list):
                    files = []
                saved = save_uploads(files)
                CLIENT_UPLOADS.setdefault(id(ws), []).extend([item for item in saved if item.get("ok")])
                await ws.send(json.dumps({"type": "upload", "data": saved}, ensure_ascii=False))
            elif msg_type == "tool":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                plugin = str(payload.get("plugin") or "")
                tool = str(payload.get("tool") or "")
                args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                meta = tool_metadata(plugin, tool)
                shell_safety = classify_shell_command(args) if plugin == "shell" and tool == "run_command" else None
                if shell_safety:
                    meta["shell_safety"] = shell_safety
                preview = await asyncio.to_thread(preview_tool, plugin, tool, args)
                if shell_safety and not preview.get("shell_safety"):
                    preview["shell_safety"] = shell_safety
                meta["preview"] = preview
                if meta.get("blocked") or (shell_safety and shell_safety.get("blocked")):
                    result = await asyncio.to_thread(execute_tool, plugin, tool, args)
                    await _record_tool_event(
                        plugin=plugin,
                        tool=tool,
                        state="blocked",
                        args=args,
                        risk=str(meta.get("risk") or "unknown"),
                        ok=False,
                        result=result,
                        reason=str(payload.get("reason") or result.get("error") or result.get("stderr") or "blocked by local safety policy"),
                    )
                    await ws.send(
                        json.dumps(
                            {
                                "type": "tool",
                                "data": {
                                    "state": "error",
                                    "plugin": plugin,
                                    "tool": tool,
                                    "ok": False,
                                    "error": result.get("error") or "tool blocked",
                                },
                            },
                            ensure_ascii=False,
                        )
                    )
                    await ws.send(json.dumps({"type": "token", "data": render_tool_result(result)}, ensure_ascii=False))
                    await ws.send(json.dumps({"type": "done", "data": {"elapsed_ms": 0, "sources_used": 0}}, ensure_ascii=False))
                    await _send_tool_audit(ws)
                    continue
                needs_approval = bool(meta.get("requires_approval") or (shell_safety and shell_safety.get("requires_approval")))
                if needs_approval and payload.get("confirmed") is not True:
                    await _record_tool_event(
                        plugin=plugin,
                        tool=tool,
                        state="approval_required",
                        args=args,
                        risk=str(meta.get("risk") or "unknown"),
                        reason=str(payload.get("reason") or (shell_safety or {}).get("approval_reason") or ""),
                    )
                    await ws.send(
                        json.dumps(
                            {
                                "type": "tool_approval_required",
                                "data": {
                                    **meta,
                                    "args": args,
                                    "reason": str(payload.get("reason") or (shell_safety or {}).get("approval_reason") or ""),
                                },
                            },
                            ensure_ascii=False,
                        )
                    )
                    await _send_tool_audit(ws)
                    continue
                if needs_approval and payload.get("confirmed") is True:
                    await _record_tool_event(
                        plugin=plugin,
                        tool=tool,
                        state="approved",
                        args=args,
                        risk=str(meta.get("risk") or "unknown"),
                        reason=str(payload.get("reason") or "approved once"),
                    )
                await ws.send(
                    json.dumps(
                        {"type": "tool", "data": {"state": "running", "plugin": plugin, "tool": tool, "args": args}},
                        ensure_ascii=False,
                    )
                )
                result = await asyncio.to_thread(execute_tool, plugin, tool, args)
                await _record_tool_event(
                    plugin=plugin,
                    tool=tool,
                    state="completed",
                    args=args,
                    risk=str(meta.get("risk") or "unknown"),
                    ok=bool(result.get("ok")),
                    result=result,
                    reason=str(payload.get("reason") or ""),
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": "tool",
                            "data": {
                                "state": "done" if result.get("ok") else "error",
                                "plugin": plugin,
                                "tool": tool,
                                "ok": bool(result.get("ok")),
                                "error": result.get("error") or result.get("stderr"),
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                await ws.send(json.dumps({"type": "token", "data": render_tool_result(result)}, ensure_ascii=False))
                await ws.send(json.dumps({"type": "done", "data": {"elapsed_ms": 0, "sources_used": 0}}, ensure_ascii=False))
                await _send_tool_audit(ws)
            elif msg_type == "tool_audit":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                state = str(payload.get("state") or "noted")
                plugin = str(payload.get("plugin") or "")
                tool = str(payload.get("tool") or "")
                args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                if plugin and tool:
                    meta = tool_metadata(plugin, tool)
                    await _record_tool_event(
                        plugin=plugin,
                        tool=tool,
                        state=state,
                        args=args,
                        risk=str(payload.get("risk") or meta.get("risk") or "unknown"),
                        ok=None,
                        reason=str(payload.get("reason") or ""),
                    )
                await _send_tool_audit(ws)
            elif msg_type == "settings":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                updates: dict[str, Any] = {}
                try:
                    if "max_ram_gb" in payload:
                        raw = payload.get("max_ram_gb")
                        if raw in (None, ""):
                            updates["max_ram_gb"] = None
                        else:
                            value = float(raw)
                            if not math.isfinite(value) or value <= 0:
                                raise ValueError("max_ram_gb must be a positive number")
                            updates["max_ram_gb"] = max(2.0, value)
                    if "auto_select_models" in payload:
                        updates["auto_select_models"] = bool(payload.get("auto_select_models"))
                    if "max_gpu_percent" in payload:
                        value = float(payload.get("max_gpu_percent"))
                        if not math.isfinite(value):
                            raise ValueError("max_gpu_percent must be a number")
                        if value < 50 or value > 99:
                            raise ValueError("max_gpu_percent must be between 50 and 99")
                        updates["max_gpu_percent"] = round(value, 1)
                    if "intelligence_level" in payload:
                        value = str(payload.get("intelligence_level") or "medium").strip().lower()
                        if value not in INTELLIGENCE_LEVELS:
                            raise ValueError("intelligence_level must be xlow, low, medium, high, xhigh, or max")
                        updates["intelligence_level"] = value
                    if "learn_step_by_step" in payload:
                        updates["learn_step_by_step"] = bool(payload.get("learn_step_by_step"))
                    if updates:
                        update_runtime(updates)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "settings",
                                "data": {
                                    "runtime": runtime_summary(),
                                    "recommendation": recommend_models(),
                                    "resource_budget": asdict(resource_budget()),
                                },
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception as exc:
                    await ws.send(json.dumps({"type": "thinking", "data": f"Settings update failed: {exc}"}))
            elif msg_type == "plugin_setting":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                plugin_id = str(payload.get("plugin") or payload.get("plugin_id") or "").strip()
                enabled = bool(payload.get("enabled"))
                try:
                    snapshot = await asyncio.to_thread(set_plugin_enabled, plugin_id, enabled)
                    await ws.send(
                        json.dumps(
                            {"type": "plugin_setting", "data": {"plugin": plugin_id, "enabled": enabled, "snapshot": snapshot}},
                            ensure_ascii=False,
                        )
                    )
                except Exception as exc:
                    await ws.send(json.dumps({"type": "plugin_setting", "data": {"plugin": plugin_id, "error": str(exc)}}))
            elif msg_type == "tool_policy":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                plugin_id = str(payload.get("plugin") or payload.get("plugin_id") or "").strip()
                tool = str(payload.get("tool") or "").strip()
                policy = str(payload.get("policy") or "default").strip().lower()
                try:
                    snapshot = await asyncio.to_thread(set_tool_policy, plugin_id, tool, policy)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "tool_policy",
                                "data": {
                                    "plugin": plugin_id,
                                    "tool": tool,
                                    "policy": policy,
                                    "snapshot": snapshot,
                                    "tools": tool_catalog(),
                                    "safety": _safety_summary(),
                                },
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception as exc:
                    await ws.send(
                        json.dumps(
                            {"type": "tool_policy", "data": {"plugin": plugin_id, "tool": tool, "policy": policy, "error": str(exc)}},
                            ensure_ascii=False,
                        )
                    )
            elif msg_type == "autonomy_mode":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                mode = str(payload.get("mode") or "guided").strip().lower()
                try:
                    snapshot = await asyncio.to_thread(set_autonomy_mode, mode)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "autonomy_mode",
                                "data": {
                                    "mode": mode,
                                    "snapshot": snapshot,
                                    "tools": tool_catalog(),
                                    "safety": _safety_summary(),
                                },
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception as exc:
                    await ws.send(json.dumps({"type": "autonomy_mode", "data": {"mode": mode, "error": str(exc)}}))
            elif msg_type == "setup":
                asyncio.create_task(_run_setup_flow())
            elif msg_type == "model_downloads":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                action = str(payload.get("action") or "plan").strip().lower()
                try:
                    if action in {"approve", "download", "start"}:
                        result = await asyncio.to_thread(approve_model_downloads)
                        await ws.send(
                            json.dumps({"type": "model_downloads", "data": {**result, "action": "approve"}}, ensure_ascii=False)
                        )
                        asyncio.create_task(_run_setup_flow())
                    elif action in {"skip", "decline"}:
                        result = await asyncio.to_thread(decline_model_downloads)
                        await ws.send(json.dumps({"type": "model_downloads", "data": {**result, "action": "skip"}}, ensure_ascii=False))
                        await ws.send(json.dumps({"type": "setup_status", "data": await _setup_status()}, ensure_ascii=False))
                    else:
                        await ws.send(json.dumps({"type": "setup_status", "data": await _setup_status()}, ensure_ascii=False))
                except Exception as exc:
                    await ws.send(
                        json.dumps({"type": "model_downloads", "data": {"ok": False, "action": action, "error": str(exc)}}, ensure_ascii=False)
                    )
                    await ws.send(json.dumps({"type": "setup_status", "data": await _setup_status()}, ensure_ascii=False))
            elif msg_type == "permission":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                action = str(payload.get("action") or "check")
                if action == "open_full_disk_access":
                    result = await asyncio.to_thread(open_full_disk_access_settings)
                    await ws.send(json.dumps({"type": "permission", "data": {"action": action, **result}}, ensure_ascii=False))
                elif action == "open_accessibility":
                    result = await asyncio.to_thread(open_accessibility_settings)
                    await ws.send(json.dumps({"type": "permission", "data": {"action": action, **result}}, ensure_ascii=False))
                if action in {"open_accessibility", "check_accessibility", "check_all"}:
                    status = await asyncio.to_thread(accessibility_status)
                    await ws.send(json.dumps({"type": "permission", "data": {"action": "check_accessibility", **status}}, ensure_ascii=False))
                if action in {"open_full_disk_access", "check_full_disk_access", "check_all", "check"}:
                    status = await asyncio.to_thread(full_disk_access_status)
                    await ws.send(json.dumps({"type": "permission", "data": {"action": "check_full_disk_access", **status}}, ensure_ascii=False))
            elif msg_type == "ping":
                await ws.send(json.dumps({"type": "pong", "data": "ok"}))
            elif msg_type == "last_result":
                await ws.send(json.dumps({"type": "result", "data": LAST_RESULT}, ensure_ascii=False))
            elif msg_type == "memory":
                await _send_memory_snapshot(ws)
            elif msg_type == "conversation":
                await _send_conversation_snapshot(ws)
            elif msg_type == "conversation_load":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                try:
                    session_id = int(payload.get("session_id") or payload.get("id"))
                    session = await asyncio.to_thread(get_session, session_id)
                    await ws.send(json.dumps({"type": "conversation_session", "data": session}, ensure_ascii=False))
                except Exception as exc:
                    await ws.send(json.dumps({"type": "conversation_session", "data": {"ok": False, "error": str(exc)}}))
            elif msg_type == "conversation_compact":
                payload = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                try:
                    session_id = int(payload.get("session_id") or 0) or None
                    result = await asyncio.to_thread(force_compress_session, session_id)
                    await ws.send(json.dumps({"type": "conversation_compacted", "data": result}, ensure_ascii=False))
                    await _send_conversation_snapshot()
                except Exception as exc:
                    await ws.send(json.dumps({"type": "conversation_compacted", "data": {"ok": False, "error": str(exc)}}))
    finally:
        CLIENTS.discard(ws)
        CLIENT_UPLOADS.pop(id(ws), None)


async def _process_request(path: str, request_headers):
    if str(request_headers.get("Upgrade", "")).lower() == "websocket":
        return None

    request_path = path.split("?", 1)[0] or "/"

    if request_path in ("/", "/index.html"):
        if DASHBOARD_HTML.exists():
            body = DASHBOARD_HTML.read_bytes()
            return (
                HTTPStatus.OK,
                [
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                ],
                body,
            )
        body = b"dashboard/index.html not found"
        return (
            HTTPStatus.NOT_FOUND,
            [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
            body,
        )

    if request_path.startswith("/assets/"):
        target = (ROOT / unquote(request_path.lstrip("/"))).resolve()
        try:
            target.relative_to(ASSETS_DIR)
        except ValueError:
            target = ASSETS_DIR / "__missing__"
        if target.is_file():
            body = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return (
                HTTPStatus.OK,
                [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "public, max-age=3600"),
                ],
                body,
            )
        body = b"asset not found"
        return (
            HTTPStatus.NOT_FOUND,
            [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
            body,
        )

    if request_path == "/api/ping":
        body = json.dumps({"ok": True, "runtime": runtime_summary()}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ],
            body,
        )

    if request_path == "/api/runtime":
        payload = json.dumps({"runtime": runtime_summary(), "resource_budget": asdict(resource_budget())}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/safety":
        payload = json.dumps(_safety_summary()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/setup":
        payload = json.dumps(await _setup_status()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/permissions":
        full_disk_access, accessibility = await asyncio.gather(
            asyncio.to_thread(full_disk_access_status),
            asyncio.to_thread(accessibility_status),
        )
        payload = json.dumps(
            {
                "full_disk_access": full_disk_access,
                "accessibility": accessibility,
            }
        ).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/plugins":
        payload = json.dumps(registry_snapshot()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/tools":
        payload = json.dumps(tool_catalog()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/workspace/index":
        index = load_cached_index()
        if index is None:
            index = build_workspace_index(write_cache=True)
        payload = json.dumps(index).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/runs":
        payload = json.dumps({"runs": list_runs(limit=20)}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/tool-audit":
        payload = json.dumps({"events": list_tool_events(limit=30)}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/models/recommendation":
        payload = json.dumps(recommend_models()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/models/download-plan":
        payload = json.dumps((await _setup_status()).get("model_download_plan", {})).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/uploads":
        payload = json.dumps({"uploads": list_uploads()}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/voice/recommendation":
        payload = json.dumps(_voice_recommendation()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/automations":
        from scripts.automation_store import list_automations

        payload = json.dumps({"automations": list_automations(limit=100)}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/memory":
        try:
            from scripts.long_term_memory import list_recent_queries

            memory = list_recent_queries(limit=5)
        except Exception:
            memory = []
        payload = json.dumps({"memory": memory}).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    if request_path == "/api/conversation":
        payload = json.dumps(get_conversation_snapshot()).encode("utf-8")
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ],
            payload,
        )

    # Returning None allows websocket handshake paths to continue.
    return None


async def _serve(host: str, port: int) -> None:
    async with serve(
        _handle_ws,
        host,
        port,
        process_request=_process_request,
        ping_interval=20,
        ping_timeout=20,
        max_size=2_000_000,
    ):
        logging.info("UI server running on ws://%s:%s (also serves /index.html)", host, port)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="Locus UI WebSocket server")
    parser.add_argument("--host", default=os.getenv("LOCAL_COMPUTER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LOCAL_COMPUTER_PORT", "8765")))
    args = parser.parse_args()
    asyncio.run(_serve(args.host, args.port))


if __name__ == "__main__":
    main()
