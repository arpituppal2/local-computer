#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
VENVDIR="$AIDIR/.venv"

source "$VENVDIR/bin/activate"
cd "$AIDIR"
python scripts/localhost_server.py
