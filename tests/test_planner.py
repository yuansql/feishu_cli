from __future__ import annotations

import unittest
from unittest.mock import patch

from partner.planner import plan_text


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


if __name__ == "__main__":
    unittest.main()
