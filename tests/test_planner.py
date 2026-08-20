from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from partner.planner import agent_plan_steps, plan_text


class PlannerTests(unittest.TestCase):
    def test_empty_goal_shows_usage(self) -> None:
        text = plan_text("", allow_llm=False)
        self.assertIn("用法", text)
        self.assertIn("feishu plan", text)

    def test_fallback_plan_uses_context_and_commands(self) -> None:
        facts = "【待办】\n未完成待办 1 条：\n- A6 上线前检查（2026-08-18）"
        text = plan_text("A6 上线前检查", facts, allow_llm=False)
        self.assertIn("任务规划：A6 上线前检查", text)
        self.assertIn("【当前上下文】", text)
        self.assertIn("A6 上线前检查", text)
        self.assertIn("【执行步骤】", text)
        self.assertIn("`feishu today`", text)
        self.assertIn("`feishu tasks`", text)

    def test_llm_plan_wins_when_valid(self) -> None:
        shaped = (
            "任务规划：A6 上线\n"
            "【当前判断】\n- 有上下文\n"
            "【执行步骤】\n1. 核对清单。\n"
            "【可直接用的飞书动作】\n- feishu today\n"
            "【需要确认】\n- 截止时间\n"
        )
        with patch("partner.planner.rewrite_plan", return_value=shaped):
            text = plan_text("A6 上线", "【待办】\n- A6")
        self.assertEqual(text, shaped)

    def test_agent_plan_is_strictly_allowlisted(self) -> None:
        shaped = """{
          "reason": "先查再写",
          "steps": [
            {"title": "查待办", "tool": "tasks", "args": {}},
            {"title": "危险命令", "tool": "shell", "args": {"cmd": "rm -rf /"}},
            {"title": "建文档", "tool": "docs_create", "args": {"query": "A6"}}
          ]
        }"""
        with patch("partner.planner._invoke_hermes", return_value=shaped):
            steps = agent_plan_steps(
                "整理 A6 并创建文档",
                "A6 已提测",
                available_tools={"tasks", "docs_create", "summarize"},
                write_tools={"docs_create"},
            )
        self.assertEqual(
            [step["tool"] for step in steps],
            ["tasks", "docs_create", "summarize"],
        )
        self.assertTrue(steps[1]["requires_confirm"])

    def test_agent_plan_rejects_non_json(self) -> None:
        with patch("partner.planner._invoke_hermes", return_value="我建议先思考"):
            steps = agent_plan_steps(
                "A6",
                "",
                available_tools={"tasks", "summarize"},
            )
        self.assertEqual(steps, [])

    def test_agent_plan_cannot_override_explicit_no_write(self) -> None:
        shaped = """{
          "reason": "误判",
          "steps": [
            {"title": "建文档", "tool": "docs_create", "args": {"query": "A6"}},
            {"title": "汇总", "tool": "summarize", "args": {}}
          ]
        }"""
        with patch("partner.planner._invoke_hermes", return_value=shaped):
            steps = agent_plan_steps(
                "只读 A6，不写入任何内容",
                "",
                available_tools={"docs_create", "summarize"},
                write_tools={"docs_create"},
            )
        self.assertEqual([step["tool"] for step in steps], ["summarize"])

    def test_agent_plan_honors_no_llm_mode(self) -> None:
        with patch.dict(os.environ, {"FEISHU_PARTNER_NO_LLM": "1"}):
            with patch("partner.planner._invoke_hermes") as invoke:
                steps = agent_plan_steps(
                    "A6",
                    "",
                    available_tools={"tasks", "summarize"},
                )
        self.assertEqual(steps, [])
        invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
