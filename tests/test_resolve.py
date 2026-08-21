"""Seam: P2P「已处理」销账 — 不搜文档；明早简报跳过；卡片按钮写同一本账。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from partner.core.events import extract_card_action, extract_inbound_message
from partner.routing.intents import parse_intent
from partner.compose.llm import should_partner
from partner.routing.resolved import (
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

    def test_quoted_question_does_not_hide_resolve_reply(self) -> None:
        text = (
            "刚记下周学彬派你的活：\n"
            "我咋记得之前不是一起做的APP吗\n\n"
            "这个我已经解决了"
        )
        intent = parse_intent(text)
        self.assertEqual(intent.action, "resolve")
        self.assertEqual(intent.query, text)

    def test_solved_phrase_is_resolve_not_person(self) -> None:
        for text in ("这个我已经解决了", "这块我解决了", "周学彬那条已解决"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "resolve", text)
            self.assertNotEqual(intent.action, "person", text)

    def test_already_finished_phrase_is_resolve_not_help(self) -> None:
        for text in (
            "邱俊立（邱俊立）好好体验下M8p，写份体验总结给我  已经完成",
            "待处理 / 待回复（2项） 已经完成",
            "这块已完成",
        ):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "resolve", text)

    def test_weekly_instruction_blob_not_resolve(self) -> None:
        text = (
            "1.只写到今天（08-19）\n"
            "2.周报接收人  吴梦晨     发一下看看情况\n"
            "3. 邱俊立的两项任务当前进展：M8p 体验总结是否已完成  已完成 "
            "https://it82yw7fgr.feishu.cn/docx/YdO2dupdQoXGQvx"
        )
        intent = parse_intent(text)
        self.assertNotEqual(intent.action, "resolve")
        self.assertNotEqual(intent.action, "task_done")

    def test_followup_done_with_doc_url_is_resolve(self) -> None:
        text = (
            "邱俊立：好好体验下M8p，写份体验总结给我（跟进）  "
            "完成 https://it82yw7fgr.feishu.cn/docx/YdO2dupdQoXGQvx"
        )
        self.assertEqual(parse_intent(text).action, "resolve")

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
        os.environ["FEISHU_PARTNER_FOLLOWUPS"] = str(Path(self.tmp.name) / "followups.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_RESOLVED", None)
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_PENDING", None)
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_FOLLOWUPS", None)

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

    def test_solved_closes_colleague_followup_not_person_dump(self) -> None:
        from partner.office.followup import load_items, save_items

        os.environ["FEISHU_PARTNER_FOLLOWUPS"] = str(Path(self.tmp.name) / "followups.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_FOLLOWUPS", None)
        save_items(
            [
                {
                    "id": "fu:zhou",
                    "kind": "direct",
                    "asker_name": "周学彬",
                    "chat_name": "周学彬",
                    "text": "我咋记得之前不是一起做的APP吗",
                    "status": "open",
                }
            ]
        )
        reply = resolve_text("这个我已经解决了")
        self.assertIn("周学彬", reply)
        self.assertIn("不再催", reply)
        self.assertNotIn("最近怎么说", reply)
        self.assertEqual(load_items()[0]["status"], "done")

    def test_quoted_reply_closes_exact_followup_for_same_person(self) -> None:
        from partner.office.followup import load_items, save_items

        save_items(
            [
                {
                    "id": "fu:zhou-a6",
                    "kind": "direct",
                    "asker_name": "周学彬",
                    "text": "A6跟A8是区分开的吗",
                    "status": "open",
                },
                {
                    "id": "fu:zhou-app",
                    "kind": "direct",
                    "asker_name": "周学彬",
                    "text": "我咋记得之前不是一起做的APP吗",
                    "status": "open",
                },
            ]
        )
        reply = resolve_text(
            "刚记下周学彬派你的活：\n"
            "我咋记得之前不是一起做的APP吗\n\n"
            "这个我已经解决了"
        )
        self.assertIn("周学彬", reply)
        self.assertIn("已读回引", reply)
        self.assertIn("我咋记得之前不是一起做的APP吗", reply)
        self.assertIn("不再催", reply)
        statuses = {item["id"]: item["status"] for item in load_items()}
        self.assertEqual(statuses["fu:zhou-a6"], "open")
        self.assertEqual(statuses["fu:zhou-app"], "done")

    def test_stale_quoted_reply_does_not_close_another_followup(self) -> None:
        from partner.office.followup import load_items, save_items

        save_items(
            [
                {
                    "id": "fu:zhou-a6",
                    "kind": "direct",
                    "asker_name": "周学彬",
                    "text": "A6跟A8是区分开的吗",
                    "status": "open",
                }
            ]
        )
        reply = resolve_text(
            "刚记下周学彬派你的活：\n"
            "我咋记得之前不是一起做的APP吗\n\n"
            "这个已解决"
        )
        self.assertIn("已经不在待处理", reply)
        self.assertIn("没有动其他条", reply)
        self.assertEqual(load_items()[0]["status"], "open")

    def test_weak_hint_two_followups_asks_which(self) -> None:
        from partner.office.followup import load_items, save_items

        os.environ["FEISHU_PARTNER_FOLLOWUPS"] = str(Path(self.tmp.name) / "followups.json")
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_FOLLOWUPS", None)
        save_items(
            [
                {
                    "id": "fu:qiu",
                    "kind": "direct",
                    "asker_name": "邱俊立",
                    "text": "技术方案",
                    "status": "open",
                },
                {
                    "id": "fu:zhou",
                    "kind": "direct",
                    "asker_name": "周学彬",
                    "text": "我咋记得之前不是一起做的APP吗",
                    "status": "open",
                },
            ]
        )
        reply = resolve_text("这个我已经解决了")
        self.assertIn("对上好几条", reply)
        self.assertIn("邱俊立", reply)
        self.assertIn("周学彬", reply)
        self.assertNotIn("最近怎么说", reply)
        statuses = {item["id"]: item["status"] for item in load_items()}
        self.assertEqual(statuses["fu:qiu"], "open")
        self.assertEqual(statuses["fu:zhou"], "open")

    def test_already_finished_closes_followup_by_task_text(self) -> None:
        from partner.office.followup import load_items, save_items

        save_items(
            [
                {
                    "id": "fu:qiu-m8",
                    "kind": "direct",
                    "asker_name": "邱俊立",
                    "text": "好好体验下M8p，写份体验总结给我",
                    "status": "open",
                },
                {
                    "id": "fu:qiu-ai",
                    "kind": "direct",
                    "asker_name": "邱俊立",
                    "text": "研究下这个品技术方案，看下录音、转写",
                    "status": "open",
                },
            ]
        )
        reply = resolve_text(
            "邱俊立（邱俊立）好好体验下M8p，写份体验总结给我  已经完成"
        )
        self.assertIn("邱俊立", reply)
        self.assertIn("不再催", reply)
        self.assertNotIn("直接说", reply)
        statuses = {item["id"]: item["status"] for item in load_items()}
        self.assertEqual(statuses["fu:qiu-m8"], "done")
        self.assertEqual(statuses["fu:qiu-ai"], "open")

    def test_section_header_closes_all_brief_pending(self) -> None:
        save_pending(
            [
                {
                    "key": "om:a",
                    "chat_name": "APP沟通群",
                    "text": "@孙景伦 @吴梦晨 一会没事，就来大会议呗",
                    "tag": "进行中·已追问未答完",
                },
                {
                    "key": "om:b",
                    "chat_name": "APP沟通群",
                    "text": "@吴梦晨 别忘了报销",
                    "tag": "进行中·已追问未答完",
                },
            ]
        )
        reply = resolve_text("待处理 / 待回复（2项） 已经完成")
        self.assertIn("2", reply)
        self.assertIn("明早简报", reply)
        self.assertTrue(is_resolved("om:a"))
        self.assertTrue(is_resolved("om:b"))


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
