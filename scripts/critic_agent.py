# scripts/critic_agent.py

from scripts.ollama_client import call_json, MODEL_HEAVY

def critique(goal: str, memory, state: dict) -> dict:
    """
    Evaluates whether the agent is behaving productively or stuck.
    Returns structured feedback.
    """

    recent_actions = list(memory.recent_actions)[-10:]
    failures = list(memory.recent_failures)[-5:]
    evidence = len(memory.evidence)

    prompt = f"""
You are a critic for a web browsing research agent.

GOAL:
{goal}

CURRENT URL:
{state.get('url')}

RECENT ACTIONS:
{recent_actions}

RECENT FAILURES:
{failures}

EVIDENCE COUNT:
{evidence}

Decide:
1. is_stuck (true/false)
2. reason (string)
3. fix_strategy (one of: "search_refine", "navigate_reset", "continue", "replan")
4. suggested_query (string or empty)

Return ONLY JSON:
{{
  "is_stuck": bool,
  "reason": string,
  "fix_strategy": string,
  "suggested_query": string
}}
"""

    return call_json(prompt, model=MODEL_HEAVY) or {
        "is_stuck": False,
        "reason": "",
        "fix_strategy": "continue",
        "suggested_query": ""
    }
