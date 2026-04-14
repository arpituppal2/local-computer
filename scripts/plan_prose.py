"""Generates PROSE-specific deterministic browser step plans."""
import json
import sys
from pathlib import Path

import requests

OLLAMA_URL     = "http://localhost:11434/api/generate"
PLANNER_MODEL  = "qwen3:4b"
ROOT           = Path(__file__).resolve().parent.parent
PROMPT_PATH    = ROOT / "prompts" / "browser_steps_prose.txt"


def call_planner(task: str) -> dict:
    system = PROMPT_PATH.read_text()
    prompt = system + "\n\nUser instruction:\n" + task + "\n\nJSON only:"
    payload = {"model": PLANNER_MODEL, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.1}}
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    text  = resp.json().get("response", "").strip()
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Planner did not return JSON:\n" + text)
    return json.loads(text[start:end + 1])


def main() -> None:
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not task:
        print("Usage: python scripts/plan_prose.py 'task description'")
        raise SystemExit(1)
    plan       = call_planner(task)
    start_url  = plan.get("start_url") or "https://prose.example.com/login"
    steps      = plan.get("steps") or []
    out_dir    = ROOT / "tasks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path   = out_dir / "prose_steps.json"
    out_path.write_text(json.dumps({"start_url": start_url, "steps": steps}, indent=2))
    print(f"Wrote plan to {out_path}")
    print(json.dumps({"start_url": start_url, "steps": steps}, indent=2))


if __name__ == "__main__":
    main()
