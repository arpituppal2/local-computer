#!/usr/bin/env bash
set -euo pipefail

AIDIR="$(cd "$(dirname "$0")" && pwd)"
AIDIR="$AIDIR"

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
if ! python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
" 2>/dev/null; then
  echo "[setup] Installing Playwright Chromium (one-time, ~150MB)..."
  playwright install chromium --with-deps 2>&1 | grep -v '^$' | tail -8
fi

# --- Ollama model check ---
for MODEL in qwen3:4b qwen3:8b; do
  if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "[setup] Pulling $MODEL (required)..."
    ollama pull "$MODEL"
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
