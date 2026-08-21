"""P2P Hermes may FETCH allowlisted facts; group never enters that loop."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
import unittest
from unittest.mock import patch

from partner.actions import dispatch, partner_reply
from partner.ops.cli import main
from partner.routing.intents import Intent, parse_intent


class PartnerLoopTests(unittest.TestCase):
    def test_fetch_then_answer(self) -> None:
        replies = ["FETCH: today", "今天先把 A6 提测收掉。"]

        def fake_rewrite(_user: str, _facts: str, **_kwargs: object) -> str:
            return replies.pop(0)

        with patch("partner.actions.rewrite_partner", side_effect=fake_rewrite):
            with patch("partner.actions._facts_for", return_value="日程：A6 提测"):
                text = partner_reply("今天咋样", "先看一眼", Intent(action="unknown"))
        self.assertEqual(text, "今天先把 A6 提测收掉。")
        self.assertFalse(replies)

    def test_group_skips_partner_loop(self) -> None:
        with patch("partner.actions.partner_reply") as partner:
            with patch("partner.actions._facts_for", return_value="搜到两份文档"):
                out = dispatch(
                    Intent(action="search", query="周报"),
                    user_text="搜 周报",
                    channel="group",
                )
        partner.assert_not_called()
        self.assertEqual(out, "搜到两份文档")

    def test_unknown_complex_prefers_hermes(self) -> None:
        with patch("partner.actions.hermes_available", return_value=True):
            with patch(
                "partner.actions.hermes_partner_turn",
                return_value="我先看了 digest，今天优先跟测试群。",
            ) as hermes:
                out = dispatch(
                    Intent(action="unknown", query="帮我梳理一下今天该优先跟谁"),
                    user_text="帮我梳理一下今天该优先跟谁",
                    channel="p2p",
                    chat_id="oc_hermes_unknown",
                )
        hermes.assert_called_once()
        self.assertIn("优先跟测试群", out)

    def test_unknown_complex_falls_back_nudge_without_hermes(self) -> None:
        with patch("partner.actions.hermes_available", return_value=False):
            out = dispatch(
                Intent(action="unknown", query="帮我梳理一下今天该优先跟谁呀？？"),
                user_text="帮我梳理一下今天该优先跟谁呀？？",
                channel="p2p",
                chat_id="oc_nudge",
            )
        self.assertIn("今天", out)
        self.assertIn("待办", out)

    def test_short_today_skips_hermes_partner_turn(self) -> None:
        with patch("partner.actions.hermes_partner_turn") as hermes:
            with patch("partner.actions.today_text", return_value="今天：日程A"):
                out = dispatch(
                    Intent(action="today"),
                    user_text="今天",
                    channel="p2p",
                    chat_id="oc_today_short",
                )
        hermes.assert_not_called()
        self.assertIn("日程A", out)


    def test_ssl_stdout_keeps_facts(self) -> None:
        ssl_err = (
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of "
            "protocol (_ssl.c:1016)"
        )
        facts = "【张三最近怎么说】\n- 张三：方案可以，明天提测"
        with patch("partner.actions.rewrite_partner", return_value=ssl_err):
            text = partner_reply("张三的回复如何？", facts, Intent(action="person", query="张三"))
        self.assertIn("方案可以", text)
        self.assertNotIn("SSL", text)
        self.assertNotIn("_ssl.c", text)

    def test_plan_dispatch_uses_today_context_without_partner_loop(self) -> None:
        with patch.dict(os.environ, {"FEISHU_PARTNER_NO_LLM": "1"}):
            with patch("partner.actions.today_text", return_value="【待办】\n- A6 上线前检查"):
                with patch("partner.actions.partner_reply") as partner:
                    out = dispatch(
                        Intent(action="plan", query="A6 上线"),
                        user_text="规划 A6 上线",
                        channel="p2p",
                        force_facts=True,
                    )
        partner.assert_not_called()
        self.assertIn("任务规划", out)
        self.assertIn("A6 上线", out)

    def test_read_my_chats_routes_to_chats(self) -> None:
        with patch("partner.actions.chats_text", return_value="会话 3 个：") as chats:
            out = dispatch(
                Intent(action="chats", query=""),
                user_text="读取我的聊天",
                channel="p2p",
                chat_id="oc_p2p",
            )
        chats.assert_called_once_with("")
        self.assertEqual(out, "会话 3 个：")

    def test_parse_read_my_chats_as_chats(self) -> None:
        self.assertEqual(parse_intent("读取我的聊天,还是不行").action, "chats")


class PartnerCliTests(unittest.TestCase):
    def test_aily_command_prints_alignment(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = main(["aily"])

        self.assertEqual(code, 0)
        self.assertIn("飞书 Aily 能力对标", output.getvalue())

    def test_plan_command_uses_goal_and_today_context(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {"FEISHU_PARTNER_NO_LLM": "1"}):
            with patch(
                "partner.actions.today_text",
                return_value="【待办】\n- A6 上线前检查",
            ):
                with redirect_stdout(output):
                    code = main(["plan", "A6", "上线"])

        self.assertEqual(code, 0)
        self.assertIn("任务规划：A6 上线", output.getvalue())
        self.assertIn("A6 上线前检查", output.getvalue())
