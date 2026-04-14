"""Tracks open Playwright pages/tabs."""
from __future__ import annotations
from typing import Any


class Tabs:
    def __init__(self) -> None:
        self.pages: list[Any] = []

    def register(self, page) -> None:
        if page not in self.pages:
            self.pages.append(page)

    def sync(self, context) -> None:
        live        = set(context.pages)
        self.pages  = [p for p in self.pages if p in live]

    def summary(self) -> str:
        out = []
        for i, p in enumerate(self.pages):
            try:
                out.append(f"{i}: {p.title()} | {p.url}")
            except Exception:
                out.append(f"{i}: ?")
        return "\n".join(out)
