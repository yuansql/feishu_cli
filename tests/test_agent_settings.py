"""Agent settings file loading."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.runtime.agent.settings import (
    agent_config_path,
    ensure_agent_config,
    load_agent_config,
    save_agent_config,
)


class AgentSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "agent.json"
        self.env = patch.dict(
            os.environ,
            {"FEISHU_PARTNER_AGENT_CONFIG": str(self.path)},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in (
            "FEISHU_PARTNER_NO_LLM",
            "FEISHU_PARTNER_AGENT_BRAIN",
            "FEISHU_PARTNER_LEGACY_RUNNER",
            "OPENAI_API_KEY",
        ):
            os.environ.pop(key, None)

    def test_ensure_writes_defaults(self) -> None:
        path, created = ensure_agent_config()
        self.assertTrue(created)
        self.assertEqual(path, self.path)
        cfg = load_agent_config()
        self.assertEqual(cfg["brain"], "auto")
        self.assertEqual(cfg["max_steps"], 8)

    def test_env_overrides_file(self) -> None:
        save_agent_config({"brain": "hermes", "max_steps": 5})
        os.environ["FEISHU_PARTNER_AGENT_BRAIN"] = "heuristic"
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        cfg = load_agent_config()
        self.assertEqual(cfg["brain"], "heuristic")
        self.assertTrue(cfg["no_llm"])

    def test_openai_keys_list(self) -> None:
        save_agent_config(
            {
                "brain": "auto",
                "openai_api_keys": ["sk-a", "sk-b"],
                "openai_base_url": "https://example.com/v1",
                "openai_model": "Kimi-K2.6",
            }
        )
        from partner.runtime.agent.settings import openai_keys

        self.assertEqual(openai_keys(), ["sk-a", "sk-b"])


if __name__ == "__main__":
    unittest.main()
