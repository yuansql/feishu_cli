from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from partner.ops.eval import (
    eval_text,
    harvest_cases_from_traces,
    load_cases,
    run_fixture_eval,
    score_case,
)
from partner.ops.versions import diff_versions, list_versions, publish, rollback, versions_text


class EvalCliTests(unittest.TestCase):
    def test_bundled_fixtures_all_pass(self) -> None:
        result = run_fixture_eval()
        self.assertGreaterEqual(result["total"], 8)
        self.assertEqual(result["passed"], result["total"], result["results"])

    def test_eval_text_mentions_local(self) -> None:
        text = eval_text()
        self.assertIn("本地评测", text)
        self.assertIn("路由 fixtures", text)


class VersionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["FEISHU_PARTNER_VERSIONS"] = str(root / "versions")
        os.environ["FEISHU_PARTNER_WORKFLOWS"] = str(root / "workflows.json")
        os.environ["FEISHU_PARTNER_KNOWLEDGE"] = str(root / "knowledge.json")
        os.environ["FEISHU_PARTNER_TERMINOLOGY"] = str(root / "terminology.json")
        (root / "workflows.json").write_text(
            json.dumps({"workflows": [{"id": "v1", "name": "一", "triggers": ["一"], "steps": [{"title": "t", "tool": "tasks", "args": {}}]}]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        for key in (
            "FEISHU_PARTNER_VERSIONS",
            "FEISHU_PARTNER_WORKFLOWS",
            "FEISHU_PARTNER_KNOWLEDGE",
            "FEISHU_PARTNER_TERMINOLOGY",
        ):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_publish_diff_rollback(self) -> None:
        first = publish(note="one")
        Path(os.environ["FEISHU_PARTNER_WORKFLOWS"]).write_text(
            json.dumps({"workflows": [{"id": "v2", "name": "二", "triggers": ["二"], "steps": [{"title": "t", "tool": "tasks", "args": {}}]}]}),
            encoding="utf-8",
        )
        second = publish(note="two")
        rows = list_versions()
        self.assertGreaterEqual(len(rows), 2)
        diff = diff_versions(str(first["id"]), str(second["id"]))
        self.assertIn("v2", diff)
        msg = rollback(str(first["id"]))
        self.assertIn("已回滚", msg)
        blob = json.loads(Path(os.environ["FEISHU_PARTNER_WORKFLOWS"]).read_text(encoding="utf-8"))
        self.assertEqual(blob["workflows"][0]["id"], "v1")
        self.assertIn(str(first["id"]), versions_text(["list"]))


class EvalHarvestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["FEISHU_PARTNER_TRACES_DIR"] = str(root / "traces")
        os.environ["FEISHU_PARTNER_EVAL_USER_CASES"] = str(root / "eval-cases.json")
        os.environ["FEISHU_PARTNER_EVAL_SCORES"] = str(root / "eval-scores.json")
        traces = root / "traces"
        traces.mkdir()
        events = [
            {"event": "workflow.started", "goal": "帮我整理本周的会议纪要"},
            {"event": "workflow.step", "step": "s1"},
            {"event": "workflow.started", "goal": "帮我整理本周的会议纪要"},  # 同文件重复
            {"event": "workflow.started", "goal": "短"},  # 太短不收
        ]
        (traces / "task-1.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        for key in (
            "FEISHU_PARTNER_TRACES_DIR",
            "FEISHU_PARTNER_EVAL_USER_CASES",
            "FEISHU_PARTNER_EVAL_SCORES",
        ):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_harvest_writes_user_cases(self) -> None:
        summary = harvest_cases_from_traces()
        self.assertEqual(summary["added"], 1)
        self.assertGreaterEqual(summary["skipped_dup"], 0)
        blob = json.loads(
            Path(os.environ["FEISHU_PARTNER_EVAL_USER_CASES"]).read_text(encoding="utf-8")
        )
        self.assertEqual(len(blob["cases"]), 1)
        case = blob["cases"][0]
        self.assertEqual(case["text"], "帮我整理本周的会议纪要")
        self.assertEqual(case["expect_action"], "")
        self.assertTrue(case["observed_action"])
        self.assertEqual(case["source"], "trace")

    def test_harvest_dedupes_against_existing(self) -> None:
        harvest_cases_from_traces()
        summary = harvest_cases_from_traces()
        self.assertEqual(summary["added"], 0)
        self.assertGreaterEqual(summary["skipped_dup"], 1)

    def test_unreviewed_case_not_counted_in_pass_rate(self) -> None:
        harvest_cases_from_traces()
        result = run_fixture_eval()
        # 内置 fixtures 全部通过；traces 转来的用例进 pending 不拉低通过率
        self.assertEqual(result["passed"], result["total"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(len(load_cases()), result["total"] + 1)

    def test_score_case_and_report(self) -> None:
        harvest_cases_from_traces()
        case_id = load_cases()[-1]["id"]
        msg = score_case(case_id, "bad", "路由错了")
        self.assertIn("bad", msg)
        text = eval_text([])
        self.assertIn("人工评分", text)
        self.assertIn("bad 1", text)
        self.assertIn("路由错了", text)

    def test_score_unknown_case_rejected(self) -> None:
        msg = score_case("no-such-id", "good")
        self.assertIn("找不到用例", msg)

    def test_score_invalid_mark_rejected(self) -> None:
        harvest_cases_from_traces()
        case_id = load_cases()[-1]["id"]
        msg = score_case(case_id, "excellent")
        self.assertIn("good / bad", msg)

    def test_eval_text_harvest_subcommand(self) -> None:
        text = eval_text(["harvest"])
        self.assertIn("新增用例 1", text)
        self.assertIn("人工确认", text)


class VersionsPromptTests(unittest.TestCase):
    """#75：Agents.md / experience.jsonl 纳入快照与回滚。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["FEISHU_PARTNER_VERSIONS"] = str(root / "versions")
        os.environ["FEISHU_PARTNER_WORKFLOWS"] = str(root / "workflows.json")
        os.environ["FEISHU_PARTNER_AGENTS_MD"] = str(root / "Agents.md")
        os.environ["FEISHU_PARTNER_EXPERIENCE"] = str(root / "experience.jsonl")
        (root / "Agents.md").write_text("# 提示词 v1\n- 写操作必须先确认\n", encoding="utf-8")
        (root / "experience.jsonl").write_text(
            json.dumps({"text": "经验一", "tags": ["a"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        for key in (
            "FEISHU_PARTNER_VERSIONS",
            "FEISHU_PARTNER_WORKFLOWS",
            "FEISHU_PARTNER_AGENTS_MD",
            "FEISHU_PARTNER_EXPERIENCE",
        ):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_publish_snapshots_prompt_files(self) -> None:
        man = publish(note="with-prompts")
        self.assertTrue(man["files"]["Agents.md"])
        self.assertTrue(man["files"]["experience.jsonl"])
        snap = Path(os.environ["FEISHU_PARTNER_VERSIONS"]) / man["id"]
        self.assertIn("提示词 v1", (snap / "Agents.md").read_text(encoding="utf-8"))

    def test_diff_and_rollback_prompt_files(self) -> None:
        first = publish(note="v1")
        agents = Path(os.environ["FEISHU_PARTNER_AGENTS_MD"])
        agents.write_text("# 提示词 v2\n- 改过的规则\n", encoding="utf-8")
        second = publish(note="v2")
        diff = diff_versions(str(first["id"]), str(second["id"]))
        self.assertIn("提示词 v2", diff)
        msg = rollback(str(first["id"]))
        self.assertIn("已回滚", msg)
        self.assertIn("提示词 v1", agents.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
