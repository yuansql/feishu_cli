from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from partner.core.run_store import (
    acquire_lease,
    apply_recovered_leases,
    init_db,
    list_runs_text,
    load_run,
    lookup_trigger,
    recover_expired_runs,
    record_trigger,
    upsert_run,
)


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

    def test_upsert_load_and_lease_recover(self) -> None:
        init_db()
        tz = timezone(timedelta(hours=8))
        now = datetime(2026, 8, 24, 12, 0, tzinfo=tz)
        upsert_run({"id": "t-lease", "status": "queued", "goal": "A6", "chat_id": "oc_x"})
        self.assertEqual(load_run("t-lease")["goal"], "A6")
        self.assertTrue(acquire_lease("t-lease", "pid:1", now=now, ttl_sec=60))
        self.assertFalse(acquire_lease("t-lease", "pid:2", now=now, ttl_sec=60))
        later = now + timedelta(seconds=90)
        recovered = recover_expired_runs(now=later)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["id"], "t-lease")
        self.assertEqual(recovered[0]["status"], "queued")
        self.assertTrue(acquire_lease("t-lease", "pid:2", now=later, ttl_sec=60))
        self.assertIn("t-lease", list_runs_text())

    def test_apply_recovered_leases_rewrites_payload(self) -> None:
        init_db()
        tz = timezone(timedelta(hours=8))
        now = datetime(2026, 8, 24, 13, 0, tzinfo=tz)
        store: dict[str, dict] = {}

        def load(tid: str):
            return store.get(tid)

        def save(task: dict) -> None:
            store[str(task["id"])] = task
            upsert_run(task)

        upsert_run({"id": "t-dead", "status": "running", "goal": "hang"})
        self.assertTrue(acquire_lease("t-dead", "pid:9", now=now, ttl_sec=30))
        store["t-dead"] = {"id": "t-dead", "status": "running", "goal": "hang"}
        n = apply_recovered_leases(load, save, now=now + timedelta(seconds=90))
        self.assertEqual(n, 1)
        self.assertEqual(store["t-dead"]["status"], "queued")
        self.assertTrue(store["t-dead"].get("lease_recovered"))


if __name__ == "__main__":
    unittest.main()
