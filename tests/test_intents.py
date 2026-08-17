"""Seam: parse_intent — natural language / short commands → partner actions."""

from __future__ import annotations

import unittest

from partner.intents import parse_intent


class ParseIntentTests(unittest.TestCase):
    def test_help_aliases(self) -> None:
        for text in ("帮助", "使用说明", "你能做什么", "help", "?"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "help", text)

    def test_today_aliases(self) -> None:
        for text in ("今天", "今日", "今日安排", "今天有什么安排", "today", "日程"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "today", text)

    def test_brief_aliases(self) -> None:
        for text in ("早报", "简报", "昨天小结", "今日规划"):
            self.assertEqual(parse_intent(text).action, "brief", text)

    def test_tasks_aliases(self) -> None:
        for text in ("待办", "我的任务", "tasks"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "tasks", text)

    def test_search_prefix(self) -> None:
        intent = parse_intent("搜 周报")
        self.assertEqual(intent.action, "search")
        self.assertEqual(intent.query, "周报")

        intent = parse_intent("搜索：请假制度")
        self.assertEqual(intent.action, "search")
        self.assertEqual(intent.query, "请假制度")

    def test_read_url_or_token(self) -> None:
        url = "https://example.feishu.cn/docx/AbCdEf"
        intent = parse_intent(f"读 {url}")
        self.assertEqual(intent.action, "read")
        self.assertEqual(intent.query, url)

    def test_chats(self) -> None:
        self.assertEqual(parse_intent("群列表").action, "chats")
        self.assertEqual(parse_intent("chats").action, "chats")

    def test_which_group_is_chats_not_docs(self) -> None:
        first = parse_intent("软件发版 测试 孙萌测试是那个群")
        self.assertEqual(first.action, "chats")
        self.assertIn("孙萌", first.query)
        follow = parse_intent("我想问的是 写完了需要交给测试人员的 那个群是那个？")
        self.assertEqual(follow.action, "chats")
        self.assertIn("测试", follow.query)

    def test_minutes_and_approval(self) -> None:
        self.assertEqual(parse_intent("会议纪要").action, "minutes")
        self.assertEqual(parse_intent("妙记").action, "minutes")
        self.assertEqual(parse_intent("审批").action, "approval")
        self.assertEqual(parse_intent("待审批").action, "approval")

    def test_inbox(self) -> None:
        for text in ("谁找我", "有人找我", "收件箱", "找我的消息"):
            self.assertEqual(parse_intent(text).action, "inbox", text)

    def test_send_needs_chat_and_text(self) -> None:
        intent = parse_intent("发 oc_abc 你好世界")
        self.assertEqual(intent.action, "send")
        self.assertEqual(intent.chat_id, "oc_abc")
        self.assertEqual(intent.query, "你好世界")

    def test_weekly_aliases(self) -> None:
        for text in ("本周的周报", "周报", "本周周报", "这周安排", "本周日程"):
            self.assertEqual(parse_intent(text).action, "weekly", text)

    def test_write_weekly(self) -> None:
        self.assertEqual(parse_intent("写周报").action, "write_weekly")
        self.assertEqual(parse_intent("生成周报").action, "write_weekly")

    def test_tomorrow(self) -> None:
        self.assertEqual(parse_intent("明天").action, "tomorrow")
        self.assertEqual(parse_intent("明日安排").action, "tomorrow")

    def test_bare_url_is_read(self) -> None:
        url = "https://it82yw7fgr.feishu.cn/wiki/EQiRwnUHkiS6GckTLuAc5oGGn5d"
        intent = parse_intent(url)
        self.assertEqual(intent.action, "read")
        self.assertEqual(intent.query, url)

    def test_unknown_stays_unknown(self) -> None:
        intent = parse_intent("随便聊聊天气")
        self.assertEqual(intent.action, "unknown")
        self.assertEqual(intent.query, "随便聊聊天气")

    def test_group_wake_prefix_stripped(self) -> None:
        intent = parse_intent("工作伙伴 今天")
        self.assertEqual(intent.action, "today")
        intent = parse_intent("伙伴 搜 周报")
        self.assertEqual(intent.action, "search")
        self.assertEqual(intent.query, "周报")


if __name__ == "__main__":
    unittest.main()
