#!/usr/bin/env bash
# Agent / delivery gate: unit tests then test-identity smoke.
# Usage: bash scripts/check.sh [--live-write]
#        bash scripts/check.sh red|green <command> [args...]
set -euo pipefail
if [[ "${1:-}" == "red" || "${1:-}" == "green" ]]; then
  TW="${FP_TRIPWIRE:-$HOME/.self-improving/bin/fp-tripwire}"
  if [[ ! -x "$TW" ]]; then
    echo "missing $TW" >&2
    exit 1
  fi
  exec "$TW" "$@"
fi
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
# Lint gate
if command -v ruff >/dev/null 2>&1; then
    ruff check partner tests || { echo "ruff check failed" >&2; exit 1; }
    ruff format --check partner tests || { echo "ruff format check failed" >&2; exit 1; }
else
    echo "warn: ruff not found, skipping lint"
fi

$PYTHON -m unittest discover -s tests -q
exec "$PYTHON" -u -m partner smoke "$@"
