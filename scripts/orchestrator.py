#!/usr/bin/env python3
"""Entry point: python scripts/orchestrator.py '<goal>'"""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from scripts.ollama_client import MODEL_PLANNER, call_json

import requests

RT = json.loads((ROOT / "configs" / "runtime.json").read_text())


def ensure_arc() -> None:
    port = RT.get("arc_debug_port", 9222)
    try:
        requests.get(f"http://localhost:{port}/json/version", timeout=1)
        print(f"[ORCH] Arc alive on :{port}")
    except Exception:
        print(f"[ORCH] Launching Arc on :{port}...")
        import subprocess as sp
        sp.Popen(["/Applications/Arc.app/Contents/MacOS/Arc",
                  f"--remote-debugging-port={port}"],
                 stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        time.sleep(4)


def plan_mission(goal: str) -> dict:
    prompt = (
        "You are a mission planner for a browser agent.\n"
        "Given a user goal, output ONLY a JSON object with:\n"
        "  mission_name: short string\n"
        "  start_url: URL to begin (usually https://www.bing.com)\n"
        "  stages: list of 2-5 stages, each with name, goal, max_steps (<=25)\n"
        "No explanations. No markdown.\n\n"
        f"Goal: {goal}"
    )
    print(f"[ORCH] Planning via {MODEL_PLANNER}...")
    obj = call_json(prompt, model=MODEL_PLANNER) or {}
    mission = {
        "mission_name": obj.get("mission_name") or "auto_mission",
        "start_url":    obj.get("start_url")    or "https://www.bing.com",
        "stages": [],
    }
    for s in (obj.get("stages") or [])[:5]:
        if not isinstance(s, dict):
            continue
        mission["stages"].append({
            "name":      s.get("name") or "stage",
            "goal":      s.get("goal") or goal,
            "max_steps": min(int(s.get("max_steps") or 15), 25),
        })
    if not mission["stages"]:
        mission["stages"] = [{"name": "main", "goal": goal, "max_steps": 20}]
    return mission


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: orchestrator.py '<goal>'", file=sys.stderr)
        raise SystemExit(1)
    goal = " ".join(sys.argv[1:])
    ensure_arc()
    mission = plan_mission(goal)
    mp = ROOT / "auto_mission.json"
    mp.write_text(json.dumps(mission, indent=2, ensure_ascii=False))
    print(f"[ORCH] Mission → {mp}")
    nav = ROOT / "scripts" / "navigation_agent.py"
    proc = subprocess.run([sys.executable, str(nav), str(mp)], cwd=str(ROOT))
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
