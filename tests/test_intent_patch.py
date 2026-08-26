"""Tests for PR3 intent-patch shared runtime."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid

from partner.core import run_store
from partner.runtime.agent import service


class TestIntentPatch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = self.tmp.name
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = os.path.join(
            self.tmp.name, "runtime.db"
        )
        run_store.init_db()
        service.RUMTIME = "agent_v2"
        service._GRAPH = None
        self.chat_id = f"oc_{uuid.uuid4().hex[:12]}"

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for key in ("FEISHU_PARTNER_TASKS_DIR", "FEISHU_PARTNER_RUNTIME_DB"):
            os.environ.pop(key, None)

    def _make_task(self, goal: str = "测试目标") -> dict:
        from partner.runtime.agent.settings import agent_max_steps

        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id,
            "chat_id": self.chat_id,
            "goal": goal,
            "runtime": "agent_v2",
            "schema_version": 4,
            "status": "running",
            "background": False,
            "allow_writes": False,
            "observations": [],
            "thoughts": [],
            "intent_patches": [],
            "pending_write": None,
            "summary": "",
            "steps_taken": 0,
            "max_steps": agent_max_steps(),
            "created_at": "2026-08-26T12:00:00+08:00",
            "updated_at": "2026-08-26T12:00:00+08:00",
        }
        service.save_task(task)
        return task

    def test_patch_schema_saved(self) -> None:
        task = self._make_task()
        patch = {
            "author_open_id": "ou_a",
            "text": "再加一条风控复盘",
            "action": "append",
            "ts": "2026-08-26T12:01:00+08:00",
        }
        updated = service.append_intent_patch(task["id"], patch)
        assert updated is not None
        self.assertEqual(len(updated["intent_patches"]), 1)
        self.assertFalse(updated["intent_patches"][0]["merged"])
        self.assertEqual(updated["intent_patches"][0]["author_open_id"], "ou_a")
        loaded = service.load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["intent_patches"][0]["text"], "再加一条风控复盘")

    def test_merge_append_into_observations(self) -> None:
        task = self._make_task()
        service.append_intent_patch(
            task["id"],
            {"author_open_id": "ou_a", "text": "补充一条", "action": "append"},
        )
        service.append_intent_patch(
            task["id"],
            {"author_open_id": "ou_b", "text": "修正一下", "action": "override"},
        )
        changed = service._merge_intent_patches(service.load_task(task["id"]))
        self.assertTrue(changed)
        loaded = service.load_task(task["id"])
        assert loaded is not None
        obs = loaded["observations"]
        self.assertEqual(len(obs), 2)
        self.assertIn("[补充要求 · ou_a] 补充一条", obs)
        self.assertIn("[目标修正 · ou_b] 修正一下", obs)
        for p in loaded["intent_patches"]:
            self.assertTrue(p["merged"])

    def test_cancel_patch_terminates_task(self) -> None:
        task = self._make_task()
        service.append_intent_patch(
            task["id"],
            {
                "author_open_id": "ou_c",
                "sender_name": "张三",
                "text": "不用做了",
                "action": "cancel",
            },
        )
        changed = service._merge_intent_patches(service.load_task(task["id"]))
        self.assertTrue(changed)
        loaded = service.load_task(task["id"])
        assert loaded is not None
        self.assertEqual(loaded["status"], "cancelled")
        self.assertIn("张三 取消任务", loaded.get("summary", ""))

    def test_active_agent_task_finds_non_terminal(self) -> None:
        task = self._make_task()
        found = service.active_agent_task(self.chat_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], task["id"])
        found["status"] = "done"
        service.save_task(found)
        self.assertIsNone(service.active_agent_task(self.chat_id))

    def test_claim_confidence_resumes_only_blocked(self) -> None:
        task = self._make_task()
        result = service.claim_confirmation(task["id"], "ou_x")
        self.assertFalse(result["ok"])
        self.assertIn("不在确认闸", result["error"])

    def test_claim_confidence_with_fake_approval(self) -> None:
        task = self._make_task()
        task["status"] = "blocked"
        task["pending_write"] = {
            "tool": "task_create",
            "args": {"summary": "x"},
            "approval": {"message_id": "apv_123", "token": "tok"},
        }
        service.save_task(task)
        run_store.request_approval(
            task_id=task["id"],
            message_id="apv_123",
            chat_id=self.chat_id,
            tool="task_create",
            args={"summary": "x"},
            token="tok",
            timeout_sec=-1,
        )
        result = service.claim_confirmation(task["id"], "ou_x")
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "running")
        approval = run_store.get_approval("apv_123")
        assert approval is not None
        self.assertEqual(approval["status"], "approved")

    def test_patch_action_from_text_append(self) -> None:
        from partner.ops.serve import _patch_action_from_text as action_from

        self.assertEqual(action_from("再加一条"), "append")
        self.assertEqual(action_from("改成周三"), "override")
        self.assertEqual(action_from("不要做了"), "cancel")


if __name__ == "__main__":
    unittest.main()
