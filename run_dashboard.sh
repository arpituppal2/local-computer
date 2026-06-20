#!/usr/bin/env bash
set -euo pipefail
AIDIR="$(cd "$(dirname "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  bash "$AIDIR/scripts/bootstrap_python_macos.sh"
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
  hash -r
fi
PYTHON3="$(command -v python3)"
if ! "$PYTHON3" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
then
  bash "$AIDIR/scripts/bootstrap_python_macos.sh"
  hash -r
  PYTHON3="$(command -v python3)"
fi
"$PYTHON3" "$AIDIR/scripts/setup_manager.py" --bootstrap
source "$AIDIR/.venv/bin/activate"
cd "$AIDIR"
export LOCAL_COMPUTER_ALLOW_MODELS="${LOCAL_COMPUTER_ALLOW_MODELS:-0}"
export LOCAL_COMPUTER_ALLOW_EXTERNAL_AI="${LOCAL_COMPUTER_ALLOW_EXTERNAL_AI:-0}"
export LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS="${LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS:-0}"
export LOCAL_COMPUTER_SKIP_MODEL_VALIDATE="${LOCAL_COMPUTER_SKIP_MODEL_VALIDATE:-1}"
export PYTHONPATH="$AIDIR${PYTHONPATH:+:$PYTHONPATH}"
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"
export LOCAL_COMPUTER_MAX_GPU_PERCENT="${LOCAL_COMPUTER_MAX_GPU_PERCENT:-90}"
export TOKENIZERS_PARALLELISM=false
export LOCAL_COMPUTER_HOST="${LOCAL_COMPUTER_HOST:-127.0.0.1}"
export LOCAL_COMPUTER_PORT="$("$PYTHON3" "$AIDIR/scripts/networking.py" --host "$LOCAL_COMPUTER_HOST" --preferred "${LOCAL_COMPUTER_PORT:-8765}")"
eval "$(
  python - <<'PY' 2>/dev/null || true
from scripts.resource_policy import resource_budget
for key, value in resource_budget().env.items():
    print(f'export {key}="{value}"')
PY
)"
echo "[dash] Starting dashboard at http://$LOCAL_COMPUTER_HOST:$LOCAL_COMPUTER_PORT"
python scripts/ui_server.py --host "$LOCAL_COMPUTER_HOST" --port "$LOCAL_COMPUTER_PORT"
