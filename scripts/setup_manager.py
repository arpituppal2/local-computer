#!/usr/bin/env python3
"""First-run setup checks and installers for Locus.

This module is intentionally stdlib-first so `run.sh` can call it before the
project virtualenv has all runtime dependencies installed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from scripts.os_profile import detect_os
from scripts.runtime_policy import FALSY

VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
DEPS_SENTINEL = VENV / ".deps_installed"
OS_PROFILE = detect_os()
STATE_DIR = Path(OS_PROFILE.state_dir)
STATE_PATH = STATE_DIR / "setup_state.json"

REQUIRED_MODULES = {
    "httpx": "httpx",
    "playwright": "playwright",
    "flask": "flask",
    "flask_cors": "flask_cors",
    "websockets": "websockets",
    "psutil": "psutil",
}
if OS_PROFILE.family == "macos":
    REQUIRED_MODULES.update(
        {
            "pyobjc_cocoa": "AppKit",
            "pyobjc_webkit": "WebKit",
        }
    )

LOCAL_DIRS = [
    ROOT / "outputs",
    ROOT / "logs",
    ROOT / "uploads",
    STATE_DIR,
]

PROTECTED_MAC_PATHS = [
    Path.home() / "Library" / "Mail",
    Path.home() / "Library" / "Messages",
    Path.home() / "Library" / "Safari",
]

EventHandler = Callable[[dict[str, Any]], None]


def _ci_mode() -> bool:
    return os.getenv("CI", "").strip().lower() in {"1", "true", "yes"} or os.getenv(
        "GITHUB_ACTIONS", ""
    ).strip().lower() == "true"


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _emit(
    emit: EventHandler | None,
    step_id: str,
    title: str,
    state: str,
    detail: str = "",
    output: str = "",
) -> None:
    if emit is None:
        return
    emit(
        {
            "id": step_id,
            "title": title,
            "state": state,
            "detail": detail,
            "output": output,
            "ts": time.time(),
        }
    )


def _run_stream(
    cmd: list[str],
    emit: EventHandler | None,
    step_id: str,
    title: str,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    _emit(emit, step_id, title, "running", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if text:
            _emit(emit, step_id, title, "running", output=text)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{title} failed with exit code {code}")


def _module_available(module_name: str, python: Path | None = None) -> bool:
    if python is None or Path(sys.executable).resolve() == python.resolve():
        return importlib.util.find_spec(module_name) is not None
    code = f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec({module_name!r}) else 1)"
    return subprocess.run([str(python), "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _missing_modules(python: Path | None = None) -> list[str]:
    return [name for name, module in REQUIRED_MODULES.items() if not _module_available(module, python=python)]


def _playwright_chromium_ready(python: Path | None = None) -> bool:
    python = python or Path(sys.executable)
    if _ci_mode():
        return True
    code = """
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    raise SystemExit(0 if Path(p.chromium.executable_path).exists() else 1)
""".strip()
    try:
        return (
            subprocess.run(
                [str(python), "-c", code],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return False


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _write_state(extra: dict[str, Any] | None = None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **_load_state(),
        **(extra or {}),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo": str(ROOT),
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def _step(
    step_id: str,
    title: str,
    state: str,
    detail: str = "",
    required: bool = True,
    action: str = "",
    help_text: str = "",
    group: str = "required",
) -> dict[str, Any]:
    return {
        "id": step_id,
        "title": title,
        "state": state,
        "detail": detail,
        "required": required,
        "action": action,
        "help": help_text,
        "group": group,
    }


def _step_state(steps: list[dict[str, Any]], step_id: str) -> str:
    for step in steps:
        if step.get("id") == step_id:
            return str(step.get("state") or "pending")
    return "pending"


def _worst_state(states: list[str]) -> str:
    if "error" in states:
        return "error"
    if "running" in states:
        return "running"
    if "pending" in states:
        return "pending"
    if "warning" in states:
        return "warning"
    return "done"


def _wizard_card(
    card_id: str,
    title: str,
    state: str,
    detail: str,
    bullets: list[str],
    action: str = "none",
) -> dict[str, Any]:
    return {
        "id": card_id,
        "title": title,
        "state": state,
        "detail": detail,
        "bullets": bullets,
        "action": action,
    }


def _setup_wizard(
    os_profile: Any,
    budget: Any,
    steps: list[dict[str, Any]],
    recommendation: dict[str, Any] | None,
    complete: bool,
) -> dict[str, Any]:
    acceleration = (recommendation or {}).get("gpu_acceleration", {})
    accel_tier = str(acceleration.get("tier") or acceleration.get("kind") or "local")
    model_budget = acceleration.get("model_budget_gb")
    model_budget_text = f"{float(model_budget):.1f} GB model budget" if model_budget is not None else "RAM-based model budget"
    os_intro = os_profile.setup_copy

    install_state = _worst_state(
        [_step_state(steps, step_id) for step_id in ["python", "venv", "deps", "chromium", "dirs"]]
    )
    permissions_state = _worst_state([_step_state(steps, "full_disk"), _step_state(steps, "accessibility")])
    if os_profile.family == "windows":
        permissions_state = "done"
        permissions_detail = "Windows does not use macOS privacy panes; approve Windows security prompts only when they appear."
        permissions_bullets = [
            "Use folders you own for the smoothest first run.",
            "Protected folders may trigger Windows security prompts.",
            "No Full Disk Access or Accessibility setup is required for dashboard mode.",
        ]
    else:
        permissions_detail = "macOS permissions are optional, but they unlock protected folders, global shortcuts, and app control."
        permissions_bullets = [
            "Full Disk Access helps Locus inspect Mail, Messages, Safari, and broad folders.",
            "Accessibility enables global shortcuts and app-control automation.",
            "Locus can open the correct System Settings panes, but macOS requires you to approve them.",
        ]

    cards = [
        _wizard_card(
            "welcome",
            f"{os_profile.name} Setup",
            "done" if os_profile.supported else "error",
            os_intro,
            [
                f"Local app state: {STATE_DIR}",
                "Setup installs app dependencies and Playwright Chromium first.",
                "Model files download only after you approve the space estimate.",
                "The full frontend works before Ollama or any model download.",
                "Downloaded model files are never run until local model mode is explicitly enabled.",
            ],
            "none" if os_profile.supported else "Use macOS or Windows",
        ),
        _wizard_card(
            "install",
            "Install Local Runtime",
            install_state,
            "Locus creates its private Python environment and local browser automatically.",
            [
                "Creates or repairs .venv.",
                "Installs requirements.txt.",
                "Installs Playwright Chromium for the in-app browser.",
            ],
            "Start Setup" if install_state != "done" else "none",
        ),
        _wizard_card(
            "permissions",
            "Permissions",
            permissions_state,
            permissions_detail,
            permissions_bullets,
            "Review permissions" if permissions_state != "done" else "none",
        ),
        _wizard_card(
            "performance",
            "Performance Profile",
            "warning" if getattr(budget, "low_ram_mode", False) or getattr(budget, "pressure_adjusted", False) else "done",
            f"{budget.max_ram_gb:.1f} GB effective RAM, {model_budget_text}, {budget.gpu_limit_pct:.0f}% GPU cap.",
            [
                f"Acceleration: {accel_tier.replace('_', ' ')}.",
                "Current memory pressure can lower the effective model budget.",
                "Close heavy apps before enabling local model mode.",
            ],
            "Adjust Max RAM" if getattr(budget, "low_ram_mode", False) else "none",
        ),
        _wizard_card(
            "plugins",
            "Plugins First",
            _step_state(steps, "plugins"),
            "Plugins are the default way Locus reads files, works in repos, controls the browser, handles uploads, and connects services.",
            [
                "Filesystem, shell, git, browser, uploads, memory, workspace, and automations are local.",
                "GitHub and email connectors stay visible as setup items when credentials are missing.",
                "Plan Mode can show tool steps before execution.",
            ],
            "Open Plugin Center" if _step_state(steps, "plugins") != "done" else "none",
        ),
        _wizard_card(
            "models",
            "Model Choice",
            _worst_state([_step_state(steps, "models"), _step_state(steps, "model_downloads")]),
            "Locus recommends models from OS, RAM, current memory pressure, and GPU/VRAM, then asks before downloading them.",
            [
                "The frontend, setup, plugins, uploads, browser control, and history work before model files exist.",
                "Setup shows a disk-space estimate before any Ollama pull runs.",
                "NVIDIA Windows machines use VRAM-aware recommendations.",
            ],
            "Enable model downloads"
            if _worst_state([_step_state(steps, "models"), _step_state(steps, "model_downloads")]) != "done"
            else "none",
        ),
        _wizard_card(
            "ready",
            "Ready",
            "done" if complete else "pending",
            "Required checks are complete." if complete else "Finish the required cards above before using every local feature.",
            [
                "Model-free workspace mode is available first.",
                "Local model mode stays off until explicitly enabled.",
                "The setup checklist remains available from Command Center.",
            ],
            "Start Setup" if not complete else "none",
        ),
    ]

    return {
        "title": "Setup Wizard",
        "platform": os_profile.family,
        "summary": f"{os_profile.name} first-run setup with local-only defaults.",
        "cards": cards,
    }


def full_disk_access_status() -> dict[str, Any]:
    """Best-effort Full Disk Access check for macOS.

    macOS does not let apps grant Full Disk Access to themselves. The best we can
    do is check a few protected user-library locations and open System Settings.
    """
    os_profile = detect_os()
    if os_profile.family == "windows":
        return {
            "state": "done",
            "detail": "macOS-only; use folders you own or approve Windows prompts",
            "available": True,
            "action": "none",
        }
    if sys.platform != "darwin":
        return {
            "state": "warning",
            "detail": "unsupported OS; Locus supports macOS and Windows",
            "available": False,
            "action": "none",
        }
    if _ci_mode():
        return {
            "state": "warning",
            "detail": "skipped on CI; approve Full Disk Access on a real Mac when needed",
            "available": False,
            "action": "open_full_disk_access",
        }

    checked = 0
    blocked: list[str] = []
    missing = 0
    for path in PROTECTED_MAC_PATHS:
        try:
            exists = path.exists()
        except OSError as exc:
            blocked.append(f"{path.name}: {exc.strerror or exc}")
            continue
        if not exists:
            missing += 1
            continue
        checked += 1
        try:
            next(path.iterdir(), None)
        except PermissionError:
            blocked.append(path.name)
        except OSError as exc:
            blocked.append(f"{path.name}: {exc.strerror or exc}")

    if blocked:
        return {
            "state": "warning",
            "detail": "needs approval for " + ", ".join(blocked[:3]),
            "available": False,
            "action": "open_full_disk_access",
        }
    if checked:
        return {"state": "done", "detail": "approved", "available": True, "action": "none"}
    return {
        "state": "warning",
        "detail": "approval could not be confirmed; open settings if file access fails",
        "available": False,
        "action": "open_full_disk_access",
        "missing_checked_paths": missing,
    }


def open_full_disk_access_settings() -> dict[str, Any]:
    os_profile = detect_os()
    if os_profile.family == "windows":
        return {
            "ok": False,
            "detail": "Full Disk Access is macOS-only. On Windows, use folders you own or approve Windows security prompts.",
        }
    if sys.platform != "darwin":
        return {"ok": False, "detail": "Full Disk Access is a macOS setting."}
    urls = [
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles",
    ]
    for url in urls:
        try:
            subprocess.run(["open", url], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {
                "ok": True,
                "detail": "System Settings opened. Add Locus or your terminal app to Full Disk Access, then return here.",
            }
        except Exception:
            continue
    return {"ok": False, "detail": "Could not open System Settings automatically."}


def accessibility_status() -> dict[str, Any]:
    """Best-effort status for macOS Accessibility automation permissions."""
    os_profile = detect_os()
    if os_profile.family == "windows":
        return {
            "state": "done",
            "detail": "not needed for dashboard mode; approve app-control prompts when required",
            "available": True,
            "action": "none",
        }
    if sys.platform != "darwin":
        return {
            "state": "warning",
            "detail": "unsupported OS; Locus supports macOS and Windows",
            "available": False,
            "action": "none",
        }
    if _ci_mode():
        return {
            "state": "warning",
            "detail": "skipped on CI; approve Accessibility on a real Mac for shortcuts and app control",
            "available": False,
            "action": "open_accessibility",
        }
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get UI elements enabled'],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception as exc:
        return {
            "state": "warning",
            "detail": f"could not check Accessibility: {exc}",
            "available": False,
            "action": "open_accessibility",
        }
    if result.returncode == 0 and result.stdout.strip().lower() == "true":
        return {
            "state": "warning",
            "detail": "system automation is enabled; approve Locus for global shortcuts",
            "available": False,
            "action": "open_accessibility",
        }
    return {
        "state": "warning",
        "detail": "approve Locus for global shortcuts and app control",
        "available": False,
        "action": "open_accessibility",
    }


def open_accessibility_settings() -> dict[str, Any]:
    os_profile = detect_os()
    if os_profile.family == "windows":
        return {
            "ok": False,
            "detail": "Accessibility is macOS-only. On Windows, approve app-control prompts when they appear.",
        }
    if sys.platform != "darwin":
        return {"ok": False, "detail": "Accessibility is a macOS setting."}
    urls = [
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility",
    ]
    for url in urls:
        try:
            subprocess.run(["open", url], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {
                "ok": True,
                "detail": "System Settings opened. Add Locus or your terminal app to Accessibility, then return here.",
            }
        except Exception:
            continue
    return {"ok": False, "detail": "Could not open System Settings automatically."}


def _run_quiet(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)


def _install_ollama(emit: EventHandler | None = None) -> None:
    from scripts.runtime_policy import auto_install_ollama

    if shutil.which("ollama"):
        _emit(emit, "ollama", "Ollama", "done", "available")
        return
    if not auto_install_ollama():
        raise RuntimeError("Ollama is required for automatic model downloads.")

    os_profile = detect_os()
    _emit(emit, "ollama", "Ollama", "running", f"installing Ollama for {os_profile.name}")
    if os_profile.family == "macos":
        if shutil.which("brew"):
            _run_stream(["brew", "install", "--cask", "ollama"], emit, "ollama", "Ollama")
        else:
            raise RuntimeError("Homebrew is required to install Ollama automatically on macOS.")
    elif os_profile.family == "windows":
        if shutil.which("winget"):
            _run_stream(
                [
                    "winget",
                    "install",
                    "--exact",
                    "--id",
                    "Ollama.Ollama",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                emit,
                "ollama",
                "Ollama",
            )
        else:
            raise RuntimeError("winget is required to install Ollama automatically on Windows.")
    else:
        raise RuntimeError("Automatic Ollama installation is supported on macOS and Windows only.")

    if not shutil.which("ollama"):
        raise RuntimeError("Ollama installation finished, but the ollama command is not available on PATH.")
    _emit(emit, "ollama", "Ollama", "done", "available")


def _ollama_list(timeout: float = 8.0) -> subprocess.CompletedProcess[str] | None:
    if not shutil.which("ollama"):
        return None
    try:
        return _run_quiet(["ollama", "list"], timeout=timeout)
    except Exception:
        return None


def _ensure_ollama_server(emit: EventHandler | None = None) -> None:
    result = _ollama_list(timeout=5.0)
    if result is not None and result.returncode == 0:
        return

    _emit(emit, "ollama", "Ollama", "running", "starting local Ollama service for model downloads")
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["ollama", "serve"], **kwargs)
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(0.8)
        result = _ollama_list(timeout=5.0)
        if result is not None and result.returncode == 0:
            return
    raise RuntimeError("Ollama did not become ready for model downloads.")


def _installed_ollama_models() -> set[str]:
    result = _ollama_list(timeout=5.0)
    if result is None or result.returncode != 0:
        return set()
    installed: set[str] = set()
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            installed.add(parts[0])
    return installed


def _recommended_models_from(recommendation: dict[str, Any] | None) -> list[str]:
    if not recommendation:
        return []
    return [str(model) for model in recommendation.get("recommended_models", []) if model]


def _format_gb(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    return f"{float(value):.1f} GB"


def model_download_plan(recommendation: dict[str, Any] | None) -> dict[str, Any]:
    """Return a permission-safe model download plan without pulling anything."""
    from scripts.runtime_policy import auto_install_models, skip_model_validation

    pull_plan = list((recommendation or {}).get("pull_plan") or [])
    installed: set[str] = set()
    installed_check_skipped = skip_model_validation() and not auto_install_models()
    if not installed_check_skipped:
        installed = _installed_ollama_models()

    models: list[dict[str, Any]] = []
    total_size_gb = 0.0
    missing_size_gb = 0.0
    missing_models: list[str] = []
    for item in pull_plan:
        model = str(item.get("model") or "").strip()
        if not model:
            continue
        approx_size = float(item.get("approx_size_gb") or 0.0)
        total_size_gb += approx_size
        is_installed = (model in installed) if not installed_check_skipped else False
        is_missing = not is_installed
        if is_missing:
            missing_models.append(model)
            missing_size_gb += approx_size
        models.append(
            {
                "model": model,
                "approx_size_gb": round(approx_size, 2),
                "estimated_runtime_ram_gb": item.get("estimated_runtime_ram_gb"),
                "estimated_runtime_vram_gb": item.get("estimated_runtime_vram_gb"),
                "roles": item.get("roles", []),
                "command": item.get("command") or f"ollama pull {model}",
                "installed": is_installed,
                "missing": is_missing,
            }
        )

    try:
        disk = shutil.disk_usage(Path.home())
        free_disk_gb = round(disk.free / (1024**3), 2)
    except Exception:
        free_disk_gb = None
    safety_margin_gb = round(max(2.0, missing_size_gb * 0.15), 2) if missing_size_gb else 0.0
    required_disk_gb = round(missing_size_gb + safety_margin_gb, 2)
    enough_disk = True if free_disk_gb is None else free_disk_gb >= required_disk_gb

    return {
        "models": models,
        "model_count": len(models),
        "missing_models": missing_models,
        "missing_model_count": len(missing_models),
        "total_size_gb": round(total_size_gb, 2),
        "missing_size_gb": round(missing_size_gb, 2),
        "safety_margin_gb": safety_margin_gb,
        "required_disk_gb": required_disk_gb,
        "free_disk_gb": free_disk_gb,
        "enough_disk": enough_disk,
        "install_location_hint": str(Path.home() / ".ollama" / "models"),
        "permission_required": bool(missing_models) and not auto_install_models(),
        "auto_install_enabled": auto_install_models(),
        "installed_check_skipped": installed_check_skipped,
    }


def _model_download_status(recommendation: dict[str, Any] | None) -> dict[str, Any]:
    from scripts.runtime_policy import auto_install_models

    models = _recommended_models_from(recommendation)
    plan = model_download_plan(recommendation)
    missing_count = int(plan.get("missing_model_count") or len(models))
    missing_size = float(plan.get("missing_size_gb") or 0.0)
    required_disk = float(plan.get("required_disk_gb") or missing_size)
    free_disk = plan.get("free_disk_gb")
    free_text = f"; {_format_gb(free_disk)} free" if free_disk is not None else ""
    if not auto_install_models():
        return {
            "state": "warning" if missing_count else "done",
            "detail": (
                f"permission needed before download: {missing_count} model(s), "
                f"~{_format_gb(missing_size)} download, {_format_gb(required_disk)} with margin{free_text}"
            )
            if missing_count
            else "recommended model files already installed",
            "required": False,
            "missing": plan.get("missing_models", models),
            "action": f"Approve model downloads ({_format_gb(missing_size)})" if missing_count else "none",
            "plan": plan,
        }
    if not models:
        return {
            "state": "pending",
            "detail": "waiting for model recommendation",
            "required": True,
            "missing": [],
            "action": "Generate recommendation",
            "plan": plan,
        }
    if not plan.get("enough_disk", True):
        return {
            "state": "error",
            "detail": (
                f"not enough disk space for {_format_gb(missing_size)} of models "
                f"({_format_gb(required_disk)} needed with margin{free_text})"
            ),
            "required": True,
            "missing": plan.get("missing_models", models),
            "action": "Free disk space or skip model downloads",
            "plan": plan,
        }
    if not shutil.which("ollama"):
        return {
            "state": "pending",
            "detail": f"Ollama will be installed, then {missing_count} model(s) will download (~{_format_gb(missing_size)})",
            "required": True,
            "missing": models,
            "action": "Install Ollama and models",
            "plan": plan,
        }
    missing = list(plan.get("missing_models") or [])
    if not missing:
        return {
            "state": "done",
            "detail": f"{len(models)} recommended model(s) installed",
            "required": True,
            "missing": [],
            "action": "none",
            "plan": plan,
        }
    return {
        "state": "pending",
        "detail": f"{len(missing)}/{len(models)} recommended model(s) need download (~{_format_gb(missing_size)})",
        "required": True,
        "missing": missing,
        "action": "Download recommended models",
        "plan": plan,
    }


def _install_recommended_models(emit: EventHandler | None, recommendation: dict[str, Any] | None) -> None:
    from scripts.runtime_policy import auto_install_models

    plan = model_download_plan(recommendation)
    if not auto_install_models():
        _emit(
            emit,
            "model_downloads",
            "Model Assets",
            "warning",
            f"waiting for permission before downloading ~{_format_gb(float(plan.get('missing_size_gb') or 0.0))}",
        )
        return

    models = _recommended_models_from(recommendation)
    if not models:
        raise RuntimeError("No recommended models were available to download.")
    if not plan.get("enough_disk", True):
        raise RuntimeError(
            "Not enough disk space for recommended model downloads: "
            f"{_format_gb(float(plan.get('required_disk_gb') or 0.0))} needed with margin, "
            f"{_format_gb(plan.get('free_disk_gb'))} free."
        )
    _install_ollama(emit)
    _ensure_ollama_server(emit)
    installed = _installed_ollama_models()
    missing = [model for model in models if model not in installed]
    if not missing:
        _emit(emit, "model_downloads", "Model Downloads", "done", f"{len(models)} recommended model(s) already installed")
        return

    size_by_model = {item.get("model"): item.get("approx_size_gb") for item in plan.get("models", [])}
    for index, model in enumerate(missing, start=1):
        size = _format_gb(size_by_model.get(model))
        _emit(emit, "model_downloads", "Model Downloads", "running", f"downloading {model} ({index}/{len(missing)}, ~{size})")
        _run_stream(["ollama", "pull", model], emit, "model_downloads", "Model Downloads")
    _emit(emit, "model_downloads", "Model Downloads", "done", f"installed {len(models)} recommended model(s)")


def approve_model_downloads() -> dict[str, Any]:
    """Persist explicit consent to download the recommended model bundle."""
    from scripts.model_selector import recommend_models
    from scripts.runtime_policy import update_runtime

    recommendation = recommend_models()
    plan = model_download_plan(recommendation)
    if os.getenv("LOCAL_COMPUTER_AUTO_INSTALL_MODELS", "").strip().lower() in FALSY:
        raise RuntimeError("Model downloads are disabled by LOCAL_COMPUTER_AUTO_INSTALL_MODELS=0.")
    if os.getenv("LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA", "").strip().lower() in FALSY and not shutil.which("ollama"):
        raise RuntimeError("Ollama installation is disabled by LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA=0.")
    if not plan.get("enough_disk", True):
        raise RuntimeError(
            "Not enough disk space for recommended model downloads: "
            f"{_format_gb(float(plan.get('required_disk_gb') or 0.0))} needed with margin, "
            f"{_format_gb(plan.get('free_disk_gb'))} free."
        )
    runtime = update_runtime({"auto_install_models": True, "auto_install_ollama": True})
    _write_state(
        {
            "model_download_permission": "approved",
            "model_download_plan": plan,
            "model_download_approved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    return {"ok": True, "runtime": runtime, "plan": plan}


def decline_model_downloads() -> dict[str, Any]:
    """Persist an explicit skip so setup remains clear but model-free."""
    from scripts.runtime_policy import update_runtime

    runtime = update_runtime({"auto_install_models": False})
    _write_state(
        {
            "model_download_permission": "skipped",
            "model_download_skipped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    return {"ok": True, "runtime": runtime}


def setup_status(lightweight: bool = False) -> dict[str, Any]:
    """Return current setup status without installing anything."""
    os_profile = detect_os()
    current_python = Path(sys.executable)
    setup_python = _venv_python() if _venv_python().exists() else current_python
    missing = [] if lightweight else _missing_modules(setup_python)
    chromium_ready = True if lightweight else False if missing else _playwright_chromium_ready(setup_python)
    model_recommendation = ROOT / "configs" / "models.recommended.json"
    state = _load_state()
    from scripts.resource_policy import resource_budget

    budget = (
        SimpleNamespace(gpu_limit_pct=90.0, max_ram_gb=8.0, low_ram_mode=False, pressure_adjusted=False)
        if lightweight
        else resource_budget()
    )
    recommendation: dict[str, Any] | None = None
    if not lightweight:
        try:
            from scripts.model_selector import recommend_models

            recommendation = recommend_models()
        except Exception:
            recommendation = None
    full_disk = (
        {"state": "warning", "detail": "deferred during CI dashboard smoke", "action": "open_full_disk_access"}
        if lightweight and os_profile.family == "macos"
        else full_disk_access_status()
    )
    accessibility = (
        {"state": "warning", "detail": "deferred during CI dashboard smoke", "action": "open_accessibility"}
        if lightweight and os_profile.family == "macos"
        else accessibility_status()
    )
    ollama_available = bool(shutil.which("ollama"))
    model_downloads = _model_download_status(recommendation)
    ollama_required = bool(model_downloads.get("required"))
    dirs_ready = all(path.exists() for path in LOCAL_DIRS)
    dirs_detail = f"outputs, logs, uploads, {STATE_DIR}"

    plugin_state = "done" if (ROOT / "plugins").exists() else "pending"
    plugin_detail = "manifest directory present" if plugin_state == "done" else "missing plugin manifests"
    plugin_action = "none" if plugin_state == "done" else "Restore plugins folder"
    try:
        from scripts.plugin_runtime import execute_tool

        if not lightweight:
            diagnostics = execute_tool("workspace", "plugin_diagnostics", {})
            summary = diagnostics.get("summary", {}) if isinstance(diagnostics, dict) else {}
            declared = int(summary.get("declared_tools", 0) or 0)
            implemented = int(summary.get("implemented_declared_tools", 0) or 0)
            pending = int(summary.get("pending_declared_tools", 0) or 0)
            plugins = int(summary.get("plugins", 0) or 0)
            if declared:
                plugin_state = "done" if pending == 0 else "warning"
                plugin_detail = f"{implemented}/{declared} tools ready across {plugins} plugins"
                plugin_action = "none" if pending == 0 else "Open Plugin Center"
    except Exception as exc:
        plugin_state = "warning"
        plugin_detail = f"could not run plugin diagnostics: {exc}"
        plugin_action = "Open Plugin Center"

    steps = [
        _step(
            "os",
            "Operating System",
            "done" if os_profile.supported else "error",
            f"{os_profile.name} {os_profile.version or os_profile.release}".strip(),
            action="none" if os_profile.supported else "Use macOS or Windows",
            help_text=os_profile.setup_copy,
        ),
        _step(
            "python",
            "Python runtime",
            "done" if sys.version_info >= (3, 12) else "error",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            action="none" if sys.version_info >= (3, 12) else "Install Python 3.12 or newer",
            help_text="Locus uses Python for the local dashboard, plugins, setup, and browser automation.",
        ),
        _step(
            "venv",
            "Virtual environment",
            "done" if _venv_python().exists() else "pending",
            str(_venv_python()),
            action="none" if _venv_python().exists() else "Created automatically during setup",
            help_text="This keeps Locus dependencies isolated from the rest of your computer.",
        ),
        _step(
            "deps",
            "Python dependencies",
            "done" if not missing else "pending",
            "installed" if not missing else "missing: " + ", ".join(missing),
            action="none" if not missing else "Installed automatically during setup",
            help_text="Only local app dependencies are installed here; no models are downloaded.",
        ),
        _step(
            "chromium",
            "Playwright Chromium",
            "done" if chromium_ready else "pending",
            "browser installed" if chromium_ready else "needs install",
            action="none" if chromium_ready else "Installed automatically during setup",
            help_text="Used for the local in-app browser and browser-control plugins.",
        ),
        _step(
            "dirs",
            "Local workspace folders",
            "done" if dirs_ready else "pending",
            dirs_detail,
            action="none" if dirs_ready else "Created automatically during setup",
            help_text="Stores logs, uploads, setup state, browser state, memory, and local run history.",
        ),
        _step(
            "plugins",
            "Plugin registry",
            plugin_state,
            plugin_detail,
            action=plugin_action,
            help_text="Plugins are the main way Locus works with files, repos, browser control, uploads, GitHub, email, memory, and automations.",
        ),
        _step(
            "models",
            "Model recommendation",
            "done" if model_recommendation.exists() else "pending",
            "recommendation file generated before downloads",
            action="none" if model_recommendation.exists() else "Generated automatically during setup",
            help_text="Locus recommends local models from CPU/GPU/RAM data before pulling any model files.",
        ),
        _step(
            "model_downloads",
            "Model Assets",
            str(model_downloads["state"]),
            str(model_downloads["detail"]),
            required=ollama_required,
            action=str(model_downloads.get("action") or "none"),
            help_text="Model files are optional. The frontend, plugins, setup, uploads, browser control, and history work before any download.",
            group="required" if ollama_required else "optional",
        ),
        _step(
            "safety",
            "Safety limits",
            "done",
            f"GPU capped at {budget.gpu_limit_pct:.0f}%; RAM budget {budget.max_ram_gb:.1f} GB",
            action="none",
            help_text="The cap is applied before model mode can run. Locus still recommends closing other heavy apps.",
        ),
        _step(
            "full_disk",
            "Full Disk Access",
            full_disk["state"],
            full_disk["detail"],
            required=False,
            action=str(full_disk.get("action") or ("open_full_disk_access" if full_disk["state"] == "warning" else "none")),
            help_text=os_profile.checklist_notes[0] if os_profile.checklist_notes else os_profile.permission_copy,
            group="permissions",
        ),
        _step(
            "accessibility",
            "Keyboard Shortcut Access",
            accessibility["state"],
            accessibility["detail"],
            required=False,
            action=str(accessibility.get("action") or ("open_accessibility" if accessibility["state"] == "warning" else "none")),
            help_text=os_profile.checklist_notes[1] if len(os_profile.checklist_notes) > 1 else os_profile.permission_copy,
            group="permissions",
        ),
        _step(
            "other_apps",
            "Close Heavy Apps",
            "warning",
            "highly recommended before local model mode",
            required=False,
            action="Close memory-heavy apps manually",
            help_text="This avoids memory pressure on 8 GB and 16 GB machines, especially when a local model is enabled.",
            group="recommended",
        ),
        _step(
            "ollama",
            "Ollama",
            "done" if ollama_available or not ollama_required else "pending",
            "available" if ollama_available else "will be installed for model downloads" if ollama_required else "not required until local model mode",
            required=ollama_required,
            action="none" if ollama_available else "Install Ollama automatically" if ollama_required else "Install Ollama only if you want local model mode",
            help_text="Model-free workspace mode, setup, plugins, uploads, and recommendations work without Ollama.",
            group="required" if ollama_required else "optional",
        ),
    ]
    complete = all(step["state"] == "done" for step in steps if step["required"])
    required_steps = [step for step in steps if step["required"]]
    optional_steps = [step for step in steps if not step["required"]]
    checklist = {
        "required_ready": sum(1 for step in required_steps if step["state"] == "done"),
        "required_total": len(required_steps),
        "optional_warnings": sum(1 for step in optional_steps if step["state"] in {"warning", "error"}),
        "needs_action": [
            {
                "id": step["id"],
                "title": step["title"],
                "state": step["state"],
                "action": step.get("action", ""),
            }
            for step in steps
            if step["state"] != "done" and step.get("action") and step.get("action") != "none"
        ],
    }
    return {
        "complete": complete,
        "os": os_profile.to_dict(),
        "wizard": _setup_wizard(os_profile, budget, steps, recommendation, complete),
        "checklist": checklist,
        "model_download_plan": model_downloads.get("plan", {}),
        "state": state,
        "steps": steps,
        "state_dir": str(STATE_DIR),
        "setup_state_path": str(STATE_PATH),
    }


def run_bootstrap(emit: EventHandler | None = None) -> None:
    """Create/repair the Python environment needed to start the app."""
    _emit(emit, "venv", "Virtual environment", "running", "checking .venv")
    if not _venv_python().exists():
        _run_stream([sys.executable, "-m", "venv", str(VENV)], emit, "venv", "Virtual environment")
    _emit(emit, "venv", "Virtual environment", "done", str(_venv_python()))

    python = _venv_python()
    missing = _missing_modules(python)
    deps_stale = not DEPS_SENTINEL.exists() or (REQUIREMENTS.exists() and REQUIREMENTS.stat().st_mtime > DEPS_SENTINEL.stat().st_mtime)
    if missing or deps_stale:
        detail = "installing " + (", ".join(missing) if missing else "updated requirements")
        _emit(emit, "deps", "Python dependencies", "running", detail)
        _run_stream([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], emit, "deps", "Python dependencies")
        _run_stream([str(python), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)], emit, "deps", "Python dependencies")
        DEPS_SENTINEL.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n")
    _emit(emit, "deps", "Python dependencies", "done", "installed")

    if not _playwright_chromium_ready(python):
        _run_stream([str(python), "-m", "playwright", "install", "chromium"], emit, "chromium", "Playwright Chromium")
    _emit(emit, "chromium", "Playwright Chromium", "done", "browser installed")


def run_app_setup(emit: EventHandler | None = None) -> dict[str, Any]:
    """Run dashboard-visible first-use setup.

    Model files are optional and download only when automatic model setup is
    explicitly enabled. Inference still stays off until local model mode is
    explicitly enabled.
    """
    _emit(emit, "setup", "Setup", "running", "preparing Locus")
    os_profile = detect_os()
    _emit(
        emit,
        "os",
        "Operating System",
        "done" if os_profile.supported else "error",
        os_profile.setup_copy,
    )
    if not os_profile.supported:
        raise RuntimeError("Locus currently supports macOS and Windows only.")

    python = _venv_python() if _venv_python().exists() else Path(sys.executable)

    if _missing_modules(python):
        _emit(emit, "deps", "Python dependencies", "running", "repairing current environment")
        _run_stream([str(python), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)], emit, "deps", "Python dependencies")
    _emit(emit, "deps", "Python dependencies", "done", "installed")

    if not _playwright_chromium_ready(python):
        _run_stream([str(python), "-m", "playwright", "install", "chromium"], emit, "chromium", "Playwright Chromium")
    _emit(emit, "chromium", "Playwright Chromium", "done", "browser installed")

    _emit(emit, "dirs", "Local workspace folders", "running", "creating local folders")
    for path in LOCAL_DIRS:
        path.mkdir(parents=True, exist_ok=True)
    _emit(emit, "dirs", "Local workspace folders", "done", f"outputs, logs, uploads, {STATE_DIR}")

    _emit(emit, "plugins", "Plugin registry", "running", "checking plugin manifests")
    from scripts.plugin_manager import registry_snapshot

    plugins = registry_snapshot()
    _emit(emit, "plugins", "Plugin registry", "done", f"{plugins.get('enabled_count', 0)} enabled plugins")

    _emit(emit, "models", "Model recommendation", "running", "detecting hardware and writing recommendation")
    from scripts.model_selector import recommend_models, write_recommendation

    recommendation = recommend_models()
    recommendation_path = write_recommendation(recommendation=recommendation)
    _emit(emit, "models", "Model recommendation", "done", f"wrote {recommendation_path.name}")

    _install_recommended_models(emit, recommendation)

    _emit(emit, "workspace", "Workspace index", "running", "indexing current folder")
    from scripts.workspace_index import build_workspace_index

    index = build_workspace_index(write_cache=True)
    _emit(emit, "workspace", "Workspace index", "done", f"{index.get('file_count', 0)} files indexed")

    from scripts.resource_policy import resource_budget

    budget = resource_budget()
    _emit(
        emit,
        "safety",
        "Safety limits",
        "done",
        f"GPU capped at {budget.gpu_limit_pct:.0f}%; RAM budget {budget.max_ram_gb:.1f} GB",
    )

    full_disk = full_disk_access_status()
    _emit(emit, "full_disk", "Full Disk Access", full_disk["state"], full_disk["detail"])

    accessibility = accessibility_status()
    _emit(emit, "accessibility", "Keyboard Shortcut Access", accessibility["state"], accessibility["detail"])

    ollama_detail = "available" if shutil.which("ollama") else "optional; skipped because local model mode is opt-in"
    _emit(emit, "ollama", "Ollama", "done" if shutil.which("ollama") else "warning", ollama_detail)

    _write_state({"completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    _emit(emit, "setup", "Setup", "done", "ready")
    return setup_status()


def _print_event(event: dict[str, Any]) -> None:
    title = event.get("title", "Setup")
    state = event.get("state", "")
    detail = event.get("detail", "")
    output = event.get("output", "")
    if output:
        print(f"[setup] {output}", flush=True)
    else:
        suffix = f" - {detail}" if detail else ""
        print(f"[setup] {title}: {state}{suffix}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Locus setup manager")
    parser.add_argument("--status", action="store_true", help="Print setup status as JSON")
    parser.add_argument("--model-download-plan", action="store_true", help="Print recommended model download plan as JSON")
    parser.add_argument("--approve-model-downloads", action="store_true", help="Approve automatic recommended model downloads on next setup run")
    parser.add_argument("--skip-model-downloads", action="store_true", help="Keep recommended model downloads disabled")
    parser.add_argument("--bootstrap", action="store_true", help="Create/repair venv, dependencies, and Playwright")
    parser.add_argument("--app-setup", action="store_true", help="Run app-level setup")
    parser.add_argument("--open-full-disk-access", action="store_true", help="Open macOS Full Disk Access settings")
    parser.add_argument("--open-accessibility", action="store_true", help="Open macOS Accessibility settings")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(setup_status(), indent=2))
        return
    if args.model_download_plan:
        from scripts.model_selector import recommend_models

        print(json.dumps(model_download_plan(recommend_models()), indent=2))
        return
    if args.approve_model_downloads:
        print(json.dumps(approve_model_downloads(), indent=2))
        return
    if args.skip_model_downloads:
        print(json.dumps(decline_model_downloads(), indent=2))
        return
    if args.bootstrap:
        run_bootstrap(_print_event)
        return
    if args.app_setup:
        run_app_setup(_print_event)
        return
    if args.open_full_disk_access:
        print(json.dumps(open_full_disk_access_settings(), indent=2))
        return
    if args.open_accessibility:
        print(json.dumps(open_accessibility_settings(), indent=2))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
