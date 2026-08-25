"""Seam: TaskRunner — plan → persist → execute → resume."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from partner.routing.intents import parse_intent
from partner.runtime.planner import plan_steps
from partner.runtime.runner import (
    active_task_for_chat,
    cancel_task,
    continue_task,
    create_task,
    format_status,
    load_task,
    run_all,
    run_next,
    save_task,
    start_task,
    tasks_dir,
    worker_once,
)
from partner.core.trace import read_traces


class PlanStepsTests(unittest.TestCase):
    def test_plan_steps_includes_context_and_summarize(self) -> None:
        steps = plan_steps("A6 上线前检查")
        tools = [s["tool"] for s in steps]
        self.assertIn("today", tools)
        self.assertIn("tasks", tools)
        self.assertIn("summarize", tools)
        self.assertNotIn("followup_add", tools)
        self.assertNotIn("task_create", tools)
        self.assertTrue(any(s["tool"] == "search" for s in steps))

    def test_meeting_goal_adds_minutes(self) -> None:
        tools = [s["tool"] for s in plan_steps("整理会议纪要和待办")]
        self.assertIn("minutes", tools)

    def test_explicit_write_goal_adds_confirmed_write_tools(self) -> None:
        tools = [
            step["tool"]
            for step in plan_steps("创建待办并写入跟进账：A6 上线")
        ]
        self.assertIn("followup_add", tools)
        self.assertIn("task_create", tools)

    def test_no_write_goal_never_adds_writes(self) -> None:
        tools = [
            step["tool"]
            for step in plan_steps("汇总今天待办，不写入任何内容")
        ]
        self.assertFalse({"followup_add", "task_create", "docs_create"} & set(tools))


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
        from partner.core.run_store import load_run

        indexed = load_run(task["id"])
        self.assertIsNotNone(indexed)
        assert indexed is not None
        self.assertEqual(indexed["goal"], "A6 上线")

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
        with patch("partner.runtime.runner.run_tool", return_value="【今日日程】\n- 提测"):
            msg = run_next(task["id"])
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["steps"][0]["status"], "done")
        self.assertIn("提测", loaded["steps"][0]["result"])
        self.assertIn("1/", msg)

    def test_report_write_injects_collected_materials(self) -> None:
        steps = [
            {"title": "today", "tool": "today", "args": {}},
            {"title": "report", "tool": "report_write", "args": {"goal": "周报"}},
        ]
        task = create_task("生成本地报告", "oc_p2p", steps)
        task["steps"][0]["status"] = "done"
        task["steps"][0]["result"] = "日程：A6 提测"
        save_task(task)
        with patch("partner.runtime.runner.run_write_tool", return_value="已生成本地 HTML") as write:
            run_next(task["id"])
        write.assert_called_once()
        self.assertEqual(write.call_args[0][0], "report_write")
        self.assertIn("A6", write.call_args[0][1]["materials"])

    def test_run_all_completes_read_steps_and_summarize(self) -> None:
        steps = [
            {"title": "today", "tool": "today", "args": {}},
            {"title": "汇总", "tool": "summarize", "args": {}},
        ]
        task = create_task("A6", "oc_p2p", steps)
        with patch(
            "partner.runtime.runner.run_tool",
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
        with patch("partner.runtime.runner.run_tool", return_value="ctx"):
            run_next(task["id"])
        with patch(
            "partner.runtime.runner.run_tool",
            side_effect=lambda tool, _args: f"ok:{tool}",
        ):
            msg = continue_task("oc_p2p")
        self.assertIn("任务", msg)
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertIn(loaded["status"], {"done", "running"})


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = os.path.join(self.tmp.name, "tasks")
        os.environ["FEISHU_PARTNER_TRACES_DIR"] = os.path.join(self.tmp.name, "traces")
        os.environ["FEISHU_PARTNER_DISABLE_NOTIFICATIONS"] = "1"
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_DISABLE_NOTIFICATIONS", None)
        os.environ.pop("FEISHU_PARTNER_NO_LLM", None)

    def test_background_task_observes_then_plans(self) -> None:
        task = create_task(
            "A6 上线",
            "oc_agent",
            [{"title": "观察待办", "tool": "tasks", "args": {}}],
            mode="agent",
            background=True,
        )
        planned = [{"title": "汇总验收", "tool": "summarize", "args": {}}]
        with (
            patch("partner.runtime.runner.run_tool", return_value="A6 已提测"),
            patch("partner.runtime.runner.agent_plan_steps", return_value=planned),
        ):
            self.assertTrue(worker_once(notify=False))
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["status"], "done")
        self.assertEqual(loaded["phase"], "execute")
        self.assertEqual(loaded["plan_version"], 1)
        self.assertIn("multi_agent", loaded)
        self.assertIn("researcher", loaded["multi_agent"])
        self.assertIn("A6 已提测", loaded["steps"][-1]["result"])
        events = [row["event"] for row in read_traces(task["id"])]
        self.assertIn("multi_agent.coordinated", events)
        self.assertIn("plan.created", events)
        self.assertIn("task.completed", events)

    def test_failed_step_replans_from_failure_evidence(self) -> None:
        task = create_task(
            "整理 A6",
            "oc_agent",
            [{"title": "查资料", "tool": "search", "args": {"query": "A6"}}],
            mode="agent",
        )
        task["phase"] = "execute"
        save_task(task)
        planned = [{"title": "汇总已知事实", "tool": "summarize", "args": {}}]
        with (
            patch("partner.runtime.runner.run_tool", return_value="unexpected upstream failure"),
            patch("partner.runtime.runner.agent_plan_steps", return_value=planned),
        ):
            run_all(task["id"])
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["status"], "done")
        self.assertEqual(loaded["replan_count"], 1)
        self.assertEqual(loaded["plan_version"], 1)
        self.assertEqual(loaded["steps"][0]["status"], "superseded")

    def test_cancelled_background_task_is_not_claimed(self) -> None:
        task = create_task(
            "不再执行",
            "oc_cancel",
            [{"title": "读待办", "tool": "tasks", "args": {}}],
            mode="agent",
            background=True,
        )
        text = cancel_task("oc_cancel")
        self.assertIn("已取消", text)
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["status"], "cancelled")
        self.assertFalse(worker_once(notify=False))


class TaskIntentTests(unittest.TestCase):
    def test_task_status_intent(self) -> None:
        self.assertEqual(parse_intent("任务进度").action, "task_status")
        self.assertEqual(parse_intent("进行到哪了").action, "task_status")

    def test_task_continue_intent(self) -> None:
        self.assertEqual(parse_intent("下一步").action, "task_continue")
        self.assertEqual(parse_intent("继续执行").action, "task_continue")

    def test_task_mode_and_cancel_intents(self) -> None:
        intent = parse_intent("任务模式 调研 A6 风险")
        self.assertEqual(intent.action, "plan")
        self.assertEqual(intent.query, "调研 A6 风险")
        self.assertEqual(parse_intent("取消任务").action, "task_cancel")


class DispatchTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tmp.name
        os.environ["FEISHU_PARTNER_LEGACY_RUNNER"] = "1"

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_LEGACY_RUNNER", None)

    def test_start_task_from_plan(self) -> None:
        with patch("partner.runtime.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
            msg = start_task("A6 上线前检查", "oc_p2p")
        self.assertIn("任务已创建", msg)
        rows = list(tasks_dir().glob("*.json"))
        self.assertEqual(len(rows), 1)

    def test_dispatch_plan_uses_runner(self) -> None:
        from partner.actions import dispatch

        with patch("partner.actions.hermes_available", return_value=False):
            with patch(
                "partner.runtime.runner.run_tool",
                side_effect=lambda tool, _a: f"ok:{tool}",
            ):
                out = dispatch(
                    parse_intent("规划 A6 上线"),
                    user_text="规划 A6 上线",
                    channel="p2p",
                    chat_id="oc_plan",
                )
        self.assertIn("任务已创建", out)
        self.assertTrue(list(tasks_dir().glob("*.json")))

    def test_dispatch_plan_prefers_hermes(self) -> None:
        from partner.actions import dispatch

        with patch("partner.actions.hermes_available", return_value=True):
            with patch(
                "partner.actions.hermes_partner_turn",
                return_value="先核对 A6 待办，再拆确认闸。",
            ) as hermes:
                with patch("partner.actions.start_task") as start:
                    with patch("partner.actions.today_text", return_value="今天：A6"):
                        out = dispatch(
                            parse_intent("规划 A6 上线"),
                            user_text="规划 A6 上线",
                            channel="p2p",
                            chat_id="oc_hermes_plan",
                        )
        hermes.assert_called_once()
        start.assert_not_called()
        self.assertIn("核对 A6", out)

    def test_plain_continue_with_active_task(self) -> None:
        from partner.actions import dispatch

        task = create_task("A6", "oc_cont", plan_steps("A6"))
        with patch("partner.runtime.runner.run_tool", return_value="ctx"):
            run_next(task["id"])
        self.assertIsNotNone(active_task_for_chat("oc_cont"))
        with patch("partner.runtime.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
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
