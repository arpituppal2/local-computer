#!/usr/bin/env python3
from __future__ import annotations
import json, os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT    = Path(__file__).parent.parent
DASH    = ROOT / "dashboard"
OUTPUTS = ROOT / "outputs"
HOST    = os.environ.get("AI_SYSTEM_HOST", "127.0.0.1")
PORT    = int(os.environ.get("AI_SYSTEM_PORT", "8765"))


def load_state() -> dict:
    ep = OUTPUTS / "agent_events.jsonl"
    state: dict = {"events":[], "tabs":[], "clusters":[], "contradictions":[],
                   "evidence_count":0, "step_count":0, "error_count":0}
    if not ep.exists():
        return state
    for line in ep.read_text(encoding="utf-8").splitlines()[-600:]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        kind = e.get("kind")
        if   kind == "tab_snapshot":    state["tabs"]          = e.get("tabs", [])
        elif kind == "clusters":
            clusters = e.get("clusters", [])
            if clusters:
                state["clusters"] = clusters
        elif kind == "contradictions":  state["contradictions"] = e.get("items", [])
        else:
            state["events"].append(e)
            if kind == "evidence":                          state["evidence_count"] += 1
            if kind == "decision":                          state["step_count"]     += 1
            if kind in ("action_failed", "nav_error"):      state["error_count"]    += 1
    return state


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        p = urlparse(path).path
        if p in {"/", "/index.html"}: return str(DASH / "index.html")
        return str(DASH / p.lstrip("/"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/state":
            body = json.dumps(load_state(), ensure_ascii=False).encode("utf-8")
            self._json(body)
            return

        if path == "/artifact":
            qs = parse_qs(parsed.query)
            fp = Path((qs.get("path") or [""])[0])
            try:
                fp.relative_to(ROOT)   # safety: must be inside repo
                text = fp.read_text(encoding="utf-8")
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type",   "text/plain; charset=utf-8")
                self.send_header("Content-Length",  str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(404)
                self.end_headers()
            return

        super().do_GET()

    def _json(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length",  str(len(body)))
        self.send_header("Cache-Control",   "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_): pass


if __name__ == "__main__":
    DASH.mkdir(parents=True, exist_ok=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[DASH] http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
