from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from partner.mcp_http import create_server


class McpHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_MCP_TOKEN"] = "test-token"
        os.environ["FEISHU_PARTNER_AILY_CONFIG"] = os.path.join(
            self.tmp.name, "aily.json"
        )
        self.server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        os.environ.pop("FEISHU_PARTNER_MCP_TOKEN", None)
        os.environ.pop("FEISHU_PARTNER_AILY_CONFIG", None)
        self.tmp.cleanup()

    def _post(self, payload: dict, *, token: str = "test-token") -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            self.base + "/mcp",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_and_initialize(self) -> None:
        with urlopen(self.base + "/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        self.assertTrue(health["ok"])
        reply = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")

    def test_bearer_token_required(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            self._post(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                token="",
            )
        self.assertEqual(caught.exception.code, 401)

    def test_remote_binding_requires_token(self) -> None:
        os.environ.pop("FEISHU_PARTNER_MCP_TOKEN", None)
        with self.assertRaises(RuntimeError):
            create_server("0.0.0.0", 0)

    def test_parity_probe_records_http_verification(self) -> None:
        reply = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "feishu_parity_probe",
                    "arguments": {"nonce": "acceptance-1"},
                },
            }
        )
        self.assertIn("acceptance-1", reply["result"]["content"][0]["text"])
        from partner.aily import load_aily_config

        config = load_aily_config()
        self.assertTrue(config.get("probed_at"))
        self.assertIn("/mcp", str(config.get("mcp_url") or ""))


if __name__ == "__main__":
    unittest.main()
