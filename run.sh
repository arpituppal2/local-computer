#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$AIDIR/.venv"

if [ ! -d "$VENV" ]; then
  echo "[setup] Creating venv..."
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"
pip install --quiet --upgrade pip >/dev/null 2>&1 || true
pip install --quiet -r "$AIDIR/requirements.txt" >/dev/null 2>&1 || true
playwright install chromium --with-deps >/dev/null 2>&1 || true

cd "$AIDIR"
python scripts/orchestrator.py "$@"
