"""Seam: P2P last turn — 详细点/序号接上澄清，不拿跟进句去搜文档。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.actions import dispatch
from partner.intents import Intent, parse_intent
from partner.session import looks_like_followup, pick_index, save_turn


class TaskIntentTests(unittest.TestCase):
    def test_today_tasks_is_today_not_docs(self) -> None:
        for text in ("今天的任务？", "今天的任务", "今日任务", "今天任务"):
            self.assertEqual(parse_intent(text).action, "today", text)


class FollowupTests(unittest.TestCase):
    def test_confused_detail_is_followup(self) -> None:
        self.assertTrue(looks_like_followup("详细点,我不知道你在说什么"))
        self.assertEqual(pick_index("2"), 2)
        self.assertEqual(pick_index("第3份"), 3)
        self.assertFalse(looks_like_followup("软件发版是那个群"))

    def test_followup_does_not_search_the_complaint(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FEISHU_PARTNER_SESSION"] = str(Path(tmp.name) / "session.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)
        save_turn(
            "oc_p2p",
            kind="clarify",
            query="今天的任务？",
            action="unknown",
            pairs=[
                ("飞猫AI工作方式升级", "https://feishu.cn/docx/a"),
                ("工作清单", "https://feishu.cn/docx/b"),
            ],
        )
        with patch("partner.actions.analyze_text") as analyze:
            with patch("partner.actions._facts_for", return_value="待办：A6 提测"):
                out = dispatch(
                    Intent(action="unknown", query="详细点,我不知道你在说什么"),
                    user_text="详细点,我不知道你在说什么",
                    channel="p2p",
                    chat_id="oc_p2p",
                )
        analyze.assert_not_called()
        self.assertIn("待办", out)
        self.assertNotIn("没对上具体材料", out)

    def test_followup_number_reads_that_doc(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FEISHU_PARTNER_SESSION"] = str(Path(tmp.name) / "session.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)
        save_turn(
            "oc_p2p",
            kind="clarify",
            query="A6",
            action="unknown",
            pairs=[("飞猫AI", "https://feishu.cn/docx/a"), ("工作清单", "https://feishu.cn/docx/b")],
        )
        with patch("partner.actions.read_text", return_value="《工作清单》要点") as read:
            out = dispatch(
                Intent(action="unknown", query="2"),
                user_text="2",
                channel="p2p",
                chat_id="oc_p2p",
            )
        read.assert_called_once_with("https://feishu.cn/docx/b")
        self.assertIn("工作清单", out)


if __name__ == "__main__":
    unittest.main()
