"""Seam: 写文档 → 云文档链接，不把 fetch 原文甩进聊天。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from partner.actions import write_doc_text
from partner.routing.intents import parse_intent


class WriteDocTests(unittest.TestCase):
    def test_write_doc_creates_cloud_doc_without_raw_html(self) -> None:
        calls: list[list[str]] = []

        def fake_lark(args: list[str], **_k: object) -> dict:
            calls.append(args)
            if args[:2] == ["docs", "+search"]:
                return {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "title": "M8 plus 体验报告",
                                "url": "https://example.feishu.cn/docx/tmpl",
                            },
                            {
                                "title": "M8Plus 三合一",
                                "url": "https://example.feishu.cn/docx/extra",
                            },
                        ]
                    },
                }
            if args[:2] == ["docs", "+fetch"]:
                doc = args[args.index("--doc") + 1]
                if doc.endswith("tmpl"):
                    return {
                        "ok": True,
                        "data": {
                            "markdown": "<title>M8 plus 体验报告</title>\n# 产品外观\n# 产品网速\n# 产品附带功能"
                        },
                    }
                return {
                    "ok": True,
                    "data": {"markdown": "# 三合一\n- 录音转写翻译合并成一个页面"},
                }
            if args[:2] == ["docs", "+create"]:
                self.assertNotIn("<title>", args[args.index("--content") + 1])
                return {
                    "ok": True,
                    "data": {"url": "https://example.feishu.cn/docx/new"},
                }
            return {"ok": False, "error": {"message": "unexpected"}}

        with patch("partner.office.docs_io.run_lark", side_effect=fake_lark):
            text = write_doc_text("M8 plus 体验报告")
        self.assertIn("已生成云文档", text)
        self.assertIn("docx/new", text)
        self.assertNotIn("<title>", text)
        self.assertNotIn("# 产品外观", text)
        self.assertTrue(any(args[:2] == ["docs", "+create"] for args in calls))

    def test_empty_query_asks_for_title(self) -> None:
        self.assertIn("标题", write_doc_text(""))
        self.assertEqual(parse_intent("需要给我写文档").query, "")
