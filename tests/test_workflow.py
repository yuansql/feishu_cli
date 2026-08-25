from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from partner.runtime.workflow import (
    _http_step,
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
        with patch("partner.runtime.workflow.execute_tool", return_value="ok-body") as mocked:
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

    def test_when_skips_inbox_without_keyword(self) -> None:
        with patch("partner.runtime.workflow.execute_tool", return_value="ok") as mocked:
            run_workflow("mention_followup", goal="开工核对日程")
        tools = [call.args[0] for call in mocked.call_args_list]
        self.assertNotIn("inbox", tools)
        self.assertIn("today", tools)
        self.assertIn("tasks", tools)

    def test_repeat_until_stops_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflows.json"
            os.environ["FEISHU_PARTNER_WORKFLOWS"] = str(path)
            path.write_text(
                json.dumps(
                    {
                        "workflows": [
                            {
                                "id": "retry_tasks",
                                "name": "重试待办",
                                "triggers": ["重试待办"],
                                "steps": [
                                    {
                                        "title": "读待办",
                                        "tool": "tasks",
                                        "repeat": 5,
                                        "until": "没有未完成",
                                        "args": {},
                                    }
                                ],
                                "finalize": "summarize",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "partner.runtime.workflow.execute_tool",
                side_effect=["还有 2 条", "没有未完成待办"],
            ) as mocked:
                body = run_workflow("retry_tasks", goal="重试待办")
        os.environ.pop("FEISHU_PARTNER_WORKFLOWS", None)
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("没有未完成", body)

    def test_http_step_fetches(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"pong-ok")

            def log_message(self, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "workflows.json"
                os.environ["FEISHU_PARTNER_WORKFLOWS"] = str(path)
                path.write_text(
                    json.dumps(
                        {
                            "workflows": [
                                {
                                    "id": "http_probe",
                                    "name": "探测",
                                    "triggers": ["探测"],
                                    "steps": [
                                        {
                                            "title": "健康检查",
                                            "tool": "http",
                                            "args": {"url": url, "method": "GET"},
                                        }
                                    ],
                                    "finalize": "summarize",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                body = run_workflow("http_probe", goal="探测")
            os.environ.pop("FEISHU_PARTNER_WORKFLOWS", None)
            self.assertIn("pong-ok", body)
            self.assertIn("HTTP 200", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_rejects_non_http(self) -> None:
        with self.assertRaises(ValueError):
            _http_step({"url": "file:///etc/passwd"})


if __name__ == "__main__":
    unittest.main()
