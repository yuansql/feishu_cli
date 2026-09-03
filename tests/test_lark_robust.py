"""run_lark 健壮性：lark-cli 超时/启动失败必须返回错误 payload，绝不抛出。

背景：2026-09-03 serve 主循环被 bitable 扫描的未捕获 TimeoutExpired 打死，
卡片回调全丢（用户点「完成」无反应）。
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from partner.core import lark


class TestRunLarkRobust(unittest.TestCase):
    def test_timeout_returns_error_payload(self):
        with patch.object(lark, "find_lark_cli", return_value="/bin/false"):
            with patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["lark"], timeout=60),
            ):
                payload = lark.run_lark(["base", "+record-list"], as_identity="user")
        self.assertFalse(payload["ok"])
        self.assertIn("timeout", payload["error"]["message"])
        self.assertIn("base", payload["error"]["message"])

    def test_spawn_failure_returns_error_payload(self):
        with patch.object(lark, "find_lark_cli", return_value="/nonexistent/lark-cli"):
            payload = lark.run_lark(["docs", "+search"], as_identity="user")
        self.assertFalse(payload["ok"])
        self.assertIn("spawn failed", payload["error"]["message"])

    def test_normal_success_unaffected(self):
        completed = subprocess.CompletedProcess(
            args=["x"], returncode=0, stdout='{"ok": true, "data": {"a": 1}}', stderr=""
        )
        with patch.object(lark, "find_lark_cli", return_value="/bin/echo"):
            with patch.object(subprocess, "run", return_value=completed):
                payload = lark.run_lark(["tasks", "+list"], as_identity="user")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["a"], 1)


if __name__ == "__main__":
    unittest.main()
