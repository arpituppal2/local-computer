#!/usr/bin/env bash
set -euo pipefail

AIDIR="$(cd "$(dirname "$0")" && pwd)"

# --- GPU / Ollama env (max out your 16GB unified memory) ---
export OLLAMA_MAX_VRAM=14336
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_NUM_PARALLEL=2
export OLLAMA_MAX_LOADED_MODELS=2

# --- venv ---
VENV="$AIDIR/.venv"
if [ ! -d "$VENV" ]; then
  echo "[setup] Creating venv..."
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

# --- deps ---
pip install --quiet --upgrade pip >/dev/null 2>&1 || true
pip install --quiet -r "$AIDIR/requirements.txt" >/dev/null 2>&1 || true

# --- Playwright Chromium (auto-installs only if missing) ---
PLAYWRIGHT_OK=0
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
" 2>/dev/null && PLAYWRIGHT_OK=1 || true

if [ "$PLAYWRIGHT_OK" -eq 0 ]; then
  echo "[setup] Installing Playwright Chromium (one-time, ~150MB)..."
  playwright install chromium --with-deps 2>&1 | grep -v '^$' | tail -8 || true
fi

# --- Ollama: start in a new terminal tab if not already running ---
if ! ollama list >/dev/null 2>&1; then
  echo "[setup] Ollama not running — launching in a new terminal tab..."
  osascript \
    -e 'tell application "Terminal"' \
    -e '  if not (exists window 1) then reopen' \
    -e '  activate' \
    -e '  tell application "System Events" to keystroke "t" using command down' \
    -e '  delay 0.4' \
    -e "  do script \"echo '[ollama] starting...'; ollama serve\" in front window" \
    -e 'end tell' 2>/dev/null || \
  osascript \
    -e 'tell application "iTerm2"' \
    -e '  tell current window' \
    -e '    create tab with default profile' \
    -e "    tell current session to write text \"echo '[ollama] starting...'; ollama serve\"" \
    -e '  end tell' \
    -e 'end tell' 2>/dev/null || \
  ( echo "[setup] Could not open terminal tab — starting ollama serve in background..."; ollama serve >/tmp/ollama.log 2>&1 & )

  echo "[setup] Waiting for Ollama to be ready..."
  for i in $(seq 1 20); do
    ollama list >/dev/null 2>&1 && break || true
    sleep 0.5
  done
  ollama list >/dev/null 2>&1 || { echo "[error] Ollama failed to start. Run 'ollama serve' manually."; exit 1; }
  echo "[setup] Ollama is ready."
fi

# --- Ollama model check ---
for MODEL in qwen3:4b qwen3:8b; do
  if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "[setup] Pulling $MODEL (required)..."
    ollama pull "$MODEL" || echo "[warn] Could not pull $MODEL — continuing anyway."
  fi
done
if ! ollama list 2>/dev/null | grep -q "qwen3:14b"; then
  echo "[setup] NOTE: qwen3:14b not found — only used for heavy tasks. Pull with: ollama pull qwen3:14b"
fi

# --- clear previous run ---
mkdir -p "$AIDIR/outputs" "$AIDIR/logs"
rm -f "$AIDIR/outputs/agent_events.jsonl"

# --- run ---
cd "$AIDIR"
echo "[run] Goal: $*"
python scripts/orchestrator.py "$@" 2>&1 | tee "$AIDIR/logs/last_run.log"
