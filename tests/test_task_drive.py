"""任务推进链路：纪要 → 负责人 → 建任务 → 私信提醒。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from partner.office import org
from partner.office import task_drive as td

CN = timezone(timedelta(hours=8))

MINUTES = """项目周会纪要 2026-09-02
结论：表格分析技能已交付，下周重点 PPT 联调。
行动项：
- 张三：完成 PPT 技能真机联调（截止：周五）
- 李四负责整理 RAG 升级方案，截止：9月10日
- @王五 回复客户报价问题 截止：明天
"""


class TestExtractActions(unittest.TestCase):
    def test_regex_extract(self):
        items = td.extract_actions_regex(MINUTES)
        self.assertGreaterEqual(len(items), 2)
        owners = {item["owner"] for item in items}
        self.assertIn("张三", owners)
        titles = " ".join(item["title"] for item in items)
        self.assertIn("PPT", titles)
        dues = {item["due"] for item in items}
        self.assertTrue(any("周五" in d or "9月10" in d or "明天" in d for d in dues))

    def test_llm_extract_parses_json(self):
        fake = '[{"title":"写方案","owner":"张三","due":"周五"},{"title":"无负责人事项","owner":"","due":""}]'
        with patch("partner.compose.llm._invoke_hermes", return_value=fake):
            items = td.extract_actions_llm("某纪要")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["owner"], "张三")
        self.assertEqual(items[1]["owner"], "")

    def test_llm_extract_bad_json(self):
        with patch("partner.compose.llm._invoke_hermes", return_value="不是 JSON"):
            self.assertEqual(td.extract_actions_llm("某纪要"), [])

    def test_no_llm_env_disables(self):
        old = os.environ.get("FEISHU_PARTNER_NO_LLM")
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        try:
            self.assertEqual(td.extract_actions_llm("纪要"), [])
        finally:
            if old is None:
                os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
            else:
                os.environ["FEISHU_PARTNER_NO_LLM"] = old

    def test_extract_prefers_llm_then_regex(self):
        with patch.object(td, "extract_actions_llm", return_value=[]):
            items = td.extract_actions(MINUTES)
        self.assertGreaterEqual(len(items), 2)


class TestParseDue(unittest.TestCase):
    def setUp(self):
        self.wed = datetime(2026, 9, 2, 10, 0, tzinfo=CN)  # 周三

    def test_absolute(self):
        self.assertEqual(td.parse_due("2026-09-10", today=self.wed), "2026-09-10")

    def test_relative_words(self):
        self.assertEqual(td.parse_due("明天", today=self.wed), "2026-09-03")
        self.assertEqual(td.parse_due("今天", today=self.wed), "2026-09-02")

    def test_weekday(self):
        self.assertEqual(td.parse_due("周五", today=self.wed), "2026-09-04")
        self.assertEqual(td.parse_due("周三前", today=self.wed), "2026-09-09")
        self.assertEqual(td.parse_due("下周一", today=self.wed), "2026-09-07")

    def test_chinese_date(self):
        self.assertEqual(td.parse_due("9月10日", today=self.wed), "2026-09-10")
        self.assertEqual(td.parse_due("1月5日", today=self.wed), "2027-01-05")

    def test_passthrough(self):
        self.assertEqual(td.parse_due("下周内", today=self.wed), "下周内")
        self.assertEqual(td.parse_due("", today=self.wed), "")


class TestCreateAndNotify(unittest.TestCase):
    def test_create_task_ok(self):
        with patch.object(td, "run_lark", return_value={"ok": True, "data": {}}) as mock_run:
            ok, err = td.create_task(
                {"title": "联调 PPT", "owner": "张三", "due": "周五"}, "ou_zhangsan"
            )
        self.assertTrue(ok)
        self.assertEqual(err, "")
        argv = mock_run.call_args[0][0]
        self.assertIn("+create", argv)
        self.assertIn("--assignee", argv)
        self.assertIn("ou_zhangsan", argv)

    def test_create_task_no_due_no_assignee(self):
        with patch.object(td, "run_lark", return_value={"ok": True, "data": {}}) as mock_run:
            ok, _ = td.create_task({"title": "杂事", "owner": "", "due": "下周内"})
        self.assertTrue(ok)
        argv = mock_run.call_args[0][0]
        self.assertNotIn("--assignee", argv)
        self.assertNotIn("--due", argv)

    def test_create_task_error(self):
        with patch.object(
            td, "run_lark", return_value={"ok": False, "error": {"message": "no scope"}}
        ):
            ok, err = td.create_task({"title": "x", "owner": "", "due": ""})
        self.assertFalse(ok)
        self.assertIn("no scope", err)

    def test_notify_owner(self):
        with patch(
            "partner.office.messaging.send_text", return_value="已发送。"
        ) as mock_send:
            result = td.notify_owner(
                "ou_zhangsan", {"title": "联调", "due": "周五"}, source="周会"
            )
        self.assertEqual(result, "已发送。")
        _, text = mock_send.call_args[0][0], mock_send.call_args[0][1]
        self.assertIn("联调", text)
        self.assertIn("周五", text)
        self.assertIn("周会", text)


class TestDriveFlow(unittest.TestCase):
    def test_dry_run_preview(self):
        with patch.object(td, "extract_actions_llm", return_value=[]):
            text = td.drive_from_minutes(MINUTES, execute=False)
        self.assertIn("预览", text)
        self.assertIn("张三", text)
        self.assertIn("--execute", text)

    def test_execute_full_chain(self):
        items = [{"title": "联调 PPT", "owner": "张三", "due": "周五"}]
        with patch.object(td, "extract_actions_llm", return_value=items), patch.object(
            td, "resolve", return_value="ou_zhangsan"
        ), patch.object(td, "create_task", return_value=(True, "")) as mock_create, patch.object(
            td, "notify_owner", return_value="已发送。"
        ) as mock_notify:
            text = td.drive_from_minutes("纪要", execute=True, source="周会")
        self.assertIn("已建任务（张三）", text)
        self.assertIn("已私信", text)
        mock_create.assert_called_once()
        mock_notify.assert_called_once()

    def test_execute_owner_unresolved(self):
        items = [{"title": "联调", "owner": "无名氏", "due": ""}]
        with patch.object(td, "extract_actions_llm", return_value=items), patch.object(
            td, "resolve", return_value=""
        ), patch.object(td, "create_task", return_value=(True, "")), patch.object(
            td, "notify_owner"
        ) as mock_notify:
            text = td.drive_from_minutes("纪要", execute=True)
        self.assertIn("未匹配到人", text)
        mock_notify.assert_not_called()

    def test_execute_create_fails(self):
        items = [{"title": "联调", "owner": "", "due": ""}]
        with patch.object(td, "extract_actions_llm", return_value=items), patch.object(
            td, "create_task", return_value=(False, "denied")
        ):
            text = td.drive_from_minutes("纪要", execute=True)
        self.assertIn("建任务失败", text)
        self.assertIn("denied", text)

    def test_empty_minutes(self):
        self.assertIn("没有纪要内容", td.drive_from_minutes("", execute=True))


class TestOrgCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(
            org, "CACHE_PATH", Path(self.tmp.name) / "org-cache.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_learn_and_resolve_cached(self):
        org.learn("张三", "ou_zhangsan", dept="研发")
        oid = org.resolve("张三", allow_remote=False)
        self.assertEqual(oid, "ou_zhangsan")

    def test_alias_semantic(self):
        org.learn("张三丰", "ou_zsf")
        org.learn_alias("张总", "张三丰")
        self.assertEqual(org.resolve("张总", allow_remote=False), "ou_zsf")

    def test_honorific_strip(self):
        org.learn("李四", "ou_lisi")
        self.assertEqual(org.resolve("李四总", allow_remote=False), "ou_lisi")

    def test_resolve_remote_and_cache(self):
        payload = {
            "ok": True,
            "data": {"users": [{"name": "王五", "open_id": "ou_wangwu"}]},
        }
        with patch.object(org, "run_lark", return_value=payload):
            oid = org.resolve("王五")
        self.assertEqual(oid, "ou_wangwu")
        # 第二次走缓存，不再远程
        with patch.object(org, "run_lark", side_effect=AssertionError("不该再远程")):
            self.assertEqual(org.resolve("王五"), "ou_wangwu")

    def test_resolve_miss(self):
        with patch.object(org, "run_lark", return_value={"ok": True, "data": {"users": []}}):
            self.assertEqual(org.resolve("不存在的人"), "")


if __name__ == "__main__":
    unittest.main()
