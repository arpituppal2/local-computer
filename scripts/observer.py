from __future__ import annotations
from typing import Any


def read(page) -> dict[str, Any]:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    try:
        visible_text = page.locator("body").inner_text(timeout=1500)
    except Exception:
        try:
            visible_text = page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            visible_text = ""

    client_script = """
() => {
  const out = [];
  document.querySelectorAll(
    'a,button,[role="button"],input,textarea,select,[role="link"],[tabindex="0"]'
  ).forEach((el, idx) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const id = idx + 1;
    el.setAttribute('data-agent-id', String(id));
    const text = (el.innerText || el.value || el.placeholder || el.ariaLabel || '')
      .trim().replace(/\\s+/g,' ').slice(0,160);
    const kind = [el.tagName, el.getAttribute('role')||el.type||''].filter(Boolean).join(':').toLowerCase();
    out.push({ id, kind, text, href: (el.href||'').toString() });
  });
  return out;
}
"""
    try:
        items = page.evaluate(client_script)
    except Exception:
        items = []

    targets = [
        {"target_id": i.get("id"), "kind": i.get("kind",""), "text": i.get("text",""), "href": i.get("href","")}
        for i in (items or [])
    ]
    return {
        "url":               page.url if hasattr(page, "url") else "",
        "title":             page.title() if hasattr(page, "title") else "",
        "visible_text":      visible_text,
        "candidate_targets": targets,
        "targets":           targets,
        "can_go_back":       True,
    }
