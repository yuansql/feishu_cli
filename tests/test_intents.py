"""Seam: parse_intent — natural language / short commands → partner actions."""

from __future__ import annotations

import unittest

from partner.routing.intents import looks_like_bare_search, parse_classified, parse_intent


class ParseIntentTests(unittest.TestCase):
    def test_help_aliases(self) -> None:
        for text in ("帮助", "使用说明", "你能做什么", "help", "?"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "help", text)

    def test_today_aliases(self) -> None:
        for text in ("今天", "今日", "今日安排", "今天有什么安排", "today", "日程"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "today", text)

    def test_today_recap_questions(self) -> None:
        for text in (
            "我今天干了什么?",
            "我今天做了什么？",
            "今天都忙了啥",
            "今日完成了哪些工作",
            "我今天干啥了",
            "读一下今天的消息再看看",
            "翻一下今天聊天，看看我做了什么",
        ):
            self.assertEqual(parse_intent(text).action, "today_recap", text)
        self.assertEqual(parse_intent("今天").action, "today")
        self.assertNotEqual(
            parse_intent("今天这个文档做了什么修改").action,
            "today_recap",
        )

    def test_brief_aliases(self) -> None:
        for text in (
            "早报",
            "简报",
            "昨天小结",
            "今日规划",
            "今日工作简报",
            "每日工作简报",
        ):
            self.assertEqual(parse_intent(text).action, "brief", text)

    def test_tasks_aliases(self) -> None:
        for text in ("待办", "我的任务", "tasks"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "tasks", text)
        self.assertEqual(parse_intent("今天的任务？").action, "today")
        self.assertEqual(parse_intent("明天的任务").action, "tomorrow")
        self.assertEqual(parse_intent("明天任务").action, "tomorrow")
        self.assertEqual(parse_intent("明日任务").action, "tomorrow")
        self.assertFalse(looks_like_bare_search("明天的任务"))
        self.assertFalse(looks_like_bare_search("明天任务"))

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

    def test_chat_history_not_group_list(self) -> None:
        for text in (
            "读取和吴梦晨的飞书 CLI的聊天记录",
            "读我和飞书CLI的聊天记录",
            "聊天记录",
            "读取聊天记录",
            "读一下消息 吴梦晨的飞书 CLI",
            "读消息",
            "看看飞书 CLI 的消息",
        ):
            self.assertEqual(parse_intent(text).action, "chat_history", text)
        self.assertEqual(parse_intent("哪个群是产品评审").action, "chats")
        self.assertEqual(parse_intent("读取我的聊天,还是不行").action, "chats")

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
        self.assertEqual(parse_intent("帮我写周报").action, "write_weekly")
        fill = parse_intent(
            "https://it82yw7fgr.feishu.cn/wiki/QLg1wnATIiVTuXkUiHecNag7nSh 填写周报"
        )
        self.assertEqual(fill.action, "write_weekly")
        self.assertIn("wiki/", fill.query)

    def test_weekly_tasks_with_output_suffix(self) -> None:
        for text in (
            "本周任务",
            "本周任务, 输出到消息中",
            "本周任务，输出到消息中",
            "生成本周任务",
            "本周的全部任务",
            "这周所有任务",
            "这周的全部任务",
        ):
            self.assertEqual(parse_intent(text).action, "weekly_tasks", text)
        self.assertNotEqual(
            parse_intent("本周任务, 输出到消息中").action,
            "unknown",
        )
        self.assertEqual(parse_intent("本周计划").action, "weekly")
        self.assertEqual(parse_intent("本周的周报").action, "weekly")
        self.assertEqual(parse_intent("任务模式 本周风险").action, "plan")

    def test_who_is_not_person_dump(self) -> None:
        self.assertEqual(parse_intent("邱俊立是谁").action, "who")
        self.assertEqual(parse_intent("邱俊立是谁").query, "邱俊立")
        self.assertEqual(parse_intent("吴梦晨是谁?").action, "identity")
        self.assertEqual(parse_intent("张三的回复如何").action, "person")

    def test_doc_url_plus_append_is_write_doc(self) -> None:
        text = (
            "https://it82yw7fgr.feishu.cn/docx/JQmIdVhCmoQLbRxHMnbcO7sMnkc\n\n"
            "本周的一些活需要添加到第二个月中"
        )
        intent = parse_intent(text)
        self.assertEqual(intent.action, "write_doc", text)
        self.assertIn("第二个月", intent.query)
        # bare URL still read
        bare = "https://it82yw7fgr.feishu.cn/docx/JQmIdVhCmoQLbRxHMnbcO7sMnkc"
        self.assertEqual(parse_intent(bare).action, "read")

    def test_write_doc_from_template_phrase(self) -> None:
        intent = parse_intent("M8 plus 体验报告 给我写个这个?")
        self.assertEqual(intent.action, "write_doc")
        self.assertIn("M8 plus", intent.query)
        self.assertEqual(parse_intent("需要给我写文档").action, "write_doc")
        url = "https://example.feishu.cn/docx/AbCdEf"
        linked = parse_intent(f"给我写个这个 {url}")
        self.assertEqual(linked.action, "write_doc")
        self.assertIn("docx/AbCdEf", linked.query)

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

    def test_person_reply_is_not_docs(self) -> None:
        for text, name in (
            ("马丽敏的回复如何？", "马丽敏"),
            ("张三回了没", "张三"),
            ("李四怎么说", "李四"),
            ("问一下王五说了什么", "王五"),
        ):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "person", text)
            self.assertEqual(intent.query, name, text)
            self.assertFalse(looks_like_bare_search(text), text)

    def test_open_asks_are_not_bare_search(self) -> None:
        for text in (
            "帮我梳理一下今天该优先跟谁",
            "接下来怎么安排",
            "能不能总结一下风险",
        ):
            self.assertFalse(looks_like_bare_search(text), text)
        self.assertTrue(looks_like_bare_search("A6接口"))
        self.assertTrue(looks_like_bare_search("登录态刷新"))

    def test_classified_json_to_intent(self) -> None:
        hit = parse_classified('{"action":"person","query":"马丽敏"}')
        self.assertIsNotNone(hit)
        self.assertEqual(hit.action, "person")
        self.assertEqual(hit.query, "马丽敏")
        plan = parse_classified('{"action":"plan","query":"A6 上线前检查"}')
        self.assertIsNotNone(plan)
        self.assertEqual(plan.action, "plan")
        self.assertEqual(plan.query, "A6 上线前检查")
        self.assertEqual(parse_classified('{"action":"tasks"}').action, "tasks")
        self.assertEqual(
            parse_classified('{"action":"weekly_tasks"}').action, "weekly_tasks"
        )
        self.assertIsNone(parse_classified('{"action":"send","query":"hi"}'))
        self.assertIsNone(parse_classified('{"action":"plan"}'))
        self.assertIsNone(parse_classified("不是 json"))

    def test_group_wake_prefix_stripped(self) -> None:
        intent = parse_intent("工作伙伴 今天")
        self.assertEqual(intent.action, "today")
        intent = parse_intent("伙伴 搜 周报")
        self.assertEqual(intent.action, "search")
        self.assertEqual(intent.query, "周报")

    def test_plan_aliases_do_not_steal_existing_shortcuts(self) -> None:
        intent = parse_intent("规划 A6 上线前检查")
        self.assertEqual(intent.action, "plan")
        self.assertEqual(intent.query, "A6 上线前检查")

        intent = parse_intent("拆解 写周报")
        self.assertEqual(intent.action, "plan")
        self.assertEqual(intent.query, "写周报")
        self.assertFalse(looks_like_bare_search("拆解 写周报"))

        intent = parse_intent("A6 怎么推进")
        self.assertEqual(intent.action, "plan")
        self.assertEqual(intent.query, "A6")

        intent = parse_intent("帮我把 A6 上线拆成步骤")
        self.assertEqual(intent.action, "plan")
        self.assertEqual(intent.query, "A6 上线")

        self.assertEqual(parse_intent("本周计划").action, "weekly")
        self.assertEqual(parse_intent("今日规划").action, "brief")
        self.assertEqual(parse_intent("能力对齐").action, "aily")

    def test_local_html_report_starts_task_mode(self) -> None:
        intent = parse_intent("生成本地 HTML 报告：本周风险")
        self.assertEqual(intent.action, "plan")
        self.assertIn("风险", intent.query)
        self.assertEqual(parse_intent("本地工作报告").action, "plan")


if __name__ == "__main__":
    unittest.main()
