"""P2P router — unknown → Hermes classify, never doc-keyword clarify."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from partner.routing.intents import Intent, parse_intent
from partner.routing.p2p_router import refine_p2p_intent
from partner.runtime.hermes_control import _wants_doc_seed


class P2pRouterTests(unittest.TestCase):
    def test_daily_brief_product_skips_classify(self) -> None:
        self.assertEqual(parse_intent("今日工作简报").action, "brief")
        with patch("partner.routing.p2p_router.classify_intent") as classify:
            intent = refine_p2p_intent("今日工作简报")
        classify.assert_not_called()
        self.assertEqual(intent.action, "brief")

    def test_trusted_parse_skips_classify(self) -> None:
        with patch("partner.routing.p2p_router.classify_intent") as classify:
            intent = refine_p2p_intent("今天", parse_intent("今天"))
        classify.assert_not_called()
        self.assertEqual(intent.action, "today")

    def test_weekly_all_tasks_skips_classify(self) -> None:
        self.assertEqual(parse_intent("本周的全部任务").action, "weekly_tasks")
        with patch("partner.routing.p2p_router.classify_intent") as classify:
            intent = refine_p2p_intent("本周的全部任务")
        classify.assert_not_called()
        self.assertEqual(intent.action, "weekly_tasks")

    def test_short_followup_is_not_classified_as_help(self) -> None:
        with patch(
            "partner.routing.p2p_router.classify_intent",
            return_value=Intent(action="help"),
        ) as classify:
            intent = refine_p2p_intent("需要")
        classify.assert_not_called()
        self.assertEqual(intent.action, "unknown")

    def test_dispatch_p2p_unknown_never_clarify_docs(self) -> None:
        from partner.actions import dispatch

        with patch("partner.actions.hermes_available", return_value=True):
            with patch(
                "partner.runtime.hermes_control.hermes_control_turn",
                return_value="📋 每日工作简报 · 测试",
            ):
                out = dispatch(
                    Intent(action="unknown", query="随便问一句"),
                    user_text="随便问一句",
                    channel="p2p",
                    chat_id="oc_route",
                )
        self.assertIn("每日工作简报", out)
        self.assertNotIn("没对上具体材料", out)

    def test_brief_does_not_inherit_open_doc(self) -> None:
        task = {"doc_url": "https://example.feishu.cn/docx/x", "status": "observed"}
        self.assertFalse(_wants_doc_seed("今日工作简报", task))
        self.assertFalse(_wants_doc_seed("重试", task))
        self.assertTrue(_wants_doc_seed("把格式改对", task))


if __name__ == "__main__":
    unittest.main()
