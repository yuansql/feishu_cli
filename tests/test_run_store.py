from __future__ import annotations

import os
import tempfile
import unittest

from partner.run_store import init_db, lookup_trigger, record_trigger


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = os.path.join(
            self.tmp.name, "runtime.db"
        )

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        self.tmp.cleanup()

    def test_trigger_dedup(self) -> None:
        init_db()
        created1, tid1 = record_trigger(
            source="webhook",
            external_id="evt-1",
            task_id="task-a",
            payload={"goal": "hello"},
        )
        created2, tid2 = record_trigger(
            source="webhook",
            external_id="evt-1",
            task_id="task-b",
            payload={"goal": "hello"},
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(tid1, "task-a")
        self.assertEqual(tid2, "task-a")
        self.assertEqual(lookup_trigger(source="webhook", external_id="evt-1"), "task-a")


if __name__ == "__main__":
    unittest.main()
