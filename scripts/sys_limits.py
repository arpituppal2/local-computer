"""Detects system RAM/CPU and recommends Ollama inference options."""
from __future__ import annotations
import os
import platform
import subprocess


def get_total_ram_gb() -> float:
    try:
        if platform.system() == "Darwin":
            mem_bytes = int(
                subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip()
            )
            return mem_bytes / (1024 ** 3)
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return 8.0


def get_total_cores() -> int:
    return os.cpu_count() or 4


def planner_options() -> dict:
    ram     = get_total_ram_gb()
    cores   = get_total_cores()
    threads = max(2, min(8, cores - 2))
    ctx     = 8192 if ram >= 14 else 4096
    return {"temperature": 0.0, "num_thread": threads, "num_ctx": ctx}


def observer_text_limit() -> int:
    return 3500 if get_total_ram_gb() >= 14 else 1800
