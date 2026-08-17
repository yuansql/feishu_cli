"""Seam: Hermes rewrite is text-only and never passes --yolo."""

from __future__ import annotations

import unittest

from partner.llm import (
    _brief_prompt,
    _extract_reply,
    _is_usable_reply,
    accept_polished_brief,
    build_hermes_argv,
    isolate_brief,
    parse_fetch,
    should_compose,
    should_partner,
)


class HermesArgvTests(unittest.TestCase):
    def test_never_yolo(self) -> None:
        from pathlib import Path

        argv = build_hermes_argv("hi", Path("/usr/bin/hermes"))
        self.assertNotIn("--yolo", argv)
        self.assertEqual(argv[1], "chat")
        self.assertIn("--max-turns", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertNotIn("-p", argv)

    def test_partner_uses_isolated_profile(self) -> None:
        from pathlib import Path

        argv = build_hermes_argv("hi", Path("/usr/bin/hermes"), mode="partner")
        self.assertNotIn("--yolo", argv)
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "feishupartner")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "6")
        self.assertNotIn("--ignore-rules", argv)

    def test_extract_drops_box_and_session(self) -> None:
        raw = (
            "┌─ Reasoning ──┐\n"
            "│ 我想调用终端  │\n"
            "└──────────────┘\n"
            "session_id: 20260815_x\n"
            "下周先把 A6 提测收掉。\n"
        )
        text = _extract_reply(raw)
        self.assertEqual(text, "下周先把 A6 提测收掉。")
        self.assertNotIn("session_id", text)
        self.assertNotIn("终端", text)

    def test_extract_drops_leaked_thinking(self) -> None:
        raw = (
            "用户要求根据材料用第一人称写回复。\n"
            "我需要以吴梦晨的身份回复。\n"
            "可以这样组织：\n"
            "下周我这边计划：A6 继续提测。\n"
        )
        text = _extract_reply(raw)
        self.assertEqual(text, "下周我这边计划：A6 继续提测。")
        self.assertNotIn("用户要求", text)

    def test_extract_drops_tool_planning_leak(self) -> None:
        raw = (
            "当前【材料】里没有群 ID。\n"
            "根据指令：材料不够就调用 feishu_* 工具补齐。不要用终端。\n"
            "没有工具时，只输出一行 FETCH: chats\n"
            "我需要搜索测试人员相关的群。\n"
            "下一步用 feishu_search 搜「测试人员 群」。\n"
        )
        text = _extract_reply(raw)
        self.assertEqual(text, "")
        self.assertFalse(_is_usable_reply(raw))

    def test_rejects_leaked_thinking_as_unusable(self) -> None:
        blob = "材料是吴梦晨的周报内容，包括：\n让我以同事的口吻回复\n再调整：\n下周计划"
        self.assertFalse(_is_usable_reply(blob))
        self.assertTrue(_is_usable_reply("我这边下周主要把 A6 提测收掉。"))

    def test_compose_gate(self) -> None:
        self.assertTrue(should_compose("weekly"))
        self.assertTrue(should_compose("minutes"))
        self.assertFalse(should_compose("search"))
        self.assertFalse(should_compose("help"))
        self.assertFalse(should_compose("brief"))

    def test_partner_only_in_p2p(self) -> None:
        self.assertTrue(should_partner("p2p", "unknown"))
        self.assertTrue(should_partner("p2p", "inbox"))
        self.assertFalse(should_partner("p2p", "send"))
        self.assertFalse(should_partner("group", "unknown"))
        self.assertFalse(should_partner("group", "weekly"))

    def test_parse_fetch_allowlist(self) -> None:
        self.assertEqual(parse_fetch("FETCH: today"), ("today", ""))
        self.assertEqual(parse_fetch("FETCH: search 周报"), ("search", "周报"))
        self.assertEqual(parse_fetch("FETCH: read https://feishu.cn/x"), ("read", "https://feishu.cn/x"))
        self.assertIsNone(parse_fetch("FETCH: send oc_xxx 你好"))
        self.assertIsNone(parse_fetch("FETCH: write_weekly"))
        self.assertIsNone(parse_fetch("下周先把 A6 提测收掉。"))
        self.assertFalse(_is_usable_reply("FETCH: today"))

    def test_brief_prompt_forbids_invent(self) -> None:
        prompt = _brief_prompt("吴梦晨 · 8月16日简报\n【昨天小结】\n- 研发部周会")
        self.assertIn("不许编造", prompt)
        self.assertIn("【昨天小结】", prompt)
        self.assertIn("【今天规划】", prompt)
        self.assertNotIn("第一人称（我）", prompt)

    def test_accept_polished_brief_keeps_headings_rejects_invent(self) -> None:
        original = (
            "吴梦晨 · 8月16日简报\n\n"
            "【昨天小结】上一个工作日 8/14\n"
            "完成/推进\n"
            "- 研发部周会（15:30）\n"
            "待回复\n"
            "- APP沟通群：主分支同步\n\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
            "2. 回：APP沟通群：主分支同步\n"
        )
        good = (
            "吴梦晨 · 8月16日简报\n\n"
            "【昨天小结】上一个工作日 8/14\n"
            "完成/推进\n"
            "- 研发部周会（15:30）\n"
            "待回复\n"
            "- APP沟通群还在等主分支同步的回复\n\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
            "2. 回 APP沟通群那条主分支同步\n"
        )
        self.assertTrue(accept_polished_brief(original, good))
        self.assertFalse(accept_polished_brief(original, good.replace("【今天规划】", "今日安排")))
        self.assertFalse(accept_polished_brief(original, good + "3. 写周报给老板\n"))
        bare = (
            "吴梦晨 · 8月16日简报\n\n"
            "【昨天小结】上一个工作日 8/14\n"
            "完成/推进\n"
            "- 研发部周会（15:30）\n\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
        )
        invented = bare + "待回复\n- APP沟通群：主分支同步\n"
        self.assertFalse(accept_polished_brief(bare, invented))

    def test_isolate_brief_takes_last_block_from_leaked_thinking(self) -> None:
        leaked = (
            "用户要求润色简报。\n"
            "分析：可以合并重复。\n"
            "吴梦晨 · 8月16日简报\n草稿不要\n"
            "最终结果：\n"
            "吴梦晨 · 8月16日简报\n\n"
            "【昨天小结】上一个工作日 8/14\n"
            "完成/推进\n"
            "- 研发部周会（15:30）\n"
            "待回复\n"
            "- APP沟通群：主分支同步\n\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
        )
        original = (
            "吴梦晨 · 8月16日简报\n\n"
            "【昨天小结】上一个工作日 8/14\n"
            "完成/推进\n"
            "- 研发部周会（15:30）\n"
            "待回复\n"
            "- APP沟通群：主分支同步\n\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
            "2. 回：APP沟通群：主分支同步\n"
        )
        block = isolate_brief(leaked)
        self.assertTrue(block.startswith("吴梦晨 ·"))
        self.assertNotIn("用户要求", block)
        self.assertNotIn("草稿不要", block)
        self.assertTrue(accept_polished_brief(original, block))

    def test_isolate_trims_thinking_after_header(self) -> None:
        blob = (
            "吴梦晨 · 8月16日简报\n\n"
            "【昨天小结】上一个工作日 8/14\n"
            "完成/推进\n"
            "- 研发部周会（15:30）\n"
            "待回复\n"
            "- APP沟通群：主分支同步\n\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
            "分析：可以合并重复。" + ("啊" * 2000)
        )
        block = isolate_brief(blob)
        self.assertLess(len(block), 400)
        self.assertNotIn("分析：", block)
        self.assertIn("【今天规划】", block)

    def test_isolate_stops_at_leftover_chat(self) -> None:
        blob = (
            "吴梦晨 · 8月16日简报\n"
            "【昨天小结】上一个工作日 8/14\n"
            "- 研发部周会（15:30）\n"
            "【今天规划】\n"
            "1. A8设备邮寄回来（卡人·紧急）\n"
            "这样应该可以。材料中第2项是重复的。\n"
        )
        block = isolate_brief(blob)
        self.assertNotIn("这样应该可以", block)
        self.assertTrue(block.startswith("吴梦晨 ·"))
        self.assertIn("A8设备邮寄回来", block)


if __name__ == "__main__":
    unittest.main()
