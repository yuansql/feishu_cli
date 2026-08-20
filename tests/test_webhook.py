from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from partner.tool_registry import (
    execute_tool,
    mcp_tools,
    read_tools,
    write_tools,
)
from partner.webhook import create_server, verify_signature


class ToolRegistryTests(unittest.TestCase):
    def test_allowlists_match_runner(self) -> None:
        self.assertIn("today", read_tools())
        self.assertIn("task_create", write_tools())
        self.assertNotIn("task_create", read_tools())

    def test_mcp_tools_include_parity_probe(self) -> None:
        names = {n for n, _d in mcp_tools()}
        self.assertIn("feishu_parity_probe", names)
        self.assertIn("feishu_today", names)

    def test_write_requires_confirmation(self) -> None:
        with self.assertRaises(RuntimeError):
            execute_tool("task_create", {"summary": "x"}, confirmed=False)


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_WEBHOOK_SECRET"] = "whsec-test"
        os.environ["FEISHU_PARTNER_RUNTIME_DB"] = os.path.join(
            self.tmp.name, "runtime.db"
        )
        os.environ["FEISHU_PARTNER_TASKS_DIR"] = os.path.join(self.tmp.name, "tasks")
        os.environ["FEISHU_PARTNER_WEBHOOK_CHAT_ID"] = "oc_test"
        self.server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for key in (
            "FEISHU_PARTNER_WEBHOOK_SECRET",
            "FEISHU_PARTNER_RUNTIME_DB",
            "FEISHU_PARTNER_TASKS_DIR",
            "FEISHU_PARTNER_WEBHOOK_CHAT_ID",
        ):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def _sign(self, body: bytes) -> tuple[str, str]:
        ts = str(int(time.time()))
        sig = hmac.new(
            b"whsec-test",
            f"{ts}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        return ts, sig

    def _post(self, payload: dict, *, event_id: str = "evt-1") -> dict:
        body = json.dumps(payload).encode("utf-8")
        ts, sig = self._sign(body)
        request = Request(
            self.base + "/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Event-Id": event_id,
                "X-Timestamp": ts,
                "X-Signature": sig,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_webhook_enqueues_and_dedups(self) -> None:
        reply1 = self._post({"goal": "汇总今天待办，不写入"})
        self.assertTrue(reply1["ok"])
        self.assertTrue(reply1.get("task_id"))
        reply2 = self._post({"goal": "汇总今天待办，不写入"})
        self.assertTrue(reply2.get("duplicate"))

    def test_invalid_signature_rejected(self) -> None:
        body = json.dumps({"goal": "test"}).encode("utf-8")
        request = Request(
            self.base + "/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Event-Id": "evt-bad",
                "X-Timestamp": str(int(time.time())),
                "X-Signature": "bad",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 401)

    def test_verify_signature_helper(self) -> None:
        body = b'{"goal":"x"}'
        ts = str(int(time.time()))
        sig = hmac.new(b"secret", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertTrue(
            verify_signature(secret="secret", timestamp=ts, body=body, signature=sig)
        )


if __name__ == "__main__":
    unittest.main()
