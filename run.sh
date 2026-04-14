#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$AIDIR/.venv"

# ── venv ─────────────────────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "[setup] Creating venv..."
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

# ── deps ─────────────────────────────────────────────────────────────────────────────
pip install --quiet --upgrade pip >/dev/null 2>&1 || true
pip install --quiet -r "$AIDIR/requirements.txt" >/dev/null 2>&1 || true

# ── playwright chromium (auto-installs only if missing) ─────────────────────────────
if ! python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
" 2>/dev/null; then
  echo "[setup] Installing Playwright Chromium (one-time, ~150MB)..."
  playwright install chromium --with-deps 2>&1 | grep -v '^$' | tail -8
fi

# ── clear previous run ──────────────────────────────────────────────────────────────
mkdir -p "$AIDIR/outputs" "$AIDIR/logs"
rm -f "$AIDIR/outputs/agent_events.jsonl"

# ── run ─────────────────────────────────────────────────────────────────────────────
cd "$AIDIR"
echo "[run] Goal: $*"
python scripts/orchestrator.py "$@" 2>&1 | tee "$AIDIR/logs/last_run.log"
