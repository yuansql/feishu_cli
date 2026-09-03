"""消息图片/文件理解：附件下载 + OCR + 文本提取。"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from partner.core.events import extract_inbound_message
from partner.office import media_understand as mu


class TestMediaRef(unittest.TestCase):
    def test_image_message(self):
        ref = mu.media_ref_from_content("image", {"image_key": "img_v3_abc"})
        self.assertEqual(ref["file_key"], "img_v3_abc")
        self.assertEqual(ref["rtype"], "image")

    def test_file_message(self):
        ref = mu.media_ref_from_content(
            "file", {"file_key": "file_v3_xyz", "file_name": "方案.docx"}
        )
        self.assertEqual(ref["file_key"], "file_v3_xyz")
        self.assertEqual(ref["file_name"], "方案.docx")
        self.assertEqual(ref["rtype"], "file")

    def test_json_string_content(self):
        ref = mu.media_ref_from_content("file", '{"file_key":"file_v3_q","file_name":"a.txt"}')
        self.assertEqual(ref["file_key"], "file_v3_q")

    def test_text_message_returns_empty(self):
        self.assertEqual(mu.media_ref_from_content("text", {"text": "hi"}), {})
        self.assertEqual(mu.media_ref_from_content("image", {}), {})


class TestExtractText(unittest.TestCase):
    def test_plain_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "笔记.md"
            p.write_text("# 标题\n第一行内容", encoding="utf-8")
            self.assertIn("第一行内容", mu.extract_file_text(p))

    def test_docx_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.docx"
            xml = (
                '<?xml version="1.0"?><w:document xmlns:w="http://x">'
                "<w:body><w:p><w:r><w:t>会议纪要内容</w:t></w:r></w:p></w:body></w:document>"
            )
            with zipfile.ZipFile(str(p), "w") as zf:
                zf.writestr("word/document.xml", xml)
            self.assertIn("会议纪要内容", mu.extract_file_text(p))

    def test_xlsx_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.xlsx"
            xml = (
                '<?xml version="1.0"?><sst xmlns="http://x">'
                "<si><t>华东区</t></si><si><t>销售额</t></si></sst>"
            )
            with zipfile.ZipFile(str(p), "w") as zf:
                zf.writestr("xl/sharedStrings.xml", xml)
            text = mu.extract_file_text(p)
            self.assertIn("华东区", text)
            self.assertIn("销售额", text)

    def test_unknown_ext_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.bin"
            p.write_bytes(b"\x00\x01\x02")
            self.assertEqual(mu.extract_file_text(p), "")


class TestOcr(unittest.TestCase):
    def test_no_swift_returns_empty(self):
        with patch.object(mu.shutil, "which", return_value=None):
            self.assertEqual(mu.ocr_image(Path("/tmp/x.png")), "")

    def test_ocr_output(self):
        class FakeProc:
            returncode = 0
            stdout = "识别出的文字\n第二行"

        with patch.object(mu.shutil, "which", return_value="/usr/bin/swift"), patch.object(
            mu, "_ensure_ocr_script", return_value=Path("/tmp/ocr.swift")
        ), patch.object(mu.subprocess, "run", return_value=FakeProc()):
            text = mu.ocr_image(Path("/tmp/x.png"))
        self.assertIn("识别出的文字", text)


class TestUnderstandFlow(unittest.TestCase):
    def test_image_with_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "img_v3_abc.png"
            img.write_bytes(b"png")
            with patch.object(mu, "download_resource", return_value=img), patch.object(
                mu, "ocr_image", return_value="报表截图文字"
            ):
                note = mu.understand_media_message("image", {"image_key": "img_v3_abc"}, "om_1")
        self.assertIn("【图片内容】", note)
        self.assertIn("报表截图文字", note)

    def test_file_with_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "file_v3_xyz.md"
            f.write_text("文件正文内容", encoding="utf-8")
            with patch.object(mu, "download_resource", return_value=f):
                note = mu.understand_media_message(
                    "file", {"file_key": "file_v3_xyz", "file_name": "笔记.md"}, "om_1"
                )
        self.assertIn("【文件内容】", note)
        self.assertIn("文件正文内容", note)

    def test_download_failed(self):
        with patch.object(mu, "download_resource", return_value=None):
            note = mu.understand_media_message("image", {"image_key": "img_v3_abc"}, "om_1")
        self.assertIn("下载失败", note)

    def test_not_media(self):
        self.assertEqual(mu.understand_media_message("text", {"text": "hi"}, "om_1"), "")

    def test_unreadable_file_reports_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "file_v3_xyz.bin"
            f.write_bytes(b"\x00" * 2048)
            with patch.object(mu, "download_resource", return_value=f):
                note = mu.understand_media_message(
                    "file", {"file_key": "file_v3_xyz", "file_name": "a.bin"}, "om_1"
                )
        self.assertIn("暂时读不了", note)


class TestEventsExtension(unittest.TestCase):
    def test_inbound_carries_msg_type_and_content(self):
        msg = extract_inbound_message(
            {
                "chat_id": "oc_x",
                "chat_type": "p2p",
                "message_id": "om_1",
                "message_type": "image",
                "content": {"image_key": "img_v3_abc"},
                "sender_type": "user",
                "sender_id": "ou_u",
            }
        )
        self.assertIsNotNone(msg)
        self.assertEqual(msg.msg_type, "image")
        self.assertEqual(msg.content.get("image_key"), "img_v3_abc")
        self.assertEqual(msg.text, "")

    def test_text_message_still_works(self):
        msg = extract_inbound_message(
            {
                "chat_id": "oc_x",
                "chat_type": "p2p",
                "message_id": "om_2",
                "content": {"text": "你好"},
                "sender_type": "user",
                "sender_id": "ou_u",
            }
        )
        self.assertEqual(msg.text, "你好")
        self.assertEqual(msg.msg_type, "")

    def test_json_string_content_parsed(self):
        msg = extract_inbound_message(
            {
                "chat_id": "oc_x",
                "chat_type": "p2p",
                "message_id": "om_3",
                "message_type": "file",
                "content": json.dumps({"file_key": "file_v3_q", "file_name": "a.txt"}),
                "sender_type": "user",
                "sender_id": "ou_u",
            }
        )
        self.assertEqual(msg.content.get("file_key"), "file_v3_q")


if __name__ == "__main__":
    unittest.main()
