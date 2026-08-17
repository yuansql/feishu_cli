"""Seam: extract_inbound_message — NDJSON event → inbound message or skip."""

from __future__ import annotations

import unittest

from partner.events import extract_inbound_message, should_reply


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


if __name__ == "__main__":
    unittest.main()
