"""Writes structured events to a JSONL file for dashboard consumption."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class EventLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")

    def emit(self, kind: str, **payload) -> None:
        obj = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **payload}
        with self.path.open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
