from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from partner.core import chat_context

CN_TZ = timezone(timedelta(hours=8))


class ChatContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        os.environ["FEISHU_PARTNER_DATA_DIR"] = self.tmp_dir
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        os.environ.pop("FEISHU_PARTNER_DATA_DIR", None)
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ambient_context_enabled_defaults_true(self) -> None:
        # No config file -> enabled.
        self.assertTrue(chat_context.ambient_context_enabled())

    def test_env_can_disable(self) -> None:
        os.environ["FEISHU_PARTNER_AMBIENT_CONTEXT"] = "off"
        self.assertFalse(chat_context.ambient_context_enabled())
        os.environ["FEISHU_PARTNER_AMBIENT_CONTEXT"] = "on"
        self.assertTrue(chat_context.ambient_context_enabled())

    def test_ingest_and_load(self) -> None:
        chat_context.ingest(
            chat_id="oc_group",
            message_id="m1",
            sender_id="u_a",
            sender_name="Alice",
            text="下周计划周三发",
            mentions=(),
        )
        signals = chat_context.load_signals("oc_group")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].text, "下周计划周三发")

    def test_disabled_does_not_ingest(self) -> None:
        os.environ["FEISHU_PARTNER_AMBIENT_CONTEXT"] = "off"
        chat_context.ingest(
            chat_id="oc_group",
            message_id="m2",
            sender_id="u_b",
            text="不该出现",
        )
        signals = chat_context.load_signals("oc_group")
        self.assertEqual(len(signals), 0)

    def test_decay_removes_old(self) -> None:
        young = (
            chat_context._data_dir()
            / chat_context._CHAT_CONTEXT_FILE
        )
        young.parent.mkdir(parents=True, exist_ok=True)
        old_ts = (datetime.now(CN_TZ) - timedelta(hours=48)).isoformat()
        new_ts = (datetime.now(CN_TZ) - timedelta(hours=1)).isoformat()
        with young.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "chat_id": "oc_group",
                        "message_id": "old",
                        "sender_id": "u1",
                        "sender_name": "x",
                        "text": "old",
                        "mentions": [],
                        "ts": old_ts,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            fh.write(
                json.dumps(
                    {
                        "chat_id": "oc_group",
                        "message_id": "new",
                        "sender_id": "u2",
                        "sender_name": "y",
                        "text": "new",
                        "mentions": [],
                        "ts": new_ts,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        removed = chat_context.decay(hours=24)
        self.assertEqual(removed, 1)
        signals = chat_context.load_signals("oc_group")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].message_id, "new")

    def test_recent_context_render(self) -> None:
        chat_context.ingest(
            chat_id="oc_group",
            message_id="m3",
            sender_id="u_c",
            sender_name="Carol",
            text="方案定了",
        )
        ctx = chat_context.recent_context("oc_group")
        self.assertIn("Carol", ctx)
        self.assertIn("方案定了", ctx)


if __name__ == "__main__":
    unittest.main()
