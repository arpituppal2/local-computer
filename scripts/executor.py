"""Browser action executor with config-driven timeouts, ambiguity guards, batch limits (fixes #25-28)."""
from __future__ import annotations
import json, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_rt = json.loads((ROOT / "configs" / "runtime.json").read_text())

CLICK_TIMEOUT = _rt.get("click_timeout", 12000)
NAV_TIMEOUT   = _rt.get("nav_timeout", 8000)
IDLE_TIMEOUT  = _rt.get("idle_timeout", 2500)
BATCH_MAX     = 20

def execute(page, context, action: dict, depth: int = 0) -> dict:
    act  = action.get("action", "")
    val  = action.get("value", "")
    text = str(action.get("text") or action.get("selector") or val or "")

    if act == "navigate":
        try:
            page.goto(str(val), timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            return {"ok": True}
        except Exception as e:
            logging.warning(f"[executor] navigate failed: {e}")
            return {"ok": False, "error": str(e)}

    elif act == "click":
        try:
            matches = page.locator(f"text={text}").all()
            if len(matches) > 5:
                logging.warning(f"[executor] click: {len(matches)} matches for '{text}', using first")
            matches[0].click(timeout=CLICK_TIMEOUT)
            return {"ok": True}
        except Exception as e2:
            return {"ok": False, "error": str(e2)}

    elif act == "type":
        try:
            page.keyboard.type(str(val))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif act == "fill":
        try:
            selector = action.get("selector", "input:visible")
            page.locator(selector).first.fill(str(val), timeout=CLICK_TIMEOUT)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif act == "press":
        try:
            page.keyboard.press(str(val))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif act == "scroll":
        try:
            page.evaluate(f"window.scrollBy(0, {int(val or 600)})")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif act == "go_back":
        try:
            page.go_back(timeout=NAV_TIMEOUT)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif act == "get_page_text":
        cached = action.get("_cached_text")
        if cached:
            return {"ok": True, "text": cached}
        try:
            text_val = page.locator("body").inner_text(timeout=3000)
            return {"ok": True, "text": text_val}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif act == "batch":
        if depth >= 2:
            return {"ok": False, "error": "batch depth limit reached"}
        sub_actions = action.get("actions", [])[:BATCH_MAX]
        results = []
        for sub in sub_actions:
            r = execute(page, context, sub, depth=depth + 1)
            results.append(r)
            if not r.get("ok"):
                break
        return {"ok": True, "results": results}

    return {"ok": False, "error": f"unknown action: {act}"}
