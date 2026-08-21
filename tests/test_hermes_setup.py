"""Isolated Hermes profile writes allowlisted MCP only."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.compose.hermes_setup import (
    PROFILE_NAME,
    ensure_profile,
    profile_config_text,
    profile_ready,
)


class HermesSetupTests(unittest.TestCase):
    def test_config_has_empty_cli_toolsets_and_feishu_mcp(self) -> None:
        text = profile_config_text()
        self.assertIn("cli: []", text)
        self.assertIn("feishu:", text)
        self.assertIn("-m", text)
        self.assertIn("partner", text)
        self.assertNotIn("peekaboo", text)
        self.assertNotIn("terminal", text)

    def test_ensure_profile_writes_ready_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / PROFILE_NAME
            with patch.dict(os.environ, {"FEISHU_HERMES_PROFILE_DIR": str(dest)}):
                self.assertTrue(ensure_profile())
                self.assertTrue(profile_ready())
                self.assertTrue((dest / "SOUL.md").is_file())
                self.assertTrue((dest / ".no-bundled-skills").is_file())
