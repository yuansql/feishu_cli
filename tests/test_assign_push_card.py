"""Tests for assign-push interactive card."""

from __future__ import annotations

import unittest

from partner.office.followup import assign_push_card


class AssignPushCardTests(unittest.TestCase):
    def test_plain_text_no_card(self) -> None:
        card = assign_push_card({"text": "记得处理这个需求", "id": "fu:1"})
        self.assertIsNotNone(card)
        self.assertEqual(card["header"]["title"]["content"], "📝 有人派活")
        # One action: "完成"
        actions = card["elements"][1]["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["text"]["content"], "完成")
        self.assertEqual(actions[0]["value"]["key"], "fu:1")

    def test_card_tag_with_link(self) -> None:
        text = '<card title="打卡月报(08/01-08/31)">上月异常考勤共 1 次，请及时处理 异常考勤：缺卡1次\n[查看详情](https://applink.feishu.cn/client/mini_program/open?a=xxx)</card>'
        card = assign_push_card({"text": text, "id": "fu:2"})
        self.assertIsNotNone(card)
        md = card["elements"][0]["text"]["content"]
        self.assertIn("**打卡月报(08/01-08/31)**", md)
        self.assertIn("缺卡1次", md)
        self.assertNotIn("[查看详情]", md)
        actions = card["elements"][1]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["text"]["content"], "查看详情")
        self.assertEqual(actions[0]["url"], "https://applink.feishu.cn/client/mini_program/open?a=xxx")
        self.assertEqual(actions[1]["text"]["content"], "完成")

    def test_markdown_link_without_card_tag(self) -> None:
        text = "飞书文档更新通知 [查看详情](https://docs.feishu.cn/x)"
        card = assign_push_card({"text": text, "id": "fu:3"})
        self.assertIsNotNone(card)
        md = card["elements"][0]["text"]["content"]
        self.assertIn("飞书文档更新通知", md)
        actions = card["elements"][1]["actions"]
        self.assertEqual(actions[0]["url"], "https://docs.feishu.cn/x")

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(assign_push_card({"text": "", "id": "fu:4"}))


if __name__ == "__main__":
    unittest.main()
