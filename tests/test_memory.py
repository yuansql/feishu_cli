from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.memory import (
    append_experience,
    ensure_agents_md,
    memory_cli,
    memory_context_for_goal,
    note_task_outcome,
    recent_experience,
)
from partner.workflow import run_workflow


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["FEISHU_PARTNER_HOME"] = str(root)
        os.environ["FEISHU_PARTNER_AGENTS_MD"] = str(root / "Agents.md")
        os.environ["FEISHU_PARTNER_EXPERIENCE"] = str(root / "experience.jsonl")

    def tearDown(self) -> None:
        for key in (
            "FEISHU_PARTNER_HOME",
            "FEISHU_PARTNER_AGENTS_MD",
            "FEISHU_PARTNER_EXPERIENCE",
        ):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_init_and_note(self) -> None:
        path = ensure_agents_md()
        self.assertTrue(path.is_file())
        self.assertIn("Agents.md", path.read_text(encoding="utf-8"))
        append_experience("A6 提测前先查权限", tags=["A6"])
        rows = recent_experience(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertIn("A6", rows[0]["text"])
        ctx = memory_context_for_goal("A6 上线")
        self.assertIn("Agents.md", ctx)
        self.assertIn("A6", ctx)
        note_task_outcome("整理 A6", "done", summary="已提测")
        self.assertGreaterEqual(len(recent_experience(limit=10)), 2)
        self.assertIn("经验", memory_cli(["show"]))


class WorkflowConditionTests(unittest.TestCase):
    def test_when_skips_inbox_without_keyword(self) -> None:
        with patch("partner.workflow.execute_tool", return_value="ok") as mocked:
            body = run_workflow("mention_followup", goal="点名跟进 看今天", chat_id="oc_x")
        self.assertIn("条件未满足", body)
        tools = [call.args[0] for call in mocked.call_args_list]
        self.assertIn("today", tools)
        self.assertIn("tasks", tools)
        self.assertNotIn("inbox", tools)

    def test_when_runs_inbox_with_keyword(self) -> None:
        with patch("partner.workflow.execute_tool", return_value="ok") as mocked:
            run_workflow("mention_followup", goal="点名跟进 谁找我了", chat_id="oc_x")
        tools = [call.args[0] for call in mocked.call_args_list]
        self.assertIn("inbox", tools)


if __name__ == "__main__":
    unittest.main()
