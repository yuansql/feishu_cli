"""Tests for disabling brief card buttons after text-based resolution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from partner.office import messaging
from partner.office.brief_card import brief_card


class SaveLoadBriefMessageIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = messaging._BRIEF_ID_FILE
        messaging._BRIEF_ID_FILE = Path(self.tmp.name) / "brief-message-id"

    def tearDown(self) -> None:
        messaging._BRIEF_ID_FILE = self.orig
        self.tmp.cleanup()

    def test_save_and_load_roundtrip(self) -> None:
        messaging.save_brief_message_id("om_123")
        self.assertEqual(messaging.load_brief_message_id(), "om_123")

    def test_load_missing_returns_empty(self) -> None:
        self.assertEqual(messaging.load_brief_message_id(), "")


class DisableCardButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_cache = messaging._CARD_CACHE_DIR
        messaging._CARD_CACHE_DIR = Path(self.tmp.name) / "card-cache"

    def tearDown(self) -> None:
        messaging._CARD_CACHE_DIR = self.orig_cache
        self.tmp.cleanup()

    def _sample_card(self) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "已处理"},
                            "type": "primary",
                            "value": {"act": "done", "key": "om:test"},
                        }
                    ],
                }
            ],
        }

    def test_disable_cached_card_button(self) -> None:
        card = self._sample_card()
        messaging.cache_card("brief_om", card)
        with mock.patch("partner.office.messaging.run_lark", return_value={"ok": True}):
            ok, msg = messaging.disable_card_button("brief_om", "om:test", "已处理")
        self.assertTrue(ok)
        updated = json.loads((messaging._CARD_CACHE_DIR / "brief_om.json").read_text())
        actions = updated["elements"][0]["actions"]
        self.assertTrue(actions[0]["disabled"])
        self.assertEqual(actions[0]["text"]["content"], "已处理")

    def test_disable_multiple_buttons_in_one_call(self) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "action",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "完成"}, "type": "primary", "value": {"act": "done", "key": "om:a"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "完成"}, "type": "primary", "value": {"act": "done", "key": "om:b"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "完成"}, "type": "primary", "value": {"act": "done", "key": "om:c"}},
                    ],
                }
            ],
        }
        messaging.cache_card("brief_om", card)
        with mock.patch("partner.office.messaging.run_lark", return_value={"ok": True}):
            ok, msg = messaging.disable_card_buttons(
                "brief_om", [("om:a", "已处理"), ("om:b", "已处理"), ("om:c", "已完成")]
            )
        self.assertTrue(ok)
        updated = json.loads((messaging._CARD_CACHE_DIR / "brief_om.json").read_text())
        actions = updated["elements"][0]["actions"]
        self.assertTrue(actions[0]["disabled"])
        self.assertTrue(actions[1]["disabled"])
        self.assertTrue(actions[2]["disabled"])
        self.assertEqual(actions[0]["text"]["content"], "已处理")
        self.assertEqual(actions[1]["text"]["content"], "已处理")
        self.assertEqual(actions[2]["text"]["content"], "已完成")

    def test_disable_card_button_fetches_from_feishu(self) -> None:
        card = self._sample_card()
        payload = {
            "ok": True,
            "data": {"messages": [{"content": json.dumps(card)}]},
        }
        with mock.patch("partner.office.messaging.run_lark", return_value=payload):
            ok, msg = messaging.disable_card_button("om_remote", "om:test", "已完成")
        self.assertTrue(ok)

    def test_disable_string_value_button(self) -> None:
        """2026-09-08 回归：飞书有时将 value 序列化为 JSON 字符串而非 dict。"""
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "完成"},
                            "type": "primary",
                            "value": '{"act":"fu_done","key":"fu:om_123"}',
                        }
                    ],
                }
            ],
        }
        messaging.cache_card("brief_om", card)
        with mock.patch("partner.office.messaging.run_lark", return_value={"ok": True}):
            ok, msg = messaging.disable_card_button("brief_om", "fu:om_123", "已完成")
        self.assertTrue(ok, msg)
        updated = json.loads((messaging._CARD_CACHE_DIR / "brief_om.json").read_text())
        actions = updated["elements"][0]["actions"]
        self.assertTrue(actions[0]["disabled"])
        self.assertEqual(actions[0]["text"]["content"], "已完成")

    def test_degraded_event_card_falls_back_to_cache(self) -> None:
        """2026-09-09 回归：事件自带 card_content 是飞书 message get API 的降级结构
        （只有 title+text，按钮全部丢失）。若调用方传入这种 card，必须回退到缓存
        里的完整卡片，否则永远 'no keys found'、按钮不变灰。"""
        full_card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📝 有人派活"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "做 WIFI 耦合"}},
                {
                    "tag": "action",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "完成"}, "type": "primary", "value": {"act": "fu_done", "key": "fu:om_x1"}}
                    ],
                },
            ],
        }
        messaging.cache_card("assign_om", full_card)
        degraded = {
            "title": "📝 有人派活",
            "elements": [[{"tag": "text", "text": "做 WIFI 耦合"}]],
        }
        with mock.patch("partner.office.messaging.run_lark", return_value={"ok": True}):
            ok, msg = messaging.disable_card_button(
                "assign_om", "fu:om_x1", "已完成", card=degraded
            )
        self.assertTrue(ok, msg)
        updated = json.loads((messaging._CARD_CACHE_DIR / "assign_om.json").read_text())
        actions = updated["elements"][1]["actions"]
        self.assertTrue(actions[0]["disabled"])
        self.assertEqual(actions[0]["text"]["content"], "已完成")

    def test_sequential_clicks_accumulate_disabled(self) -> None:
        """2026-09-03 回归：连点多个「完成」，每次 patch 都必须基于上一次的合并结果，
        否则后写的全量卡片会把先点的按钮还原成可点。"""
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "action",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "完成"}, "type": "primary", "value": {"act": "done", "key": "om:a"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "完成"}, "type": "primary", "value": {"act": "done", "key": "om:b"}},
                    ],
                }
            ],
        }
        messaging.cache_card("brief_om", card)
        with mock.patch("partner.office.messaging.run_lark", return_value={"ok": True}):
            ok1, _ = messaging.disable_card_button("brief_om", "om:a", "已处理")
            ok2, _ = messaging.disable_card_button("brief_om", "om:b", "已处理")
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        updated = json.loads((messaging._CARD_CACHE_DIR / "brief_om.json").read_text())
        actions = updated["elements"][0]["actions"]
        # 两次独立点击后，两个按钮都必须保持 disabled——第一个不能被第二次覆盖还原
        self.assertTrue(actions[0]["disabled"], "第一个按钮被第二次 patch 还原了")
        self.assertTrue(actions[1]["disabled"])


class DisableBriefButtonIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_cache = messaging._CARD_CACHE_DIR
        self.orig_brief = messaging._BRIEF_ID_FILE
        messaging._CARD_CACHE_DIR = Path(self.tmp.name) / "card-cache"
        messaging._BRIEF_ID_FILE = Path(self.tmp.name) / "brief-message-id"

    def tearDown(self) -> None:
        messaging._CARD_CACHE_DIR = self.orig_cache
        messaging._BRIEF_ID_FILE = self.orig_brief
        self.tmp.cleanup()

    def test_disable_brief_button_after_text_resolve(self) -> None:
        # Build a realistic brief card and cache it as if push_brief sent it.
        real_card = brief_card(
            {
                "today": "2026-09-01",
                "workday": "2026-09-01",
                "progressed": [],
                "unreplied": [],
                "priorities": [],
                "week_notes": [],
                "pending": [
                    {
                        "key": "om:sprint-demo",
                        "text": "准备 sprint demo",
                        "chat_name": "技术群",
                        "link": "https://x.com",
                    }
                ],
            }
        )
        messaging.cache_card("brief_om", real_card)
        messaging.save_brief_message_id("brief_om")

        # Simulate the side effect of resolve_text after a text mark-done.
        with mock.patch("partner.office.messaging.run_lark", return_value={"ok": True}):
            ok, msg = messaging.disable_card_button("brief_om", "om:sprint-demo", "已处理")
        self.assertTrue(ok)

        updated = json.loads((messaging._CARD_CACHE_DIR / "brief_om.json").read_text())

        def walk(node):
            if isinstance(node, dict):
                value = node.get("value")
                if isinstance(value, dict) and value.get("key") == "om:sprint-demo":
                    self.assertTrue(node.get("disabled"))
                    self.assertEqual(node["text"]["content"], "已处理")
                    return True
                return any(walk(v) for v in node.values())
            if isinstance(node, list):
                return any(walk(item) for item in node)
            return False

        self.assertTrue(walk(updated))


if __name__ == "__main__":
    unittest.main()
