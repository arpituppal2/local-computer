"""Browser action executor — click, type, fill, scroll, hover, drag, screenshot,
file read/write, code execution, and batch actions.
"""
from __future__ import annotations
import json
import logging
import subprocess
import sys
import time
import textwrap
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_rt = json.loads((ROOT / "configs" / "runtime.json").read_text())

CLICK_TIMEOUT = _rt.get("click_timeout", 12000)
NAV_TIMEOUT   = _rt.get("nav_timeout",   8000)
IDLE_TIMEOUT  = _rt.get("idle_timeout",  2500)
BATCH_MAX     = 20
OUT_DIR       = ROOT / _rt.get("outputs_dir", "outputs")


def _wait_for_load(page, timeout: int = 3000) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass


def _locate(page, text: str, timeout: int = CLICK_TIMEOUT):
    for loc in [
        page.get_by_text(text, exact=True),
        page.locator(f"text={text}"),
        page.locator(text) if text.startswith(("#", ".", "[", "//")) else None,
    ]:
        if loc is None:
            continue
        try:
            if loc.first.is_visible(timeout=1000):
                return loc.first
        except Exception:
            pass
    return None


def execute(page, context, action: dict, depth: int = 0) -> dict:  # noqa: C901
    act = action.get("action", "")
    val = action.get("value", "")
    text = str(action.get("text") or action.get("selector") or val or "")

    # ── goto (alias used by BrowserAgent) ──────────────────────────────────
    if act in ("goto", "navigate"):
        url = action.get("url") or str(val)
        if not url.startswith(("http://", "https://", "chrome://")):
            url = "https://" + url
        try:
            page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            return {"ok": True}
        except Exception as e:
            logging.warning(f"[executor] goto failed: {e}")
            return {"ok": False, "error": str(e)}

    # ── Click ──────────────────────────────────────────────────────────────
    elif act == "click":
        x, y = action.get("x"), action.get("y")
        if x is not None and y is not None:
            try:
                page.mouse.click(float(x), float(y))
                _wait_for_load(page)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        # Try CSS selector first if it looks like one
        selector = action.get("selector", "")
        if selector:
            try:
                page.locator(selector).first.click(timeout=CLICK_TIMEOUT)
                _wait_for_load(page)
                return {"ok": True}
            except Exception:
                pass
        loc = _locate(page, text)
        if loc is None:
            return {"ok": False, "error": f"no element found for '{text}'"}
        try:
            loc.click(timeout=CLICK_TIMEOUT)
            _wait_for_load(page)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Hover ───────────────────────────────────────────────────────────────
    elif act == "hover":
        x, y = action.get("x"), action.get("y")
        if x is not None and y is not None:
            try:
                page.mouse.move(float(x), float(y))
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        loc = _locate(page, text)
        if loc is None:
            return {"ok": False, "error": f"no element found for '{text}'"}
        try:
            loc.hover(timeout=CLICK_TIMEOUT)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Mouse move ─────────────────────────────────────────────────────────
    elif act == "mouse_move":
        try:
            page.mouse.move(float(action.get("x", 0)), float(action.get("y", 0)))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Drag ────────────────────────────────────────────────────────────────
    elif act == "drag":
        try:
            x1, y1 = float(action["x1"]), float(action["y1"])
            x2, y2 = float(action["x2"]), float(action["y2"])
            page.mouse.move(x1, y1)
            page.mouse.down()
            page.mouse.move(x2, y2, steps=10)
            page.mouse.up()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Type ─────────────────────────────────────────────────────────────────
    elif act == "type":
        try:
            page.keyboard.type(str(val))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Fill ─────────────────────────────────────────────────────────────────
    elif act == "fill":
        selector = action.get("selector", "input:visible")
        fill_val = action.get("value", val)
        try:
            page.locator(selector).first.fill(str(fill_val), timeout=CLICK_TIMEOUT)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Press ─────────────────────────────────────────────────────────────────
    elif act == "press":
        key = action.get("key") or str(val)
        try:
            page.keyboard.press(key)
            _wait_for_load(page)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Scroll ───────────────────────────────────────────────────────────────
    elif act == "scroll":
        direction = str(action.get("direction", "down")).lower()
        # Accept amount from multiple field names the LLM might use
        raw_amount = action.get("amount") or action.get("value") or action.get("pixels") or 600
        try:
            amount = int(raw_amount)
        except (TypeError, ValueError):
            amount = 600
        delta_y = amount if direction == "down" else -amount
        try:
            page.evaluate(f"window.scrollBy(0, {delta_y})")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Select (dropdown) ────────────────────────────────────────────────────
    elif act == "select":
        selector = action.get("selector", "select")
        select_val = action.get("value", val)
        try:
            page.locator(selector).first.select_option(str(select_val), timeout=CLICK_TIMEOUT)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Go back ──────────────────────────────────────────────────────────────
    elif act == "go_back":
        try:
            page.go_back(timeout=NAV_TIMEOUT)
            _wait_for_load(page)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Screenshot ────────────────────────────────────────────────────────────
    elif act == "screenshot":
        try:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            name = action.get("filename") or f"screenshot_{int(time.time())}.png"
            path = OUT_DIR / name
            page.screenshot(path=str(path), full_page=action.get("full_page", False))
            return {"ok": True, "path": str(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Get page text ─────────────────────────────────────────────────────────
    elif act == "get_page_text":
        cached = action.get("_cached_text")
        if cached:
            return {"ok": True, "text": cached}
        try:
            t = page.locator("body").inner_text(timeout=3000)
            return {"ok": True, "text": t}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Open new tab ─────────────────────────────────────────────────────────
    elif act == "open_tab":
        try:
            new_page = context.new_page()
            if val:
                new_page.goto(str(val), timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            return {"ok": True, "note": "new tab opened"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Close tab ─────────────────────────────────────────────────────────────
    elif act == "close_tab":
        try:
            page.close()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Wait ─────────────────────────────────────────────────────────────────
    elif act == "wait":
        try:
            ms = int(action.get("ms") or action.get("value") or 1000)
            time.sleep(ms / 1000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Read local file ────────────────────────────────────────────────────────
    elif act == "read_file":
        try:
            p = Path(str(val)).expanduser()
            if not p.exists():
                return {"ok": False, "error": f"file not found: {p}"}
            return {"ok": True, "content": p.read_text(errors="replace")[:8000]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Write local file ───────────────────────────────────────────────────────
    elif act == "write_file":
        try:
            p = Path(str(action.get("path", val))).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(action.get("content", "")))
            return {"ok": True, "path": str(p)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Run Python code ───────────────────────────────────────────────────────
    elif act == "run_code":
        code = str(action.get("code", val))
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(textwrap.dedent(code))
                tmp = f.name
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=30
            )
            return {
                "ok":     result.returncode == 0,
                "stdout": result.stdout[:4000],
                "stderr": result.stderr[:2000],
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "code execution timed out (30s)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass

    # ── Batch ────────────────────────────────────────────────────────────────
    elif act == "batch":
        if depth >= 2:
            return {"ok": False, "error": "batch depth limit reached"}
        results = []
        for sub in action.get("actions", [])[:BATCH_MAX]:
            r = execute(page, context, sub, depth=depth + 1)
            results.append(r)
            if not r.get("ok"):
                break
        return {"ok": True, "results": results}

    # ── Done (BrowserAgent sentinel — should never reach executor) ──────
    elif act == "done":
        return {"ok": True}

    return {"ok": False, "error": f"unknown action: {act}"}
