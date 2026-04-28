#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# local-computer · single-command launcher
# Usage: ./start.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

AIDIR="$(cd "$(dirname "$0")" && pwd)"
PORT=7878

# ── Memory / GPU env (M4, 16GB) ───────────────────────────────────────────────
export OLLAMA_MAX_VRAM=14336
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=2

# ── venv ──────────────────────────────────────────────────────────────────────
VENV="$AIDIR/.venv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
source "$VENV/bin/activate"

# ── deps ──────────────────────────────────────────────────────────────────────
SENTINEL="$VENV/.deps_ok"
REQS="$AIDIR/requirements.txt"
if [ ! -f "$SENTINEL" ] || [ "$REQS" -nt "$SENTINEL" ]; then
  echo "[setup] Installing dependencies…"
  pip install --quiet --upgrade pip
  pip install --quiet -r "$REQS"
  touch "$SENTINEL"
fi

# ── flask / sse extra deps ─────────────────────────────────────────────────────
pip install --quiet flask flask-cors 2>/dev/null || true

# ── Playwright Chromium ────────────────────────────────────────────────────────
if ! python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
" 2>/dev/null; then
  echo "[setup] Installing Playwright Chromium (one-time ~150MB)…"
  playwright install chromium --with-deps 2>&1 | tail -5 || true
fi

# ── Ollama ─────────────────────────────────────────────────────────────────────
if ! ollama list >/dev/null 2>&1; then
  echo "[setup] Starting Ollama…"
  osascript \
    -e 'tell application "Terminal"' \
    -e '  if not (exists window 1) then reopen' \
    -e '  activate' \
    -e '  tell application "System Events" to keystroke "t" using command down' \
    -e '  delay 0.4' \
    -e "  do script \"ollama serve\" in front window" \
    -e 'end tell' 2>/dev/null || \
  ollama serve >/tmp/ollama.log 2>&1 &
  echo -n "[setup] Waiting for Ollama"
  for i in $(seq 1 30); do
    ollama list >/dev/null 2>&1 && break || true
    sleep 0.5; echo -n "."
  done
  echo " ready."
fi

for MODEL in qwen3:8b; do
  ollama list 2>/dev/null | grep -q "$MODEL" || {
    echo "[setup] Pulling $MODEL…"
    ollama pull "$MODEL" || echo "[warn] pull failed — continuing."
  }
done

# ── prep dirs ─────────────────────────────────────────────────────────────────
mkdir -p "$AIDIR/outputs" "$AIDIR/logs"
rm -f "$AIDIR/outputs/agent_events.jsonl"

# ── launch UI server ──────────────────────────────────────────────────────────
echo "[start] Launching local-computer at http://localhost:$PORT"
python "$AIDIR/scripts/ui_server.py" --port "$PORT" &
SERVER_PID=$!

# ── wait for server then open browser ─────────────────────────────────────────
echo -n "[start] Waiting for UI server"
for i in $(seq 1 20); do
  curl -sf "http://localhost:$PORT/api/ping" >/dev/null 2>&1 && break || true
  sleep 0.3; echo -n "."
done
echo " ready."

open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null || true

echo "[start] local-computer is running. Press Ctrl+C to stop."
trap "kill $SERVER_PID 2>/dev/null; exit" INT TERM
wait $SERVER_PID
