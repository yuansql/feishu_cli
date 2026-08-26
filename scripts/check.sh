#!/usr/bin/env bash
# Agent / delivery gate: unit tests then test-identity smoke.
# Usage: bash scripts/check.sh [--live-write]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
# Use project venv if it exists, otherwise fall back to system python3.
VENV="${FEISHU_PARTNER_VENV:-$HOME/.workbuddy/binaries/python/envs/feishu_cli}"
if [ -x "$VENV/bin/python3" ]; then
    PYTHON="$VENV/bin/python3"
else
    PYTHON="python3"
fi
$PYTHON -m unittest discover -s tests -q
exec "$PYTHON" -u -m partner smoke "$@"
