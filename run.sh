#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
VENVDIR="$AIDIR/.venv"

if [ ! -d "$VENVDIR" ]; then
  python3 -m venv "$VENVDIR"
fi

source "$VENVDIR/bin/activate"
python -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
python -m pip install --quiet -r "$AIDIR/requirements.txt" >/dev/null 2>&1 || true
python -m playwright install chromium >/dev/null 2>&1 || true

cd "$AIDIR"
python scripts/orchestrator.py "$@"
