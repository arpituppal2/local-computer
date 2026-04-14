#!/usr/bin/env python3
from __future__ import annotations
import json, os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT    = Path(__file__).parent.parent
DASH    = ROOT / "dashboard"
OUTPUTS = ROOT / "outputs"
HOST    = os.environ.get("AI_SYSTEM_HOST", "127.0.0.1")
PORT    = int(os.environ.get("AI_SYSTEM_PORT", "8765"))


def load_state() -> dict:
    ep = OUTPUTS / "agent_events.jsonl"
    state: dict = {"events":[],"tabs":[],"clusters":[],"contradictions":[],
                   "evidence_count":0,"step_count":0,"error_count":0}
    if not ep.exists():
        return state
    for line in ep.read_text().splitlines()[-400:]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        kind = e.get("kind")
        if   kind == "tab_snapshot":    state["tabs"]          = e.get("tabs",[])
        elif kind == "clusters":        state["clusters"]       = e.get("clusters",[])
        elif kind == "contradictions":  state["contradictions"] = e.get("items",[])
        else:
            state["events"].append(e)
            if kind == "evidence":   state["evidence_count"] += 1
            if kind == "action":     state["step_count"]     += 1
            if kind == "error":      state["error_count"]    += 1
    return state


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        p = urlparse(path).path
        if p in {"/", "/index.html"}: return str(DASH / "index.html")
        if p == "/app.js":            return str(DASH / "app.js")
        if p == "/styles.css":        return str(DASH / "styles.css")
        return str(DASH / p.lstrip("/"))

    def do_GET(self):
        if urlparse(self.path).path == "/state":
            body = json.dumps(load_state(), ensure_ascii=False).encode()
            self.send_response(200)
            for h, v in [("Content-Type","application/json; charset=utf-8"),
                         ("Cache-Control","no-cache"),("Content-Length",str(len(body)))]:
                self.send_header(h, v)
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *_): pass


if __name__ == "__main__":
    DASH.mkdir(parents=True, exist_ok=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[DASH] http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
