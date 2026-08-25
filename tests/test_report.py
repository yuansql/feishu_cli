from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from partner.office.report import (
    audio_from_goal,
    chart_from_goal,
    write_chart_svg,
    write_tone_wav,
)
from partner.runtime.tool_registry import execute_tool


class ReportArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FEISHU_PARTNER_REPORTS"] = str(Path(self.tmp.name) / "reports")

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_REPORTS", None)
        self.tmp.cleanup()

    def test_svg_chart(self) -> None:
        path = write_chart_svg(title="进度", series="预发:3,回滚:1")
        self.assertTrue(path.is_file())
        body = path.read_text(encoding="utf-8")
        self.assertIn("<svg", body)
        self.assertIn("预发", body)

    def test_wav_tone(self) -> None:
        path = write_tone_wav(title="叮", seconds=0.2)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 100)

    def test_registry_tools(self) -> None:
        chart = execute_tool(
            "chart_write",
            {"goal": "本周", "series": "A:2,B:4"},
            confirmed=False,
        )
        self.assertIn("SVG", chart)
        audio = execute_tool("audio_write", {"goal": "提示"}, confirmed=False)
        self.assertIn("WAV", audio)
        self.assertIn("SVG", chart_from_goal("x", "a:1"))
        self.assertIn("WAV", audio_from_goal("y", "0.15"))


if __name__ == "__main__":
    unittest.main()
