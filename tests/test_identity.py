from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.core import ids
from partner.office.report import render_html, report_from_goal, write_report
from partner.ops.setup import setup_text
from partner.runtime.tool_registry import execute_tool


_ENV_KEYS = (
    "FEISHU_PARTNER_CONFIG",
    "FEISHU_PARTNER_USER_OPEN_ID",
    "FEISHU_PARTNER_BOT_OPEN_ID",
    "FEISHU_PARTNER_P2P_CHAT_ID",
    "FEISHU_PARTNER_USER_NAMES",
    "FEISHU_PARTNER_WEEKLY_QUERY",
    "FEISHU_PARTNER_REPORTS",
)


class IdentityIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: os.environ.get(key) for key in _ENV_KEYS}
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.json"
        os.environ["FEISHU_PARTNER_CONFIG"] = str(self.cfg)
        for key in _ENV_KEYS:
            if key != "FEISHU_PARTNER_CONFIG":
                os.environ.pop(key, None)
        ids.reload_identity()

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        ids.reload_identity()
        self.tmp.cleanup()

    def test_empty_until_config(self) -> None:
        self.assertFalse(ids.identity_ready())
        self.assertIn("feishu setup", ids.identity_hint())

    def test_loads_config_json(self) -> None:
        self.cfg.write_text(
            json.dumps(
                {
                    "user_open_id": "ou_user_test",
                    "bot_open_id": "ou_bot_test",
                    "p2p_chat_id": "oc_p2p_test",
                    "user_names": "测试员",
                    "weekly_query": "测试员 周报",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ids.reload_identity()
        self.assertTrue(ids.identity_ready())
        self.assertEqual(ids.USER_OPEN_ID, "ou_user_test")
        self.assertEqual(ids.display_name(), "测试员")

    def test_env_overrides_config(self) -> None:
        self.cfg.write_text(
            json.dumps(
                {
                    "user_open_id": "ou_from_file",
                    "bot_open_id": "ou_bot_test",
                    "p2p_chat_id": "oc_p2p_test",
                    "user_names": "文件名",
                }
            ),
            encoding="utf-8",
        )
        os.environ["FEISHU_PARTNER_USER_OPEN_ID"] = "ou_from_env"
        ids.reload_identity()
        self.assertEqual(ids.USER_OPEN_ID, "ou_from_env")


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: os.environ.get(key) for key in _ENV_KEYS}
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.json"
        os.environ["FEISHU_PARTNER_CONFIG"] = str(self.cfg)
        for key in _ENV_KEYS:
            if key != "FEISHU_PARTNER_CONFIG":
                os.environ.pop(key, None)
        ids.reload_identity()

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        ids.reload_identity()
        self.tmp.cleanup()

    def test_setup_writes_config(self) -> None:
        def fake_lark(args: list[str], **_kwargs: object) -> dict[str, object]:
            if args[:1] == ["whoami"] and "--as" in args and args[args.index("--as") + 1] == "user":
                return {"ok": True, "data": {"open_id": "ou_user_setup"}}
            if args[:1] == ["whoami"]:
                return {"ok": True, "data": {"open_id": "ou_bot_setup"}}
            if args[:2] == ["im", "+chat-list"]:
                return {
                    "ok": True,
                    "data": {
                        "chats": [
                            {"chat_id": "oc_setup_p2p", "chat_mode": "p2p", "name": "bot"},
                        ]
                    },
                }
            raise AssertionError(args)

        with patch("partner.ops.setup.run_lark", side_effect=fake_lark):
            with patch("partner.compose.hermes_setup.ensure_profile", return_value=True):
                text = setup_text(name="测试员", force=True)
        self.assertIn("已写入", text)
        self.assertTrue(self.cfg.is_file())
        ids.reload_identity()
        self.assertEqual(ids.P2P_CHAT_ID, "oc_setup_p2p")
        self.assertEqual(ids.display_name(), "测试员")


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_REPORTS"] = str(Path(self.tmp.name) / "reports")

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_REPORTS", None)
        self.tmp.cleanup()

    def test_writes_html(self) -> None:
        html = render_html(title="周小结", body_md_or_text="# 标题\n\n- 一项")
        self.assertIn("<h1>周小结</h1>", html)
        self.assertIn("<li>一项</li>", html)
        path = write_report(title="周小结", body="- 一项")
        self.assertTrue(path.is_file())
        self.assertIn("已生成本地 HTML", report_from_goal("周小结", "- 一项"))

    def test_registry_report_write(self) -> None:
        text = execute_tool(
            "report_write",
            {"goal": "验收报告", "materials": "- 通过"},
            confirmed=False,
        )
        self.assertIn("已生成本地 HTML", text)


if __name__ == "__main__":
    unittest.main()
