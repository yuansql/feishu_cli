from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.workflow import (
    list_workflows_text,
    load_workflows,
    match_workflow,
    run_workflow,
)


class WorkflowTests(unittest.TestCase):
    def test_load_bundled_workflows(self) -> None:
        rows = load_workflows()
        self.assertIn("morning_checkin", rows)
        self.assertGreaterEqual(len(rows["morning_checkin"].steps), 2)

    def test_match_trigger_and_prefix(self) -> None:
        self.assertEqual(match_workflow("晨间核对"), "morning_checkin")
        self.assertEqual(match_workflow("workflow:meeting_prep"), "meeting_prep")
        self.assertIsNone(match_workflow("今天"))

    def test_run_workflow_executes_read_tools(self) -> None:
        with patch("partner.workflow.execute_tool", return_value="ok-body") as mocked:
            body = run_workflow("approval_sweep", goal="审批清扫", chat_id="oc_wf")
        self.assertIn("Workflow 完成", body)
        self.assertGreaterEqual(mocked.call_count, 2)

    def test_user_override_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflows.json"
            os.environ["FEISHU_PARTNER_WORKFLOWS"] = str(path)
            path.write_text(
                """
                {"workflows":[{"id":"custom","name":"自定义","triggers":["自定义流"],
                "steps":[{"title":"t","tool":"tasks","args":{}}],"finalize":"summarize"}]}
                """,
                encoding="utf-8",
            )
            self.assertIn("custom", load_workflows())
            self.assertIn("custom", list_workflows_text())
        os.environ.pop("FEISHU_PARTNER_WORKFLOWS", None)


if __name__ == "__main__":
    unittest.main()
