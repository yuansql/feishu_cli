"""PPT 生成技能：大纲 → pptx → 上传飞书。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.office import ppt_gen as pg

OUTLINE = """# 项目周报
2026 年第 36 周进展
## 背景与目标
- 本周聚焦交付
- 对齐 OKR
## 核心进展
- 完成表格分析技能
- 全量 489 测试通过
## 下一步计划
- PPT 技能联调
- RAG 升级
"""


class TestParseOutline(unittest.TestCase):
    def test_full_parse(self):
        deck = pg.parse_outline(OUTLINE)
        self.assertEqual(deck["title"], "项目周报")
        self.assertEqual(deck["subtitle"], "2026 年第 36 周进展")
        self.assertEqual(len(deck["slides"]), 3)
        self.assertEqual(deck["slides"][0]["heading"], "背景与目标")
        self.assertEqual(deck["slides"][0]["bullets"], ["本周聚焦交付", "对齐 OKR"])

    def test_bullet_cap(self):
        text = "# t\n## s\n" + "\n".join(f"- 要点{i}" for i in range(10))
        deck = pg.parse_outline(text)
        self.assertEqual(len(deck["slides"][0]["bullets"]), pg.MAX_BULLETS_PER_SLIDE)

    def test_slide_cap(self):
        text = "# t\n" + "\n".join(f"## 页{i}\n- 点" for i in range(30))
        deck = pg.parse_outline(text)
        self.assertEqual(len(deck["slides"]), pg.MAX_SLIDES)

    def test_empty(self):
        deck = pg.parse_outline("")
        self.assertEqual(deck["title"], "")
        self.assertEqual(deck["slides"], [])

    def test_star_bullets_and_plain_lines(self):
        deck = pg.parse_outline("# t\n## s\n* 要点A\n普通行也算要点\n")
        self.assertEqual(deck["slides"][0]["bullets"], ["要点A", "普通行也算要点"])


class TestOutlineSources(unittest.TestCase):
    def test_no_llm_env_disables(self):
        old = os.environ.get("FEISHU_PARTNER_NO_LLM")
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        try:
            self.assertEqual(pg.outline_via_llm("主题"), "")
        finally:
            if old is None:
                os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
            else:
                os.environ["FEISHU_PARTNER_NO_LLM"] = old

    def test_fallback_outline_parseable(self):
        deck = pg.parse_outline(pg.fallback_outline("季度规划"))
        self.assertEqual(deck["title"], "季度规划")
        self.assertGreaterEqual(len(deck["slides"]), 3)


class TestBuildPptx(unittest.TestCase):
    def test_build_and_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pg, "ppt_dir", lambda: Path(tmp)):
                deck = pg.parse_outline(OUTLINE)
                path = pg.build_pptx(deck)
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 1000)
                from pptx import Presentation

                prs = Presentation(str(path))
                self.assertEqual(
                    len(prs.slides.__iter__.__self__._sldIdLst), 4
                )  # 标题页 + 3 内容页

    def test_build_with_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pg, "ppt_dir", lambda: Path(tmp)):
                deck = pg.parse_outline("# t\n## s\n- 点\n")
                path = pg.build_pptx(deck, filename="我的演示")
                self.assertEqual(path.name, "我的演示.pptx")


class TestUpload(unittest.TestCase):
    def test_upload_ok(self):
        payload = {"ok": True, "data": {"result": {"url": "https://x.feishu.cn/slides/abc"}}}
        with patch.object(pg, "run_lark", return_value=payload) as mock_run:
            result = pg.upload_slides(Path("/tmp/x.pptx"), name="周报")
        self.assertIn("https://x.feishu.cn/slides/abc", result)
        argv = mock_run.call_args[0][0]
        self.assertIn("--type", argv)
        self.assertIn("slides", argv)
        self.assertIn("--name", argv)

    def test_upload_error(self):
        with patch.object(
            pg, "run_lark", return_value={"ok": False, "error": {"message": "denied"}}
        ):
            result = pg.upload_slides(Path("/tmp/x.pptx"))
        self.assertIn("denied", result)

    def test_pick_url_nested(self):
        self.assertEqual(
            pg._pick_url({"ok": True, "data": {"task": {"url": "https://a.b/c"}}}),
            "https://a.b/c",
        )
        self.assertEqual(pg._pick_url({"ok": True, "data": {}}), "")


class TestGeneratePpt(unittest.TestCase):
    def test_user_outline_no_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pg, "ppt_dir", lambda: Path(tmp)):
                text = pg.generate_ppt("", outline_text=OUTLINE)
        self.assertIn("【项目周报】共 3 页", text)
        self.assertIn("大纲来源：用户提供", text)
        self.assertIn("未上传", text)
        self.assertIn("已生成：", text)

    def test_llm_disabled_falls_back(self):
        old = os.environ.get("FEISHU_PARTNER_NO_LLM")
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(pg, "ppt_dir", lambda: Path(tmp)):
                    text = pg.generate_ppt("季度规划")
        finally:
            if old is None:
                os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
            else:
                os.environ["FEISHU_PARTNER_NO_LLM"] = old
        self.assertIn("【季度规划】", text)
        self.assertIn("大纲来源：模板骨架", text)

    def test_upload_flow(self):
        payload = {"ok": True, "data": {"url": "https://x.feishu.cn/slides/zzz"}}
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(pg, "ppt_dir", lambda: Path(tmp)),
                patch.object(pg, "run_lark", return_value=payload),
            ):
                text = pg.generate_ppt("", outline_text=OUTLINE, upload=True)
        self.assertIn("已上传为飞书幻灯片", text)
        self.assertIn("https://x.feishu.cn/slides/zzz", text)

    def test_empty_outline_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pg, "ppt_dir", lambda: Path(tmp)):
                text = pg.generate_ppt("", outline_text="# 只有标题\n")
        self.assertIn("没有解析出任何页面", text)


class TestCli(unittest.TestCase):
    def test_cli_dispatch(self):
        with (
            patch.object(pg, "generate_ppt", return_value="done") as fake,
            patch("builtins.print") as fake_print,
        ):
            code = pg.ppt_cli(["周报", "-o", "# t\n## s\n- 点\n", "--upload"])
        self.assertEqual(code, 0)
        fake.assert_called_once_with(
            "周报",
            outline_text="# t\n## s\n- 点\n",
            materials="",
            upload=True,
            name="",
        )
        fake_print.assert_called_once_with("done")

    def test_cli_at_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# 文件大纲\n## s\n- 点\n")
            fpath = f.name
        try:
            with (
                patch.object(pg, "generate_ppt", return_value="done") as fake,
                patch("builtins.print"),
            ):
                code = pg.ppt_cli(["主题", "-o", f"@{fpath}"])
            self.assertEqual(code, 0)
            self.assertIn("# 文件大纲", fake.call_args[1]["outline_text"])
        finally:
            os.unlink(fpath)


if __name__ == "__main__":
    unittest.main()
