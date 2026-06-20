"""Local resource policy for macOS and Windows deployments.

The limits here are intentionally conservative. Apple Silicon uses unified
memory, and low-RAM Windows laptops still need enough free RAM for the OS,
browser automation, and filesystem plugins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.hardware_profile import HardwareProfile, detect_hardware
from scripts.os_profile import detect_os
from scripts.runtime_policy import _load_runtime, max_gpu_percent


@dataclass
class ResourceBudget:
    total_ram_gb: float
    available_ram_gb: float | None
    memory_pressure: str
    configured_max_ram_gb: float
    max_ram_gb: float
    usable_for_models_gb: float
    reserved_system_gb: float
    gpu_limit_pct: float
    pressure_adjusted: bool
    low_ram_mode: bool
    os_family: str
    supported_os: bool
    platform_notes: list[str]
    warnings: list[str]
    env: dict[str, str]


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def configured_max_ram_gb() -> float | None:
    env_value = _env_float("LOCAL_COMPUTER_MAX_RAM_GB")
    if env_value is not None:
        return env_value
    runtime = _load_runtime()
    raw = runtime.get("max_ram_gb")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def default_max_ram_gb(profile: HardwareProfile) -> float:
    ram = profile.ram_gb
    if profile.os_family == "windows":
        if ram <= 8.5:
            return 4.0
        if ram <= 12.5:
            return 6.0
        if ram <= 16.5:
            return 8.5
        if ram <= 24.5:
            return 15.0
        if ram <= 32.5:
            return 18.0
        return max(22.0, ram * 0.58)

    if ram <= 8.5:
        return 4.5
    if ram <= 12.5:
        return 6.5
    if ram <= 16.5:
        return 9.5
    if ram <= 24.5:
        return 17.0
    if ram <= 32.5:
        return 20.0
    if ram >= 48:
        return max(36.0, ram * 0.76)
    return max(24.0, ram * 0.62)


def current_available_ram_gb() -> float | None:
    """Return currently available system RAM without starting model runtimes."""
    try:
        import psutil

        return round(float(psutil.virtual_memory().available) / (1024**3), 2)
    except Exception:
        return None


def _memory_pressure(profile: HardwareProfile, available_ram_gb: float | None) -> str:
    if available_ram_gb is None:
        return "unknown"
    ratio = available_ram_gb / max(profile.ram_gb, 1.0)
    if available_ram_gb < 2.5 or ratio < 0.14:
        return "critical"
    if available_ram_gb < 4.0 or ratio < 0.22:
        return "high"
    if ratio < 0.35:
        return "elevated"
    return "nominal"


def _minimum_free_ram_gb(profile: HardwareProfile) -> float:
    os_padding = 0.5 if profile.os_family == "windows" else 0.0
    if profile.ram_gb <= 8.5:
        return 1.4 + os_padding
    if profile.ram_gb <= 16.5:
        return 2.0 + os_padding
    if profile.ram_gb <= 32.5:
        return 2.5 + os_padding
    return 3.0 + os_padding


def _reservation_floor(profile: HardwareProfile) -> float:
    if profile.os_family == "windows":
        if profile.ram_gb <= 8.5:
            return 3.5
        if profile.ram_gb <= 16.5:
            return 3.0
        return 3.5
    if profile.ram_gb <= 8.5:
        return 2.5
    if profile.ram_gb <= 16.5:
        return 2.5
    return 3.0


def _os_name_for_budget(family: str, detected_name: str) -> str:
    if family == "macos":
        return "macOS"
    if family == "windows":
        return "Windows"
    return detected_name


def _platform_notes_for_budget(family: str, detected_notes: list[str]) -> list[str]:
    if family == "macos":
        return [
            "Uses conservative unified-memory budgets.",
            "Caps MPS/GPU pressure at the configured GPU limit.",
            "Keeps one loaded local model and one local job by default on low-RAM Macs.",
        ]
    if family == "windows":
        return [
            "Uses conservative system-memory budgets and avoids macOS-only MPS settings.",
            "Keeps one loaded local model and one local job by default on low-RAM PCs.",
            "Stores local app state under the Windows local app data folder.",
        ]
    return list(detected_notes)


def resource_budget(
    profile: HardwareProfile | None = None,
    max_ram_gb: float | None = None,
    available_ram_gb: float | None = None,
) -> ResourceBudget:
    profile = profile or detect_hardware()
    os_profile = detect_os()
    os_family = profile.os_family or os_profile.family
    os_name = _os_name_for_budget(os_family, os_profile.name)
    supported_os = (profile.supported_os or os_family in {"macos", "windows"}) if profile.os_family else os_profile.supported
    requested = max_ram_gb if max_ram_gb is not None else configured_max_ram_gb()
    default_budget = default_max_ram_gb(profile)
    configured_max_ram = requested if requested is not None else default_budget
    configured_max_ram = max(
        2.0,
        min(float(configured_max_ram), profile.ram_gb - 1.0 if profile.ram_gb > 4 else profile.ram_gb),
    )
    available = current_available_ram_gb() if available_ram_gb is None else round(float(available_ram_gb), 2)
    pressure = _memory_pressure(profile, available)
    pressure_adjusted = False
    max_ram = configured_max_ram
    if available is not None:
        pressure_cap = max(2.0, available - _minimum_free_ram_gb(profile))
        if pressure_cap < max_ram:
            max_ram = pressure_cap
            pressure_adjusted = True
    max_ram = max(2.0, min(float(max_ram), configured_max_ram))

    reserved = max(_reservation_floor(profile), profile.ram_gb - max_ram)
    usable = max(1.0, max_ram * 0.82)
    warnings: list[str] = []
    platform_notes = _platform_notes_for_budget(os_family, os_profile.optimization_notes)
    if not supported_os:
        warnings.append(f"{os_name} is not supported yet. Locus currently supports macOS and Windows.")
    if pressure in {"critical", "high"}:
        warnings.append(
            f"Current memory pressure is {pressure}; only {available:.1f} GB RAM appears available right now."
        )
    elif pressure == "elevated":
        warnings.append(f"Current memory pressure is elevated; {available:.1f} GB RAM appears available right now.")
    if pressure_adjusted:
        warnings.append(
            f"Locus reduced its effective RAM budget from {configured_max_ram:.1f} GB to {max_ram:.1f} GB to leave memory for {os_name}."
        )
    if requested is not None:
        if requested < 4:
            warnings.append("Max RAM below 4 GB will be very slow and will cause more routing fallbacks/errors.")
        elif requested < 6:
            warnings.append("Max RAM below 6 GB is only suitable for tiny local models and deterministic plugins.")
    if profile.ram_gb <= 8.5 and profile.apple_silicon:
        warnings.append("8 GB unified memory requires tiny models, low context, one local job, and aggressive fallbacks.")
    elif profile.ram_gb <= 8.5:
        warnings.append("8 GB RAM requires tiny models, low context, one local job, and aggressive fallbacks.")
    if max_ram < default_budget * 0.75:
        warnings.append("Configured RAM cap is below the recommended budget for this machine.")
    gpu_limit = max_gpu_percent()
    if gpu_limit < 75:
        warnings.append("Compute cap below 75% may make local models and browser-control loops noticeably slower.")
    elif gpu_limit > 90:
        warnings.append("Compute cap above 90% can cause heat, fan noise, and system instability on long local runs.")
    warnings.append("For best results, it is highly recommended not to use other apps while Locus is running.")

    env = {
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_FLASH_ATTENTION": "1",
        "LOCAL_COMPUTER_MAX_GPU_PERCENT": str(int(gpu_limit) if gpu_limit.is_integer() else gpu_limit),
        "TOKENIZERS_PARALLELISM": "false",
    }
    if profile.os_family == "macos":
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = f"{gpu_limit / 100:.2f}"
    if profile.ram_gb <= 8.5 or max_ram <= 5:
        env["OLLAMA_KEEP_ALIVE"] = "1m"
    elif max_ram <= 8:
        env["OLLAMA_KEEP_ALIVE"] = "5m"
    else:
        env["OLLAMA_KEEP_ALIVE"] = "15m"

    return ResourceBudget(
        total_ram_gb=profile.ram_gb,
        available_ram_gb=available,
        memory_pressure=pressure,
        configured_max_ram_gb=round(configured_max_ram, 2),
        max_ram_gb=round(max_ram, 2),
        usable_for_models_gb=round(usable, 2),
        reserved_system_gb=round(reserved, 2),
        gpu_limit_pct=round(gpu_limit, 1),
        pressure_adjusted=pressure_adjusted,
        low_ram_mode=profile.ram_gb <= 8.5 or max_ram <= 5,
        os_family=os_family,
        supported_os=supported_os,
        platform_notes=platform_notes,
        warnings=warnings,
        env=env,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Locus RAM budget")
    parser.add_argument("--max-ram-gb", type=float)
    parser.add_argument("--simulate-available-ram-gb", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    budget = resource_budget(max_ram_gb=args.max_ram_gb, available_ram_gb=args.simulate_available_ram_gb)
    payload = asdict(budget)
    if args.json:
        print(json.dumps(payload, indent=2))
        return
    print(f"Total RAM: {budget.total_ram_gb:.1f} GB")
    if budget.available_ram_gb is not None:
        print(f"Available RAM now: {budget.available_ram_gb:.1f} GB ({budget.memory_pressure})")
    else:
        print("Available RAM now: unknown")
    if budget.pressure_adjusted:
        print(f"Configured Max Locus RAM: {budget.configured_max_ram_gb:.1f} GB")
    print(f"Max Locus RAM: {budget.max_ram_gb:.1f} GB")
    print(f"Usable for models: {budget.usable_for_models_gb:.1f} GB")
    os_name = detect_os().name
    print(f"OS: {os_name}")
    print(f"Reserved for {os_name}/apps: {budget.reserved_system_gb:.1f} GB")
    cap_label = "GPU/MPS cap" if budget.os_family == "macos" else "GPU cap"
    print(f"{cap_label}: {budget.gpu_limit_pct:.0f}%")
    if budget.warnings:
        print("Warnings:")
        for warning in budget.warnings:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
