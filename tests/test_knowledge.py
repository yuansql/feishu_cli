"""Seam: knowledge sources + write-back confirm for task mode."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.actions import create_task_item
from partner.office.followup import add_goal_item, load_items
from partner.routing.intents import parse_intent
from partner.office.knowledge import load_sources, search_prioritized
from partner.runtime.planner import plan_steps
from partner.runtime.runner import confirm_writes, create_task, load_task, run_all


class KnowledgeTests(unittest.TestCase):
    def test_default_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FEISHU_PARTNER_KNOWLEDGE"] = f"{tmp}/missing.json"
            self.addCleanup(os.environ.pop, "FEISHU_PARTNER_KNOWLEDGE", None)
            names = [str(s.get("name")) for s in load_sources()]
            self.assertIn("云文档", names)

    def test_search_prioritized_tags_source(self) -> None:
        with patch(
            "partner.actions.docs_search_text",
            return_value="【文档】\n- 周报",
        ) as search:
            out = search_prioritized("周报")
        search.assert_called_once_with("周报")
        self.assertIn("知识源", out)
        self.assertIn("周报", out)


class WriteBackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tmp.name
        os.environ["FEISHU_PARTNER_FOLLOWUPS"] = str(
            Path(self.tmp.name) / "followups.json"
        )

    def test_plan_steps_include_write_confirm(self) -> None:
        steps = plan_steps("创建待办并写入跟进账：A6 上线")
        tools = [s["tool"] for s in steps]
        self.assertIn("followup_add", tools)
        self.assertIn("task_create", tools)
        self.assertTrue(any(s.get("requires_confirm") for s in steps))

    def test_plan_steps_weekly_includes_docs_create(self) -> None:
        tools = [s["tool"] for s in plan_steps("整理本周周报")]
        self.assertIn("docs_create", tools)
        doc_step = next(s for s in plan_steps("整理本周周报") if s["tool"] == "docs_create")
        self.assertTrue(doc_step.get("requires_confirm"))

    def test_run_all_stops_at_write_confirm(self) -> None:
        goal = "创建待办并写入跟进账：A6"
        task = create_task(goal, "oc_x", plan_steps(goal))
        with patch("partner.runtime.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
            msg = run_all(task["id"])
        loaded = load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["status"], "blocked")
        self.assertIn("确认写入", msg)

    def test_confirm_writes_followup_and_task(self) -> None:
        goal = "创建待办并写入跟进账：A6 上线"
        task = create_task(goal, "oc_x", plan_steps(goal))
        with patch("partner.runtime.runner.run_tool", side_effect=lambda tool, _a: f"ok:{tool}"):
            run_all(task["id"])
        with patch(
            "partner.actions.create_task_item",
            return_value="已创建飞书待办：A6 上线",
        ):
            msg = confirm_writes("oc_x")
        self.assertIn("写操作已确认", msg)
        items = load_items()
        self.assertTrue(any("A6 上线" in str(i.get("text") or "") for i in items))

    def test_task_confirm_intent(self) -> None:
        self.assertEqual(parse_intent("确认写入").action, "task_confirm")


class CreateTaskItemTests(unittest.TestCase):
    def test_create_task_item_success(self) -> None:
        with patch("partner.office.tasks_io.run_lark", return_value={"ok": True, "data": {}}):
            out = create_task_item("A6 上线前检查")
        self.assertIn("已创建飞书待办", out)

    def test_add_goal_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            item = add_goal_item("预发打包", chat_id="oc_x", path=path)
            self.assertEqual(item["kind"], "task_runner")
            saved = load_items(path)
            self.assertEqual(len(saved), 1)


if __name__ == "__main__":
    unittest.main()
