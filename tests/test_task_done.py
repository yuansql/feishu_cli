"""Seam: 删/完成待办 — 不搜文档；飞书没勾上不许说已删。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.actions import complete_task_text, dispatch
from partner.intents import parse_intent
from partner.session import save_turn


class TaskDoneIntentTests(unittest.TestCase):
    def test_paste_title_and_delete_is_task_done(self) -> None:
        text = "A8设备邮寄回来（海外Esim卡处理）（2026-08-15） 删除这个待办"
        intent = parse_intent(text)
        self.assertEqual(intent.action, "task_done")
        self.assertIn("A8设备邮寄回来", intent.query)
        self.assertNotIn("删除", intent.query)

    def test_today_tasks_still_lists(self) -> None:
        self.assertEqual(parse_intent("今天的任务？").action, "today")

    def test_task_done_with_reply_suffix_keeps_task_done(self) -> None:
        intent = parse_intent("完成任务 回复 结束")
        self.assertEqual(intent.action, "task_done")
        self.assertEqual(intent.query, "")


class CompleteTaskTests(unittest.TestCase):
    def test_unique_title_completes_and_does_not_search(self) -> None:
        payload = {
            "ok": True,
            "data": {
                "items": [
                    {
                        "guid": "guid-a8",
                        "summary": "A8设备邮寄回来（海外Esim卡处理）",
                        "due_at": "2026-08-15",
                    }
                ]
            },
        }
        with patch("partner.actions.run_lark") as run:
            run.side_effect = [payload, {"ok": True}]
            with patch("partner.actions.analyze_text") as analyze:
                out = complete_task_text("A8设备邮寄回来（海外Esim卡处理）")
        analyze.assert_not_called()
        self.assertIn("已勾完成", out)
        self.assertIn("A8", out)
        self.assertEqual(run.call_args_list[1].args[0][:3], ["task", "+complete", "--task-id"])
        self.assertIn("guid-a8", run.call_args_list[1].args[0])

    def test_feishu_fail_does_not_claim_done(self) -> None:
        payload = {
            "ok": True,
            "data": {"items": [{"guid": "guid-a8", "summary": "A8设备邮寄回来（海外Esim卡处理）"}]},
        }
        with patch("partner.actions.run_lark") as run:
            run.side_effect = [payload, {"ok": False, "error": {"message": "forbidden"}}]
            out = complete_task_text("A8设备邮寄回来")
        self.assertNotIn("已勾完成", out)
        self.assertIn("forbidden", out)

    def test_dispatch_unknown_sentence_does_not_search(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FEISHU_PARTNER_SESSION"] = str(Path(tmp.name) / "session.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)
        save_turn("oc_p2p", kind="action", query="今天的任务？", action="tasks", items=[])
        asked = "A8设备邮寄回来（海外Esim卡处理）（2026-08-15） 删除这个待办"
        with patch("partner.actions.analyze_result") as analyze:
            with patch("partner.actions.complete_task_text", return_value="已勾完成：A8") as done:
                out = dispatch(
                    parse_intent(asked),
                    user_text=asked,
                    channel="p2p",
                    chat_id="oc_p2p",
                )
        analyze.assert_not_called()
        done.assert_called()
        self.assertIn("已勾完成", out)
        self.assertNotIn("没对上具体材料", out)

    def test_dispatch_task_done_can_append_short_closing_reply(self) -> None:
        with patch("partner.actions.complete_task_text", return_value="已勾完成：A8"):
            out = dispatch(
                parse_intent("完成任务 回复 结束"),
                user_text="完成任务 回复 结束",
                channel="p2p",
                chat_id="oc_p2p",
            )
        self.assertIn("已勾完成：A8", out)
        self.assertTrue(out.endswith("结束"))


if __name__ == "__main__":
    unittest.main()
