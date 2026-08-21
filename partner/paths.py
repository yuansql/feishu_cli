"""Stable filesystem anchors for the partner package.

Prefer these over Path(__file__).parent chains so modules can move across layers.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
WORKFLOWS_DIR = PACKAGE_DIR / "workflows"
BIN_FEISHU = REPO_ROOT / "bin" / "feishu"
