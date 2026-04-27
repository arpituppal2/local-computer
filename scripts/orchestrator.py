"""Mission orchestrator with parallel stage dispatch and chatbot subagent routing."""
from __future__ import annotations
import json, sys, time, logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ollama_client import call_json, MODEL_PLANNER, MODEL_HEAVY
from scripts.navigation_agent import run_mission
from scripts.router import route_goal, complexity_score
from scripts.subagents import run_parallel_subagents, dispatch

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

_rt_path = ROOT / "configs" / "runtime.json"
_rt = json.loads(_rt_path.read_text()) if _rt_path.exists() else {}


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
    """Ask the planner model to decompose the goal into stages.

    Each stage gets a chatbot_mode flag if its complexity is too high for local Ollama.
    """
    parallel_keywords = ["while also", "simultaneously", "in parallel", "multiple", "&&"]
    use_heavy = any(k in goal.lower() for k in parallel_keywords)
    model = MODEL_HEAVY if use_heavy else MODEL_PLANNER

    prompt = (
        f"Create a research mission plan for this goal: {goal}\n\n"
        "Return JSON:\n"
        '{"mission_name": "...", "stages": [{"stage": "...", "goal": "...", "max_steps": 15}]}'
    )
    plan = call_json(prompt, model=model)
    if not plan or "stages" not in plan:
        plan = {"mission_name": goal[:60], "stages": [{"stage": "research", "goal": goal, "max_steps": 20}]}

    for s in plan.get("stages", []):
        s["max_steps"] = _safe_int(s.get("max_steps"), 15)
        # Tag each stage with its routing decision
        route = route_goal(s.get("goal", goal))
        s["chatbot_mode"]    = route["mode"] == "chatbot"
        s["chatbot_backend"] = route.get("chatbot_backend")
        s["complexity"]      = route["complexity"]

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mission_file = out_dir / f"mission_{ts}.json"
    mission_file.write_text(json.dumps(plan, indent=2))
    logging.info(f"[orchestrator] Mission plan saved to {mission_file}")

    return plan


def _execute_stages_parallel(plan: dict) -> list[dict]:
    """Dispatch all stages in parallel as subagent tasks.

    Browser-based stages (chatbot or regular) each get their own tab.
    Local Ollama stages share the MAX_LOCAL_PARALLEL semaphore.
    """
    stages = plan.get("stages", [])
    tasks = [
        {
            "goal":            s.get("goal", ""),
            "complexity":      s.get("complexity", 0),
            "chatbot_mode":    s.get("chatbot_mode", False),
            "chatbot_backend": s.get("chatbot_backend"),
        }
        for s in stages
    ]
    logging.info(f"[orchestrator] Dispatching {len(tasks)} stage(s) in parallel")
    return run_parallel_subagents(tasks)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="local-computer mission runner")
    parser.add_argument("goal", nargs="*", help="Goal string")
    parser.add_argument("--parallel", action="store_true",
                        help="Dispatch all stages as parallel subagents (skips browser loop)")
    parser.add_argument("--chatbot", metavar="BACKEND",
                        help="Force chatbot subagent: gemini|chatgpt|claude|copilot|perplexity")
    args = parser.parse_args()

    goal = " ".join(args.goal).strip() or input("Goal: ").strip()
    if not goal:
        print("No goal provided."); sys.exit(1)

    # Direct chatbot shortcut: skip planning, send straight to chatbot UI
    if args.chatbot:
        from scripts.ai_chatbot_subagent import chatbot_query
        logging.info(f"[orchestrator] Direct chatbot dispatch → {args.chatbot}")
        result = chatbot_query(goal, backend=args.chatbot)
        print(result["response"] or f"[ERROR] {result['error']}")
        return

    plan = plan_mission(goal)

    # If --parallel or all stages are chatbot-routed: parallel dispatch
    all_chatbot = all(s.get("chatbot_mode") for s in plan.get("stages", []))
    if args.parallel or all_chatbot:
        results = _execute_stages_parallel(plan)
        combined = "\n\n".join(
            f"## Stage: {r.get('goal', '')[:80]}\n{r.get('output', {}).get('findings', r.get('output', ''))}"
            for r in results
        )
        out = ROOT / "outputs" / "result.md"
        out.write_text(combined)
        print(combined)
        return

    # Default: sequential browser-based research loop
    run_mission(plan, root=ROOT)


if __name__ == "__main__":
    main()
