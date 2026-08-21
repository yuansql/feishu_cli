"""Smoke / eval regression for test-identity harness."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from partner.ops.eval import run_fixture_eval
from partner.ops.smoke import run_smoke, smoke_text


class SmokeHarnessTests(unittest.TestCase):
    def test_fixtures_include_recent_regressions(self) -> None:
        result = run_fixture_eval()
        self.assertGreaterEqual(result["total"], 14)
        self.assertEqual(result["passed"], result["total"], result["results"])

    def test_smoke_routing_and_safe_dispatch(self) -> None:
        with patch.dict(os.environ, {"FEISHU_PARTNER_NO_LLM": "1"}):

            def fake_dispatch(intent, **_kwargs):  # type: ignore[no-untyped-def]
                if intent.action == "identity":
                    return "你是吴梦晨。我是你的飞书工作伙伴。"
                if intent.action == "chat_history":
                    return "【与工作伙伴（飞书 CLI）的单聊·最近消息】\n- hi"
                if intent.action == "help":
                    return "直接说：今天 / 待办 / 写周报"
                if intent.action == "weekly_tasks":
                    return "本周任务\n- [例行] 填写任务清单"
                return "【测】ok"

            with patch("partner.actions.dispatch", side_effect=fake_dispatch):
                report = run_smoke(allow_write=False)
        self.assertEqual(report["passed"], report["total"], report["results"])
        self.assertIn("测试身份", smoke_text(allow_write=False, report=report))


if __name__ == "__main__":
    unittest.main()
