"""Goal routing: chatbot UI | workflow | search | browse.

Routing hierarchy (first match wins):
  1. chatbot — explicit AI assistant request, or high-complexity reasoning
  2. workflow — known productivity app (Docs, Drive, Calendar, etc.)
  3. search — information lookup
  4. browse — default general navigation
"""
from __future__ import annotations
import re

# ── Chatbot trigger patterns ───────────────────────────────────────────────────
# These tell the orchestrator to use AI chatbot UI subagents instead of local Ollama
_CHATBOT_PATTERNS: list[tuple[str, str]] = [
    # Explicit backend requests
    (r'\bask\s+gemini\b',      "gemini"),
    (r'\buse\s+gemini\b',      "gemini"),
    (r'\bvia\s+gemini\b',      "gemini"),
    (r'\bask\s+chatgpt\b',     "chatgpt"),
    (r'\buse\s+chatgpt\b',     "chatgpt"),
    (r'\bask\s+gpt\b',         "chatgpt"),
    (r'\buse\s+claude\b',      "claude"),
    (r'\bask\s+claude\b',      "claude"),
    (r'\bvia\s+claude\b',      "claude"),
    (r'\bask\s+copilot\b',     "copilot"),
    (r'\buse\s+copilot\b',     "copilot"),
    (r'\bask\s+perplexity\b',  "perplexity"),
    (r'\buse\s+perplexity\b',  "perplexity"),
    # Implicit heavy-reasoning triggers (route to auto-selected chatbot)
    (r'\bprove\s+that\b',      "auto"),
    (r'\bderive\s+',           "auto"),
    (r'\brefactor.*entire\b',  "auto"),
    (r'\bwrite.*full.*report\b', "auto"),
    (r'\bdeep\s+analysis\b',   "auto"),
    (r'\bsynthesize.*across\b', "auto"),
    (r'\bcomprehensive.*overview\b', "auto"),
]

# ── App workflow routes ────────────────────────────────────────────────────────
_APP_ROUTES: list[tuple[str, str]] = [
    (r'\bdocs?\b',      "https://docs.google.com"),
    (r'\bdrive\b',      "https://drive.google.com"),
    (r'\bcalendar\b',   "https://calendar.google.com"),
    (r'\byoutube\b',    "https://youtube.com"),
    (r'\bprose\b',      "prose"),
    (r'\bgmail\b',      "https://mail.google.com"),
    (r'\bnotion\b',     "https://notion.so"),
    (r'\blinear\b',     "https://linear.app"),
    (r'\bgithub\b',     "https://github.com"),
]


def complexity_score(goal: str) -> int:
    """Estimate task complexity 0-10.

    >= 7 → recommend chatbot routing (too heavy for local 8b model).
    """
    g = goal.lower()
    score = 0
    # Length signals complexity
    if len(goal) > 200: score += 2
    elif len(goal) > 80: score += 1
    # Reasoning keywords
    heavy_kw = [
        "prove", "derive", "mathematical", "theorem", "formally",
        "comprehensive", "full report", "synthesize", "deep analysis",
        "refactor", "entire codebase", "across multiple", "in depth",
        "explain why", "research paper",
    ]
    for kw in heavy_kw:
        if kw in g:
            score += 2
    # Counting conjunctions (multi-part tasks)
    score += min(3, sum(1 for kw in [" and ", " while ", " also ", " then ", " after "] if kw in g))
    return min(10, score)


def route_goal(goal: str) -> dict:
    """Return routing dict for a goal string.

    Returns
    -------
    dict with keys:
        mode (str): 'chatbot' | 'workflow' | 'search' | 'browse'
        url (str): start URL (chatbot sites included)
        chatbot_backend (str | None): which AI chatbot to use
        complexity (int): 0-10 complexity score
        answer (str): empty — filled by agent later
    """
    g = (goal or "").lower().strip()
    if not g:
        return {"mode": "browse", "url": "https://www.bing.com", "chatbot_backend": None, "complexity": 0, "answer": ""}

    score = complexity_score(goal)

    # 1. Explicit chatbot routes
    for pattern, backend in _CHATBOT_PATTERNS:
        if re.search(pattern, g):
            from scripts.ai_chatbot_subagent import BACKENDS, DEFAULT_BACKEND
            resolved = backend if backend != "auto" else _auto_pick(g)
            return {
                "mode": "chatbot",
                "url": BACKENDS.get(resolved, BACKENDS[DEFAULT_BACKEND])["url"],
                "chatbot_backend": resolved,
                "complexity": score,
                "answer": "",
            }

    # 2. High complexity → auto chatbot
    from scripts.ollama_client import CHATBOT_THRESHOLD
    if score >= CHATBOT_THRESHOLD:
        from scripts.ai_chatbot_subagent import pick_best_backend, BACKENDS, DEFAULT_BACKEND
        resolved = pick_best_backend(goal)
        return {
            "mode": "chatbot",
            "url": BACKENDS.get(resolved, BACKENDS[DEFAULT_BACKEND])["url"],
            "chatbot_backend": resolved,
            "complexity": score,
            "answer": "",
        }

    # 3. Workflow / app routes
    for pattern, dest in _APP_ROUTES:
        if re.search(pattern, g):
            return {"mode": "workflow", "url": dest, "chatbot_backend": None, "complexity": score, "answer": ""}

    # 4. Default: Bing search
    encoded = goal.replace(" ", "+")
    return {
        "mode": "browse",
        "url": f"https://www.bing.com/search?q={encoded}",
        "chatbot_backend": None,
        "complexity": score,
        "answer": "",
    }


def _auto_pick(goal: str) -> str:
    """Pick a chatbot backend automatically when the router says 'auto'."""
    from scripts.ai_chatbot_subagent import pick_best_backend
    return pick_best_backend(goal)
