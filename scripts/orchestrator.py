"""Mission orchestrator — CDP browser polling, safe int parsing, timestamped outputs (fixes #18-21)."""
from __future__ import annotations
import json, sys, time, subprocess, logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ollama_client import call_json, MODEL_PLANNER, MODEL_HEAVY
from scripts.navigation_agent import run_mission

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

_rt_path = ROOT / "configs" / "runtime.json"
_rt = json.loads(_rt_path.read_text())


def _wait_for_browser_ready(port: int, max_wait: float = 10.0, interval: float = 0.5):
    import httpx
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            httpx.get(f"http://localhost:{port}/json/version", timeout=1)
            return True
        except Exception:
            time.sleep(interval)
    return False


def _safe_int(val, default: int) -> int:
    try:
        return int(str(val).split("-")[0].strip())
    except (TypeError, ValueError):
        return default


def plan_mission(goal: str) -> dict:
    parallel_keywords = ["while also", "simultaneously", "in parallel", "multiple", "&&"]
    use_heavy = any(k in goal.lower() for k in parallel_keywords)
    model = MODEL_HEAVY if use_heavy else MODEL_PLANNER

    prompt = (
        f"Create a research mission plan for this goal: {goal}

"
        f"Return JSON:
"
        f'{{"mission_name": "...", "stages": [{{"stage": "...", "goal": "...", "max_steps": 15}}]}}'
    )
    plan = call_json(model, prompt)
    if not plan or "stages" not in plan:
        plan = {"mission_name": goal[:60], "stages": [{"stage": "research", "goal": goal, "max_steps": 20}]}

    for s in plan.get("stages", []):
        s["max_steps"] = _safe_int(s.get("max_steps"), 15)

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mission_file = out_dir / f"mission_{ts}.json"
    mission_file.write_text(json.dumps(plan, indent=2))
    logging.info(f"[orchestrator] Mission plan saved to {mission_file}")

    return plan


def main():
    goal = " ".join(sys.argv[1:]).strip() or input("Goal: ").strip()
    if not goal:
        print("No goal provided."); sys.exit(1)

    plan = plan_mission(goal)
    run_mission(plan, root=ROOT)


if __name__ == "__main__":
    main()
