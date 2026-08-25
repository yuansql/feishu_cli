from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from partner.runtime.sandbox import resolve_path, sandbox_ls, sandbox_read, sandbox_run, sandbox_write
from partner.runtime.tool_registry import execute_tool


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_SANDBOX"] = str(Path(self.tmp.name) / "box")

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_SANDBOX", None)
        self.tmp.cleanup()

    def test_write_read_and_python(self) -> None:
        self.assertIn("已写入", sandbox_write("hello.py", "print('sandbox-ok')\n"))
        self.assertIn("hello.py", sandbox_ls("."))
        self.assertIn("sandbox-ok", sandbox_run("python3 hello.py"))

    def test_rejects_escape_and_shell(self) -> None:
        with self.assertRaises(ValueError):
            resolve_path("../secret")
        self.assertIn("允许列表", sandbox_run("rm -rf /"))
        self.assertIn("越出沙箱", sandbox_run("cat /etc/passwd"))

    def test_mkdir_and_status_shows_quota(self) -> None:
        from partner.runtime.sandbox import sandbox_status_text

        self.assertIn("exit 0", sandbox_run("mkdir nested/dir"))
        self.assertIn("nested", sandbox_ls("."))
        status = sandbox_status_text()
        self.assertIn("超时", status)
        self.assertIn("进程配额", status)

    def test_quota_blocks_when_full(self) -> None:
        from partner.runtime import sandbox as sb

        os.environ["FEISHU_PARTNER_SANDBOX_MAX"] = "1"
        with sb._active_lock:
            sb._active_runs = 1
        try:
            msg = sandbox_run("uname")
            self.assertIn("配额已满", msg)
        finally:
            with sb._active_lock:
                sb._active_runs = 0
            os.environ.pop("FEISHU_PARTNER_SANDBOX_MAX", None)

    def test_registry_write_without_feishu_confirm(self) -> None:
        text = execute_tool(
            "sandbox_write",
            {"path": "note.txt", "content": "hi"},
            confirmed=False,
        )
        self.assertIn("已写入", text)
        self.assertEqual(execute_tool("sandbox_read", {"path": "note.txt"}), "hi")


if __name__ == "__main__":
    unittest.main()
