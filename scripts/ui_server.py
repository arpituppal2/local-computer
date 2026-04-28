"""local-computer UI server — Flask backend for the Perplexity Computer–style dashboard.

Endpoints
---------
GET  /                     → serve dashboard/index.html
GET  /api/ping             → health check
POST /api/goal             → kick off a mission (body: {"goal": "..."})
POST /api/inject           → inject a mid-run instruction (body: {"text": "..."})
GET  /api/events           → SSE stream of agent events
GET  /api/status           → current agent state JSON
POST /api/permission       → user grants or denies a pending permission request
POST /api/cancel           → cancel the running mission
GET  /api/result           → last mission result markdown
POST /api/login_creds      → supply credentials for a login task
POST /api/login_take_over  → signal user has taken over browser for login
POST /api/login_deny       → skip the login entirely
"""
from __future__ import annotations
import argparse
import json
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

ROOT      = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboard"

# Persistent Playwright profile — survives across runs, stores cookies/session
BROWSER_PROFILE = ROOT / "browser_profile"
BROWSER_PROFILE.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(DASHBOARD), static_url_path="")
CORS(app)

# ── Shared state ──────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "status":        "idle",
    "goal":          "",
    "current_task":  "",
    "agent_role":    "",
    "step":          0,
    "total_steps":   0,
    "url":           "",
    "result":        "",
    "error":         "",
    "tasks":         [],
    "timeline":      [],
    "login_pending": False,
    "login_site":    "",
}

_event_queue:         queue.Queue  = queue.Queue(maxsize=500)
_permission_event     = threading.Event()
_permission_response: dict         = {}
_cancel_event         = threading.Event()
_mission_thread: threading.Thread | None = None
_inject_queue:   queue.Queue       = queue.Queue(maxsize=32)
_login_event     = threading.Event()
_login_response: dict              = {}


def _push(kind: str, text: str, **extra):
    entry = {"ts": time.time(), "kind": kind, "text": text, **extra}
    with _state_lock:
        _state["timeline"].append(entry)
    try:
        _event_queue.put_nowait(entry)
    except queue.Full:
        pass


def _set_state(**kw):
    with _state_lock:
        _state.update(kw)


# ── Permission bridge ─────────────────────────────────────────────────────

def request_permission(action_type: str, description: str, details: dict | None = None) -> bool:
    global _permission_response
    _permission_event.clear()
    _permission_response = {}
    _set_state(status="waiting_permission")
    _push("permission_request", description,
          action_type=action_type, details=details or {})
    granted = _permission_event.wait(timeout=120)
    if not granted:
        _push("permission_timeout", "No response — defaulting to deny")
        _set_state(status="acting")
        return False
    approved = _permission_response.get("approved", False)
    _push("permission_response", "Approved" if approved else "Denied", approved=approved)
    _set_state(status="acting")
    return approved


# ── Login bridge ──────────────────────────────────────────────────────────

def request_login(site: str, page) -> bool:
    global _login_response
    _login_event.clear()
    _login_response = {}
    _set_state(status="waiting_login", login_pending=True, login_site=site)
    _push("login_required", f"Login required for: {site}",
          site=site, action_type="login")
    resolved = _login_event.wait(timeout=180)
    _set_state(login_pending=False, login_site="")
    if not resolved:
        _push("login_timeout", "Login timed out — skipping")
        _set_state(status="acting")
        return False
    mode = _login_response.get("mode")
    if mode == "deny":
        _push("login_denied", "User skipped login")
        _set_state(status="acting")
        return False
    if mode == "creds":
        _push("login_creds_provided", "Credentials received — filling form")
        _set_state(status="acting")
        return True
    if mode == "takeover":
        _push("login_takeover", "You have control — complete login then click Done")
        _set_state(status="waiting_login_takeover")
        done_event = threading.Event()
        _login_response["done_event"] = done_event
        done_event.wait(timeout=300)
        _push("login_takeover_done", "User finished login — resuming")
        _set_state(status="acting")
        return True
    return False


def get_login_creds() -> dict:
    return {k: v for k, v in _login_response.items()
            if k in ("username", "password", "email")}


# ── Injection ─────────────────────────────────────────────────────────────

def pop_injected_instruction() -> str | None:
    try:
        return _inject_queue.get_nowait()
    except queue.Empty:
        return None


