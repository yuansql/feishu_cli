"""Seam: watch — group traffic about 吴梦晨 → store, maybe push to P2P."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from partner.core.events import extract_inbound_message, should_reply
from partner.office.watch import consider, format_inbox_digest, format_watch_push, should_watch


USER = "ou_user_wmc"
BOT = "ou_bot"


def _group(
    text: str,
    *,
    mentions: list | None = None,
    sender_id: str = "ou_other",
    message_id: str = "om_x",
    sender_type: str = "user",
):
    return extract_inbound_message(
        {
            "chat_id": "oc_g",
            "chat_type": "group",
            "chat_name": "研发群",
            "content": text,
            "message_id": message_id,
            "sender_type": sender_type,
            "sender_id": sender_id,
            "mentions": mentions or [],
        }
    )


class WatchDecideTests(unittest.TestCase):
    def test_plain_group_chat_is_ignored(self) -> None:
        msg = _group("今天开会吗")
        assert msg is not None
        self.assertFalse(should_watch(msg, user_open_id=USER, bot_open_id=BOT))
        self.assertIsNone(consider(msg, user_open_id=USER, bot_open_id=BOT))

    def test_at_user_is_stored_and_notified(self) -> None:
        msg = _group(
            "@_user_1 把这条记录发给我",
            mentions=[{"id": USER, "name": "吴梦晨", "key": "@_user_1"}],
        )
        assert msg is not None
        self.assertFalse(should_reply(msg, bot_open_id=BOT))
        self.assertTrue(should_watch(msg, user_open_id=USER, bot_open_id=BOT))
        decision = consider(msg, user_open_id=USER, bot_open_id=BOT)
        assert decision is not None
        self.assertTrue(decision.notify)
        self.assertEqual(decision.reason, "mention")
        self.assertIn("发给我", format_watch_push(decision.item))

    def test_nested_mention_id_object(self) -> None:
        msg = _group(
            "看一下",
            mentions=[{"id": {"open_id": USER}, "name": "吴梦晨"}],
        )
        assert msg is not None
        self.assertIn(USER, msg.mention_ids)
        decision = consider(msg, user_open_id=USER, bot_open_id=BOT)
        assert decision is not None
        self.assertTrue(decision.notify)

    def test_name_plus_assign_notifies(self) -> None:
        msg = _group("请吴梦晨看一下设备邮寄")
        assert msg is not None
        decision = consider(msg, user_open_id=USER, bot_open_id=BOT)
        assert decision is not None
        self.assertTrue(decision.notify)
        self.assertEqual(decision.reason, "assign")

    def test_casual_name_store_only(self) -> None:
        msg = _group("吴梦晨也在这个群哈哈")
        assert msg is not None
        decision = consider(msg, user_open_id=USER, bot_open_id=BOT)
        assert decision is not None
        self.assertFalse(decision.notify)
        self.assertEqual(decision.reason, "name")

    def test_own_message_and_bot_command_skipped(self) -> None:
        own = _group("请吴梦晨看一下", sender_id=USER)
        assert own is not None
        self.assertIsNone(consider(own, user_open_id=USER, bot_open_id=BOT))

        cmd = _group(
            "@_user_1 今天",
            mentions=[{"id": BOT, "name": "吴梦晨的飞书 CLI"}],
        )
        assert cmd is not None
        self.assertTrue(should_reply(cmd, bot_open_id=BOT))
        self.assertFalse(should_watch(cmd, user_open_id=USER, bot_open_id=BOT))

    def test_inbox_digest_and_dedup(self) -> None:
        from partner.core import inbox
        msg = _group(
            "@_user_1 处理退换货",
            mentions=[{"id": USER, "name": "吴梦晨"}],
            message_id="om_dup",
        )
        assert msg is not None
        decision = consider(msg, user_open_id=USER, bot_open_id=BOT)
        assert decision is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            self.assertTrue(inbox.append_item(decision.item, path=path))
            self.assertFalse(inbox.append_item(decision.item, path=path))
            items = inbox.recent_items(days=7, path=path)
            self.assertEqual(len(items), 1)
            digest = format_inbox_digest(items)
            self.assertIn("退换货", digest)
            self.assertIn("研发群", digest)


if __name__ == "__main__":
    unittest.main()
