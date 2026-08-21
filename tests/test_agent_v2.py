"""Agent Runtime v2: think→act with confirm gate."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.runtime.agent.brain import Decision, heuristic_decide
from partner.runtime.agent.graph import run_loop
from partner.runtime.agent.service import confirm_agent_writes, start_agent_task
from partner.runtime.runner import start_task


class AgentBrainTests(unittest.TestCase):
    def test_heuristic_reads_then_finishes(self) -> None:
        d1 = heuristic_decide("A6 上线前检查", [], allow_writes=False)
        self.assertEqual(d1.kind, "tool")
        self.assertEqual(d1.tool, "today")
        d2 = heuristic_decide(
            "A6 上线前检查",
            ["[tool:today] 日程空", "[tool:tasks] 待办空"],
            allow_writes=False,
        )
        self.assertEqual(d2.kind, "finish")

    def test_heuristic_confirm_before_write(self) -> None:
        d = heuristic_decide(
            "创建待办：跟进 eSIM",
            ["[tool:today] ok", "[tool:tasks] ok"],
            allow_writes=False,
        )
        self.assertEqual(d.kind, "confirm")


class AgentLoopTests(unittest.TestCase):
    def test_run_loop_with_mocked_tools(self) -> None:
        decisions = iter(
            [
                Decision("读今天", "tool", "today", {}),
                Decision("读待办", "tool", "tasks", {}),
                Decision("够了", "finish", summary="检查完成：日程与待办已看过。"),
            ]
        )

        def fake_decide(goal, observations, *, allow_writes=False):
            return next(decisions)

        with (
            patch("partner.runtime.agent.graph.decide", side_effect=fake_decide),
            patch(
                "partner.runtime.agent.graph.execute_tool",
                side_effect=lambda tool, args, confirmed=False: f"{tool}-ok",
            ),
        ):
            final = run_loop(goal="A6 检查", chat_id="oc_t", max_steps=8)
        self.assertEqual(final.get("status"), "done")
        self.assertIn("检查完成", str(final.get("summary") or ""))
        obs = list(final.get("observations") or [])
        self.assertTrue(any("today" in o for o in obs))
        self.assertTrue(any("tasks" in o for o in obs))

    def test_write_blocks_for_confirm(self) -> None:
        decisions = iter(
            [
                Decision("要写", "confirm"),
            ]
        )

        def fake_decide(goal, observations, *, allow_writes=False):
            return next(decisions)

        with patch("partner.runtime.agent.graph.decide", side_effect=fake_decide):
            final = run_loop(goal="创建待办：X", chat_id="oc_t")
        self.assertEqual(final.get("status"), "blocked")


class AgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tasks = Path(self._tmpdir.name)
        self.env = patch.dict(
            os.environ,
            {
                "FEISHU_PARTNER_TASKS_DIR": str(self.tasks),
                "FEISHU_PARTNER_NO_LLM": "1",
                "FEISHU_PARTNER_AGENT_BRAIN": "heuristic",
                "FEISHU_PARTNER_DISABLE_NOTIFICATIONS": "1",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_start_and_confirm_write_path(self) -> None:
        with patch(
            "partner.runtime.agent.graph.execute_tool",
            side_effect=lambda tool, args, confirmed=False: (
                "created" if tool == "task_create" else f"{tool}-ok"
            ),
        ):
            msg = start_agent_task("创建待办：跟进 eSIM 邮寄", "oc_agent")
            self.assertIn("确认", msg)
            msg2 = confirm_agent_writes("oc_agent")
            self.assertTrue("created" in msg2 or "task_create" in msg2 or "done" in msg2)

    def test_start_task_uses_agent_v2_by_default(self) -> None:
        with patch(
            "partner.runtime.agent.graph.execute_tool",
            side_effect=lambda tool, args, confirmed=False: f"{tool}-ok",
        ):
            msg = start_task("A6 上线前检查", "oc_plan")
        self.assertIn("agent_v2", msg)
        self.assertNotIn("正在后台读取真实上下文", msg)


if __name__ == "__main__":
    unittest.main()
