"""Seam: TaskRunner — plan → persist → execute → resume."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from partner.intents import parse_intent
from partner.planner import plan_steps
from partner.runner import (
    active_task_for_chat,
    continue_task,
    create_task,
    format_status,
    load_task,
    run_all,
    run_next,
    start_task,
    tasks_dir,
)


class PlanStepsTests(unittest.TestCase):
    def test_plan_steps_includes_context_and_summarize(self) -> None:
        steps = plan_steps("A6 上线前检查")
        tools = [s["tool"] for s in steps]
        self.assertIn("today", tools)
        self.assertIn("tasks", tools)
        self.assertIn("summarize", tools)
        self.assertIn("followup_add", tools)
        self.assertIn("task_create", tools)
        self.assertTrue(any(s["tool"] == "search" for s in steps))

    def test_meeting_goal_adds_minutes(self) -> None:
        tools = [s["tool"] for s in plan_steps("整理会议纪要和待办")]
        self.assertIn("minutes", tools)


class TaskPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tmp.name

    def test_create_and_load_roundtrip(self) -> None:
        steps = plan_steps("A6 上线")
        task = create_task("A6 上线", "oc_p2p", steps)
        loaded = load_task(task["id"])
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["goal"], "A6 上线")
        self.assertEqual(loaded["chat_id"], "oc_p2p")
        self.assertEqual(len(loaded["steps"]), len(steps))
        self.assertEqual(loaded["steps"][0]["status"], "pending")

    def test_active_task_for_chat(self) -> None:
        create_task("one", "oc_a", plan_steps("one"))
        active = active_task_for_chat("oc_a")
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["goal"], "one")
        self.assertIsNone(active_task_for_chat("oc_other"))


class TaskExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tmp.name

    def test_run_next_executes_first_step(self) -> None:
        task = create_task("A6", "oc_p2p", plan_steps("A6"))
        with patch("partner.runner.run_tool", return_value="【今日日程】\n- 提测"):
            msg = run_next(task["id"])
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["steps"][0]["status"], "done")
        self.assertIn("提测", loaded["steps"][0]["result"])
        self.assertIn("1/", msg)

    def test_run_all_completes_read_steps_and_summarize(self) -> None:
        steps = [
            {"title": "today", "tool": "today", "args": {}},
            {"title": "汇总", "tool": "summarize", "args": {}},
        ]
        task = create_task("A6", "oc_p2p", steps)
        with patch(
            "partner.runner.run_tool",
            side_effect=lambda tool, _args: f"ok:{tool}",
        ):
            msg = run_all(task["id"])
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["status"], "done")
        self.assertTrue(all(s["status"] == "done" for s in loaded["steps"]))
        self.assertIn("任务完成", msg)

    def test_continue_task_runs_remaining(self) -> None:
        task = create_task("A6", "oc_p2p", plan_steps("A6"))
        with patch("partner.runner.run_tool", return_value="ctx"):
            run_next(task["id"])
        with patch(
            "partner.runner.run_tool",
            side_effect=lambda tool, _args: f"ok:{tool}",
        ):
            msg = continue_task("oc_p2p")
        self.assertIn("任务", msg)
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertIn(loaded["status"], {"done", "running"})


class TaskIntentTests(unittest.TestCase):
    def test_task_status_intent(self) -> None:
        self.assertEqual(parse_intent("任务进度").action, "task_status")
        self.assertEqual(parse_intent("进行到哪了").action, "task_status")

    def test_task_continue_intent(self) -> None:
        self.assertEqual(parse_intent("下一步").action, "task_continue")
        self.assertEqual(parse_intent("继续执行").action, "task_continue")


class DispatchTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tmp.name

    def test_start_task_from_plan(self) -> None:
        with patch("partner.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
            msg = start_task("A6 上线前检查", "oc_p2p")
        self.assertIn("任务已创建", msg)
        rows = list(tasks_dir().glob("*.json"))
        self.assertEqual(len(rows), 1)

    def test_dispatch_plan_uses_runner(self) -> None:
        from partner.actions import dispatch

        with patch("partner.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
            out = dispatch(
                parse_intent("规划 A6 上线"),
                user_text="规划 A6 上线",
                channel="p2p",
                chat_id="oc_plan",
            )
        self.assertIn("任务已创建", out)
        self.assertTrue(list(tasks_dir().glob("*.json")))

    def test_plain_continue_with_active_task(self) -> None:
        from partner.actions import dispatch

        task = create_task("A6", "oc_cont", plan_steps("A6"))
        with patch("partner.runner.run_tool", return_value="ctx"):
            run_next(task["id"])
        self.assertIsNotNone(active_task_for_chat("oc_cont"))
        with patch("partner.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
            out = dispatch(
                parse_intent("继续"),
                user_text="继续",
                channel="p2p",
                chat_id="oc_cont",
            )
        self.assertNotIn("上一句", out)
        self.assertIn("任务", out)


class FormatStatusTests(unittest.TestCase):
    def test_format_status_shows_steps(self) -> None:
        task = {
            "id": "t1",
            "goal": "A6",
            "status": "running",
            "steps": [
                {"id": 1, "title": "读今天", "tool": "today", "status": "done", "result": "x"},
                {"id": 2, "title": "读待办", "tool": "tasks", "status": "pending"},
            ],
        }
        text = format_status(task)
        self.assertIn("A6", text)
        self.assertIn("读今天", text)
        self.assertIn("✓", text)


if __name__ == "__main__":
    unittest.main()
