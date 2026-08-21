from __future__ import annotations

from datetime import datetime

import unittest

from partner.actions import _week_bounds
from partner.routing.intents import parse_intent


class WeekBoundsTests(unittest.TestCase):
    def test_monday_to_sunday(self) -> None:
        friday = datetime.fromisoformat("2026-08-14T17:00:00+08:00")
        start, end = _week_bounds(friday)
        self.assertEqual(start.date().isoformat(), "2026-08-10")
        self.assertEqual(end.date().isoformat(), "2026-08-16")


class AskWeeklyIntentTests(unittest.TestCase):
    def test_user_screenshot_phrase(self) -> None:
        self.assertEqual(parse_intent("本周的周报").action, "weekly")
        self.assertEqual(parse_intent("搜 周报").action, "search")
        long = "你需要全力查一下我本周的工作内容 你写的有点人机的感觉需要像人写的一样 继续"
        self.assertEqual(parse_intent(long).action, "weekly")
        self.assertEqual(parse_intent("继续").action, "weekly")
        self.assertEqual(parse_intent("本周工作内容").action, "weekly")
        self.assertEqual(
            parse_intent(
                "你需要全力查一下我本周的工作内容\n你写的有点人机的感觉需要像人写的一样\n继续"
            ).action,
            "weekly",
        )

    def test_week_activity_and_write_weekly_aliases(self) -> None:
        self.assertEqual(parse_intent("本周干了什么?").action, "weekly")
        self.assertEqual(parse_intent("这周忙了啥").action, "weekly")
        self.assertEqual(parse_intent("写个周报").action, "write_weekly")
        self.assertEqual(parse_intent("帮我写周报").action, "write_weekly")
        self.assertEqual(parse_intent("写周报").action, "write_weekly")
        self.assertEqual(parse_intent("本周的周报").action, "weekly")

    def test_next_week_plan_is_weekly_not_search(self) -> None:
        for text in ("下周计划", "吴梦晨的下周计划", "下周安排"):
            intent = parse_intent(text)
            self.assertEqual(intent.action, "weekly", text)
            self.assertEqual(intent.query, "next", text)
        self.assertEqual(parse_intent("搜 下周计划").action, "search")
        self.assertEqual(parse_intent("本周的周报").query, "")
        self.assertEqual(parse_intent("本周计划").action, "weekly")
        self.assertEqual(parse_intent("搜本周").action, "search")
        self.assertEqual(parse_intent("搜本周").query, "本周")
        self.assertEqual(parse_intent("A6").action, "unknown")
        self.assertEqual(parse_intent("本周计划有吗?").action, "weekly")
