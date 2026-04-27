"""AI chatbot UI subagent — uses Playwright to submit a prompt to a cloud AI
(Gemini, ChatGPT, Claude, Copilot, or Perplexity) and return the response text.

Designed for tasks that exceed local model capability (14b+ reasoning,
long-form synthesis, complex math). Runs in a non-headless Chromium tab
so the user can log in / handle CAPTCHAs on first use.

Usage
-----
    from scripts.ai_chatbot_subagent import chatbot_query

    result = chatbot_query("Explain the Riemann hypothesis", backend="gemini")
    print(result["response"])
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

# ── Backend configs ────────────────────────────────────────────────────────────
# Each entry: url, input_selector, submit_selector (None = Enter key),
# response_selector, response_poll_interval_s, max_wait_s

BACKENDS: dict[str, dict] = {
    "gemini": {
        "url": "https://gemini.google.com/app",
        "input_selector": "rich-textarea div[contenteditable='true'], textarea[aria-label], textarea",
        "submit_selector": None,  # press Enter
        "response_selector": "message-content, .response-content, model-response .markdown",
        "poll_interval": 1.5,
        "max_wait": 90,
    },
    "chatgpt": {
        "url": "https://chatgpt.com",
        "input_selector": "#prompt-textarea, textarea[data-id='root']",
        "submit_selector": "button[data-testid='send-button']",
        "response_selector": "article[data-testid='conversation-turn']:last-child .markdown, .message:last-child .markdown",
        "poll_interval": 1.5,
        "max_wait": 120,
    },
    "claude": {
        "url": "https://claude.ai/new",
        "input_selector": "div[contenteditable='true'][data-placeholder], .ProseMirror",
        "submit_selector": "button[aria-label='Send Message']",
        "response_selector": ".claude-message:last-child .prose, [data-is-streaming='false']:last-child",
        "poll_interval": 2.0,
        "max_wait": 120,
    },
    "copilot": {
        "url": "https://copilot.microsoft.com",
        "input_selector": "textarea#searchbox, cib-text-input textarea, textarea[name='q']",
        "submit_selector": "button[aria-label='Submit'], cib-icon-button[aria-label='Submit']",
        "response_selector": "cib-message-group:last-child cib-message:last-child .ac-textBlock, .response-message-text",
        "poll_interval": 1.5,
        "max_wait": 90,
    },
    "perplexity": {
        "url": "https://www.perplexity.ai",
        "input_selector": "textarea[placeholder], textarea#pplx-search-input",
        "submit_selector": None,  # press Enter
        "response_selector": ".prose, [data-testid='answer'] .markdown-content",
        "poll_interval": 1.5,
        "max_wait": 90,
    },
}

DEFAULT_BACKEND = "gemini"


def _load_backend_overrides() -> dict:
    """Load any backend URL/selector overrides from configs/models.json."""
    try:
        cfg = json.loads((ROOT / "configs" / "models.json").read_text())
        return cfg.get("chatbot_backends", {})
    except Exception:
        return {}


def chatbot_query(
    prompt: str,
    backend: str = DEFAULT_BACKEND,
    headless: bool = False,
    timeout_override: Optional[int] = None,
) -> dict:
    """Submit a prompt to a web AI chatbot and return the response.

    Parameters
    ----------
    prompt: str
        The question or task to send.
    backend: str
        One of: gemini, chatgpt, claude, copilot, perplexity.
    headless: bool
        False (default) shows the browser so users can log in.
    timeout_override: int | None
        Override max wait seconds.

    Returns
    -------
    dict with keys: backend, prompt, response, success, error
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return {
            "backend": backend,
            "prompt": prompt,
            "response": "",
            "success": False,
            "error": "playwright not installed — run: pip install playwright && playwright install chromium",
        }

    # Merge config overrides
    overrides = _load_backend_overrides()
    cfg = {**BACKENDS.get(backend, BACKENDS[DEFAULT_BACKEND]), **overrides.get(backend, {})}
    max_wait = timeout_override or cfg["max_wait"]

    logging.info(f"[chatbot_subagent] backend={backend} url={cfg['url']} prompt_len={len(prompt)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

        try:
            # ── Navigate ────────────────────────────────────────────────────
            page.goto(cfg["url"], timeout=30_000, wait_until="domcontentloaded")
            time.sleep(2)  # let JS hydrate

            # ── Find input ──────────────────────────────────────────────────
            input_el = None
            for sel in cfg["input_selector"].split(", "):
                try:
                    page.wait_for_selector(sel, timeout=8000, state="visible")
                    input_el = page.locator(sel).first
                    break
                except PWTimeout:
                    continue

            if not input_el:
                return {
                    "backend": backend,
                    "prompt": prompt,
                    "response": "",
                    "success": False,
                    "error": f"Could not find input on {backend}. You may need to log in — open the browser and sign in first.",
                }

            # ── Type prompt ─────────────────────────────────────────────────
            input_el.click()
            # Use keyboard fill for contenteditable, fill() for textarea
            try:
                input_el.fill(prompt)
            except Exception:
                input_el.type(prompt, delay=20)

            time.sleep(0.5)

            # ── Submit ──────────────────────────────────────────────────────
            if cfg["submit_selector"]:
                try:
                    btn = page.locator(cfg["submit_selector"]).first
                    btn.click(timeout=5000)
                except Exception:
                    input_el.press("Enter")
            else:
                input_el.press("Enter")

            # ── Wait for response ───────────────────────────────────────────
            response_text = _poll_for_response(
                page, cfg["response_selector"],
                cfg["poll_interval"], max_wait
            )

            return {
                "backend": backend,
                "prompt": prompt,
                "response": response_text,
                "success": bool(response_text),
                "error": "" if response_text else "No response found after polling",
            }

        except Exception as e:
            logging.error(f"[chatbot_subagent] Error with {backend}: {e}")
            return {"backend": backend, "prompt": prompt, "response": "", "success": False, "error": str(e)}
        finally:
            browser.close()


def _poll_for_response(
    page,
    selector: str,
    poll_interval: float,
    max_wait: float,
) -> str:
    """Poll for a non-empty response element. Returns stripped text or empty string."""
    from playwright.sync_api import TimeoutError as PWTimeout

    deadline = time.time() + max_wait
    last_text = ""
    stable_count = 0
    STABLE_NEEDED = 3  # response text must be stable for 3 consecutive polls

    while time.time() < deadline:
        time.sleep(poll_interval)
        for sel in selector.split(", "):
            try:
                els = page.locator(sel).all()
                if els:
                    # Take last visible element (the most recent message)
                    for el in reversed(els):
                        try:
                            txt = el.inner_text(timeout=2000).strip()
                            if len(txt) > 80:  # meaningful response
                                if txt == last_text:
                                    stable_count += 1
                                    if stable_count >= STABLE_NEEDED:
                                        return txt
                                else:
                                    last_text = txt
                                    stable_count = 0
                                break
                        except Exception:
                            continue
            except Exception:
                continue

    # Return whatever we have even if not fully stable
    return last_text


def pick_best_backend(goal: str) -> str:
    """Heuristically pick the best chatbot backend for a given goal."""
    g = goal.lower()
    if any(k in g for k in ["code", "debug", "python", "javascript", "git", "refactor", "claude"]):
        return "claude"
    if any(k in g for k in ["math", "proof", "equation", "calculus", "statistics", "theorem"]):
        return "chatgpt"
    if any(k in g for k in ["search", "latest", "news", "current", "today", "perplexity"]):
        return "perplexity"
    if any(k in g for k in ["microsoft", "office", "excel", "word", "teams", "copilot"]):
        return "copilot"
    return "gemini"  # default: Gemini 1.5/2.0 for general tasks


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "What is the derivative of x^2?"
    b = pick_best_backend(q)
    print(f"Using backend: {b}")
    r = chatbot_query(q, backend=b)
    print("\n--- RESPONSE ---")
    print(r["response"] or f"[ERROR] {r['error']}")
