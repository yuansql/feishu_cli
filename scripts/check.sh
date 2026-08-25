#!/usr/bin/env bash
# Agent / delivery gate: unit tests then test-identity smoke.
# Usage: bash scripts/check.sh [--live-write]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
python3 -m unittest discover -s tests -q
exec "$ROOT/bin/feishu" smoke "$@"
