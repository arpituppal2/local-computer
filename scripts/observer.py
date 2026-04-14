"""Reads current Playwright page state into a structured dict."""
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
      const elements = document.querySelectorAll(
        'a,button,[role="button"],input,textarea,select,[role="link"],[tabindex="0"]'
      );
      let id = 0;
      elements.forEach((el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        id += 1;
        el.setAttribute('data-agent-id', String(id));
        const text = (el.innerText || el.value || el.placeholder || el.ariaLabel || '')
          .trim().replace(/\\s+/g, ' ').slice(0, 160);
        const href = (el.href || '').toString();
        const tag  = el.tagName;
        const role = el.getAttribute('role') || '';
        const type = (el.type || '').toString();
        const kind = [tag, role || type].filter(Boolean).join(':').toLowerCase();
        out.push({ id, kind, text, href });
      });
      // Explicitly grab Bing/Google search input if present
      try {
        const searchEl = document.querySelector(
          'input[type="search"], input[aria-label*="Search"], input[name="q"]'
        );
        if (searchEl) {
          const rect = searchEl.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            const existing = out.find(
              o => o.kind.includes('input') && o.text.toLowerCase().includes('search')
            );
            if (!existing) {
              const id2 = (out.length ? out[out.length - 1].id : 0) + 1;
              searchEl.setAttribute('data-agent-id', String(id2));
              const txt = (searchEl.placeholder || searchEl.ariaLabel || 'Search').trim();
              out.push({ id: id2, kind: 'input:search', text: txt.slice(0, 160), href: '' });
            }
          }
        }
      } catch (e) {}
      return out;
    }
    """
    try:
        items = page.evaluate(client_script)
    except Exception:
        items = []

    targets = [
        {
            "target_id": item.get("id"),
            "kind":      item.get("kind") or "",
            "text":      item.get("text") or "",
            "href":      item.get("href") or "",
        }
        for item in (items or [])
    ]

    try:
        url = page.url
    except Exception:
        url = ""
    try:
        title = page.title()
    except Exception:
        title = ""

    return {
        "url":              url,
        "title":            title,
        "visible_text":     visible_text,
        "candidate_targets": targets,
        "targets":          targets,
        "can_go_back":      True,
    }
