#!/usr/bin/env python3
"""Top-level mission planner + entrypoint.

Usage:
    python scripts/orchestrator.py 'research the latest AI news'
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests

# ── Model config (tuned for your Ollama list) ─────────────────────────────────
MODEL_FAST   = "qwen3:4b"    # router, quick tasks
MODEL_SMART  = "qwen3:14b"   # mission planner, claim extraction
OLLAMA_HOST  = "http://localhost:11434"
ROOT         = Path(__file__).resolve().parent.parent


def ensure_arc_running() -> None:
    try:
        requests.get("http://localhost:9222/json/version", timeout=1)
        print("[ORCH] Arc is alive on port 9222.")
    except Exception:
        print("[ORCH] Launching Arc with debug port 9222...")
        subprocess.Popen(
            ["/Applications/Arc.app/Contents/MacOS/Arc", "--remote-debugging-port=9222"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(4)


def call_ollama(model: str, prompt: str, fmt: str = "json") -> dict:
    payload = {"model": model, "prompt": prompt, "stream": False, "format": fmt}
    resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=90)
    resp.raise_for_status()
    text = resp.json().get("response", "{}").strip()
    try:
        return json.loads(text)
    except Exception:
        return {}


def plan_mission(goal: str) -> dict:
    """Ask local LLM to break the goal into browser stages."""
    system = (
        "You are a mission planner for a local browser agent.\n"
        "Given a user's goal, output ONLY a JSON object with:\n"
        "- mission_name: short string\n"
        "- start_url: URL where the agent should begin (usually https://www.bing.com)\n"
        "- stages: list of 2-5 stages, each with name, goal, max_steps (<=25)\n"
        "Do NOT include explanations or markdown.\n"
    )
    prompt = f"{system}\n\nUser goal:\n{goal}\n"
    print(f"[ORCH] Planning mission via {MODEL_SMART}...")
    obj = call_ollama(MODEL_SMART, prompt, fmt="json") or {}

    mission = {
        "mission_name": obj.get("mission_name") or "auto_mission",
        "start_url": obj.get("start_url") or "https://www.bing.com",
        "stages": [],
    }
    for s in obj.get("stages", [])[:5]:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or "stage"
        sgoal = s.get("goal") or goal
        max_steps = min(int(s.get("max_steps") or 15), 25)
        mission["stages"].append({"name": name, "goal": sgoal, "max_steps": max_steps})

    if not mission["stages"]:
        mission["stages"] = [{"name": "main", "goal": goal, "max_steps": 20}]

    return mission


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: orchestrator.py ''", file=sys.stderr)
        raise SystemExit(1)

    goal = " ".join(sys.argv[1:])
    ensure_arc_running()

    mission = plan_mission(goal)
    mission_path = ROOT / "auto_mission.json"
    mission_path.write_text(json.dumps(mission, indent=2, ensure_ascii=False))
    print(f"[ORCH] Mission written to {mission_path}")
    print(json.dumps(mission, indent=2))

    nav_agent = ROOT / "scripts" / "navigation_agent.py"
    if nav_agent.exists():
        print("[ORCH] Handing off to navigation_agent.py...")
        proc = subprocess.run([sys.executable, str(nav_agent), str(mission_path)], cwd=str(ROOT))
        raise SystemExit(proc.returncode)
    else:
        print("[ORCH] navigation_agent.py not found yet — mission saved, run it manually.")


if __name__ == "__main__":
    main()
