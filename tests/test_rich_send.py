"""Tests for rich-text send routing (markdown links/card wrappers)."""

from __future__ import annotations

import unittest
from unittest import mock

from partner.office.messaging import (
    looks_like_rich_text,
    send_markdown,
    send_message,
)


class RichTextDetectionTests(unittest.TestCase):
    def test_markdown_link_is_rich(self) -> None:
        self.assertTrue(looks_like_rich_text("[查看详情](https://x.com)"))

    def test_plain_text_is_not_rich(self) -> None:
        self.assertFalse(looks_like_rich_text("记得处理一下这个需求"))

    def test_card_tag_is_rich(self) -> None:
        self.assertTrue(looks_like_rich_text('<card title="周报">缺卡1次</card>'))

    def test_html_a_tag_is_rich(self) -> None:
        self.assertTrue(looks_like_rich_text('<a href="https://x.com">link</a>'))


class SendMessageRoutingTests(unittest.TestCase):
    @mock.patch("partner.office.messaging.send_markdown")
    @mock.patch("partner.office.messaging.send_text")
    def test_rich_text_routes_to_markdown(
        self, mock_text: mock.MagicMock, mock_md: mock.MagicMock
    ) -> None:
        mock_md.return_value = "已发送。"
        send_message("oc_xxx", "[查看详情](https://applink.feishu.cn/x)")
        mock_md.assert_called_once()
        mock_text.assert_not_called()

    @mock.patch("partner.office.messaging.send_markdown")
    @mock.patch("partner.office.messaging.send_text")
    def test_plain_text_routes_to_text(
        self, mock_text: mock.MagicMock, mock_md: mock.MagicMock
    ) -> None:
        mock_text.return_value = "已发送。"
        send_message("oc_xxx", " plain 消息")
        mock_text.assert_called_once()
        mock_md.assert_not_called()


class SendMarkdownTests(unittest.TestCase):
    @mock.patch("partner.office.messaging.run_lark", return_value={"ok": True})
    def test_send_markdown_uses_markdown_flag(self, mock_run: mock.MagicMock) -> None:
        result = send_markdown("oc_xxx", "**bold** [link](https://x.com)")
        self.assertEqual(result, "已发送。")
        args = mock_run.call_args[0][0]
        self.assertIn("--markdown", args)
        self.assertNotIn("--text", args)


if __name__ == "__main__":
    unittest.main()
