"""Seam: daily brief — last workday, omit empty, 3–5 priorities, no padding."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import unittest

from partner.brief import (
    approval_priority_lines,
    clip_line,
    dedupe_priorities,
    format_daily_brief,
    last_workday,
    long_term_task_lines,
    parse_msg_time,
    pick_priorities,
    reply_status,
    user_spoke_after,
)
from partner.schedule import BRIEF_LABEL, SERVE_LABEL, plist_body, serve_plist_body


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
                "sender": {"id": "ou_757b70ff62056f5c427a56f67b903ba9"},
            },
        ]
        self.assertTrue(
            user_spoke_after(
                messages,
                after=after,
                user_id="ou_757b70ff62056f5c427a56f67b903ba9",
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
                user_id="ou_757b70ff62056f5c427a56f67b903ba9",
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


if __name__ == "__main__":
    unittest.main()
