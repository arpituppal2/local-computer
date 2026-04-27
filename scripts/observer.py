"""Page observation — domcontentloaded instead of networkidle, no DOM mutation, real can_go_back (fixes #41-43)."""
from __future__ import annotations
import logging

def observe(page) -> dict:
    url = page.url

    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:
        pass

    try:
        targets = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('a,button,input,select,textarea'));
            return els.slice(0, 60).map((el, i) => ({
                idx: i,
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 80),
                href: el.href || null,
                type: el.type || null,
            }));
        }""")
    except Exception as e:
        logging.warning(f"[observer] target extraction failed: {e}")
        targets = []

    try:
        visible_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        visible_text = ""

    try:
        can_go_back = page.evaluate("() => window.history.length > 1")
    except Exception:
        can_go_back = False

    return {
        "url": url,
        "visible_text": visible_text[:6000],
        "targets": targets,
        "can_go_back": can_go_back,
        "target_count": len(targets),
    }
