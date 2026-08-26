from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from partner.core.events import CardAction, extract_card_action
from partner.core.run_store import (
    approve_approval,
    decline_approval,
    expire_stale_approvals,
    get_approval,
    get_approval_by_task,
    init_db,
    request_approval,
    upsert_run,
)
from partner.office.approval_card import (
    approval_card_payload,
    approval_expired_card,
    approval_result_card,
)


class ApprovalCardPayloadTests(unittest.TestCase):
    def test_card_has_approve_and_decline_buttons(self) -> None:
        card = approval_card_payload(
            tool="task_create",
            args={"summary": "跟进 A 项目"},
            task_id="t1",
            message_id="apv_1",
            token="tok",
        )
        self.assertEqual(card["header"]["template"], "orange")
        actions = card["elements"][-1]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertIn("确认写入", actions[0]["text"]["content"])
        self.assertIn("取消", actions[1]["text"]["content"])
        approve_value = json.loads(actions[0]["value"])
        self.assertEqual(approve_value["act"], "approve")
        self.assertEqual(approve_value["task_id"], "t1")
        self.assertEqual(approve_value["message_id"], "apv_1")
        self.assertEqual(approve_value["token"], "tok")

    def test_card_shows_args(self) -> None:
        card = approval_card_payload(
            tool="docs_create",
            args={"query": "周报"},
            task_id="t2",
            message_id="apv_2",
            token="tok2",
        )
        text_blocks = [
            el["text"]["content"]
            for el in card["elements"]
            if el.get("text") and "content" in el["text"]
        ]
        self.assertTrue(
            any("周报" in block for block in text_blocks),
            f"expected args visible, got {text_blocks}",
        )

    def test_result_card(self) -> None:
        card = approval_result_card(approved=True, tool="task_create", detail="done")
        self.assertEqual(card["header"]["template"], "green")
        card2 = approval_result_card(approved=False, tool="task_create")
        self.assertEqual(card2["header"]["template"], "grey")

    def test_expired_card(self) -> None:
        card = approval_expired_card(tool="docs_create", goal="写周报")
        self.assertEqual(card["header"]["template"], "grey")
        self.assertIn("确认已超时", card["header"]["title"]["content"])


class ApprovalStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = os.path.join(
            self.tmp.name, "runtime.db"
        )
        init_db()
        upsert_run({"id": "task-a", "status": "blocked", "goal": "g", "chat_id": "oc_x"})

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_RUNTIME_DB", None)
        self.tmp.cleanup()

    def test_request_and_approve(self) -> None:
        self.assertTrue(
            request_approval(
                task_id="task-a",
                message_id="apv_a",
                tool="task_create",
                args={"summary": "s"},
                token="t1",
            )
        )
        # duplicate request returns False
        self.assertFalse(
            request_approval(
                task_id="task-a",
                message_id="apv_a",
                tool="task_create",
                args={"summary": "s"},
                token="t1",
            )
        )
        row = get_approval("apv_a")
        assert row is not None
        self.assertEqual(row["status"], "pending")
        resolved = approve_approval("apv_a", token="t1")
        assert resolved is not None
        self.assertEqual(resolved["status"], "approved")
        self.assertEqual(get_approval("apv_a")["status"], "approved")

    def test_wrong_token_blocked(self) -> None:
        request_approval(
            task_id="task-a",
            message_id="apv_b",
            tool="task_create",
            args={},
            token="t1",
        )
        self.assertIsNone(approve_approval("apv_b", token="t2"))

    def test_decline_then_approve_fails(self) -> None:
        request_approval(
            task_id="task-a",
            message_id="apv_c",
            tool="task_create",
            args={},
            token="t1",
        )
        self.assertIsNotNone(decline_approval("apv_c", token="t1"))
        self.assertIsNone(approve_approval("apv_c", token="t1"))

    def test_get_by_task(self) -> None:
        request_approval(
            task_id="task-a",
            message_id="apv_d",
            tool="task_create",
            args={},
            token="t1",
        )
        row = get_approval_by_task("task-a")
        self.assertIsNotNone(row)
        self.assertEqual(row["message_id"], "apv_d")

    def test_expire_stale_approvals(self) -> None:
        request_approval(
            task_id="task-a",
            message_id="apv_e",
            tool="task_create",
            args={},
            token="t1",
            timeout_sec=-1,  # immediately stale
        )
        self.assertEqual(get_approval("apv_e")["status"], "pending")
        expired = expire_stale_approvals()
        self.assertTrue(any(r["message_id"] == "apv_e" for r in expired))
        self.assertEqual(get_approval("apv_e")["status"], "expired")


class CardActionParseTests(unittest.TestCase):
    def test_approve_action_value(self) -> None:
        value = {
            "act": "approve",
            "key": "task-x",
            "task_id": "task-x",
            "message_id": "apv_x",
            "token": "tokx",
        }
        payload = {
            "chat_id": "oc_1",
            "operator_id": "ou_user",
            "event_id": "evt_1",
            "action_value": value,
        }
        act = extract_card_action(payload)
        assert isinstance(act, CardAction)
        self.assertEqual(act.act, "approve")
        self.assertEqual(act.task_id, "task-x")
        self.assertEqual(act.message_id, "apv_x")
        self.assertEqual(act.token, "tokx")

    def test_decline_action_value(self) -> None:
        value = {
            "act": "decline",
            "key": "task-y",
            "task_id": "task-y",
            "message_id": "apv_y",
        }
        payload = {
            "chat_id": "oc_1",
            "operator_id": "ou_user",
            "event_id": "evt_2",
            "action_value": json.dumps(value),
        }
        act = extract_card_action(payload)
        assert isinstance(act, CardAction)
        self.assertEqual(act.act, "decline")
        self.assertEqual(act.task_id, "task-y")
        self.assertEqual(act.message_id, "apv_y")


if __name__ == "__main__":
    unittest.main()
