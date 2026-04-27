"""Dashboard server — path traversal fix, state caching, bounded thread pool (fixes #29-32)."""
from __future__ import annotations
import json, time, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "outputs" / "agent_events.jsonl"
DASHBOARD_DIR = ROOT / "dashboard"

_executor = ThreadPoolExecutor(max_workers=8)

_state_cache = {"data": {}, "ts": 0.0, "ttl": 1.0}
_state_lock = threading.Lock()

def _load_state() -> dict:
    with _state_lock:
        now = time.time()
        if now - _state_cache["ts"] < _state_cache["ttl"]:
            return _state_cache["data"]
        events = []
        if EVENTS_FILE.exists():
            for line in EVENTS_FILE.read_text().splitlines()[-600:]:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        state = {
            "events": events,
            "evidence_count": sum(1 for e in events if e.get("kind") == "evidence"),
            "step_count": sum(1 for e in events if e.get("kind") == "observe"),
            "error_count": sum(1 for e in events if e.get("kind") in ("action_failed", "nav_error")),
            "clusters": next((e.get("clusters", []) for e in reversed(events) if e.get("kind") == "clusters"), []),
            "contradictions": next((e.get("items", []) for e in reversed(events) if e.get("kind") == "contradictions"), []),
        }
        _state_cache["data"] = state
        _state_cache["ts"] = now
        return state

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/state":
            data = json.dumps(_load_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(data))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/artifact":
            qs = parse_qs(parsed.query)
            raw_path = (qs.get("path") or [""])[0]
            try:
                fp = Path(raw_path).resolve()
                fp.relative_to(ROOT)
                if fp.is_symlink():
                    raise ValueError("symlink not allowed")
                text = fp.read_text(errors="replace").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", len(text))
                self.end_headers()
                self.wfile.write(text)
            except Exception:
                self.send_response(404); self.end_headers()
            return

        rel = parsed.path.lstrip("/") or "index.html"
        fp = (DASHBOARD_DIR / rel).resolve()
        try:
            fp.relative_to(DASHBOARD_DIR)
            if not fp.exists() or not fp.is_file():
                raise FileNotFoundError
            data = fp.read_bytes()
            ctype = "text/html" if fp.suffix == ".html" else "text/javascript" if fp.suffix == ".js" else "text/css"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_response(404); self.end_headers()

def start(port: int = 7788):
    class BoundedServer(ThreadingTCPServer):
        allow_reuse_address = True
        def process_request(self, request, client_address):
            _executor.submit(self.finish_request, request, client_address)
    srv = BoundedServer(("", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[dashboard] http://localhost:{port}")
    return srv
