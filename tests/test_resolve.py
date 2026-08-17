"""Seam: P2P「已处理」销账 — 不搜文档；明早简报跳过；卡片按钮写同一本账。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from partner.events import extract_card_action, extract_inbound_message
from partner.intents import parse_intent
from partner.llm import should_partner
from partner.resolved import (
    is_resolved,
    mark_resolved,
    match_pending,
    pending_card,
    pending_key,
    resolve_text,
    save_pending,
)


class ResolveIntentTests(unittest.TestCase):
    def test_p2p_done_phrase_is_resolve_not_docs(self) -> None:
        intent = parse_intent("回 APP沟通群 那条（进行中）已经处理")
        self.assertEqual(intent.action, "resolve")
        self.assertIn("APP沟通群", intent.query)

    def test_which_group_stays_chats(self) -> None:
        self.assertEqual(
            parse_intent("软件发版 测试 孙萌测试是那个群").action, "chats"
        )

    def test_question_is_not_resolve(self) -> None:
        self.assertNotEqual(parse_intent("今天待办已经处理完了吗").action, "resolve")

    def test_resolve_skips_hermes(self) -> None:
        self.assertFalse(should_partner("p2p", "resolve"))


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_RESOLVED"] = str(Path(self.tmp.name) / "resolved.jsonl")
        os.environ["FEISHU_PARTNER_PENDING"] = str(Path(self.tmp.name) / "pending.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_RESOLVED", None)
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_PENDING", None)

    def test_mark_then_skip(self) -> None:
        key = pending_key(message_id="om_app1")
        self.assertFalse(is_resolved(key))
        self.assertTrue(mark_resolved(key, source="text", chat_name="APP沟通群"))
        self.assertTrue(is_resolved(key))
        self.assertTrue(mark_resolved(key, source="card"))

    def test_match_chat_name_unique(self) -> None:
        items = [
            {
                "key": "om:om_app1",
                "chat_name": "APP沟通群",
                "text": "主分支同步一下",
                "chat_id": "oc_app",
            },
            {
                "key": "om:om_other",
                "chat_name": "终端测试专项组",
                "text": "发版",
                "chat_id": "oc_other",
            },
        ]
        hits = match_pending("APP沟通群", items)
        self.assertEqual([h["key"] for h in hits], ["om:om_app1"])

    def test_ambiguous_same_chat_does_not_guess(self) -> None:
        items = [
            {"key": "om:a", "chat_name": "APP沟通群", "text": "主分支", "chat_id": "oc_app"},
            {"key": "om:b", "chat_name": "APP沟通群", "text": "发版包", "chat_id": "oc_app"},
        ]
        hits = match_pending("APP沟通群", items)
        self.assertEqual(len(hits), 2)

    def test_resolve_text_marks_unique_and_skips_docs(self) -> None:
        save_pending(
            [
                {
                    "key": "om:om_app1",
                    "chat_name": "APP沟通群",
                    "text": "主分支同步一下",
                    "chat_id": "oc_app",
                    "tag": "进行中·已追问未答完",
                }
            ]
        )
        reply = resolve_text("回 APP沟通群 那条（进行中）已经处理")
        self.assertIn("已记下", reply)
        self.assertIn("APP沟通群", reply)
        self.assertNotIn("【摘录】", reply)
        self.assertNotIn("文档", reply)
        self.assertTrue(is_resolved("om:om_app1"))

    def test_resolve_text_asks_when_ambiguous(self) -> None:
        save_pending(
            [
                {"key": "om:a", "chat_name": "APP沟通群", "text": "主分支", "chat_id": "oc_app"},
                {"key": "om:b", "chat_name": "APP沟通群", "text": "发版包", "chat_id": "oc_app"},
            ]
        )
        reply = resolve_text("APP沟通群那条已经处理")
        self.assertIn("好几条", reply)
        self.assertFalse(is_resolved("om:a"))
        self.assertFalse(is_resolved("om:b"))

    def test_resolve_falls_back_to_inbox_without_brief(self) -> None:
        inbox = Path(self.tmp.name) / "inbox.jsonl"
        os.environ["FEISHU_PARTNER_INBOX"] = str(inbox)
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_INBOX", None)
        inbox.write_text(
            json.dumps(
                {
                    "message_id": "om_live",
                    "chat_id": "oc_app",
                    "chat_name": "APP沟通群",
                    "text": "主分支同步一下",
                    "ts": "2026-08-16T18:00:00+08:00",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        reply = resolve_text("回 APP沟通群 那条（进行中）已经处理")
        self.assertIn("已记下", reply)
        self.assertTrue(is_resolved("om:om_live"))


class CardTests(unittest.TestCase):
    def test_card_button_carries_item_key(self) -> None:
        card = pending_card(
            [
                {
                    "key": "om:om_app1",
                    "chat_name": "APP沟通群",
                    "text": "主分支同步一下",
                    "tag": "进行中",
                }
            ]
        )
        blob = json.dumps(card, ensure_ascii=False)
        self.assertIn("已处理", blob)
        self.assertIn("om:om_app1", blob)
        self.assertNotIn("今日", blob)
        self.assertNotIn("未结束", blob)

    def test_card_click_is_not_an_im_message(self) -> None:
        payload = {
            "ok": True,
            "data": {
                "chat_id": "oc_p2p",
                "message_id": "om_card",
                "operator_id": "ou_user",
                "action_tag": "button",
                "action_value": json.dumps({"act": "done", "key": "om:om_app1"}),
                "event_id": "ev_1",
            },
        }
        self.assertIsNone(extract_inbound_message(payload))
        act = extract_card_action(payload)
        assert act is not None
        self.assertEqual(act.key, "om:om_app1")
        self.assertEqual(act.operator_id, "ou_user")


if __name__ == "__main__":
    unittest.main()
