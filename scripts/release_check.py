#!/usr/bin/env python3
"""Production readiness checks for Locus.

The release gate is intentionally model-free. It validates configuration,
plugins, launch safety, dashboard syntax, local acceptance coverage, and core
assets without starting Ollama or pulling/running local models.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

JSON_FILES = [
    ROOT / "configs" / "runtime.json",
    ROOT / "configs" / "plugins.json",
    ROOT / "configs" / "models.json",
    ROOT / "configs" / "model_catalog.json",
    ROOT / "configs" / "cloud_connectors.json",
]
ICON_FILES = [
    ROOT / "assets" / "icons" / "locus-app-icon-source.png",
    ROOT / "assets" / "icons" / "locus-app-icon-1024.png",
    ROOT / "assets" / "icons" / "macos" / "Locus.icns",
    ROOT / "assets" / "icons" / "windows" / "Locus.ico",
]


class ReleaseFailure(AssertionError):
    pass


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseFailure(message)


def _run(cmd: list[str], *, timeout: float = 120.0, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def _no_model_env() -> dict[str, str]:
    return {
        "LOCAL_COMPUTER_ALLOW_MODELS": "0",
        "LOCAL_COMPUTER_SKIP_MODEL_VALIDATE": "1",
        "LOCAL_COMPUTER_AUTO_INSTALL_MODELS": "0",
        "LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA": "0",
        "LOCAL_COMPUTER_ALLOW_EXTERNAL_AI": "0",
        "LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS": "0",
    }


def _check_python() -> None:
    _assert(sys.version_info >= (3, 12), "Python 3.12+ is required for production Locus builds")


def _check_json_and_manifests() -> None:
    for path in JSON_FILES:
        _assert(path.exists(), f"missing config file: {path.relative_to(ROOT)}")
        json.loads(path.read_text(encoding="utf-8"))
    manifests = sorted((ROOT / "plugins").glob("*/plugin.json"))
    _assert(len(manifests) >= 10, "expected the core plugin manifest set")
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        _assert(data.get("id") == path.parent.name, f"plugin id must match folder name: {path}")
        _assert(data.get("tools"), f"plugin has no declared tools: {path}")


def _check_assets() -> None:
    for path in ICON_FILES:
        _assert(path.exists(), f"missing icon asset: {path.relative_to(ROOT)}")
        _assert(path.stat().st_size > 512, f"icon asset appears empty: {path.relative_to(ROOT)}")
    dashboard = ROOT / "dashboard" / "index.html"
    text = dashboard.read_text(encoding="utf-8")
    _assert("/assets/icons/locus-app-icon-64.png" in text, "dashboard is not using the production app icon")
    _assert("Learn Step-by-Step" in text, "dashboard is missing the Learn Step-by-Step control")
    _assert("Compute Usage" in text, "dashboard is missing compute usage settings")
    _assert("Model files are optional" in text, "dashboard must state that model assets are optional")
    _assert("Approve Model Downloads" in text, "dashboard must expose permissioned model downloads")
    _assert("Locus asks permission before pulling" in text, "dashboard must explain model download permission")
    _assert("work without downloaded models" in text, "dashboard must expose model-free frontend readiness")


def _check_plugins_and_safety() -> None:
    from scripts.plugin_runtime import execute_tool
    from scripts.setup_manager import setup_status

    diagnostics = execute_tool("workspace", "plugin_diagnostics", {})
    summary = diagnostics.get("summary", {})
    _assert(summary.get("pending_declared_tools") == 0, "all declared plugin tools must be implemented")
    _assert(summary.get("implemented_declared_tools") == summary.get("declared_tools"), "plugin tool counts are inconsistent")
    _assert(summary.get("connectors_needing_setup", 0) >= 2, "cloud connectors should remain unconfigured by default")
    drive = next((item for item in diagnostics.get("plugins", []) if item.get("id") == "google_drive"), None)
    _assert(drive is not None, "Google Drive connector stub is missing")
    _assert(not drive.get("status", {}).get("configured"), "Google Drive must not be configured without credentials")

    blocked = execute_tool("shell", "run_command", {"command": "ollama serve", "timeout": 1})
    _assert(not blocked.get("ok") and blocked.get("shell_safety", {}).get("blocked"), "Ollama launch guard must block model serving")

    status = setup_status()
    _assert(status.get("wizard", {}).get("cards"), "setup wizard cards are missing")
    steps = {step.get("id"): step for step in status.get("steps", [])}
    for required in ["os", "python", "deps", "chromium", "plugins", "models", "safety"]:
        _assert(required in steps, f"setup status missing step: {required}")
    _assert(steps.get("model_downloads", {}).get("required") is False, "model assets must not block model-free frontend readiness")
    _assert(
        steps.get("model_downloads", {}).get("state") in {"warning", "done"},
        "setup must show permissioned model downloads without blocking frontend use",
    )
    download_plan = status.get("model_download_plan", {})
    _assert(download_plan.get("model_count", 0) >= 1, "setup must expose the recommended model download plan")
    _assert(download_plan.get("total_size_gb", 0) > 0, "model download plan must include a total space estimate")
    _assert("free_disk_gb" in download_plan, "model download plan must include local free disk estimate")
    _assert(steps.get("ollama", {}).get("required") is False, "Ollama must not be required for the frontend")
    _assert(os.getenv("LOCAL_COMPUTER_AUTO_INSTALL_MODELS") == "0", "release gate must keep automatic model downloads disabled")


def _check_model_matrix() -> None:
    from scripts.plugin_runtime import execute_tool

    cases: list[tuple[str, dict[str, Any], Callable[[dict[str, Any]], None]]] = [
        (
            "8 GB Apple Silicon",
            {"simulate_os_family": "macos", "simulate_ram_gb": 8, "simulate_available_ram_gb": 5},
            lambda rec: _assert(rec["roles"]["orchestrator"] == "qwen2.5:3b", "8 GB Mac must default to 3B"),
        ),
        (
            "64 GB Apple Silicon",
            {"simulate_os_family": "macos", "simulate_ram_gb": 64, "simulate_available_ram_gb": 54},
            lambda rec: _assert("llama3.1:70b" in rec["recommended_models"], "64 GB Mac should include 70B"),
        ),
        (
            "RTX 4070 laptop",
            {
                "simulate_os_family": "windows",
                "simulate_ram_gb": 32,
                "simulate_available_ram_gb": 28,
                "simulate_gpu_name": "NVIDIA GeForce RTX 4070 Laptop GPU",
                "simulate_gpu_vram_gb": 8,
            },
            lambda rec: _assert(rec["roles"]["heavy"] == "qwen2.5:14b", "RTX 4070 laptop should cap heavy work at 14B"),
        ),
    ]
    for label, args, validator in cases:
        result = execute_tool("workspace", "model_recommendation", args)
        _assert(result.get("ok"), f"model recommendation failed for {label}")
        validator(result["recommendation"])


def _check_dashboard_js() -> None:
    node = shutil.which("node")
    if not node:
        raise ReleaseFailure("node is required for dashboard JavaScript syntax verification")
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    start = html.find("<script>")
    end = html.rfind("</script>")
    _assert(start != -1 and end != -1 and end > start, "dashboard script block not found")
    script = html[start + len("<script>") : end]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = handle.name
    try:
        result = _run([node, "--check", script_path], timeout=30)
        _assert(result.returncode == 0, result.stderr or result.stdout or "dashboard JavaScript check failed")
    finally:
        Path(script_path).unlink(missing_ok=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_json(url: str, timeout: float = 45.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise ReleaseFailure(f"dashboard did not become ready at {url}: {last_error}")


def _check_dashboard_frontend_no_model() -> None:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    env = {**os.environ, **_no_model_env(), "LOCAL_COMPUTER_PORT": str(port), "LOCAL_COMPUTER_HOST": "127.0.0.1"}
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "ui_server.py"), "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        ping = _wait_json(f"http://127.0.0.1:{port}/api/ping")
        runtime = ping.get("runtime", {})
        _assert(runtime.get("allow_local_models") is False, "frontend smoke must run with local models off")
        _assert(runtime.get("auto_install_models") is False, "frontend smoke must not allow automatic model downloads")

        setup = _wait_json(f"http://127.0.0.1:{port}/api/setup")
        steps = {step.get("id"): step for step in setup.get("steps", [])}
        _assert(steps.get("model_downloads", {}).get("required") is False, "frontend setup must not require model downloads")
        _assert(steps.get("ollama", {}).get("required") is False, "frontend setup must not require Ollama")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            console_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_selector("text=Local models off", timeout=12000)
            page.wait_for_selector("text=Set up Locus", timeout=12000)
            body_text = page.locator("body").inner_text(timeout=8000)
            body_lower = body_text.lower()
            for text in ["local models off", "set up locus"]:
                _assert(text in body_lower, f"dashboard missing model-free frontend text: {text}")
            _assert(
                "model files are optional" in body_lower or "model assets optional" in body_lower,
                "dashboard did not expose optional model-file setup copy",
            )
            _assert(
                "frontend readiness" in body_lower
                or "works before ollama" in body_lower
                or "work before model files exist" in body_lower,
                "dashboard did not expose model-free readiness or first-run setup copy",
            )
            conversation_text = page.locator("#conversationSummary").text_content(timeout=5000) or ""
            _assert(
                "conversation" in conversation_text.lower() or "message(s) saved" in conversation_text,
                "conversation history component did not initialize",
            )
            _assert(not console_errors, "dashboard console errors: " + " | ".join(console_errors[:5]))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _check_compile() -> None:
    files = [str(path) for path in sorted((ROOT / "scripts").glob("*.py"))]
    result = _run([sys.executable, "-m", "py_compile", *files], timeout=120)
    _assert(result.returncode == 0, result.stderr or "Python compile check failed")


def _check_acceptance(skip_browser: bool) -> None:
    cmd = [sys.executable, str(ROOT / "scripts" / "locus_acceptance.py")]
    if skip_browser:
        cmd.append("--skip-browser")
    result = _run(cmd, timeout=180, env=_no_model_env())
    _assert(result.returncode == 0, result.stdout + result.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run model-free production readiness checks")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright browser acceptance")
    parser.add_argument("--skip-acceptance", action="store_true", help="Skip the full no-model acceptance harness")
    args = parser.parse_args()

    os.environ.update(_no_model_env())
    checks: list[tuple[str, Callable[[], None]]] = [
        ("python", _check_python),
        ("json/manifests", _check_json_and_manifests),
        ("assets", _check_assets),
        ("plugins/safety", _check_plugins_and_safety),
        ("model matrix", _check_model_matrix),
        ("dashboard js", _check_dashboard_js),
        ("dashboard no-model frontend", _check_dashboard_frontend_no_model),
        ("python compile", _check_compile),
    ]
    if not args.skip_acceptance:
        checks.append(("acceptance", lambda: _check_acceptance(args.skip_browser)))

    failures: list[str] = []
    for name, check in checks:
        try:
            check()
            print(f"PASS {name}")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            print(f"FAIL {name} - {exc}")

    if failures:
        print("\nRelease check failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("\nLocus release check passed without starting local models.")


if __name__ == "__main__":
    main()
