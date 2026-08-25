"""Seam: 收到消息先回馈，再慢慢查。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from partner.core.ack import ACK_EMOJI, ack_line, should_ack_text
from partner.ops.serve import reply_user


class AckLineTests(unittest.TestCase):
    def test_tasks_says_checking(self) -> None:
        self.assertEqual(ack_line("tasks"), "在查待办…")
        self.assertEqual(ack_line("person"), "在看她怎么回的…")
        self.assertEqual(ack_line("plan"), "在拆计划…")
        self.assertEqual(ack_line("aily"), "在整理对齐项…")
        self.assertEqual(ack_line("write_doc"), "在写文档…")
        self.assertEqual(ack_line("today_recap"), "在读今天的消息…")
        self.assertEqual(ack_line("weekly"), "在看周报…")
        self.assertEqual(ack_line("weekly_tasks"), "在写本周任务…")
        self.assertEqual(ack_line("unknown"), "收到，在办…")
        self.assertTrue(should_ack_text("tasks"))
        self.assertFalse(should_ack_text("help"))
        self.assertEqual(ACK_EMOJI, "OnIt")

    def test_reply_user_acks_before_dispatch(self) -> None:
        order: list[str] = []

        def fake_react(message_id: str, emoji: str = "OnIt") -> str:
            order.append(f"react:{message_id}")
            return "ok"

        def fake_send(chat_id: str, text: str, *, as_identity: str = "bot") -> str:
            order.append(f"send:{text[:12]}")
            return "已发送。"

        def fake_dispatch(*_a: object, **_k: object) -> str:
            order.append("dispatch")
            return "未完成待办 1 条"

        with patch("partner.ops.serve.add_reaction", side_effect=fake_react):
            with patch("partner.ops.serve.send_text", side_effect=fake_send):
                with patch("partner.ops.serve.dispatch", side_effect=fake_dispatch):
                    from partner.core.events import InboundMessage

                    reply_user(
                        InboundMessage(
                            chat_id="oc_p2p",
                            chat_type="p2p",
                            text="待办",
                            message_id="om_1",
                            sender_type="user",
                        )
                    )
        self.assertEqual(order[0], "react:om_1")
        self.assertEqual(order[1], "send:在查待办…")
        self.assertEqual(order[2], "dispatch")
        self.assertTrue(any(item.startswith("send:未完成待办") for item in order))

    def test_reply_user_sends_brief_card_not_plain_text(self) -> None:
        order: list[str] = []

        def fake_card(intent: object, chat_id: str) -> bool:
            order.append(f"card:{chat_id}")
            return True

        def fake_dispatch(*_a: object, **_k: object) -> str:
            order.append("dispatch")
            return "should-not-send"

        with patch("partner.ops.serve.add_reaction", return_value="ok"):
            with patch("partner.ops.serve.send_text", return_value="已发送。"):
                with patch("partner.ops.serve.send_style_card", side_effect=fake_card):
                    with patch("partner.ops.serve.dispatch", side_effect=fake_dispatch):
                        from partner.core.events import InboundMessage

                        reply_user(
                            InboundMessage(
                                chat_id="oc_p2p",
                                chat_type="p2p",
                                text="明天任务",
                                message_id="om_card",
                                sender_type="user",
                            )
                        )
        self.assertIn("card:oc_p2p", order)
        self.assertNotIn("dispatch", order)

    def test_reply_retries_transient_send(self) -> None:
        from partner.core.events import InboundMessage

        n_reply = {"n": 0}

        def fake_send(_chat: str, text: str, *, as_identity: str = "bot") -> str:
            if text.startswith("未完成"):
                n_reply["n"] += 1
                if n_reply["n"] == 1:
                    return "飞书权威失败，本地不假装成功。\n原因：timeout"
                return "已发送。"
            return "已发送。"

        with patch("partner.ops.serve.add_reaction", return_value="ok"):
            with patch("partner.ops.serve.send_text", side_effect=fake_send) as send:
                with patch("partner.ops.serve.dispatch", return_value="未完成待办 1 条"):
                    reply_user(
                        InboundMessage(
                            chat_id="oc_p2p",
                            chat_type="p2p",
                            text="待办",
                            message_id="om_2",
                            sender_type="user",
                        )
                    )
        reply_sends = [
            call.args[1]
            for call in send.call_args_list
            if call.args[1].startswith("未完成")
        ]
        self.assertEqual(reply_sends, ["未完成待办 1 条", "未完成待办 1 条"])

    def test_reply_retries_ssl_body_with_facts(self) -> None:
        from partner.core.events import InboundMessage

        ssl_err = (
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of "
            "protocol (_ssl.c:1016)"
        )
        sent: list[str] = []

        def fake_send(_chat: str, text: str, *, as_identity: str = "bot") -> str:
            sent.append(text)
            return "已发送。"

        def fake_dispatch(*_a: object, **kwargs: object) -> str:
            if kwargs.get("force_facts"):
                return "【张三最近怎么说】\n- 张三：方案可以"
            return ssl_err

        with patch("partner.ops.serve.add_reaction", return_value="ok"):
            with patch("partner.ops.serve.send_text", side_effect=fake_send):
                with patch("partner.ops.serve.dispatch", side_effect=fake_dispatch):
                    reply_user(
                        InboundMessage(
                            chat_id="oc_p2p",
                            chat_type="p2p",
                            text="张三的回复如何？",
                            message_id="om_3",
                            sender_type="user",
                        )
                    )
        bodies = [text for text in sent if not text.endswith("…")]
        self.assertTrue(any("方案可以" in text for text in bodies))
        self.assertFalse(any("SSL" in text or "_ssl.c" in text for text in bodies))


if __name__ == "__main__":
    unittest.main()
