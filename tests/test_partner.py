"""P2P Hermes may FETCH allowlisted facts; group never enters that loop."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from partner.actions import dispatch, partner_reply
from partner.intents import Intent


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

    def test_p2p_send_stays_facts(self) -> None:
        with patch("partner.actions.partner_reply") as partner:
            with patch("partner.actions._facts_for", return_value="已发送"):
                out = dispatch(
                    Intent(action="send", query="hi", chat_id="oc_x"),
                    user_text="hi",
                    channel="p2p",
                )
        partner.assert_not_called()
        self.assertEqual(out, "已发送")
