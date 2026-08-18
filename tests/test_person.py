"""Seam: 问某人回复 → 会话/消息，不搜文档。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.actions import dispatch, person_text
from partner.intents import parse_intent


def _chat_search(name: str, chat_id: str = "oc_p2p_z") -> dict:
    return {
        "ok": True,
        "data": {
            "chats": [
                {"name": name, "chat_id": chat_id, "chat_mode": "p2p"},
            ]
        },
    }


def _members(name: str, oid: str = "ou_zhang") -> dict:
    return {"ok": True, "data": {"users": [{"name": name, "member_id": oid}]}}


def _sender_hits(who: str, text: str) -> dict:
    return {
        "ok": True,
        "data": {
            "messages": [
                {
                    "sender": {"name": who, "id": "ou_zhang"},
                    "content": text,
                    "create_time": "2026-08-17 10:12",
                    "chat_id": "oc_p2p_z",
                }
            ]
        },
    }


class PersonIntentDispatchTests(unittest.TestCase):
    def test_person_text_uses_chat_not_docs(self) -> None:
        with patch("partner.actions.run_lark") as run:
            run.side_effect = [
                _chat_search("张三"),
                _members("张三"),
                _sender_hits("张三", "方案可以，明天提测"),
            ]
            with patch("partner.actions.analyze_result") as analyze:
                with patch("partner.actions.recent_items", return_value=[]):
                    out = person_text("张三")
        analyze.assert_not_called()
        argv0 = run.call_args_list[0].args[0]
        self.assertEqual(argv0[:2], ["im", "+chat-search"])
        self.assertNotIn("--disable-search-by-user", argv0)
        cmds = [call.args[0][:2] for call in run.call_args_list]
        self.assertIn(["im", "+chat-members-list"], cmds)
        self.assertIn(["im", "+messages-search"], cmds)
        sender_argv = run.call_args_list[2].args[0]
        self.assertIn("--sender", sender_argv)
        self.assertIn("方案可以", out)
        self.assertNotIn("没对上具体材料", out)

    def test_dispatch_person_does_not_search_docs(self) -> None:
        asked = "马丽敏的回复如何？"
        intent = parse_intent(asked)
        self.assertEqual(intent.action, "person")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FEISHU_PARTNER_SESSION"] = str(Path(tmp.name) / "session.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)
        with patch("partner.actions.person_text", return_value="马丽敏：方案可以") as person:
            with patch("partner.actions.analyze_result") as analyze:
                with patch("partner.actions.should_partner", return_value=False):
                    out = dispatch(
                        intent,
                        user_text=asked,
                        channel="p2p",
                        chat_id="oc_p2p",
                    )
        analyze.assert_not_called()
        person.assert_called()
        self.assertIn("方案可以", out)
        self.assertNotIn("没对上具体材料", out)
        self.assertNotIn("进度、接口", out)

    def test_unknown_classifies_to_person_not_docs(self) -> None:
        asked = "马丽敏那边怎么样了"
        self.assertEqual(parse_intent(asked).action, "unknown")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FEISHU_PARTNER_SESSION"] = str(Path(tmp.name) / "session.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)
        classified = parse_intent("马丽敏的回复如何")
        with patch("partner.actions.classify_intent", return_value=classified):
            with patch("partner.actions.person_text", return_value="她说方案可以") as person:
                with patch("partner.actions.analyze_result") as analyze:
                    with patch("partner.actions.should_partner", return_value=False):
                        out = dispatch(
                            parse_intent(asked),
                            user_text=asked,
                            channel="p2p",
                            chat_id="oc_p2p",
                        )
        analyze.assert_not_called()
        person.assert_called()
        self.assertIn("方案可以", out)


if __name__ == "__main__":
    unittest.main()
