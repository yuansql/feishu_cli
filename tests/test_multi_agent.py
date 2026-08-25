from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from partner.runtime.multi_agent import (
    MultiAgentResult,
    RoleBrief,
    coordinate,
    enrich_facts_for_plan,
    select_roles,
)


class MultiAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        os.environ["FEISHU_PARTNER_NO_MULTI_AGENT"] = "1"

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
        os.environ.pop("FEISHU_PARTNER_NO_MULTI_AGENT", None)

    def test_deterministic_three_roles(self) -> None:
        result = coordinate("汇总 A6 风险并写周报，不写入", "【done】 today\n日程 A6 提测")
        self.assertIsInstance(result, MultiAgentResult)
        self.assertIn("researcher", result.researcher.role)
        self.assertIn("today", result.executor.text)
        self.assertIn("交付", result.writer.text)
        self.assertEqual(result.researcher.source, "deterministic")

    def test_enrich_facts_appends_context(self) -> None:
        merged, brief = enrich_facts_for_plan("拆解上线检查", "facts block")
        self.assertIn("Multi-Agent", merged)
        self.assertIn("facts block", merged)
        self.assertEqual(brief.executor.role, "executor")

    def test_model_path_when_available(self) -> None:
        os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
        os.environ.pop("FEISHU_PARTNER_NO_MULTI_AGENT", None)
        payload = MultiAgentResult(
            researcher=RoleBrief("researcher", "调研", "r", "model"),
            executor=RoleBrief("executor", "执行", "e", "model"),
            writer=RoleBrief("writer", "交付", "w", "model"),
        )
        with patch("partner.runtime.multi_agent._model_coordinate", return_value=payload):
            result = coordinate("测试目标", "some facts")
        self.assertEqual(result.researcher.source, "model")

    def test_select_roles_subset(self) -> None:
        self.assertEqual(select_roles("待办"), ("executor",))
        roles = select_roles("调研 A6 风险并写周报")
        self.assertIn("researcher", roles)
        self.assertIn("executor", roles)
        self.assertIn("writer", roles)
        brief = coordinate("待办", "")
        self.assertEqual(brief.active_roles, ("executor",))
        self.assertEqual(brief.researcher.source, "skipped")
        self.assertNotIn("调研", brief.as_plan_context())


if __name__ == "__main__":
    unittest.main()
