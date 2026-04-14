"""Uses LLM to generate a deterministic JSON step plan for a browser task."""
import json
import re
import sys
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen3:4b"
TASKS_DIR  = Path(__file__).resolve().parent.parent / "tasks"

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "browser_steps.txt").read_text


def extract_json_block(text: str) -> str:
    matches = re.findall(r"\{.*\}", text, flags=re.DOTALL)
    if not matches:
        raise ValueError("No JSON object found in model response")
    return matches[-1]


def call_planner(task: str) -> dict:
    prompt_text = PROMPT() if callable(PROMPT) else PROMPT
    payload = {"model": MODEL, "prompt": prompt_text + "\n\nTask:\n" + task, "stream": False}
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    text = resp.json()["response"]
    return json.loads(extract_json_block(text))


def main():
    TASKS_DIR.mkdir(exist_ok=True)
    task  = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Go to https://example.com and take a screenshot."
    plan  = call_planner(task)
    out   = TASKS_DIR / "browser_steps.json"
    out.write_text(json.dumps(plan, indent=2))
    print(f"Wrote plan to {out}")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
