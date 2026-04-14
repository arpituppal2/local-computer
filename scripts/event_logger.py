from __future__ import annotations
import json, time
from pathlib import Path


class EventLogger:
    def __init__(self, output_dir: str | Path = "outputs"):
        self._path = Path(output_dir) / "agent_events.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, kind: str, **kwargs) -> None:
        entry = {"kind": kind, "ts": time.time(), **kwargs}
        with self._path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        preview = {k: v for k, v in kwargs.items() if k != "visible_text"}
        print(f"[{kind.upper()}] {json.dumps(preview, ensure_ascii=False)[:200]}", flush=True)
