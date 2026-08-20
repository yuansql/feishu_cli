from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from partner.eval import eval_text, run_fixture_eval
from partner.versions import diff_versions, list_versions, publish, rollback, versions_text


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


if __name__ == "__main__":
    unittest.main()
