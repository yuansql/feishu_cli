"""Seam: daily brief — last workday, omit empty, 3–5 priorities, no padding."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import unittest

from partner.office.brief import (
    _fetch_group_mentions,
    _thread_participants,
    agenda_entries,
    approval_priority_lines,
    clip_line,
    cn_day,
    dedupe_priorities,
    format_agenda_today,
    format_daily_brief,
    last_workday,
    long_term_task_lines,
    parse_msg_time,
    pending_brief_line,
    pick_priorities,
    rank_priorities,
    reply_status,
    third_party_ack,
    user_spoke_after,
    work_priorities,
)
from partner.core.ids import USER_OPEN_ID
from partner.office.brief_card import brief_card, day_work_card
from partner.office.schedule import BRIEF_LABEL, SERVE_LABEL, plist_body, serve_plist_body


CN = timezone(timedelta(hours=8))


class LastWorkdayTests(unittest.TestCase):
    def test_weekday_is_yesterday(self) -> None:
        fri = datetime(2026, 8, 14, 9, 0, tzinfo=CN)
        self.assertEqual(last_workday(fri).isoformat(), "2026-08-13")

    def test_monday_and_weekend_skip_to_friday(self) -> None:
        mon = datetime(2026, 8, 17, 9, 0, tzinfo=CN)
        sun = datetime(2026, 8, 16, 9, 0, tzinfo=CN)
        sat = datetime(2026, 8, 15, 9, 0, tzinfo=CN)
        self.assertEqual(last_workday(mon).isoformat(), "2026-08-14")
        self.assertEqual(last_workday(sun).isoformat(), "2026-08-14")
        self.assertEqual(last_workday(sat).isoformat(), "2026-08-14")


class FormatBriefTests(unittest.TestCase):
    def test_omits_empty_sections_and_does_not_pad(self) -> None:
        text = format_daily_brief(
            today=datetime(2026, 8, 17, 9, 0, tzinfo=CN).date(),
            workday=datetime(2026, 8, 14).date(),
            progressed=["主分支已合并"],
            unreplied=["APP沟通群：问版本号"],
            priorities=["回版本号（卡人）", "早会"],
            week_notes=[],
        )
        self.assertIn("昨天小结", text)
        self.assertIn("待处理", text)
        self.assertIn("今天规划", text)
        self.assertNotIn("邮件", text)
        self.assertNotIn("本周值得关注", text)
        self.assertEqual(text.count("主分支已合并"), 1)

    def test_empty_today_falls_back_to_week_notes(self) -> None:
        text = format_daily_brief(
            today=datetime(2026, 8, 16, 9, 0, tzinfo=CN).date(),
            workday=datetime(2026, 8, 14).date(),
            progressed=[],
            unreplied=[],
            priorities=[],
            week_notes=["周五研发部周会"],
        )
        self.assertNotIn("昨天小结", text)
        self.assertIn("本周值得关注", text)
        self.assertIn("周五研发部周会", text)

    def test_priorities_sort_block_then_urgent_cap_five(self) -> None:
        picked = pick_priorities(
            [
                {"title": "闲逛文档", "blocks": False, "urgent": False},
                {"title": "回景伦版本号", "blocks": True, "urgent": True},
                {"title": "自己看看周报", "blocks": False, "urgent": True},
                {"title": "对齐发版", "blocks": True, "urgent": False},
                {"title": "第六件不该出现", "blocks": False, "urgent": False},
                {"title": "第七件", "blocks": False, "urgent": False},
            ]
        )
        titles = [item["title"] for item in picked]
        self.assertEqual(titles[0], "回景伦版本号")
        self.assertEqual(titles[1], "对齐发版")
        self.assertLessEqual(len(titles), 5)
        self.assertNotIn("第六件不该出现", titles)

    def test_approval_lines_become_blockers(self) -> None:
        lines = approval_priority_lines(
            {
                "ok": True,
                "data": {
                    "tasks": [
                        {"title": "请假申请", "status": "PENDING"},
                        {"approval_name": "用印", "status": "PENDING"},
                    ]
                },
            }
        )
        self.assertEqual(lines[0], "审批 请假申请")
        self.assertIn("审批 用印", lines)

    def test_reply_status_clarifying_is_not_answered(self) -> None:
        ask = "@杨庆海 @吴梦晨 主分支更新了代码，同步下其他分支"
        self.assertEqual(reply_status(ask, []), "none")
        self.assertEqual(reply_status(ask, ["main 还是 mian-hm", "?"]), "clarifying")
        self.assertEqual(reply_status(ask, ["好的", "收到"]), "clarifying")
        self.assertEqual(reply_status(ask, ["已同步 main"]), "answered")
        self.assertEqual(reply_status("版本号是多少？", ["1.2.3"]), "answered")

    def test_format_marks_in_progress_and_unfinished(self) -> None:
        text = format_daily_brief(
            today=datetime(2026, 8, 16, 9, 0, tzinfo=CN).date(),
            workday=datetime(2026, 8, 14).date(),
            progressed=["研发部周会（15:30）（已结束）"],
            unreplied=["APP沟通群：同步其他分支（进行中·已追问未答完）"],
            priorities=["A8设备邮寄回来（未完成·卡人·紧急）"],
            week_notes=[],
            today_agenda=[],
        )
        self.assertIn("已结束", text)
        self.assertIn("进行中", text)
        self.assertIn("未完成", text)
        self.assertIn("待处理", text)

    def test_same_minute_own_reply_clears_unreplied(self) -> None:
        after = parse_msg_time("2026-08-14 13:51")
        self.assertIsNotNone(after)
        messages = [
            {
                "message_id": "om_mention",
                "create_time": "2026-08-14 13:51",
                "sender": {"id": "ou_other"},
            },
            {
                "message_id": "om_reply",
                "create_time": "2026-08-14 13:51",
                "sender": {"id": "ou_fixture_user"},
            },
        ]
        self.assertTrue(
            user_spoke_after(
                messages,
                after=after,
                user_id="ou_fixture_user",
                skip_id="om_mention",
            )
        )
        self.assertFalse(
            user_spoke_after(
                [
                    {
                        "message_id": "om_mention",
                        "create_time": "2026-08-14 13:51",
                        "sender": {"id": "ou_other"},
                    }
                ],
                after=after,
                user_id="ou_fixture_user",
                skip_id="om_mention",
            )
        )

    def test_dedupe_reply_priority_does_not_repeat_unreplied(self) -> None:
        out = dedupe_priorities(
            ["APP沟通群：主分支同步"],
            ["A8设备邮寄回来（卡人·紧急）", "回：APP沟通群：主分支同步"],
        )
        self.assertEqual(out[0], "A8设备邮寄回来（卡人·紧急）")
        self.assertEqual(out[1], "回 APP沟通群 那条（未完成）")
        self.assertEqual(sum(1 for item in out if "主分支同步" in item), 0)

    def test_long_term_skips_recent_and_priority_titles(self) -> None:
        today = datetime(2026, 8, 16, tzinfo=CN).date()
        lines = long_term_task_lines(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "summary": "A8设备邮寄回来",
                            "created_at": "2026-08-11T17:15:21+08:00",
                        },
                        {
                            "summary": "双号远程写号",
                            "created_at": "2026-02-01T10:00:00+08:00",
                        },
                    ]
                },
            },
            today=today,
            skip_titles=["A8设备邮寄回来（未完成·卡人·紧急）"],
        )
        self.assertEqual(lines, ["双号远程写号（未完成）"])

    def test_format_includes_long_term_and_link(self) -> None:
        text = format_daily_brief(
            today=datetime(2026, 8, 16, tzinfo=CN).date(),
            workday=datetime(2026, 8, 14).date(),
            progressed=["研发部周会（已结束）"],
            unreplied=["APP沟通群：同步（进行中·已追问未答完） https://applink.feishu.cn/x"],
            priorities=["A8（未完成）"],
            week_notes=[],
            long_term=["双号远程写号（未完成）"],
        )
        self.assertIn("长期待办", text)
        self.assertIn("双号远程写号", text)
        self.assertIn("applink.feishu.cn", text)

    def test_clip_line_keeps_short_and_ellipsis_long(self) -> None:
        self.assertEqual(clip_line("主分支同步", 72), "主分支同步")
        long = "甲" * 80
        clipped = clip_line(long, 72)
        self.assertTrue(clipped.endswith("…"))
        self.assertEqual(len(clipped), 73)

    def test_format_matches_aily_sections(self) -> None:
        text = format_daily_brief(
            today=datetime(2026, 7, 31, tzinfo=CN).date(),
            workday=datetime(2026, 7, 30).date(),
            progressed=["日程功能对齐（蔡俭、郭香港）（已结束）"],
            unreplied=["胡柳斌（AI品沟通群）：设备关机了APP设置为何生效（未完成·未回复）"],
            priorities=[
                {
                    "level": "P0",
                    "title": "研发部周会（15:30–16:30）",
                    "reason": "固定会议，全员",
                    "kind": "meeting",
                },
                {
                    "level": "P1",
                    "title": "回复胡柳斌：设备关机APP设置",
                    "reason": "卡测试排查",
                    "kind": "reply",
                },
            ],
            week_notes=[],
            today_agenda=["15:30–16:30 研发部周会 · 张怡佳 · 已接受"],
            long_term=["双号远程写号（未完成）"],
        )
        self.assertIn("📋 每日工作简报 · 7月31日 (周五)", text)
        self.assertIn("一、昨天小结（7月30日 周四）", text)
        self.assertIn("推进事项", text)
        self.assertIn("待处理 / 待回复（1项）", text)
        self.assertIn("1. 胡柳斌（AI品沟通群）", text)
        self.assertIn("长期待办（1项）", text)
        self.assertIn("二、今天规划（7月31日 周五）", text)
        self.assertIn("15:30–16:30 研发部周会 · 张怡佳 · 已接受", text)
        self.assertIn("优先处理", text)
        self.assertIn("P1  回复胡柳斌", text)
        self.assertIn("卡测试排查", text)
        self.assertNotIn("优先级 | 事项 | 原因", text)
        self.assertNotIn("P0  研发部周会", text)
        self.assertNotIn("applink.feishu.cn", text)

    def test_cn_day_uses_weekday(self) -> None:
        self.assertEqual(cn_day(datetime(2026, 7, 31).date()), "7月31日 (周五)")
        self.assertEqual(cn_day(datetime(2026, 8, 17).date()), "8月17日 (周一)")

    def test_agenda_line_has_range_organizer_rsvp(self) -> None:
        entries = agenda_entries(
            {
                "ok": True,
                "data": [
                    {
                        "summary": "研发部周会",
                        "start_time": {"datetime": "2026-07-31T15:30:00+08:00"},
                        "end_time": {"datetime": "2026-07-31T16:30:00+08:00"},
                        "event_organizer": {"display_name": "张怡佳"},
                        "self_rsvp_status": "accept",
                        "app_link": "https://applink.feishu.cn/client/calendar/event/detail?key=abc",
                        "vchat": {"meeting_url": "https://vc.feishu.cn/j/1"},
                    }
                ],
            }
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            format_agenda_today(entries[0]),
            "15:30–16:30 研发部周会 · 张怡佳 · 已接受",
        )
        self.assertIn("applink.feishu.cn", entries[0]["app_link"])
        self.assertEqual(entries[0]["meet_url"], "https://vc.feishu.cn/j/1")

    def test_rank_puts_meetings_first_with_reason(self) -> None:
        rows = rank_priorities(
            [
                {
                    "title": "闲逛文档",
                    "blocks": False,
                    "urgent": False,
                    "kind": "task",
                },
                {
                    "title": "研发部周会（15:30–16:30）",
                    "blocks": True,
                    "urgent": True,
                    "kind": "meeting",
                    "level": "P0",
                    "reason": "固定会议，全员",
                },
                {
                    "title": "回胡柳斌",
                    "blocks": True,
                    "urgent": True,
                    "kind": "reply",
                    "level": "P1",
                    "reason": "卡测试排查",
                },
            ]
        )
        self.assertEqual(rows[0]["title"], "研发部周会（15:30–16:30）")
        self.assertEqual(rows[0]["level"], "P0")
        self.assertEqual(rows[1]["level"], "P1")
        self.assertEqual(rows[1]["reason"], "卡测试排查")
        self.assertTrue(all("title" in row and "reason" in row for row in rows))

    def test_work_priorities_drops_meetings_when_agenda_listed(self) -> None:
        rows = work_priorities(
            [
                {"level": "P0", "title": "早会", "kind": "meeting", "reason": "固定会议"},
                {"level": "P1", "title": "回群", "kind": "reply", "reason": "未回复"},
            ],
            agenda=["09:00–09:30 早会"],
        )
        self.assertEqual([row["title"] for row in rows], ["回群"])

    def test_brief_card_is_schema2_without_pipe_table(self) -> None:
        card = brief_card(
            {
                "today": "2026-07-31",
                "workday": "2026-07-30",
                "progressed": ["日程功能对齐（已结束）"],
                "unreplied": ["胡柳斌（AI品沟通群）：设备关机"],
                "long_term": ["双号远程写号（未完成）"],
                "today_agenda": ["15:30–16:30 研发部周会 · 张怡佳 · 已接受"],
                "today_entries": [
                    {
                        "title": "研发部周会",
                        "start": "15:30",
                        "end": "16:30",
                        "organizer": "张怡佳",
                        "rsvp": "已接受",
                        "app_link": "https://applink.feishu.cn/client/calendar/event/detail?key=abc",
                        "meet_url": "https://vc.feishu.cn/j/1",
                    },
                    {
                        "title": "A6评审",
                        "start": "14:00",
                        "end": "15:00",
                        "organizer": "侯帅臣",
                        "rsvp": "待回复",
                        "app_link": "https://applink.feishu.cn/client/calendar/event/detail?key=a6",
                    },
                ],
                "priorities": [
                    {
                        "level": "P0",
                        "title": "研发部周会（15:30–16:30）",
                        "reason": "固定会议",
                        "kind": "meeting",
                    },
                    {
                        "level": "P1",
                        "title": "回复胡柳斌",
                        "reason": "卡测试排查",
                        "kind": "reply",
                        "key": "om:p1",
                        "link": "https://applink.feishu.cn/client/chat/open?openChatId=oc_x&position=om_p1",
                    },
                ],
                "week_notes": [],
                "pending": [
                    {
                        "key": "om:1",
                        "chat_name": "AI品沟通群",
                        "sender_name": "胡柳斌",
                        "text": "设备关机",
                        "tag": "未完成·未回复",
                        "link": "https://applink.feishu.cn/client/chat/open?openChatId=oc_x",
                    }
                ],
            }
        )
        blob = str(card)
        self.assertIn("每日工作简报", blob)
        self.assertIn("column_set", blob)
        self.assertIn("回复胡柳斌", blob)
        self.assertIn("applink.feishu.cn/client/calendar/event/detail?key=abc", blob)
        self.assertNotIn("进会", blob)
        self.assertIn("15:30–16:30", blob)
        self.assertIn("已接受", blob)
        self.assertNotIn("优先级 | 事项 | 原因", blob)
        self.assertNotIn("空栏表示", blob)
        self.assertIn("回复胡柳斌", blob)
        self.assertNotIn("研发部周会（15:30–16:30）", blob)
        self.assertIn("'tag': 'button'", blob)
        # Priorities with a link should render an "打开消息" jump button
        # alongside the "完成" callback button.
        self.assertIn("'content': '打开消息'", blob)
        self.assertIn("'content': '完成'", blob)
        self.assertIn("https://applink.feishu.cn/client/chat/open?openChatId=oc_x&position=om_p1", blob)
        self.assertFalse(any(el.get("tag") == "tag" for el in card["elements"]))
        self.assertFalse(any(el.get("tag") == "note" for el in card["elements"]))

    def test_brief_card_strips_html_from_pending(self) -> None:
        card = brief_card(
            {
                "today": "2026-08-18",
                "workday": "2026-08-17",
                "progressed": [],
                "unreplied": [],
                "long_term": [],
                "today_agenda": [],
                "today_entries": [],
                "priorities": [],
                "week_notes": [],
                "pending": [
                    {
                        "key": "om:p",
                        "chat_name": "APP沟通群",
                        "sender_name": "杨庆海",
                        "text": "<p>@吴梦晨 记得提前熟悉飞猫听见功能</p>",
                        "tag": "未完成·未回复",
                        "link": "https://applink.feishu.cn/x",
                    }
                ],
            }
        )
        blob = str(card)
        self.assertNotIn("<p>", blob)
        self.assertNotIn("</p>", blob)
        self.assertIn("记得提前熟悉飞猫听见功能", blob)

    def test_brief_card_scrubs_image_md_and_keeps_yesterday(self) -> None:
        card = brief_card(
            {
                "today": "2026-08-25",
                "workday": "2026-08-24",
                "progressed": ["早会（已结束）"],
                "unreplied": [
                    "杨庆海（APP沟通群）：@吴梦晨 ![Image](img_v3_abc)（进行中·已追问未答完）"
                ],
                "long_term": [],
                "today_agenda": [],
                "today_entries": [
                    {
                        "title": "A6 - 再次讨论",
                        "start": "14:00",
                        "end": "15:00",
                        "organizer": "侯帅臣",
                        "rsvp": "已接受",
                        "app_link": "https://applink.feishu.cn/client/calendar/event/detail?key=a6",
                    }
                ],
                "priorities": [],
                "week_notes": [],
                "pending": [],
            },
            followups="- 陈星丞（AR101缺陷）大模型切换",
        )
        blob = str(card)
        self.assertIn("一、昨天小结", blob)
        self.assertIn("二、今天规划", blob)
        self.assertIn("要跟的活", blob)
        self.assertIn("陈星丞", blob)
        self.assertIn("[图片]", blob)
        self.assertNotIn("![Image]", blob)
        self.assertNotIn("img_v3_abc", blob)

    def test_pending_brief_line_scrubs_image_and_keeps_link(self) -> None:
        line = pending_brief_line(
            {
                "sender_name": "杨庆海",
                "chat_name": "APP沟通群",
                "text": "@吴梦晨 ![Image](img_v3_x)",
                "tag": "进行中·已追问未答完",
                "link": "https://applink.feishu.cn/client/chat/open?openChatId=oc_x",
            }
        )
        self.assertIn("[图片]", line)
        self.assertNotIn("![Image]", line)
        self.assertIn("applink.feishu.cn", line)

    def test_day_work_card_matches_brief_chrome(self) -> None:
        card = day_work_card(
            kind="tomorrow",
            day=datetime(2026, 8, 18).date(),
            entries=[
                {
                    "title": "早会",
                    "start": "09:00",
                    "end": "09:30",
                    "organizer": "张夏夏",
                    "rsvp": "已接受",
                    "app_link": "https://applink.feishu.cn/client/calendar/event/detail?key=x",
                }
            ],
            followups="- 立哥：写份体验总结给我",
            task_lines=[],
        )
        blob = str(card)
        self.assertEqual(card["header"]["title"]["content"], "明日安排")
        self.assertIn("明日日程", blob)
        self.assertIn("已接受", blob)
        self.assertIn("green", blob)
        self.assertIn("要跟的活", blob)
        self.assertIn("体验总结", blob)
        self.assertIn("applink.feishu.cn", blob)
        self.assertIn("column_set", blob)

    def test_pending_brief_line_is_who_chat_quote(self) -> None:
        line = pending_brief_line(
            {
                "sender_name": "胡柳斌",
                "chat_name": "AI品沟通群",
                "text": "设备关机了APP设置为何生效",
                "tag": "未完成·未回复",
                "link": "https://applink.feishu.cn/x",
            }
        )
        self.assertEqual(
            line,
            "胡柳斌（AI品沟通群）：设备关机了APP设置为何生效（未完成·未回复） 跳转：https://applink.feishu.cn/x",
        )


class SchedulePlistTests(unittest.TestCase):
    def test_plist_is_0900_daily(self) -> None:
        body = plist_body("/tmp/feishu", "/usr/bin:/bin")
        self.assertIn(BRIEF_LABEL, body)
        self.assertIn("<key>Hour</key>", body)
        self.assertIn("<integer>9</integer>", body)
        self.assertIn("<key>Minute</key>", body)
        self.assertIn("brief", body)
        self.assertIn("--push", body)

    def test_serve_plist_keeps_alive(self) -> None:
        body = serve_plist_body(
            "/tmp/feishu",
            "/usr/bin:/bin",
            {"FEISHU_PARTNER_USER_OPEN_ID": "ou_x"},
        )
        self.assertIn(SERVE_LABEL, body)
        self.assertIn("<string>serve</string>", body)
        self.assertIn("<key>KeepAlive</key>", body)
        self.assertIn("<true/>", body)
        self.assertIn("ou_x", body)
        self.assertNotIn("StartCalendarInterval", body)


class PushOnceTests(unittest.TestCase):
    def test_concurrent_push_brief_sends_one_card(self) -> None:
        import tempfile
        import threading
        import time
        from pathlib import Path
        from unittest.mock import patch

        from partner.office.brief import CN_TZ, push_brief

        now = datetime(2026, 8, 18, 9, 0, tzinfo=CN_TZ)
        data = {
            "today": "2026-08-18",
            "workday": "2026-08-17",
            "progressed": [],
            "unreplied": [],
            "priorities": [],
            "week_notes": [],
            "today_agenda": [],
            "today_entries": [],
            "long_term": [],
            "pending": [],
        }
        barrier = threading.Barrier(2)
        sends: list[str] = []

        def slow_card(*_a: object, **_k: object) -> str:
            time.sleep(0.05)
            sends.append("card")
            return "已发送。"

        def run() -> None:
            barrier.wait(timeout=2)
            push_brief(now=now)

        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "brief-sent.on"
            with patch("partner.office.brief.STAMP", stamp):
                with patch("partner.office.brief._collect", return_value=data):
                    with patch(
                        "partner.office.followup.followups_for_command",
                        return_value="",
                    ):
                        with patch(
                            "partner.office.followup.followup_items_for_command",
                            return_value=[],
                        ):
                                with patch("partner.actions.send_card", side_effect=slow_card):
                                    with patch("partner.actions.send_text"):
                                        threads = [
                                            threading.Thread(target=run)
                                            for _ in range(2)
                                        ]
                                        for thread in threads:
                                            thread.start()
                                        for thread in threads:
                                            thread.join(timeout=3)
        self.assertEqual(sends, ["card"])


class ThirdPartyAckTests(unittest.TestCase):
    """A third party's ack (e.g. 吴梦晨 "ok") settles an @ thread the user followed up on."""

    def _msg(self, who: str, text: str, minute: int, mid: str = "") -> dict:
        return {
            "message_id": mid or f"om_{minute}",
            "sender": {"id": who, "name": "x"},
            "create_time": f"2026-08-28T10:{minute:02d}:00",
            "content": {"text": text},
        }

    def test_third_party_ok_settles_thread(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        msgs = [
            self._msg(USER_OPEN_ID, "我喊上少华和梦晨一起", 5),
            self._msg("ou_wumengchen", "ok", 7),
        ]
        self.assertTrue(third_party_ack(msgs, after=after))

    def test_bot_ok_is_ignored(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        msgs = [
            {
                "message_id": "om_bot",
                "sender": {"id": "bot_1", "type": "bot", "name": "小助手"},
                "create_time": "2026-08-28T10:07:00",
                "content": {"text": "ok"},
            }
        ]
        self.assertFalse(third_party_ack(msgs, after=after))

    def test_non_participant_ok_does_not_settle(self) -> None:
        """A random passer-by's ack must not close a thread they weren't in."""
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        msgs = [
            self._msg("ou_stranger", "ok", 7),
        ]
        participants = {USER_OPEN_ID, "ou_wumengchen"}
        self.assertFalse(
            third_party_ack(msgs, after=after, participants=participants)
        )

    def test_participant_by_mention_ok_settles(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        msgs = [
            self._msg("ou_wumengchen", "ok", 7),
        ]
        participants = {USER_OPEN_ID, "ou_wumengchen"}
        self.assertTrue(
            third_party_ack(msgs, after=after, participants=participants)
        )

    def test_third_party_ok_with_punctuation(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        self.assertTrue(third_party_ack([self._msg("ou_a", "好的。", 3)], after=after))
        self.assertTrue(third_party_ack([self._msg("ou_a", "👍", 3)], after=after))
        self.assertTrue(third_party_ack([self._msg("ou_a", "收到～", 3)], after=after))

    def test_affirmative_phrase_counts(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        for text in ("没问题", "可以", "我知道了", "同意", "准时参加"):
            self.assertTrue(
                third_party_ack([self._msg("ou_a", text, 3)], after=after), msg=text
            )

    def test_own_message_does_not_count(self) -> None:
        """Only the user spoke → not a third-party ack."""
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        msgs = [self._msg(USER_OPEN_ID, "我喊上少华和梦晨一起", 5)]
        self.assertFalse(third_party_ack(msgs, after=after))

    def test_message_before_window_ignored(self) -> None:
        after = datetime(2026, 8, 28, 10, 30, tzinfo=CN)
        self.assertFalse(third_party_ack([self._msg("ou_a", "ok", 5)], after=after))

    def test_third_party_question_is_not_ack(self) -> None:
        """A follow-up question is not a resolution."""
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        for text in ("什么时候？", "几点开始吗", "哪个会议室"):
            self.assertFalse(
                third_party_ack([self._msg("ou_a", text, 3)], after=after), msg=text
            )

    def test_exclude_id_skips_origin_message(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        msgs = [self._msg("ou_a", "ok", 1, mid="om_origin")]
        self.assertFalse(third_party_ack(msgs, after=after, exclude_id="om_origin"))


class ThreadParticipantTests(unittest.TestCase):
    """_thread_participants licenses only people @'d or already in the thread."""

    def _msg(self, who: str, text: str, minute: int) -> dict:
        return {
            "message_id": f"om_{minute}",
            "sender": {"id": who, "name": "x"},
            "create_time": f"2026-08-28T10:{minute:02d}:00",
            "content": {"text": text},
        }

    def test_participants_include_mentioned_open_id(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        item = {
            "message_id": "om_origin",
            "mentions": [
                {"id": {"open_id": "ou_wumengchen"}, "name": "吴梦晨"},
                {"id": {"open_id": "ou_shaohua"}, "name": "周少华"},
            ],
        }
        part = _thread_participants(
            item=item,
            messages=[],
            after=after,
            exclude_id="om_origin",
            user_id=USER_OPEN_ID,
        )
        self.assertIn("ou_wumengchen", part)
        self.assertIn("ou_shaohua", part)
        self.assertIn("name:吴梦晨", part)

    def test_participants_include_thread_speakers(self) -> None:
        after = datetime(2026, 8, 28, 10, 0, tzinfo=CN)
        item = {"message_id": "om_origin"}
        msgs = [
            self._msg("ou_qiu", "那5点约个会", 1),
            self._msg("ou_wumengchen", "ok", 2),
        ]
        part = _thread_participants(
            item=item,
            messages=msgs,
            after=after,
            exclude_id="om_origin",
            user_id=USER_OPEN_ID,
        )
        self.assertIn("ou_qiu", part)
        self.assertIn("ou_wumengchen", part)
        self.assertNotIn(USER_OPEN_ID, part)


class FetchMentionTests(unittest.TestCase):
    """_fetch_group_mentions detects truncation and fetch failures."""

    def _make_hit(self, i: int) -> dict:
        return {
            "message_id": f"om_{i}",
            "sender": {"id": f"ou_{i}"},
            "content": {"text": f"hit {i}"},
        }

    def test_hits_below_cap_are_not_truncated(self) -> None:
        from unittest.mock import patch

        payload = {
            "ok": True,
            "data": {"items": [self._make_hit(i) for i in range(5)]},
        }
        with patch("partner.office.brief.run_lark", return_value=payload):
            hits, truncated, failed = _fetch_group_mentions(
                datetime(2026, 8, 28, 0, 0, tzinfo=CN),
                datetime(2026, 8, 28, 23, 59, tzinfo=CN),
            )
        self.assertEqual(len(hits), 5)
        self.assertFalse(truncated)
        self.assertFalse(failed)

    def test_hits_equal_cap_are_truncated(self) -> None:
        from unittest.mock import patch

        payload = {
            "ok": True,
            "data": {"items": [self._make_hit(i) for i in range(20)]},
        }
        with patch("partner.office.brief.run_lark", return_value=payload):
            hits, truncated, failed = _fetch_group_mentions(
                datetime(2026, 8, 28, 0, 0, tzinfo=CN),
                datetime(2026, 8, 28, 23, 59, tzinfo=CN),
            )
        self.assertEqual(len(hits), 20)
        self.assertTrue(truncated)
        self.assertFalse(failed)

    def test_failure_returns_empty_and_fetch_failed(self) -> None:
        from unittest.mock import patch

        with patch(
            "partner.office.brief.run_lark", return_value={"ok": False}
        ):
            hits, truncated, failed = _fetch_group_mentions(
                datetime(2026, 8, 28, 0, 0, tzinfo=CN),
                datetime(2026, 8, 28, 23, 59, tzinfo=CN),
            )
        self.assertEqual(hits, [])
        self.assertFalse(truncated)
        self.assertTrue(failed)


class FormatWarningTests(unittest.TestCase):
    def test_warnings_rendered_in_text_brief(self) -> None:
        text = format_daily_brief(
            today=datetime(2026, 8, 18, tzinfo=CN).date(),
            workday=datetime(2026, 8, 17).date(),
            progressed=["早会（已结束）"],
            unreplied=[],
            priorities=[],
            week_notes=[],
            notes=["⚠️ 部分 @ 消息采集失败，今天的待处理列表可能不完整"],
        )
        self.assertIn("部分 @ 消息采集失败", text)


if __name__ == "__main__":
    unittest.main()