# ── Memory proxy ──────────────────────────────────────────────────────────

class _UIMemoryProxy:
    def __init__(self):
        from scripts.memory import Memory
        self._mem = Memory()

    def __getattr__(self, name):
        return getattr(self._mem, name)

    def request_permission(self, action_type: str, description: str, details=None) -> bool:
        return request_permission(action_type, description, details)

    def request_login(self, site: str, page) -> bool:
        return request_login(site, page)

    def get_login_creds(self) -> dict:
        return get_login_creds()

    def pop_injected_instruction(self) -> str | None:
        return pop_injected_instruction()


# ── Mission runner ────────────────────────────────────────────────────────

def _run_mission_thread(goal: str):
    try:
        _set_state(
            status="thinking", goal=goal, current_task="", step=0,
            total_steps=0, url="", result="", error="", tasks=[], timeline=[],
            login_pending=False, login_site="",
        )
        _cancel_event.clear()
        while not _inject_queue.empty():
            try: _inject_queue.get_nowait()
            except queue.Empty: break

        _push("start", f"Starting: {goal}")
        _push("think", "Planning task graph…")

        from scripts.task_planner import build_task_graph
        tasks = build_task_graph(goal)
        task_list = [{"id": t["id"], "role": t["role"],
                      "goal": t["goal"], "status": "pending"} for t in tasks]
        _set_state(tasks=task_list, total_steps=len(tasks))
        _push("plan", f"{len(tasks)} task(s) planned",
              tasks=[{"id": t["id"], "role": t["role"], "goal": t["goal"]}
                     for t in tasks])

        from scripts.agent_roles import get_agent
        from playwright.sync_api import sync_playwright

        completed: dict[str, dict] = {}

        def _ready(task):
            return all(d in completed for d in task["depends_on"])

        with sync_playwright() as pw:
            # ── Persistent context: remembers cookies/sessions across runs
            ctx  = pw.chromium.launch_persistent_context(
                str(BROWSER_PROFILE),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            max_rounds = len(tasks) + 4
            for round_i in range(max_rounds):
                if _cancel_event.is_set():
                    _push("cancelled", "Mission cancelled by user")
                    break

                injection = pop_injected_instruction()
                if injection:
                    _push("injected", f"New instruction: {injection}")
                    augmented_goal = goal + f"\n\nAdditional instruction: {injection}"
                    new_tasks = build_task_graph(augmented_goal)
                    existing_ids = {t["id"] for t in tasks}
                    for nt in new_tasks:
                        if nt["id"] not in existing_ids:
                            tasks.append(nt)
                            task_list.append({"id": nt["id"], "role": nt["role"],
                                              "goal": nt["goal"], "status": "pending"})
                    _set_state(tasks=list(task_list))
                    _push("plan", f"Re-planned: {len(tasks)} total task(s)")

                pending = [t for t in tasks
                           if t["id"] not in completed and _ready(t)]
                if not pending:
                    break

                for t in pending:
                    needs_permission = t["role"] in ("file", "coder", "browser")
                    if needs_permission:
                        desc = (f"Allow agent to perform {t['role'].upper()} action:\n"
                                f"{t['goal'][:120]}")
                        if not request_permission(t["role"], desc, {"goal": t["goal"]}):
                            completed[t["id"]] = {
                                "role": t["role"],
                                "findings": "[permission denied by user]",
                                "status": "denied",
                            }
                            for tl in task_list:
                                if tl["id"] == t["id"]:
                                    tl["status"] = "denied"
                            _set_state(tasks=list(task_list))
                            continue

                for t in pending:
                    if t["id"] in completed:
                        continue
                    if _cancel_event.is_set():
                        break

                    _set_state(status="acting", current_task=t["goal"],
                               agent_role=t["role"], step=len(completed) + 1)
                    _push("task_start", f"[{t['role']}] {t['goal'][:80]}",
                          task_id=t["id"], role=t["role"])

                    for tl in task_list:
                        if tl["id"] == t["id"]:
                            tl["status"] = "running"
                    _set_state(tasks=list(task_list))

                    try:
                        mem    = _UIMemoryProxy()
                        agent  = get_agent(t["role"])
                        result = agent.run(t, page=page, context=ctx, memory=mem)
                    except Exception as exc:
                        result = {"role": t["role"],
                                  "findings": f"[error: {exc}]",
                                  "status": "error"}
                        _push("error", str(exc), task_id=t["id"])

                    completed[t["id"]] = result
                    for tl in task_list:
                        if tl["id"] == t["id"]:
                            tl["status"] = result.get("status", "done")
                    _set_state(tasks=list(task_list))

                    snippet = (result.get("findings") or "")[:200]
                    _push("task_done", snippet, task_id=t["id"],
                          role=t["role"], status=result.get("status"))

            ctx.close()   # close persistent context (not browser.close)

        final = ""
        for role in ("writer", "analyst", "researcher"):
            for tid, res in completed.items():
                if res.get("role") == role and res.get("findings"):
                    final = res["findings"]
                    break
            if final:
                break

        if not final:
            final = "\n\n".join(
                f"**{completed[tid]['role']}**: {completed[tid].get('findings','')}"
                for tid in completed
            )

        out = ROOT / "outputs" / "result.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(final)

        _set_state(status="done", result=final)
        _push("done", "Mission complete", result_preview=final[:300])

    except Exception as exc:
        tb = traceback.format_exc()
        _set_state(status="error", error=str(exc))
        _push("error", str(exc), traceback=tb)


# ── Flask routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(DASHBOARD), "index.html")

@app.route("/api/ping")
def ping():
    return jsonify({"ok": True})

@app.route("/api/goal", methods=["POST"])
def start_goal():
    global _mission_thread
    data = request.get_json(force=True, silent=True) or {}
    goal = (data.get("goal") or "").strip()
    if not goal:
        return jsonify({"error": "goal is required"}), 400
    _cancel_event.set()
    if _mission_thread and _mission_thread.is_alive():
        _mission_thread.join(timeout=3)
    _cancel_event.clear()
    _mission_thread = threading.Thread(
        target=_run_mission_thread, args=(goal,), daemon=True
    )
    _mission_thread.start()
    return jsonify({"ok": True, "goal": goal})

@app.route("/api/inject", methods=["POST"])
def inject():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    try:
        _inject_queue.put_nowait(text)
    except queue.Full:
        return jsonify({"error": "injection queue full"}), 429
    _push("user_inject", f"You said: {text}")
    return jsonify({"ok": True})

@app.route("/api/events")
def sse_events():
    def generate():
        with _state_lock:
            snap = dict(_state)
        yield f"data: {json.dumps({'kind': 'snapshot', 'state': snap})}\n\n"
        while True:
            try:
                evt = _event_queue.get(timeout=20)
                yield f"data: {json.dumps(evt)}\n\n"
            except queue.Empty:
                yield 'data: {"kind": "heartbeat"}\n\n'
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/status")
def status():
    with _state_lock:
        return jsonify(dict(_state))

@app.route("/api/permission", methods=["POST"])
def permission():
    global _permission_response
    data = request.get_json(force=True, silent=True) or {}
    _permission_response = {"approved": bool(data.get("approved", False))}
    _permission_event.set()
    return jsonify({"ok": True})

@app.route("/api/cancel", methods=["POST"])
def cancel():
    _cancel_event.set()
    _set_state(status="idle")
    _push("cancelled", "Mission cancelled")
    return jsonify({"ok": True})

@app.route("/api/result")
def result():
    with _state_lock:
        return jsonify({"result": _state["result"]})

@app.route("/api/login_creds", methods=["POST"])
def login_creds():
    global _login_response
    data = request.get_json(force=True, silent=True) or {}
    _login_response = {
        "mode":     "creds",
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "email":    data.get("email", ""),
    }
    _login_event.set()
    return jsonify({"ok": True})

@app.route("/api/login_take_over", methods=["POST"])
def login_take_over():
    global _login_response
    data = request.get_json(force=True, silent=True) or {}
    if data.get("done") and "done_event" in _login_response:
        _login_response["done_event"].set()
        return jsonify({"ok": True, "phase": "done"})
    _login_response = {"mode": "takeover"}
    _login_event.set()
    return jsonify({"ok": True, "phase": "takeover"})

@app.route("/api/login_deny", methods=["POST"])
def login_deny():
    global _login_response
    _login_response = {"mode": "deny"}
    _login_event.set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7878)
    args = parser.parse_args()
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False)
