#!/usr/bin/env bash
# Run the whole Upflow2x test suite without opening DaVinci Resolve.
#
#   ./test.sh              run everything
#   ./test.sh -k markers   run tests matching an expression
#   ./test.sh -x           stop at the first failure
#
# Creates .venv and installs test dependencies on first run.

set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
    echo "==> creating $VENV"
    python3 -m venv "$VENV"
fi

if ! "$PY" -c "import cv2, numpy, pytest, lupa" >/dev/null 2>&1; then
    echo "==> installing test dependencies"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
    "$PY" -m pip install --quiet -r requirements-dev.txt
fi

exec "$PY" -m pytest "$@"
