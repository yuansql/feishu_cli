"""Seam: extract_inbound_message — NDJSON event → inbound message or skip."""

from __future__ import annotations

import unittest

from partner.core.events import extract_inbound_message, should_reply
from partner.routing.intents import parse_intent


class ExtractInboundMessageTests(unittest.TestCase):
    def test_p2p_text(self) -> None:
        msg = extract_inbound_message(
            {
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "content": "今天",
                "message_id": "om_1",
                "message_type": "text",
                "sender_type": "user",
                "sender_id": "ou_user",
            }
        )
        assert msg is not None
        self.assertEqual(msg.chat_id, "oc_1")
        self.assertEqual(msg.text, "今天")
        self.assertTrue(should_reply(msg, bot_open_id="ou_bot"))

    def test_skips_bot_sender(self) -> None:
        msg = extract_inbound_message(
            {
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "content": "今天",
                "message_id": "om_1",
                "sender_type": "app",
                "sender_id": "ou_bot",
            }
        )
        assert msg is not None
        self.assertFalse(should_reply(msg, bot_open_id="ou_bot"))

    def test_group_requires_mention_or_wake(self) -> None:
        plain = extract_inbound_message(
            {
                "chat_id": "oc_g",
                "chat_type": "group",
                "content": "今天开会吗",
                "message_id": "om_2",
                "sender_type": "user",
                "mentions": [],
            }
        )
        assert plain is not None
        self.assertFalse(should_reply(plain, bot_open_id="ou_bot"))

        woke = extract_inbound_message(
            {
                "chat_id": "oc_g",
                "chat_type": "group",
                "content": "工作伙伴 今天",
                "message_id": "om_3",
                "sender_type": "user",
                "mentions": [],
            }
        )
        assert woke is not None
        self.assertTrue(should_reply(woke, bot_open_id="ou_bot"))

        mentioned = extract_inbound_message(
            {
                "chat_id": "oc_g",
                "chat_type": "group",
                "content": "@_user_1 待办",
                "message_id": "om_4",
                "sender_type": "user",
                "mentions": [{"id": "ou_bot", "name": "吴梦晨的飞书 CLI", "key": "@_user_1"}],
            }
        )
        assert mentioned is not None
        self.assertTrue(should_reply(mentioned, bot_open_id="ou_bot"))

        at_person = extract_inbound_message(
            {
                "chat_id": "oc_g",
                "chat_type": "group",
                "content": "@_user_1 把这条发给我",
                "message_id": "om_5",
                "sender_type": "user",
                "mentions": [{"id": "ou_user", "name": "吴梦晨", "key": "@_user_1"}],
            }
        )
        assert at_person is not None
        self.assertFalse(at_person.woke)
        self.assertFalse(should_reply(at_person, bot_open_id="ou_bot"))

    def test_skips_non_object(self) -> None:
        self.assertIsNone(extract_inbound_message("nope"))
        self.assertIsNone(extract_inbound_message({"ok": False}))


class ShareCardLiftTests(unittest.TestCase):
    """Share-card payloads carry the doc URL inside `content`, not in `text`.
    Surface it so parse_intent can route to `read` instead of falling to `help`."""

    _BASE = {
        "chat_id": "oc_1",
        "chat_type": "p2p",
        "message_id": "om_1",
        "sender_type": "user",
        "sender_id": "ou_user",
    }

    _URL = "https://bytedance.feishu.cn/docx/ABC123"

    def test_share_card_lifts_url_from_share_block(self) -> None:
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "share_card",
                "content": {
                    "share_card": {
                        "title": "平台研发部周报9月份第1周",
                        "url": self._URL,
                    }
                },
            }
        )
        assert msg is not None
        self.assertEqual(msg.msg_type, "share_card")
        self.assertEqual(msg.text, self._URL)
        intent = parse_intent(msg.text)
        self.assertEqual(intent.action, "read")
        self.assertEqual(intent.query, self._URL)

    def test_share_card_lifts_url_from_top_level(self) -> None:
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "share_card",
                "content": {"url": self._URL, "title": "周报"},
            }
        )
        assert msg is not None
        self.assertEqual(msg.text, self._URL)
        intent = parse_intent(msg.text)
        self.assertEqual(intent.action, "read")

    def test_share_card_lifts_url_from_json_string_content(self) -> None:
        # lark-cli 偶尔把 content 序列化成 JSON string
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "share_card",
                "content": '{"share_card": {"url": "' + self._URL + '"}}',
            }
        )
        assert msg is not None
        self.assertEqual(msg.text, self._URL)
        intent = parse_intent(msg.text)
        self.assertEqual(intent.action, "read")

    def test_share_card_preserves_existing_text(self) -> None:
        # lark-cli 已把 URL 提到 content.text 时不重复提升
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "share_card",
                "content": {"text": self._URL, "url": "https://bytedance.feishu.cn/docx/OTHER"},
            }
        )
        assert msg is not None
        self.assertEqual(msg.text, self._URL)

    def test_share_card_no_url_keeps_empty_text(self) -> None:
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "share_card",
                "content": {"title": "只有标题没有 URL"},
            }
        )
        assert msg is not None
        self.assertEqual(msg.text, "")
        intent = parse_intent(msg.text)
        self.assertEqual(intent.action, "help")

    def test_share_card_non_feishu_url_ignored(self) -> None:
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "share_card",
                "content": {"url": "https://example.com/not-a-doc"},
            }
        )
        assert msg is not None
        self.assertEqual(msg.text, "")

    def test_text_message_not_affected(self) -> None:
        # 非 share 类型即使 content 里有 url 字段也不提升
        msg = extract_inbound_message(
            {
                **self._BASE,
                "message_type": "text",
                "content": {"text": "今天", "url": self._URL},
            }
        )
        assert msg is not None
        self.assertEqual(msg.text, "今天")


if __name__ == "__main__":
    unittest.main()
