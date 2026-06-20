"""Hardware-aware Ollama model recommendation.

Safe by default: this module never runs inference and never downloads a model
unless the CLI is called with --pull.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.hardware_profile import GPUInfo, HardwareProfile, detect_hardware
from scripts.resource_policy import ResourceBudget, resource_budget
from scripts.runtime_policy import auto_select_models, max_ram_gb

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "configs" / "model_catalog.json"
DEFAULT_TARGET = ROOT / "configs" / "models.recommended.json"


def _primary_gpu(profile: HardwareProfile) -> dict[str, Any] | None:
    gpu = profile.primary_gpu
    if not gpu:
        return None
    return asdict(gpu)


def _gpu_acceleration(profile: HardwareProfile, budget: ResourceBudget) -> dict[str, Any]:
    primary = profile.primary_gpu
    primary_name = primary.name if primary else ""
    vram = profile.dedicated_vram_gb
    gpu_limit = max(50.0, min(float(budget.gpu_limit_pct), 99.0)) / 100

    if profile.apple_silicon:
        unified_budget = max(1.0, min(budget.usable_for_models_gb, budget.max_ram_gb * gpu_limit))
        if profile.ram_gb <= 8.5:
            tier = "apple_unified_low_ram"
        elif profile.ram_gb >= 48:
            tier = "apple_unified_workstation"
        else:
            tier = "apple_unified_balanced"
        return {
            "kind": "apple_unified",
            "tier": tier,
            "primary_gpu": primary_name or "Apple GPU",
            "dedicated_vram_gb": None,
            "usable_vram_gb": None,
            "model_budget_gb": round(unified_budget, 2),
            "requires_vram_fit": False,
            "use_gpu": True,
            "max_local_parallel": 2 if profile.ram_gb >= 48 and not budget.low_ram_mode else 1,
            "notes": [
                "Apple Silicon uses unified memory, so model size is limited by the RAM budget instead of separate VRAM.",
            ],
        }

    if profile.os_family == "windows" and profile.nvidia_gpu:
        if vram is None:
            usable_vram = None
            tier = "nvidia_unknown_vram"
            model_budget = min(budget.usable_for_models_gb, 6.0)
            notes = [
                "NVIDIA GPU detected, but VRAM could not be confirmed. Locus uses a conservative CUDA budget.",
            ]
        else:
            usable_vram = round(max(1.0, vram * gpu_limit), 2)
            model_budget = min(budget.usable_for_models_gb, usable_vram)
            if vram < 6:
                tier = "nvidia_low_vram"
            elif vram < 10:
                tier = "nvidia_laptop_8gb"
            elif vram < 16:
                tier = "nvidia_desktop_12gb"
            elif vram < 22:
                tier = "nvidia_desktop_16gb"
            else:
                tier = "nvidia_workstation"
            notes = [
                f"NVIDIA VRAM budget is {model_budget:.1f} GB after the {budget.gpu_limit_pct:.0f}% GPU cap.",
            ]
        max_parallel = 1
        if vram is not None and vram >= 16 and profile.ram_gb >= 48 and not budget.low_ram_mode:
            max_parallel = 2
        return {
            "kind": "nvidia_cuda",
            "tier": tier,
            "primary_gpu": primary_name or "NVIDIA GPU",
            "dedicated_vram_gb": round(vram, 2) if vram is not None else None,
            "usable_vram_gb": usable_vram,
            "model_budget_gb": round(model_budget, 2),
            "requires_vram_fit": True,
            "use_gpu": True,
            "max_local_parallel": max_parallel,
            "notes": notes,
        }

    if profile.os_family == "windows" and profile.has_gpu:
        return {
            "kind": "windows_gpu",
            "tier": "non_nvidia_or_unknown",
            "primary_gpu": primary_name or "Windows GPU",
            "dedicated_vram_gb": round(vram, 2) if vram is not None else None,
            "usable_vram_gb": round(vram * gpu_limit, 2) if vram is not None else None,
            "model_budget_gb": round(min(budget.usable_for_models_gb, 6.0), 2),
            "requires_vram_fit": False,
            "use_gpu": True,
            "max_local_parallel": 1,
            "notes": [
                "A Windows GPU was detected, but CUDA-class acceleration was not confirmed. Recommendations stay conservative.",
            ],
        }

    return {
        "kind": "cpu",
        "tier": "cpu_only",
        "primary_gpu": primary_name,
        "dedicated_vram_gb": round(vram, 2) if vram is not None else None,
        "usable_vram_gb": None,
        "model_budget_gb": round(budget.usable_for_models_gb, 2),
        "requires_vram_fit": False,
        "use_gpu": False,
        "max_local_parallel": 1,
        "notes": ["No supported GPU acceleration was detected. Locus will prefer CPU-safe model sizes."],
    }


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def _pick_tier(catalog: dict[str, Any], ram_gb: float) -> dict[str, Any]:
    tiers = catalog.get("tiers", [])
    for tier in tiers:
        if float(tier.get("min_ram_gb", 0)) <= ram_gb <= float(tier.get("max_ram_gb", 999)):
            return tier
    if not tiers:
        raise ValueError("model catalog contains no tiers")
    return tiers[0]


def _unique_models(roles: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for model in roles.values():
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def _threads(profile: HardwareProfile, budget: ResourceBudget) -> int:
    if profile.ram_gb <= 8.5 or budget.max_ram_gb <= 5:
        return max(2, min(4, profile.logical_cores - 2 if profile.logical_cores > 3 else profile.logical_cores))
    if budget.max_ram_gb <= 8:
        return max(2, min(6, profile.logical_cores - 2 if profile.logical_cores > 3 else profile.logical_cores))
    return max(2, min(8, profile.logical_cores - 2 if profile.logical_cores > 3 else profile.logical_cores))


def _model_fits(model_name: str, models: dict[str, Any], budget: ResourceBudget, acceleration: dict[str, Any]) -> bool:
    meta = models.get(model_name, {})
    runtime_ram = float(meta.get("runtime_ram_gb") or meta.get("approx_size_gb") or 99)
    min_ram = float(meta.get("min_ram_gb") or 0)
    if runtime_ram > budget.usable_for_models_gb or budget.total_ram_gb < min_ram:
        return False
    if acceleration.get("requires_vram_fit"):
        runtime_vram = float(meta.get("runtime_vram_gb") or runtime_ram)
        if runtime_vram > float(acceleration.get("model_budget_gb") or 0):
            return False
    return True


def _role_candidates(role: str, models: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    candidates = []
    for name, meta in models.items():
        if role in meta.get("roles", []):
            candidates.append((name, meta))
    candidates.sort(key=lambda item: float(item[1].get("runtime_ram_gb") or item[1].get("approx_size_gb") or 0), reverse=True)
    return candidates


def _fit_roles(
    roles: dict[str, str],
    models: dict[str, Any],
    budget: ResourceBudget,
    acceleration: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    fitted: dict[str, str] = {}
    warnings: list[str] = []
    for role, desired in roles.items():
        if _model_fits(desired, models, budget, acceleration):
            fitted[role] = desired
            continue

        replacement = None
        for name, _meta in _role_candidates(role, models):
            if _model_fits(name, models, budget, acceleration):
                replacement = name
                break

        if replacement is None:
            replacement = "qwen2.5:3b" if role != "memory" else "nomic-embed-text"
            warnings.append(f"No comfortable model fit for role '{role}'; using smallest fallback '{replacement}'.")
        else:
            warnings.append(f"Role '{role}' downgraded from '{desired}' to '{replacement}' to fit RAM budget.")
        fitted[role] = replacement
    return fitted, warnings


def _ctx_for_budget(tier: dict[str, Any], budget: ResourceBudget, acceleration: dict[str, Any]) -> int:
    tier_ctx = int(tier.get("num_ctx", 8192))
    if budget.usable_for_models_gb <= 3.8:
        return 2048
    if budget.usable_for_models_gb <= 5.5:
        return min(tier_ctx, 3072)
    if budget.usable_for_models_gb <= 8.5:
        return min(tier_ctx, 4096)
    if budget.usable_for_models_gb <= 14:
        return min(tier_ctx, 8192)
    if acceleration.get("kind") == "nvidia_cuda":
        model_budget = float(acceleration.get("model_budget_gb") or 0)
        if model_budget < 7:
            return min(tier_ctx, 4096)
        if model_budget < 12:
            return min(tier_ctx, 8192)
        if model_budget < 18:
            return min(tier_ctx, 12288)
    return tier_ctx


def _parallel_for_runtime(tier: dict[str, Any], budget: ResourceBudget, acceleration: dict[str, Any]) -> int:
    if budget.low_ram_mode:
        return 1
    tier_parallel = int(tier.get("max_local_parallel", 1))
    gpu_parallel = int(acceleration.get("max_local_parallel") or 1)
    return max(1, min(tier_parallel, gpu_parallel))


def _timeout_for_model(model: str) -> int:
    if model == "nomic-embed-text":
        return 45
    tag = model.rsplit(":", 1)[-1].lower()
    if tag in {"0.6b", "1.7b", "3b", "4b"}:
        return 45
    if tag == "8b":
        return 90
    if tag == "14b":
        return 120
    if tag == "70b":
        return 420
    return 180


def _acceleration_warnings(profile: HardwareProfile, acceleration: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if acceleration.get("kind") == "nvidia_cuda":
        vram = acceleration.get("dedicated_vram_gb")
        tier = acceleration.get("tier")
        if vram is None:
            warnings.append("NVIDIA GPU detected, but VRAM could not be read; Locus is using conservative CUDA recommendations.")
        elif float(vram) < 10:
            warnings.append("RTX laptop-class VRAM limits heavy local reasoning; Locus will favor 3B/14B staged routing over 70B-class models.")
        elif tier in {"nvidia_desktop_12gb", "nvidia_desktop_16gb"}:
            warnings.append("Desktop NVIDIA VRAM can run strong local models, but 70B-class models are reserved for very high VRAM or unified-memory systems.")
    elif acceleration.get("kind") == "windows_gpu":
        warnings.append("GPU acceleration is not confirmed as NVIDIA CUDA; recommendations remain conservative.")
    elif not profile.has_gpu:
        warnings.append("No GPU was detected. Recommendations will favor CPU-safe settings and lower parallelism.")
    return warnings


def recommend_models(
    profile: HardwareProfile | None = None,
    catalog: dict[str, Any] | None = None,
    max_ram_override_gb: float | None = None,
    available_ram_override_gb: float | None = None,
) -> dict[str, Any]:
    profile = profile or detect_hardware()
    catalog = catalog or load_catalog()
    tier = _pick_tier(catalog, profile.ram_gb)
    budget = resource_budget(
        profile,
        max_ram_override_gb if max_ram_override_gb is not None else max_ram_gb(),
        available_ram_gb=available_ram_override_gb,
    )
    acceleration = _gpu_acceleration(profile, budget)
    models = catalog.get("models", {})
    roles, fit_warnings = _fit_roles(dict(tier.get("roles", {})), models, budget, acceleration)
    recommended = _unique_models(roles)

    warnings: list[str] = list(budget.warnings) + _acceleration_warnings(profile, acceleration) + fit_warnings
    if profile.ram_gb < 9 and profile.apple_silicon:
        warnings.append("8 GB Macs should expect slower responses, smaller context, and more deterministic plugin fallbacks.")
    elif profile.ram_gb < 9:
        warnings.append("8 GB machines should expect slower responses, smaller context, and more deterministic plugin fallbacks.")
    if profile.ram_gb < 24 and "heavy" not in roles:
        warnings.append("Heavy local reasoning is not recommended on this hardware; route hard tasks to plugins, smaller staged plans, or deterministic workspace tools.")

    pull_plan = [
        {
            "model": model,
            "approx_size_gb": models.get(model, {}).get("approx_size_gb"),
            "estimated_runtime_ram_gb": models.get(model, {}).get("runtime_ram_gb"),
            "estimated_runtime_vram_gb": models.get(model, {}).get("runtime_vram_gb"),
            "roles": [role for role, role_model in roles.items() if role_model == model],
            "command": f"ollama pull {model}",
        }
        for model in recommended
    ]

    num_ctx = _ctx_for_budget(tier, budget, acceleration)
    ollama_options = {
        "num_ctx": num_ctx,
        "num_thread": _threads(profile, budget),
        "num_gpu": 999 if acceleration.get("use_gpu") else 0,
        "temperature": 0.0,
    }

    config = {
        **roles,
        "ollama_host": "http://localhost:11434",
        "chatbot_threshold": int(tier.get("chatbot_threshold", 8)),
        "max_local_parallel": _parallel_for_runtime(tier, budget, acceleration),
        "timeouts": {
            model: _timeout_for_model(model)
            for model in recommended
        },
        "ollama_options": ollama_options,
        "resource_budget": asdict(budget),
        "gpu_acceleration": acceleration,
        "_generated_by": "scripts/model_selector.py",
        "_hardware_tier": tier.get("id", "unknown"),
        "_auto_selected": True,
    }

    return {
        "hardware": asdict(profile),
        "tier": tier.get("id", "unknown"),
        "tier_notes": tier.get("notes", ""),
        "resource_budget": asdict(budget),
        "gpu_acceleration": acceleration,
        "roles": roles,
        "pull_plan": pull_plan,
        "recommended_models": recommended,
        "ollama_options": ollama_options,
        "models_config": config,
        "warnings": warnings,
    }


def effective_models_config() -> dict[str, Any]:
    configured = json.loads((ROOT / "configs" / "models.json").read_text()) if (ROOT / "configs" / "models.json").exists() else {}
    if not auto_select_models():
        return configured
    recommendation = recommend_models()
    merged = dict(configured)
    merged.update(recommendation["models_config"])
    return merged


def write_recommendation(target: Path = DEFAULT_TARGET, recommendation: dict[str, Any] | None = None) -> Path:
    recommendation = recommendation or recommend_models()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recommendation["models_config"], indent=2) + "\n")
    return target


def pull_models(models: list[str]) -> None:
    for model in models:
        subprocess.run(["ollama", "pull", model], check=True)


def _print_human(recommendation: dict[str, Any]) -> None:
    hardware = recommendation["hardware"]
    budget = recommendation["resource_budget"]
    acceleration = recommendation.get("gpu_acceleration") or {}
    print(f"Hardware tier: {recommendation['tier']}")
    if recommendation.get("tier_notes"):
        print(f"Tier notes: {recommendation['tier_notes']}")
    print(f"RAM: {hardware['ram_gb']:.1f} GB | CPU: {hardware['cpu_brand']} | cores: {hardware['logical_cores']}")
    print(
        f"RAM budget: max {budget['max_ram_gb']:.1f} GB, "
        f"usable for models {budget['usable_for_models_gb']:.1f} GB, "
        f"reserved {budget['reserved_system_gb']:.1f} GB"
    )
    if budget.get("available_ram_gb") is not None:
        pressure = budget.get("memory_pressure", "unknown")
        adjusted = " pressure-adjusted" if budget.get("pressure_adjusted") else ""
        print(f"Available RAM now: {budget['available_ram_gb']:.1f} GB ({pressure}{adjusted})")
    cap_label = "GPU/MPS cap" if hardware.get("os_family") == "macos" else "GPU cap"
    print(f"{cap_label}: {budget.get('gpu_limit_pct', 90):.0f}%")
    if hardware.get("gpus"):
        print("GPU:", ", ".join(gpu["name"] for gpu in hardware["gpus"]))
    if acceleration:
        vram_text = (
            f", usable VRAM/model budget {float(acceleration.get('model_budget_gb') or 0):.1f} GB"
            if acceleration.get("kind") == "nvidia_cuda"
            else ""
        )
        print(f"Acceleration: {acceleration.get('tier', acceleration.get('kind', 'unknown'))}{vram_text}")
    print("\nRole assignments:")
    for role, model in recommendation["roles"].items():
        print(f"  {role:13s} {model}")
    print("\nDownload plan:")
    for item in recommendation["pull_plan"]:
        size = item["approx_size_gb"]
        runtime = item.get("estimated_runtime_ram_gb")
        size_text = f" (~{size} GB download, ~{runtime} GB runtime RAM)" if size and runtime else ""
        print(f"  {item['command']}{size_text}")
    if recommendation["warnings"]:
        print("\nWarnings:")
        for warning in recommendation["warnings"]:
            print(f"  - {warning}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend local Ollama models for this machine")
    parser.add_argument("--json", action="store_true", help="Print full JSON recommendation")
    parser.add_argument("--write-config", action="store_true", help="Write configs/models.recommended.json")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="Where --write-config writes")
    parser.add_argument("--max-ram-gb", type=float, help="Override max RAM budget for Locus")
    parser.add_argument("--simulate-ram-gb", type=float, help="Simulate installed RAM for testing recommendations")
    parser.add_argument("--simulate-available-ram-gb", type=float, help="Simulate currently available RAM for testing pressure-aware routing")
    parser.add_argument("--simulate-os-family", choices=["macos", "windows"], help="Simulate OS family for testing")
    parser.add_argument("--simulate-gpu-name", help="Simulate a GPU name for testing")
    parser.add_argument("--simulate-gpu-vram-gb", type=float, help="Simulate dedicated GPU VRAM for testing")
    parser.add_argument("--pull", action="store_true", help="Download recommended models with ollama pull")
    parser.add_argument("--quiet", action="store_true", help="Suppress human-readable output")
    args = parser.parse_args()

    profile = detect_hardware()
    updates: dict[str, Any] = {}
    if args.simulate_ram_gb is not None:
        updates["ram_gb"] = float(args.simulate_ram_gb)
    if args.simulate_os_family:
        updates["os_family"] = args.simulate_os_family
        updates["os"] = "Windows" if args.simulate_os_family == "windows" else "Darwin"
        updates["supported_os"] = True
        updates["arch"] = "AMD64" if args.simulate_os_family == "windows" else "arm64"
    if args.simulate_gpu_name or args.simulate_gpu_vram_gb is not None:
        name = args.simulate_gpu_name or ("NVIDIA GPU" if args.simulate_os_family == "windows" else "Apple GPU")
        vendor = "nvidia" if "nvidia" in name.lower() or "rtx" in name.lower() else "apple" if args.simulate_os_family == "macos" else ""
        updates["gpus"] = [GPUInfo(name=name, vendor=vendor, memory_gb=args.simulate_gpu_vram_gb)]
    if updates:
        profile = replace(profile, **updates)
    recommendation = recommend_models(
        profile=profile,
        max_ram_override_gb=args.max_ram_gb,
        available_ram_override_gb=args.simulate_available_ram_gb,
    )
    if args.write_config:
        path = write_recommendation(Path(args.target), recommendation)
        if not args.quiet:
            print(f"Wrote {path}")
    if args.pull:
        pull_models(recommendation["recommended_models"])
    if args.json:
        print(json.dumps(recommendation, indent=2))
    elif not args.quiet and not args.write_config:
        _print_human(recommendation)


if __name__ == "__main__":
    main()
