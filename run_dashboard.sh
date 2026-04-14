#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -d "$AIDIR/.venv" ]; then
  echo "[error] Run ./run.sh first to set up the environment."
  exit 1
fi
source "$AIDIR/.venv/bin/activate"
cd "$AIDIR"
echo "[dash] Starting dashboard at http://127.0.0.1:8765"
python scripts/localhost_server.py
