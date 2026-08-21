"""Seam: format_lark_result — CLI JSON → human Feishu text; never fake success."""

from __future__ import annotations

import json
import unittest

from partner.compose.formatters import (
    format_agenda,
    format_chats,
    format_day_work,
    format_doc,
    format_clarify,
    format_docs_materials,
    format_docs_search,
    format_lark_error,
    format_tasks,
    format_weekly_from_doc,
    format_weekly_human,
    format_wiki_spaces,
    draft_doc_markdown,
    pick_personal_weekly,
)
from partner.core.lark import parse_cli_output


class FormattersTests(unittest.TestCase):
    def test_missing_scope_is_explicit(self) -> None:
        text = format_lark_error(
            {
                "ok": False,
                "error": {
                    "subtype": "missing_scope",
                    "message": "missing required scope(s): calendar:calendar.event:read",
                    "missing_scopes": ["calendar:calendar.event:read"],
                    "hint": "run lark-cli auth login ...",
                },
            }
        )
        self.assertIn("calendar:calendar.event:read", text)
        self.assertIn("缺权限", text)
        self.assertNotIn("今天没有日程", text)

    def test_tasks_lists_summaries(self) -> None:
        text = format_tasks(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "summary": "A8设备邮寄回来（海外Esim卡处理）",
                            "due_at": "2026-08-15T08:00:00+08:00",
                            "completed": False,
                        }
                    ]
                },
            }
        )
        self.assertIn("A8设备邮寄回来", text)
        self.assertIn("2026-08-15", text)

    def test_agenda_missing_scope_not_empty_day(self) -> None:
        parsed = parse_cli_output(
            "",
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "subtype": "missing_scope",
                        "message": "missing required scope(s): calendar:calendar.event:read",
                        "missing_scopes": ["calendar:calendar.event:read"],
                    },
                }
            ),
            1,
        )
        text = format_agenda(parsed)
        self.assertIn("缺权限", text)
        self.assertNotIn("今天没有日程", text)

    def test_agenda_list_payload_shows_meeting(self) -> None:
        text = format_agenda(
            {
                "ok": True,
                "data": [
                    {
                        "summary": "研发部周会",
                        "start_time": {
                            "datetime": "2026-08-14T15:30:00+08:00",
                            "timezone": "Asia/Shanghai",
                        },
                        "end_time": {"datetime": "2026-08-14T16:30:00+08:00"},
                    }
                ],
            }
        )
        self.assertIn("研发部周会", text)
        self.assertIn("15:30", text)
        self.assertNotIn("今天没有日程", text)

    def test_docs_search_uses_highlighted_title_and_meta_url(self) -> None:
        text = format_docs_search(
            {
                "ok": True,
                "data": {
                    "results": [
                        {
                            "entity_type": "WIKI",
                            "title_highlighted": "产品需求评审三轮<h>流程</h>",
                            "result_meta": {
                                "url": "https://it82yw7fgr.feishu.cn/wiki/EQiRwnUHkiS6GckTLuAc5oGGn5d",
                                "token": "EQiRwnUHkiS6GckTLuAc5oGGn5d",
                            },
                        }
                    ]
                },
            },
            query="流程",
        )
        self.assertIn("产品需求评审三轮流程", text)
        self.assertNotIn("<h>", text)
        self.assertIn("https://it82yw7fgr.feishu.cn/wiki/", text)
        self.assertNotIn("(无标题)", text)

    def test_doc_nested_document_content(self) -> None:
        text = format_doc(
            {
                "ok": True,
                "data": {
                    "document": {
                        "content": "# 产品需求评审三轮流程\n\n从粗到细",
                    }
                },
            }
        )
        self.assertIn("产品需求评审三轮流程", text)
        self.assertNotIn("文档是空的", text)

    def test_format_doc_strips_html_title(self) -> None:
        text = format_doc(
            {
                "ok": True,
                "data": {
                    "markdown": "<title>M8 plus 体验报告</title>\n\n# 产品外观\n",
                },
            }
        )
        self.assertNotIn("<title>", text)
        self.assertIn("产品外观", text)

    def test_draft_doc_fills_outline_from_extras(self) -> None:
        body = draft_doc_markdown(
            "M8 plus 体验报告",
            "# 产品外观\n\n# 产品网速\n\n# 产品附带功能",
            extras=["# 三合一\n- 将录音、转写、翻译合并成一个页面\n- 无本地存储必须买AI套餐"],
            today="2026-08-18",
        )
        self.assertNotIn("<title>", body)
        self.assertIn("## 产品附带功能", body)
        self.assertIn("录音", body)
        self.assertIn("待实测补", body)

    def test_wiki_spaces_filter(self) -> None:
        payload = {
            "ok": True,
            "data": {
                "spaces": [
                    {"name": "流程库", "description": "公司流程指南库", "space_id": "1"},
                    {"name": "商务部", "description": "协议", "space_id": "2"},
                ]
            },
        }
        text = format_wiki_spaces(payload, query="流程")
        self.assertIn("流程库", text)
        self.assertNotIn("商务部", text)

    def test_docs_search_unescapes_amp(self) -> None:
        text = format_docs_search(
            {
                "ok": True,
                "data": {
                    "results": [
                        {
                            "title_highlighted": "个人内容消费记录 &amp; 管理",
                            "result_meta": {"url": "https://example.feishu.cn/wiki/abc"},
                        }
                    ]
                },
            },
            query="内容",
        )
        self.assertIn("个人内容消费记录 & 管理", text)
        self.assertNotIn("&amp;", text)

    def test_docs_materials_not_search_dump(self) -> None:
        text = format_docs_materials(
            {
                "ok": True,
                "data": {
                    "results": [
                        {
                            "title": "A6 终端离线配置同步接口文档",
                            "result_meta": {"url": "https://example.feishu.cn/docx/a6"},
                        }
                    ]
                },
            },
            query="A6",
        )
        self.assertIn("【相关材料】", text)
        self.assertIn("A6 终端离线配置同步接口文档", text)
        self.assertNotIn("文档搜索「A6」", text)

    def test_clarify_asks_instead_of_dumping(self) -> None:
        text = format_clarify(
            "A6",
            [
                "A6 终端离线配置同步接口文档",
                "A6 - 产品适配",
                "A6,A8退货换货的数据流转",
            ],
        )
        self.assertIn("你想问哪件", text)
        self.assertIn("1. A6 终端离线配置同步接口文档", text)
        self.assertNotIn("【摘录】", text)
        self.assertNotIn("文档搜索", text)
        self.assertNotIn("<title>", text)

    def test_topic_brief_is_not_search_dump(self) -> None:
        from partner.compose.formatters import format_topic_brief, looks_like_clarify

        text = format_topic_brief(
            "A6",
            "A6 终端离线配置同步接口文档",
            "https://example.feishu.cn/docx/Abc",
            "# 离线配置\n设备从中控拉配置。",
        )
        self.assertIn("《A6 终端离线配置同步接口文档》", text)
        self.assertNotIn("文档搜索", text)
        self.assertFalse(looks_like_clarify(text))
        self.assertTrue(looks_like_clarify("「A6」对上好几块，你想问哪件？"))

    def test_weekly_human_not_search_dump(self) -> None:
        from datetime import datetime, timezone, timedelta

        start = datetime(2026, 8, 10, tzinfo=timezone(timedelta(hours=8)))
        end = datetime(2026, 8, 16, 23, 59, 59, tzinfo=timezone(timedelta(hours=8)))
        text = format_weekly_human(
            start,
            end,
            {
                "ok": True,
                "data": [
                    {
                        "summary": "早会",
                        "start_time": {"datetime": "2026-08-10T09:00:00+08:00"},
                    },
                    {
                        "summary": "早会",
                        "start_time": {"datetime": "2026-08-12T09:00:00+08:00"},
                    },
                    {
                        "summary": "设备上线支持离线设置（中控配置拉取）",
                        "start_time": {"datetime": "2026-08-11T10:30:00+08:00"},
                    },
                    {
                        "summary": "研发部周会",
                        "start_time": {"datetime": "2026-08-14T15:30:00+08:00"},
                    },
                ],
            },
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "summary": "A8设备邮寄回来（海外Esim卡处理）",
                            "due_at": "2026-08-15T08:00:00+08:00",
                        }
                    ]
                },
            },
            {
                "ok": True,
                "data": {
                    "results": [
                        {
                            "title_highlighted": "个人内容消费记录 &amp; 管理",
                            "result_meta": {"url": "https://example.feishu.cn/wiki/dup"},
                        },
                        {
                            "title_highlighted": "个人内容消费记录 &amp; 管理",
                            "result_meta": {"url": "https://example.feishu.cn/wiki/dup"},
                        },
                        {
                            "title": "吴梦晨 周报",
                            "result_meta": {"url": "https://example.feishu.cn/wiki/me"},
                        },
                    ]
                },
            },
        )
        self.assertIn("8/10–8/16 这周我这边的情况", text)
        self.assertIn("【工作内容】", text)
        self.assertIn("日常早会过了几轮", text)
        self.assertIn("8/10", text)
        self.assertIn("开了「研发部周会」", text)
        self.assertIn("【待推进】", text)
        self.assertIn("A8设备邮寄回来", text)
        self.assertIn("【下周工作计划】", text)
        self.assertIn("吴梦晨 周报", text)
        self.assertNotIn("按文档搜索处理", text)
        self.assertNotIn("文档搜索「周报」", text)
        self.assertNotIn("&amp;", text)
        self.assertLessEqual(text.count("个人内容消费记录"), 1)

    def test_weekly_from_personal_doc_not_calendar_dump(self) -> None:
        from datetime import datetime, timedelta, timezone

        start = datetime(2026, 8, 10, tzinfo=timezone(timedelta(hours=8)))
        end = datetime(2026, 8, 16, tzinfo=timezone(timedelta(hours=8)))
        raw = """<title>平台研发部周报 - 吴梦晨</title>

> 部分内容由豆包生成

| **姓名** | 吴梦晨 | **部门** | 平台研发部 |
|-|-|-|-|

# 一、本周完成工作

## 1. 飞猫管家 A6 项目开发与提测

- 完成 A6 安卓测试版本打包并提测
- 处理 main 分支代码合并冲突

## 2. 套餐转移功能开发与联调

- 完成套餐转移新队列消费逻辑开发

## 3. 设备离线设置（中控配置拉取）需求推进

- 调研 MQTT 离线消息机制

## 4. 日常协作与支持

- 参与每日早会
- 协助 eSIM 二次翻新测试

# 二、遇到的问题与风险

<callout emoji="💡">
**A6 版本发版延迟**：原计划 8.12 发版
</callout>

# 三、下周计划

- [ ] 继续推进 A6 项目剩余开发与测试
"""
        text = format_weekly_from_doc(
            raw,
            start,
            end,
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "summary": "A8设备邮寄回来（海外Esim卡处理）",
                            "due_at": "2026-08-15T08:00:00+08:00",
                        }
                    ]
                },
            },
            source_title="平台研发部周报 - 吴梦晨",
            source_url="https://example.feishu.cn/docx/abc",
        )
        self.assertIn("【工作内容】", text)
        self.assertIn("飞猫管家 A6", text)
        self.assertIn("套餐转移功能", text)
        self.assertIn("【重点项目与进度】", text)
        self.assertIn("完成 A6 安卓测试版本打包并提测", text)
        self.assertNotIn("处理 main 分支代码合并冲突", text)
        self.assertNotIn("协助 eSIM 二次翻新测试", text)
        self.assertIn("【下周工作计划】", text)
        self.assertIn("A8设备邮寄回来", text)
        self.assertIn("材料：《平台研发部周报 - 吴梦晨》", text)
        self.assertNotIn("部分内容由豆包生成", text)
        self.assertNotIn("<title>", text)
        self.assertNotIn("<callout", text)
        self.assertNotIn("按文档搜索处理", text)
        self.assertNotIn("日常早会过了几轮", text)

        next_text = format_weekly_from_doc(
            raw,
            start,
            end,
            source_title="平台研发部周报 - 吴梦晨",
            source_url="https://example.feishu.cn/docx/abc",
            focus="next",
        )
        self.assertIn("下周我这边打算", next_text)
        self.assertIn("继续推进 A6", next_text)
        self.assertNotIn("【工作内容】", next_text)
        self.assertNotIn("文档搜索", next_text)

    def test_pick_personal_weekly_prefers_name(self) -> None:
        title, url = pick_personal_weekly(
            {
                "ok": True,
                "data": {
                    "results": [
                        {
                            "title": "平台研发部周报8月份第2周",
                            "result_meta": {"url": "https://example.feishu.cn/wiki/dept"},
                        },
                        {
                            "title": "平台研发部周报 - 吴梦晨",
                            "result_meta": {"url": "https://example.feishu.cn/docx/me"},
                        },
                    ]
                },
            }
        )
        self.assertEqual(title, "平台研发部周报 - 吴梦晨")
        self.assertIn("/docx/me", url)

    def test_chats_filter_prefers_name_hit(self) -> None:
        payload = {
            "ok": True,
            "data": {
                "chats": [
                    {"name": "孙萌测试", "chat_id": "oc_sun", "chat_mode": "group"},
                    {"name": "研发部周会", "chat_id": "oc_dev", "chat_mode": "group"},
                ]
            },
        }
        text = format_chats(payload, query="软件发版 测试 孙萌测试")
        self.assertIn("孙萌测试", text)
        self.assertNotIn("研发部周会", text)
        self.assertIn("oc_sun", text)

    def test_chats_filter_matches_suffix_token(self) -> None:
        payload = {
            "ok": True,
            "data": {
                "chats": [
                    {
                        "name": "飞猫管家APP发版对齐群",
                        "chat_id": "oc_rel",
                        "chat_mode": "group",
                    },
                    {"name": "研发部周会", "chat_id": "oc_dev", "chat_mode": "group"},
                ]
            },
        }
        text = format_chats(payload, query="软件发版")
        self.assertIn("发版对齐群", text)
        self.assertNotIn("研发部周会", text)

    def test_chat_tokens_keeps_测试(self) -> None:
        from partner.compose.formatters import _chat_tokens

        self.assertIn("测试", _chat_tokens("写完了需要交给测试人员的"))

    def test_day_work_keeps_followups_when_feishu_tasks_empty(self) -> None:
        text = format_day_work(
            "明天没有日程。",
            "没有未完成待办。",
            "- 立哥：写份体验总结给我",
        )
        self.assertIn("体验总结", text)
        self.assertIn("要跟的活", text)
        self.assertNotEqual(text.strip(), "没有未完成待办。")

    def test_day_work_all_empty_is_honest(self) -> None:
        text = format_day_work("明天没有日程。", "没有未完成待办。", "")
        self.assertIn("空", text)
        self.assertNotIn("体验总结", text)


if __name__ == "__main__":
    unittest.main()
