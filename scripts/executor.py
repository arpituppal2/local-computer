"""Executes structured agent actions via Playwright."""
from __future__ import annotations


def execute(page, context, action: dict) -> dict:
    act = action.get("action")

    if act == "navigate":
        try:
            page.goto(str(action.get("value", "")), wait_until="domcontentloaded", timeout=10000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "click":
        target = action.get("target") or {}
        tid = target.get("target_id")
        try:
            if tid:
                loc = page.locator(f'[data-agent-id="{tid}"]')
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    return {"ok": True}
        except Exception:
            pass
        txt = (target.get("text") or "").strip()
        if txt:
            try:
                page.get_by_text(txt, exact=False).first.click(timeout=2500)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "no clickable target"}

    if act == "type":
        target = action.get("target") or {}
        tid = target.get("target_id") or action.get("target_id")
        value = str(action.get("value", ""))
        try:
            if tid:
                loc = page.locator(f'[data-agent-id="{tid}"]')
                if loc.count() > 0:
                    loc.first.fill(value, timeout=2500)
                    return {"ok": True}
            page.keyboard.type(value)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "fill":
        target = action.get("target") or {}
        tid = target.get("target_id")
        if not tid:
            return {"ok": False, "error": "missing target_id"}
        try:
            loc = page.locator(f'[data-agent-id="{tid}"]')
            loc.first.fill(str(action.get("value", "")), timeout=2500)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "press":
        try:
            page.keyboard.press(str(action.get("value", "")))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "batch":
        for sub in action.get("actions", []):
            result = execute(page, context, sub)
            if not result.get("ok"):
                return result
        return {"ok": True}

    if act == "go_back":
        try:
            page.go_back(wait_until="domcontentloaded", timeout=8000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "scroll":
        try:
            page.mouse.wheel(0, int(action.get("value", 900)))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "get_page_text":
        try:
            return {"ok": True, "text": page.locator("body").inner_text(timeout=1500)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "open_in_new_tab":
        target = action.get("target") or {}
        href = target.get("href") or ""
        if href:
            try:
                new_page = context.new_page()
                new_page.goto(href, wait_until="domcontentloaded", timeout=10000)
                return {"ok": True, "new_page": new_page}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "no href for new tab"}

    if act == "finish":
        return {"ok": True, "finish": True, "text": action.get("value", "")}

    return {"ok": False, "error": f"unknown action: {act}"}
