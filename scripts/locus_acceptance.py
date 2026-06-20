#!/usr/bin/env python3
"""Local acceptance checks for Locus.

The suite validates the local-first feature surface without pulling models,
starting Ollama, or sending data to cloud services.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent


class AcceptanceFailure(AssertionError):
    pass


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def _prepare_env() -> tempfile.TemporaryDirectory[str]:
    real_playwright_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    state_home = tempfile.TemporaryDirectory(prefix="locus-acceptance-home-")
    workspace = Path(state_home.name) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("# Fixture\n\nTODO: local acceptance marker\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("Locus local acceptance search target\n", encoding="utf-8")

    os.environ["HOME"] = state_home.name
    os.environ["LOCAL_COMPUTER_WORKSPACE"] = str(workspace)
    os.environ["LOCAL_COMPUTER_ALLOW_MODELS"] = "0"
    os.environ["LOCAL_COMPUTER_SKIP_MODEL_VALIDATE"] = "1"
    os.environ["LOCAL_COMPUTER_AUTO_INSTALL_MODELS"] = "0"
    os.environ["LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA"] = "0"
    os.environ["LOCAL_COMPUTER_ALLOW_EXTERNAL_AI"] = "0"
    os.environ["LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS"] = "0"
    if real_playwright_cache.exists() and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(real_playwright_cache)
    os.environ["PYTHONPATH"] = str(ROOT) + (":" + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return state_home


def _run_case(name: str, fn: Callable[[], None]) -> dict[str, Any]:
    try:
        fn()
        return {"name": name, "ok": True}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc), "type": exc.__class__.__name__}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Locus acceptance checks")
    parser.add_argument("--json", action="store_true", help="Print JSON results")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright browser checks")
    args = parser.parse_args()

    state_home = _prepare_env()
    cleanup_paths: list[Path] = []

    from scripts.plugin_runtime import execute_tool
    from scripts.setup_manager import setup_status
    from scripts.conversation_history import get_context_bundle, store_turn
    from scripts.workspace_agent import run_workspace_query
    from scripts.workspace_planner import plan_workspace_task

    def diagnostics() -> None:
        result = execute_tool("workspace", "plugin_diagnostics", {})
        summary = result["summary"]
        _assert(summary["plugins"] >= 10, "expected core plugin set")
        _assert(summary["declared_tools"] == summary["implemented_declared_tools"], "declared tools must be implemented")
        _assert(summary["pending_declared_tools"] == 0, "no pending plugin tools expected")
        _assert(summary["connectors_needing_setup"] >= 2, "cloud connectors should be visible but unconfigured by default")
        drive = next((item for item in result["plugins"] if item["id"] == "google_drive"), None)
        _assert(drive is not None and not drive["status"]["configured"], "Google Drive should remain a credential-gated stub")

    def model_selection() -> None:
        result = execute_tool(
            "workspace",
            "model_recommendation",
            {"simulate_ram_gb": 8, "simulate_available_ram_gb": 3, "max_ram_gb": 4.5},
        )
        rec = result["recommendation"]
        budget = rec["resource_budget"]
        _assert(result["ok"], "model recommendation failed")
        _assert(budget["low_ram_mode"], "8 GB profile should use low RAM mode")
        _assert(budget["gpu_limit_pct"] == 90, "GPU cap should default to 90%")
        _assert(rec["pull_plan"] and all(item["command"].startswith("ollama pull ") for item in rec["pull_plan"]), "pull plan missing")
        windows = execute_tool(
            "workspace",
            "model_recommendation",
            {
                "simulate_os_family": "windows",
                "simulate_ram_gb": 32,
                "simulate_available_ram_gb": 28,
                "simulate_gpu_name": "NVIDIA GeForce RTX 4070 Laptop GPU",
                "simulate_gpu_vram_gb": 8,
            },
        )
        win_rec = windows["recommendation"]
        _assert(win_rec["gpu_acceleration"]["tier"] == "nvidia_laptop_8gb", "Windows RTX laptop tier not detected")
        _assert("llama3.1:70b" not in win_rec["recommended_models"], "8 GB VRAM laptop should not recommend 70B")
        _assert(win_rec["roles"]["heavy"] == "qwen2.5:14b", "8 GB VRAM laptop should cap heavy work at 14B")

    def workspace_and_planner() -> None:
        plan = plan_workspace_task("check this repo and show todos and git status")
        pairs = {(step.plugin, step.tool) for step in plan}
        _assert(("workspace", "health_report") in pairs, "compound plan should include health report")
        _assert(("workspace", "todo_report") in pairs, "compound plan should include TODO report")
        _assert(("git", "git_status") in pairs, "compound plan should include git status")
        result = execute_tool("workspace", "workspace_index", {})
        _assert(result["ok"] and result["index"]["file_count"] >= 2, "workspace index failed")

    def plan_mode() -> None:
        result = __import__("asyncio").run(run_workspace_query("show plugin status", plan_only=True))
        _assert("Plan Mode" in result["answer"], "plan-only answer missing")
        _assert("workspace.plugin_diagnostics" in result["answer"], "plan mode should show plugin diagnostics step")

    def uploads() -> None:
        payload = base64.b64encode(b"Locus upload acceptance fixture").decode("ascii")
        result = execute_tool(
            "uploads",
            "save_upload",
            {"name": "locus-acceptance.txt", "content_b64": payload, "type": "text/plain", "size": 31},
        )
        _assert(result["ok"] and result["uploads"][0]["ok"], "upload save failed")
        path = Path(result["uploads"][0]["path"])
        cleanup_paths.extend([path, Path(str(path) + ".meta.json")])
        read = execute_tool("uploads", "read_upload", {"path": str(path)})
        _assert("acceptance fixture" in read.get("content", ""), "upload read failed")

    def memory() -> None:
        stored = execute_tool(
            "memory",
            "store_query_answer",
            {"query": "Locus acceptance memory check", "answer": "Local memory stores facts without local inference."},
        )
        _assert(stored["ok"] and stored["id"] >= 1, "memory store failed")
        retrieved = execute_tool("memory", "retrieve_relevant_answers", {"query": "acceptance memory", "top_k": 1})
        _assert(retrieved["ok"] and retrieved["matches"], "memory retrieve failed")

    def conversation_history() -> None:
        for idx in range(16):
            snapshot = store_turn(
                f"Locus acceptance conversation turn {idx}",
                "The deterministic answer keeps local history available without model inference.",
                thinking=[f"acceptance thinking step {idx}-{step}" for step in range(4)],
                metadata={"acceptance": True, "idx": idx},
            )
        context = get_context_bundle(session_id=snapshot["session_id"])
        _assert(context["message_count"] >= 32, "conversation turns were not stored")
        _assert(context["compression_active"], "long conversation should auto-compress")
        _assert(context["summary"], "compressed conversation summary missing")
        _assert(context["active_message_count"] <= context["thresholds"]["active_message_limit"], "active context was not bounded")
        result = __import__("asyncio").run(run_workspace_query("show conversation history", conversation_context=context))
        _assert("Local conversation context" in result["answer"], "workspace agent did not expose conversation context")

    def automations() -> None:
        created = execute_tool(
            "automations",
            "create_automation",
            {"name": "acceptance-check", "prompt": "show plugin status", "schedule": "now"},
        )
        _assert(created["ok"], "automation create failed")
        automation_id = created["automation"]["id"]
        listed = execute_tool("automations", "list_automations", {})
        _assert(any(item["id"] == automation_id for item in listed["automations"]), "automation list missed created item")
        ran = execute_tool("automations", "run_due_automations", {})
        _assert(ran["ok"] and ran["ran"] >= 1, "due automation did not run")
        deleted = execute_tool("automations", "delete_automation", {"id": automation_id})
        _assert(deleted["ok"], "automation delete failed")

    def safety_and_permissions() -> None:
        status = setup_status()
        steps = {step["id"]: step for step in status["steps"]}
        _assert(status.get("wizard") and status["wizard"].get("cards"), "setup status missing setup wizard")
        _assert("full_disk" in steps, "setup status missing Full Disk Access")
        _assert("accessibility" in steps, "setup status missing Accessibility")
        _assert("safety" in steps and "90" in steps["safety"]["detail"], "safety step should show 90% GPU cap")
        blocked = execute_tool("shell", "run_command", {"command": "ollama run qwen2.5:3b", "timeout": 1})
        _assert(not blocked["ok"] and blocked["shell_safety"]["blocked"], "model launch guard failed")

    def frontend_model_free_ready() -> None:
        status = setup_status()
        steps = {step["id"]: step for step in status["steps"]}
        _assert(steps["model_downloads"]["required"] is False, "model downloads must not block frontend readiness")
        _assert(steps["model_downloads"]["state"] in {"warning", "done"}, "model downloads should ask permission without blocking frontend readiness")
        plan = status.get("model_download_plan", {})
        _assert(plan.get("model_count", 0) >= 1, "model download plan missing recommended models")
        _assert(plan.get("total_size_gb", 0) > 0, "model download plan missing space estimate")
        _assert("free_disk_gb" in plan, "model download plan missing free disk estimate")
        _assert(steps["ollama"]["required"] is False, "Ollama must be optional while models are disabled")
        wizard_text = json.dumps(status.get("wizard", {}))
        _assert("works before Ollama" in wizard_text, "setup wizard must explain model-free frontend readiness")
        _assert("space estimate" in wizard_text or "disk-space estimate" in wizard_text, "setup wizard must explain permissioned model download estimates")
        _assert(os.environ.get("LOCAL_COMPUTER_AUTO_INSTALL_MODELS") == "0", "acceptance must keep automatic model downloads disabled")

    def browser() -> None:
        html = Path(state_home.name) / "browser.html"
        html.write_text(
            "<!doctype html><title>Locus Browser Fixture</title>"
            "<main><h1>Locus Browser Works</h1><a href='#next'>Next</a>"
            "<input aria-label='Search' placeholder='Search'></main>",
            encoding="utf-8",
        )
        opened = execute_tool("browser", "open_page", {"url": html.resolve().as_uri(), "max_chars": 1000})
        _assert(opened["ok"] and "Locus Browser Works" in opened.get("text", ""), "browser open/extract failed")
        filled = execute_tool("browser", "fill", {"url": html.resolve().as_uri(), "label": "Search", "value": "local test"})
        _assert(filled["ok"], "browser fill failed")
        shot = execute_tool("browser", "screenshot", {"url": html.resolve().as_uri()})
        _assert(shot["ok"] and Path(shot["path"]).exists(), "browser screenshot failed")
        cleanup_paths.append(Path(shot["path"]))

    cases: list[tuple[str, Callable[[], None]]] = [
        ("plugin diagnostics", diagnostics),
        ("model selection", model_selection),
        ("workspace planner", workspace_and_planner),
        ("plan mode", plan_mode),
        ("uploads", uploads),
        ("memory", memory),
        ("conversation history", conversation_history),
        ("automations", automations),
        ("safety and permissions", safety_and_permissions),
        ("frontend model-free readiness", frontend_model_free_ready),
    ]
    if not args.skip_browser:
        cases.append(("local browser", browser))

    results = [_run_case(name, fn) for name, fn in cases]

    for path in cleanup_paths:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    state_home.cleanup()

    ok = all(item["ok"] for item in results)
    if args.json:
        print(json.dumps({"ok": ok, "results": results}, indent=2))
    else:
        for item in results:
            marker = "PASS" if item["ok"] else "FAIL"
            suffix = "" if item["ok"] else f" - {item.get('error')}"
            print(f"{marker} {item['name']}{suffix}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
