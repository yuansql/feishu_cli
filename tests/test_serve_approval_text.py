from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from partner.core.events import InboundMessage
from partner.core.run_store import (
    get_approval,
    init_db,
    request_approval,
    upsert_run,
)
from partner.ops import serve as serve_mod
from partner.runtime.agent.service import save_task


class ApprovalTextFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        self.tasks_dir = tempfile.mkdtemp()
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = self.db_path
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tasks_dir
        init_db()

    def tearDown(self) -> None:
        import shutil

        os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        os.environ.pop("FEISHU_PARTNER_TASKS_DIR", None)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.tasks_dir):
            shutil.rmtree(self.tasks_dir, ignore_errors=True)

    def _make_task(self, task_id: str, chat_id: str) -> dict:
        task = {
            "id": task_id,
            "chat_id": chat_id,
            "goal": "测试写入",
            "runtime": "agent_v2",
            "schema_version": 3,
            "status": "blocked",
            "background": False,
            "allow_writes": False,
            "observations": [],
            "thoughts": [],
            "pending_write": {"reason": "confirmation"},
            "summary": "",
            "steps_taken": 0,
            "max_steps": 8,
            "created_at": "2026-08-26T12:00:00+08:00",
            "updated_at": "2026-08-26T12:00:00+08:00",
        }
        save_task(task)
        return task

    @mock.patch.object(serve_mod, "send_card", return_value="已发送。")
    @mock.patch.object(serve_mod, "send_checked", return_value="已发送。")
    def test_text_decline_cancels_task(
        self, _send_checked: mock.Mock, _send_card: mock.Mock
    ) -> None:
        chat_id = "oc_testchat"
        task_id = "task_text_decline"
        upsert_run({"id": task_id, "status": "blocked", "goal": "g", "chat_id": chat_id})
        self._make_task(task_id, chat_id)
        request_approval(
            task_id=task_id,
            message_id="apv_text_decline",
            chat_id=chat_id,
            tool="docs_create",
            args={"title": "测试文档"},
            token="tok",
        )
        msg = InboundMessage(
            chat_id=chat_id,
            chat_type="p2p",
            text="取消",
            message_id="msg_reply_1",
            sender_type="user",
            sender_id="ou_user",
        )
        handled = serve_mod._maybe_handle_approval_text(msg)
        self.assertTrue(handled)
        self.assertEqual(get_approval("apv_text_decline")["status"], "declined")

        from partner.runtime.agent.service import load_task

        task = load_task(task_id)
        assert isinstance(task, dict)
        self.assertEqual(task["status"], "cancelled")

    @mock.patch.object(serve_mod, "send_card", return_value="已发送。")
    @mock.patch.object(serve_mod, "send_checked", return_value="已发送。")
    def test_text_approve_resumes_task(
        self, _send_checked: mock.Mock, _send_card: mock.Mock
    ) -> None:
        chat_id = "oc_testchat2"
        task_id = "task_text_approve"
        upsert_run({"id": task_id, "status": "blocked", "goal": "g", "chat_id": chat_id})
        self._make_task(task_id, chat_id)
        request_approval(
            task_id=task_id,
            message_id="apv_text_approve",
            chat_id=chat_id,
            tool="docs_create",
            args={"title": "测试文档"},
            token="tok",
        )
        msg = InboundMessage(
            chat_id=chat_id,
            chat_type="p2p",
            text="确认写入",
            message_id="msg_reply_2",
            sender_type="user",
            sender_id="ou_user",
        )
        with mock.patch.object(serve_mod, "_run_resumed_task", return_value="已确认执行"):
            handled = serve_mod._maybe_handle_approval_text(msg)
        self.assertTrue(handled)
        self.assertEqual(get_approval("apv_text_approve")["status"], "approved")

    def test_non_approval_text_not_consumed(self) -> None:
        msg = InboundMessage(
            chat_id="oc_testchat",
            chat_type="p2p",
            text="帮我查下今天的日程",
            message_id="msg_reply_3",
            sender_type="user",
            sender_id="ou_user",
        )
        self.assertFalse(serve_mod._maybe_handle_approval_text(msg))


if __name__ == "__main__":
    unittest.main()
