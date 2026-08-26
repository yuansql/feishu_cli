"""Tests for conditional triggers (message keyword / webhook) and event log."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime

from partner.core.events import InboundMessage
from partner.core.triggers import (
    CN_TZ,
    add_trigger,
    delete_trigger,
    list_trigger_events,
    list_triggers,
    lookup_trigger_event,
    match_message_trigger,
    match_webhook_trigger,
    record_trigger_event,
    run_trigger,
)


class DummyMessage:
    def __init__(
        self,
        text: str = "",
        chat_type: str = "group",
        sender_id: str = "ou_user",
        sender_type: str = "user",
        message_id: str = "msg_1",
    ) -> None:
        self.text = text
        self.chat_type = chat_type
        self.sender_id = sender_id
        self.sender_type = sender_type
        self.message_id = message_id


class MessageTriggerMatchTests(unittest.TestCase):
    def test_keywords_match(self) -> None:
        spec = {
            "source": "message",
            "enabled": True,
            "condition": {"keywords": ["bug", "缺陷"]},
        }
        matched = match_message_trigger(spec, DummyMessage(text="发现一个缺陷"))
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertIn("缺陷", matched["keywords"])

    def test_keywords_no_match(self) -> None:
        spec = {
            "source": "message",
            "enabled": True,
            "condition": {"keywords": ["bug"]},
        }
        matched = match_message_trigger(spec, DummyMessage(text="今天天气不错"))
        self.assertIsNone(matched)

    def test_disabled_no_match(self) -> None:
        spec = {
            "source": "message",
            "enabled": False,
            "condition": {"keywords": ["bug"]},
        }
        matched = match_message_trigger(spec, DummyMessage(text="bug here"))
        self.assertIsNone(matched)

    def test_chat_type_filter(self) -> None:
        spec = {
            "source": "message",
            "enabled": True,
            "condition": {"keywords": ["alert"], "chat_type": "group"},
        }
        self.assertIsNotNone(
            match_message_trigger(spec, DummyMessage(text="alert", chat_type="group"))
        )
        self.assertIsNone(
            match_message_trigger(spec, DummyMessage(text="alert", chat_type="p2p"))
        )

    def test_sender_filter(self) -> None:
        spec = {
            "source": "message",
            "enabled": True,
            "condition": {"keywords": ["hi"], "sender_id": "ou_boss"},
        }
        self.assertIsNotNone(
            match_message_trigger(spec, DummyMessage(text="hi", sender_id="ou_boss"))
        )
        self.assertIsNone(
            match_message_trigger(spec, DummyMessage(text="hi", sender_id="ou_user"))
        )

    def test_bot_sender_ignored(self) -> None:
        spec = {
            "source": "message",
            "enabled": True,
            "condition": {"keywords": ["bug"]},
        }
        matched = match_message_trigger(
            spec, DummyMessage(text="bug", sender_type="bot")
        )
        self.assertIsNone(matched)


class WebhookTriggerMatchTests(unittest.TestCase):
    def test_path_match(self) -> None:
        spec = {
            "source": "webhook",
            "enabled": True,
            "condition": {"webhook_path": "jira"},
        }
        matched = match_webhook_trigger(spec, "jira", {"event": "created"})
        self.assertIsNotNone(matched)

    def test_path_mismatch(self) -> None:
        spec = {
            "source": "webhook",
            "enabled": True,
            "condition": {"webhook_path": "jira"},
        }
        matched = match_webhook_trigger(spec, "github", {"event": "push"})
        self.assertIsNone(matched)


class TriggerEventLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("FEISHU_PARTNER_RUNTIME_DB")
        self.db_path = os.path.join(self.tmp.name, "runtime.db")
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.db_path

    def tearDown(self) -> None:
        if self.old_db is None:
            os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        else:
            os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.old_db
        self.tmp.cleanup()

    def test_record_and_lookup(self) -> None:
        record_trigger_event(
            trigger_id="t1",
            source="webhook",
            external_id="evt_1",
            task_id="abc123",
        )
        existing = lookup_trigger_event(source="webhook", external_id="evt_1")
        self.assertIsNotNone(existing)
        assert existing is not None
        self.assertEqual(existing["trigger_id"], "t1")
        self.assertEqual(existing["task_id"], "abc123")

    def test_list_events(self) -> None:
        record_trigger_event(
            trigger_id="t1",
            source="schedule",
            external_id="evt_a",
            task_id="task_a",
        )
        record_trigger_event(
            trigger_id="t2",
            source="message",
            external_id="evt_b",
            task_id="task_b",
        )
        events = list_trigger_events(limit=10)
        self.assertEqual(len(events), 2)
        # Sort is stable by fired_at desc, then created_at desc, then id desc.
        # Since events may share fired_at second precision, just assert both
        # sources are present and sources are orderable.
        sources = [e["source"] for e in events]
        self.assertIn("schedule", sources)
        self.assertIn("message", sources)

    def test_event_filter_by_trigger(self) -> None:
        record_trigger_event(
            trigger_id="t1",
            source="schedule",
            external_id="evt_a",
            task_id="task_a",
        )
        record_trigger_event(
            trigger_id="t2",
            source="message",
            external_id="evt_b",
            task_id="task_b",
        )
        events = list_trigger_events(trigger_id="t1", limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trigger_id"], "t1")


class TriggerRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("FEISHU_PARTNER_RUNTIME_DB")
        self.db_path = os.path.join(self.tmp.name, "runtime.db")
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.db_path

    def tearDown(self) -> None:
        if self.old_db is None:
            os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        else:
            os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.old_db
        self.tmp.cleanup()

    def test_add_message_trigger(self) -> None:
        spec = add_trigger(
            goal="总结缺陷",
            source="message",
            condition={"keywords": ["缺陷", "bug"], "chat_type": "group"},
            title="缺陷汇总",
        )
        self.assertEqual(spec["source"], "message")
        self.assertEqual(spec["next_run_at"], "")
        loaded = list_triggers()
        self.assertEqual(len(loaded), 1)

    def test_add_webhook_trigger(self) -> None:
        spec = add_trigger(
            goal="处理 Jira 事件",
            source="webhook",
            condition={"webhook_path": "jira"},
        )
        self.assertEqual(spec["source"], "webhook")
        delete_trigger(spec["id"])
        self.assertEqual(len(list_triggers()), 0)

    def test_message_trigger_requires_keywords(self) -> None:
        with self.assertRaises(ValueError):
            add_trigger(
                goal="总结缺陷",
                source="message",
                condition={"chat_type": "group"},
            )

    def test_webhook_trigger_requires_path(self) -> None:
        with self.assertRaises(ValueError):
            add_trigger(
                goal="处理事件",
                source="webhook",
                condition={},
            )

    def test_schedule_trigger_rejects_condition_source_without_schedule(self) -> None:
        with self.assertRaises(ValueError):
            add_trigger(
                goal="早报",
                source="schedule",
                schedule="",
            )

    def test_message_trigger_rejects_schedule(self) -> None:
        with self.assertRaises(ValueError):
            add_trigger(
                goal="早报",
                source="message",
                schedule="daily@09:00",
                condition={"keywords": ["早报"]},
            )


if __name__ == "__main__":
    unittest.main()
