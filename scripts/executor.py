from __future__ import annotations


def execute(page, context, action: dict) -> dict:
    act = action.get("action")

    if act == "navigate":
        try:
            page.goto(str(action.get("value", "")), wait_until="domcontentloaded", timeout=12000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "click":
        target = action.get("target") or {}
        tid = target.get("target_id")
        if tid:
            try:
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

    if act == "fill":
        target = action.get("target") or {}
        tid = target.get("target_id")
        if not tid:
            return {"ok": False, "error": "missing target_id"}
        try:
            page.locator(f'[data-agent-id="{tid}"]').first.fill(str(action.get("value", "")), timeout=2500)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "press":
        try:
            page.keyboard.press(str(action.get("value", "")))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "scroll":
        try:
            page.mouse.wheel(0, int(action.get("value", 900)))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "go_back":
        try:
            page.go_back(wait_until="domcontentloaded", timeout=8000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "get_page_text":
        try:
            return {"ok": True, "text": page.locator("body").inner_text(timeout=1500)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "batch":
        for sub in action.get("actions", []):
            r = execute(page, context, sub)
            if not r.get("ok"):
                return r
        return {"ok": True}

    if act == "finish":
        return {"ok": True, "finish": True, "text": action.get("value", "")}

    return {"ok": False, "error": f"unknown action: {act}"}
