"""Page observation with screenshot capture and a11y-tree fallback.

Perplexity-Computer parity: every observe() call returns
  - visible_text     — body inner-text (truncated)
  - candidate_targets — interactive elements with bounding boxes
  - screenshot_b64   — base64 PNG for vision-capable models
  - title, url, can_go_back
"""
from __future__ import annotations
import base64
import logging


def observe(page, capture_screenshot: bool = True) -> dict:
    url = page.url

    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:
        pass

    # ── interactive elements with bounding boxes ──────────────────────────
    try:
        targets = page.evaluate("""
        () => {
            const els = Array.from(
                document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="link"],[role="menuitem"],[role="tab"]')
            );
            return els.slice(0, 80).map((el, i) => {
                const r = el.getBoundingClientRect();
                return {
                    target_id: i,
                    kind:  el.tagName.toLowerCase(),
                    text:  (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().slice(0, 100),
                    href:  el.href || null,
                    type:  el.type || null,
                    x:     Math.round(r.left + r.width  / 2),
                    y:     Math.round(r.top  + r.height / 2),
                    w:     Math.round(r.width),
                    h:     Math.round(r.height),
                    visible: r.width > 0 && r.height > 0,
                };
            }).filter(t => t.visible);
        }
        """)
    except Exception as e:
        logging.warning(f"[observer] target extraction failed: {e}")
        targets = []

    # ── visible text ──────────────────────────────────────────────────────
    try:
        visible_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        visible_text = ""

    # ── page title ────────────────────────────────────────────────────────
    try:
        title = page.title()
    except Exception:
        title = ""

    # ── history ───────────────────────────────────────────────────────────
    try:
        can_go_back = page.evaluate("() => window.history.length > 1")
    except Exception:
        can_go_back = False

    # ── screenshot (base64 PNG) ───────────────────────────────────────────
    screenshot_b64 = None
    if capture_screenshot:
        try:
            png = page.screenshot(type="png", full_page=False)
            screenshot_b64 = base64.b64encode(png).decode()
        except Exception as e:
            logging.debug(f"[observer] screenshot failed: {e}")

    return {
        "url":               url,
        "title":             title,
        "visible_text":      visible_text[:6000],
        "candidate_targets": targets,
        "target_count":      len(targets),
        "can_go_back":       can_go_back,
        "screenshot_b64":    screenshot_b64,
    }
