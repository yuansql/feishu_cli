from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from partner.actions import write_weekly_text
from partner.office.recap import EvidenceBundle, collect_message_evidence


CN = timezone(timedelta(hours=8))


class WriteWeeklyFromChatsTests(unittest.TestCase):
    def test_write_weekly_uses_chats_not_doc_search(self) -> None:
        start = datetime(2026, 8, 17, tzinfo=CN)
        end = datetime(2026, 8, 23, 23, 59, 59, tzinfo=CN)
        bundle = EvidenceBundle(
            context="周期：2026-08-17\n## APP沟通群\n[E001 id=om_1][08-18 10:00][我] A6 安卓包已提测",
            message_count=12,
            evidence_count=1,
        )
        calls: list[list[str]] = []

        def fake_lark(args: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(list(args))
            if args[:2] == ["task", "+get-my-tasks"]:
                return {
                    "ok": True,
                    "data": {
                        "items": [
                            {"summary": "上周未完成：eSIM 邮寄跟进", "complete": False},
                        ]
                    },
                }
            if args[:2] == ["docs", "+create"]:
                content = ""
                if "--content" in args:
                    content = args[args.index("--content") + 1]
                self.assertIn("聊天证据", content or "依据本周")
                self.assertNotIn("平台研发部周报", content)
                self.assertIn("eSIM", content)
                return {
                    "ok": True,
                    "data": {
                        "document": {
                            "url": "https://example.feishu.cn/docx/weekly_from_chat",
                        }
                    },
                }
            raise AssertionError(args)

        with (
            patch("partner.office.calendar_views._week_bounds", return_value=(start, end)),
            patch("partner.office.recap.collect_week_evidence", return_value=bundle),
            patch("partner.office.calendar_views.run_lark", side_effect=fake_lark),
            patch("partner.compose.llm.draft_weekly_from_chats", return_value=""),
            patch.dict(os.environ, {"FEISHU_PARTNER_NO_LLM": "1"}),
        ):
            text = write_weekly_text()
        self.assertIn("已根据本周聊天与未完成待办生成云文档", text)
        self.assertIn("weekly_from_chat", text)
        self.assertTrue(any(c[:2] == ["docs", "+create"] for c in calls))
        self.assertFalse(any(c[:2] == ["docs", "+search"] for c in calls))

    def test_message_evidence_skips_bot_noise_without_work(self) -> None:
        start = datetime(2026, 8, 17, tzinfo=CN)
        end = datetime(2026, 8, 18, tzinfo=CN)

        def fake_lark(args: list[str], **_kwargs: object) -> dict[str, object]:
            if args[:2] == ["im", "+chat-list"]:
                return {
                    "ok": True,
                    "data": {
                        "chats": [
                            {"chat_id": "oc_bot", "name": "吴梦晨的飞书 CLI"},
                            {"chat_id": "oc_work", "name": "APP沟通群"},
                        ]
                    },
                }
            if args[:2] == ["im", "+messages-search"]:
                return {
                    "ok": True,
                    "data": {
                        "messages": [
                            {
                                "chat_id": "oc_bot",
                                "message_id": "om_help",
                                "create_time": "2026-08-17T10:00:00+08:00",
                                "sender": {"id": "ou_user", "name": "吴梦晨"},
                                "content": {"text": "写个周报"},
                            },
                            {
                                "chat_id": "oc_work",
                                "message_id": "om_a6",
                                "create_time": "2026-08-17T11:00:00+08:00",
                                "sender": {"id": "ou_user", "name": "吴梦晨"},
                                "content": {"text": "A6 安卓包提测了"},
                            },
                        ]
                    },
                }
            raise AssertionError(args)

        with (
            patch("partner.office.recap.run_lark", side_effect=fake_lark),
            patch("partner.office.recap.USER_OPEN_ID", "ou_user"),
            patch("partner.office.recap.P2P_CHAT_ID", "oc_bot"),
        ):
            bundle = collect_message_evidence(start, end, label="周期：测试")
        self.assertIn("A6", bundle.context)
        self.assertNotIn("写个周报", bundle.context)


if __name__ == "__main__":
    unittest.main()
