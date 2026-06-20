#!/usr/bin/env bash
set -euo pipefail

if command -v python3 >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
then
  exit 0
fi

if [ "${LOCAL_COMPUTER_AUTO_INSTALL_PYTHON:-1}" = "0" ]; then
  echo "[python] Python 3.12+ is required. Install Python and run Locus again."
  exit 1
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "[python] Automatic Python bootstrap is currently supported on macOS and Windows only."
  exit 1
fi

echo "[python] Python 3.12+ was not found. Installing it automatically for Locus."

if ! command -v brew >/dev/null 2>&1; then
  echo "[python] Homebrew was not found. Installing Homebrew first."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

brew update
brew install python

if ! command -v python3 >/dev/null 2>&1 || ! python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
then
  echo "[python] Python installation finished, but Python 3.12+ is still not on PATH."
  exit 1
fi

echo "[python] Python installed: $(python3 --version)"
