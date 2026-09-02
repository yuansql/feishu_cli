"""Standard logging setup for operational diagnostics.

Complements the existing trace system (JSONL events per task) with
rotated text logs for debugging and monitoring.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".feishu-partner"
LOG_FILE = LOG_DIR / "partner.log"

# Default: INFO for file, WARNING for console
_LOG_LEVEL_FILE = os.environ.get("FEISHU_PARTNER_LOG_LEVEL_FILE", "INFO")
_LOG_LEVEL_CONSOLE = os.environ.get("FEISHU_PARTNER_LOG_LEVEL_CONSOLE", "WARNING")
_LOG_MAX_BYTES = int(os.environ.get("FEISHU_PARTNER_LOG_MAX_BYTES", "5_000_000"))
_LOG_BACKUP_COUNT = int(os.environ.get("FEISHU_PARTNER_LOG_BACKUP_COUNT", "3"))

_LOG_CONFIGURED = False


def setup_logging() -> None:
    """Configure root logger with file rotation and console output.

    Idempotent: safe to call multiple times.
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, _LOG_LEVEL_FILE.upper(), logging.INFO))
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Console handler (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(
        getattr(logging, _LOG_LEVEL_CONSOLE.upper(), logging.WARNING)
    )
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    _LOG_CONFIGURED = True
    logging.getLogger(__name__).info("Logging configured: file=%s", LOG_FILE)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the 'partner' namespace."""
    return logging.getLogger(name)
