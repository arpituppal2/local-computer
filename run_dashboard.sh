#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
source "$AIDIR/.venv/bin/activate"
cd "$AIDIR"
python scripts/localhost_server.py
