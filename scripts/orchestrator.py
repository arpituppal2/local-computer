"""Mission orchestrator with multi-agent task graph, parallel dispatch, and chatbot routing.

Upgrade: goals are now decomposed by task_planner.build_task_graph() into a
DAG of typed tasks, each executed by a specialist agent_roles agent.
Falls back to the legacy navigation_agent stage loop for simple tasks.
"""
from __future__ import annotations
import json
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ollama_client import call_json, MODEL_PLANNER, MODEL_HEAVY
from scripts.navigation_agent import run_mission
from scripts.router import route_goal, complexity_score
from scripts.subagents import run_parallel_subagents, dispatch
from scripts.task_planner import build_task_graph, tasks_to_stages
from scripts.agent_roles import get_agent

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


# ── Legacy plan_mission (kept for --parallel / chatbot-only flows) ────────────

def plan_mission(goal: str) -> dict:
    """Lightweight single-model plan for simple goals or chatbot-only dispatch."""
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


# ── New: multi-agent task-graph execution ─────────────────────────────────────

def _execute_task_graph(goal: str) -> str:
    """Decompose goal → task DAG → execute each task with its specialist agent.

    Tasks with no unfulfilled dependencies are dispatched in parallel.
    Results are accumulated and passed as context to dependent tasks.
    """
    from playwright.sync_api import sync_playwright
    from scripts.memory import Memory
    from scripts.navigation_agent import SEARCH_BASE
    import threading

    tasks  = build_task_graph(goal)
    stages = {t["id"]: t for t in tasks}

    completed: dict[str, dict] = {}   # task_id → result dict
    memory = Memory()
    results_lock = threading.Lock()

    def _run_task(task: dict, page, context):
        agent  = get_agent(task["role"])
        result = agent.run(task, page=page, context=context, memory=memory)
        with results_lock:
            completed[task["id"]] = result
        logging.info(f"[orchestrator] task {task['id']} ({task['role']}) → {result['status']}")

    def _ready(task: dict) -> bool:
        return all(d in completed for d in task["depends_on"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx     = browser.new_context()
        page    = ctx.new_page()
        page.goto(SEARCH_BASE + goal.replace(" ", "+"))

        max_rounds = len(tasks) + 2
        for _ in range(max_rounds):
            pending = [t for t in tasks if t["id"] not in completed and _ready(t)]
            if not pending:
                break

            # Sort by priority, run ready tasks in parallel threads
            pending.sort(key=lambda t: t["priority"])
            threads = [
                threading.Thread(target=_run_task, args=(t, page, ctx), daemon=True)
                for t in pending
            ]
            for th in threads: th.start()
            for th in threads: th.join()

        browser.close()

    # Collect writer output, else fall back to analyst, else concatenate findings
    for role in ("writer", "analyst", "researcher"):
        for tid, res in completed.items():
            if stages[tid]["role"] == role and res.get("findings"):
                return res["findings"]

    return "\n\n".join(
        f"## {stages[tid]['role'].title()}: {stages[tid]['goal'][:60]}\n{res.get('findings','')}"
        for tid, res in completed.items()
    )


def _execute_stages_parallel(plan: dict) -> list[dict]:
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


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="local-computer mission runner")
    parser.add_argument("goal", nargs="*", help="Goal string")
    parser.add_argument("--parallel", action="store_true",
                        help="Dispatch all stages as parallel subagents (legacy mode)")
    parser.add_argument("--chatbot", metavar="BACKEND",
                        help="Force chatbot subagent: gemini|chatgpt|claude|copilot|perplexity")
    parser.add_argument("--simple", action="store_true",
                        help="Skip task-graph planner; use legacy sequential stage loop")
    args = parser.parse_args()

    goal = " ".join(args.goal).strip() or input("Goal: ").strip()
    if not goal:
        print("No goal provided."); sys.exit(1)

    # Direct chatbot shortcut
    if args.chatbot:
        from scripts.ai_chatbot_subagent import chatbot_query
        logging.info(f"[orchestrator] Direct chatbot dispatch → {args.chatbot}")
        result = chatbot_query(goal, backend=args.chatbot)
        print(result["response"] or f"[ERROR] {result['error']}")
        return

    # Legacy --simple or --parallel flags
    if args.simple or args.parallel:
        plan = plan_mission(goal)
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
        run_mission(plan, root=ROOT)
        return

    # Default: multi-agent task graph
    output = _execute_task_graph(goal)
    out_path = ROOT / "outputs" / "result.md"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(output)
    print(output)


if __name__ == "__main__":
    main()
