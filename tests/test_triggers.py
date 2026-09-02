from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

from partner.core import triggers
from partner.core.triggers import (
    CN_TZ,
    add_trigger,
    delete_trigger,
    format_triggers_text,
    get_trigger,
    list_triggers,
    mark_trigger_run,
    parse_schedule,
    poll_due_triggers,
    toggle_trigger,
)
from partner.ops.triggers_cli import triggers_cli


class TriggerScheduleTests(unittest.TestCase):
    def test_daily(self) -> None:
        s = parse_schedule("daily@09:00")
        self.assertEqual(s["type"], "daily")
        self.assertEqual(s["hour"], 9)
        self.assertEqual(s["minute"], 0)
        base = datetime(2026, 8, 26, 8, 0, tzinfo=CN_TZ)
        self.assertEqual(
            triggers.next_run(s, base),
            datetime(2026, 8, 26, 9, 0, tzinfo=CN_TZ),
        )
        base2 = datetime(2026, 8, 26, 10, 0, tzinfo=CN_TZ)
        self.assertEqual(
            triggers.next_run(s, base2),
            datetime(2026, 8, 27, 9, 0, tzinfo=CN_TZ),
        )

    def test_weekly(self) -> None:
        s = parse_schedule("weekly@Mon09:00")
        self.assertEqual(s["type"], "weekly")
        self.assertEqual(s["weekday"], 0)
        # 2026-08-24 is Monday.
        base = datetime(2026, 8, 24, 8, 0, tzinfo=CN_TZ)
        self.assertEqual(
            triggers.next_run(s, base),
            datetime(2026, 8, 24, 9, 0, tzinfo=CN_TZ),
        )
        base2 = datetime(2026, 8, 24, 10, 0, tzinfo=CN_TZ)
        self.assertEqual(
            triggers.next_run(s, base2),
            datetime(2026, 8, 31, 9, 0, tzinfo=CN_TZ),
        )

    def test_once(self) -> None:
        dt = datetime(2099, 8, 28, 9, 0, tzinfo=CN_TZ)
        s = parse_schedule("once@2099-08-28T09:00")
        self.assertEqual(s["type"], "once")
        self.assertEqual(s["dt"], dt)
        self.assertEqual(triggers.next_run(s, dt - timedelta(hours=1)), dt)
        self.assertIsNone(triggers.next_run(s, dt + timedelta(hours=1)))

    def test_once_past_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_schedule("once@2020-01-01T09:00")

    def test_cron(self) -> None:
        s = parse_schedule("cron@*/15 9 * * 1-5")
        self.assertEqual(s["type"], "cron")
        # Friday 2026-08-28 09:00 -> next is 09:15 same day.
        base = datetime(2026, 8, 28, 9, 0, tzinfo=CN_TZ)
        self.assertEqual(
            triggers.next_run(s, base),
            datetime(2026, 8, 28, 9, 15, tzinfo=CN_TZ),
        )
        # Saturday 09:00 -> next Monday 09:00.
        base2 = datetime(2026, 8, 29, 9, 0, tzinfo=CN_TZ)
        self.assertEqual(
            triggers.next_run(s, base2),
            datetime(2026, 8, 31, 9, 0, tzinfo=CN_TZ),
        )

    def test_invalid_formats(self) -> None:
        for expr in ("", "hourly@09:00", "daily@25:00", "cron@* *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    parse_schedule(expr)


class TriggerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("FEISHU_PARTNER_RUNTIME_DB")
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = os.path.join(
            self.tmp.name, "runtime.db"
        )

    def tearDown(self) -> None:
        if self.old_db is None:
            os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        else:
            os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.old_db
        self.tmp.cleanup()

    def test_add_and_list(self) -> None:
        spec = add_trigger(goal="生成简报", schedule="daily@09:00", title="早报")
        self.assertIn("id", spec)
        self.assertEqual(spec["goal"], "生成简报")
        self.assertTrue(spec["enabled"])
        self.assertTrue(spec["next_run_at"])

        specs = list_triggers()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["goal"], "生成简报")

    def test_get_and_delete(self) -> None:
        spec = add_trigger(goal="g", schedule="daily@10:00")
        fetched = get_trigger(spec["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], spec["id"])
        self.assertTrue(delete_trigger(spec["id"]))
        self.assertIsNone(get_trigger(spec["id"]))
        self.assertFalse(delete_trigger(spec["id"]))

    def test_toggle(self) -> None:
        spec = add_trigger(goal="g", schedule="daily@10:00")
        updated = toggle_trigger(spec["id"], False)
        self.assertIsNotNone(updated)
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["next_run_at"], "")
        re_enabled = toggle_trigger(spec["id"], True)
        self.assertTrue(re_enabled["enabled"])
        self.assertTrue(re_enabled["next_run_at"])

    def test_poll_due_and_mark(self) -> None:
        future = datetime.now(CN_TZ) + timedelta(minutes=5)
        iso = future.strftime("%Y-%m-%dT%H:%M")
        spec = add_trigger(goal="g", schedule=f"once@{iso}")
        self.assertEqual(len(poll_due_triggers()), 0)

        after = future + timedelta(minutes=1)
        due = poll_due_triggers(now=after)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], spec["id"])

        updated = mark_trigger_run(spec["id"], now=after)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["run_count"], 1)
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["next_run_at"], "")

    def test_daily_mark_recomputes_next(self) -> None:
        spec = add_trigger(goal="g", schedule="daily@09:00")
        run_at = datetime(2026, 8, 26, 9, 5, tzinfo=CN_TZ)
        updated = mark_trigger_run(spec["id"], now=run_at)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["run_count"], 1)
        self.assertTrue(updated["enabled"])
        self.assertEqual(
            updated["next_run_at"],
            "2026-08-27T09:00:00+08:00",
        )

    def test_format_text(self) -> None:
        add_trigger(goal="生成简报", schedule="daily@09:00", title="早报")
        text = format_triggers_text(list_triggers())
        self.assertIn("触发器 1 条", text)
        self.assertIn("早报", text)
        self.assertIn("daily@09:00", text)


class TriggerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("FEISHU_PARTNER_RUNTIME_DB")
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = os.path.join(
            self.tmp.name, "runtime.db"
        )
        self.stdout = io.StringIO()
        self.old_stdout = sys.stdout
        sys.stdout = self.stdout

    def tearDown(self) -> None:
        sys.stdout = self.old_stdout
        if self.old_db is None:
            os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        else:
            os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.old_db
        self.tmp.cleanup()

    def test_cli_add_list_delete(self) -> None:
        code = triggers_cli(["add", "--goal", "g", "--schedule", "daily@09:00"])
        self.assertEqual(code, 0)
        out = self.stdout.getvalue()
        # Extract id from "已创建触发器 xxxxxx ..."
        trigger_id = out.split("已创建触发器 ")[1].split()[0]
        self.stdout.truncate(0)
        self.stdout.seek(0)

        self.assertEqual(triggers_cli(["list"]), 0)
        self.assertIn(trigger_id, self.stdout.getvalue())

        self.stdout.truncate(0)
        self.stdout.seek(0)
        self.assertEqual(triggers_cli(["del", trigger_id]), 0)
        self.assertIn("已删除", self.stdout.getvalue())

    def test_cli_default_lists(self) -> None:
        self.assertEqual(triggers_cli([]), 0)
        self.assertIn("暂无触发器", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
