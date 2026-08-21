from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.actions import dispatch
from partner.core.ids import P2P_CHAT_ID, USER_OPEN_ID
from partner.routing.intents import Intent, parse_intent
from partner.office.recap import (
    DayRecap,
    collect_today_evidence,
    looks_like_recap_followup,
    today_recap,
)
from partner.core.session import load_turn, save_turn

CN_TZ = timezone(timedelta(hours=8))


def _message(
    chat_id: str,
    sender_id: str,
    text: str,
    minute: int,
    message_id: str,
) -> dict[str, object]:
    return {
        "chat_id": chat_id,
        "chat_type": "group" if chat_id == "oc_work" else "p2p",
        "message_id": message_id,
        "msg_type": "text",
        "create_time": f"2026-08-19T16:{minute:02d}:00+08:00",
        "sender": {
            "id": sender_id,
            "name": "吴梦晨" if sender_id == USER_OPEN_ID else "同事",
            "sender_type": "user",
        },
        "content": {"text": text},
        "deleted": False,
    }


class TodayEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 19, 19, 0, tzinfo=CN_TZ)

    def _fake_lark(self, args: list[str], **_kwargs: object) -> dict[str, object]:
        if args[:2] == ["im", "+chat-list"]:
            return {
                "ok": True,
                "data": {
                    "chats": [
                        {"chat_id": "oc_work", "name": "AI中台接口对接群"},
                        {"chat_id": "oc_social", "name": "AI兴趣群"},
                        {"chat_id": P2P_CHAT_ID, "name": "吴梦晨的飞书 CLI"},
                    ]
                },
            }
        if args[:2] == ["im", "+messages-search"]:
            return {
                "ok": True,
                "data": {
                    "messages": [
                        _message(
                            "oc_work",
                            USER_OPEN_ID,
                            "我这边改完 A8 接口，测试环境可以用了",
                            10,
                            "om_work_1",
                        ),
                        _message(
                            "oc_work",
                            "ou_peer",
                            "收到，预发环境还没上",
                            11,
                            "om_work_2",
                        ),
                        _message(
                            "oc_work",
                            USER_OPEN_ID,
                            "可以",
                            12,
                            "om_noise",
                        ),
                        _message(
                            P2P_CHAT_ID,
                            USER_OPEN_ID,
                            "我今天干了什么？",
                            46,
                            "om_bot_query",
                        ),
                        _message(
                            "oc_social",
                            USER_OPEN_ID,
                            "今天薅羊毛试了会员",
                            50,
                            "om_social",
                        ),
                    ],
                    "has_more": False,
                },
            }
        raise AssertionError(args)

    def test_collects_work_context_and_excludes_bot_loop(self) -> None:
        with patch("partner.office.recap.run_lark", side_effect=self._fake_lark):
            bundle = collect_today_evidence(self.now)
        self.assertEqual(bundle.message_count, 5)
        self.assertIn("AI中台接口对接群", bundle.context)
        self.assertIn("改完 A8 接口", bundle.context)
        self.assertIn("预发环境还没上", bundle.context)
        self.assertNotIn("我今天干了什么", bundle.context)
        self.assertNotIn("om_noise", bundle.context)
        self.assertNotIn("薅羊毛", bundle.context)

    def test_recap_falls_back_to_evidence_summary(self) -> None:
        with patch("partner.office.recap.run_lark", side_effect=self._fake_lark):
            result = today_recap("我今天干了什么", now=self.now)
        self.assertIn("A8 接口", result.text)
        self.assertIn("预发", result.text)
        self.assertIn("待你确认", result.text)
        self.assertGreater(result.evidence_count, 0)

    def test_followup_focuses_pending_without_raw_message_ids(self) -> None:
        with patch("partner.office.recap.run_lark", side_effect=self._fake_lark):
            bundle = collect_today_evidence(self.now)
        result = today_recap(
            "继续确认",
            previous_context=bundle.context,
            refresh=False,
        )
        self.assertTrue(result.text.startswith("【继续确认】"))
        self.assertIn("预发", result.text)
        self.assertNotIn("id=om_", result.text)
        narrowed = today_recap(
            "继续确认 预发",
            previous_context=bundle.context,
            refresh=False,
        )
        self.assertIn("预发环境", narrowed.text)
        self.assertNotIn("A6 全功能", narrowed.text)

    def test_recap_followup_phrases(self) -> None:
        for text in ("继续确认", "再详细点", "还有呢", "接着看"):
            self.assertTrue(looks_like_recap_followup(text), text)
        self.assertFalse(looks_like_recap_followup("今天"))


class TodayRecapDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_SESSION"] = str(
            Path(self.tmp.name) / "session.json"
        )
        os.environ["FEISHU_PARTNER_ARTIFACTS"] = str(
            Path(self.tmp.name) / "artifacts.json"
        )
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_ARTIFACTS", None)

    def test_recap_persists_evidence_for_next_turn(self) -> None:
        result = DayRecap("今天推进了 A8。", "EVIDENCE", 12, 5)
        with patch("partner.actions.today_recap", return_value=result) as recap:
            reply = dispatch(
                parse_intent("我今天干了什么？"),
                user_text="我今天干了什么？",
                channel="p2p",
                chat_id="oc_recap",
            )
        self.assertEqual(reply, result.text)
        recap.assert_called_once()
        turn = load_turn("oc_recap")
        assert turn is not None
        self.assertEqual(turn["action"], "today_recap")
        self.assertEqual(turn["context"], "EVIDENCE")
        self.assertEqual(turn["result"], result.text)

    def test_continue_confirmation_reuses_previous_evidence(self) -> None:
        save_turn(
            "oc_recap",
            kind="action",
            query="我今天干了什么？",
            action="today_recap",
            context="OLD EVIDENCE",
            result="旧结论",
        )
        result = DayRecap("继续确认后的结论", "OLD EVIDENCE", 12, 5)
        with patch("partner.actions.today_recap", return_value=result) as recap:
            reply = dispatch(
                Intent(action="unknown", query="继续确认"),
                user_text="继续确认",
                channel="p2p",
                chat_id="oc_recap",
            )
        self.assertEqual(reply, result.text)
        self.assertFalse(recap.call_args.kwargs["refresh"])
        self.assertEqual(
            recap.call_args.kwargs["previous_context"],
            "OLD EVIDENCE",
        )


if __name__ == "__main__":
    unittest.main()
