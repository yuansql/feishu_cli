"""Seam: Feishu MCP only exposes allowlisted fact tools."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from partner.mcp_server import handle


class FeishuMcpTests(unittest.TestCase):
    def test_lists_allowlisted_tools(self) -> None:
        resp = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp is not None
        names = {tool["name"] for tool in resp["result"]["tools"]}
        self.assertIn("feishu_today", names)
        self.assertIn("feishu_search", names)
        self.assertIn("feishu_person", names)
        self.assertNotIn("feishu_send", names)
        self.assertNotIn("feishu_write_weekly", names)

    def test_call_today(self) -> None:
        with patch("partner.mcp_server._facts_for", return_value="日程：A6 提测"):
            resp = handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "feishu_today", "arguments": {}},
                }
            )
        assert resp is not None
        self.assertIn("A6", resp["result"]["content"][0]["text"])

    def test_search_requires_query(self) -> None:
        resp = handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "feishu_search", "arguments": {}},
            }
        )
        assert resp is not None
        self.assertIn("query", resp["result"]["content"][0]["text"])
