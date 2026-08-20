from __future__ import annotations

import os
import tempfile
import unittest

from partner.aily import (
    CAPABILITIES,
    alignment_score,
    alignment_target_score,
    alignment_text,
)


class AilyAlignmentTests(unittest.TestCase):
    def test_alignment_is_local_only_and_shows_roadmap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FEISHU_PARTNER_AILY_CONFIG"] = os.path.join(tmp, "parity.json")
            text = alignment_text()
        os.environ.pop("FEISHU_PARTNER_AILY_CONFIG", None)
        self.assertIn("本地完全独立", text)
        self.assertIn("功能不低于 Aily", text)
        self.assertIn("Agentic 循环与动态规划", text)
        self.assertIn("异步长任务与隔离环境", text)
        self.assertNotIn("Aily 连接验收", text)

    def test_weights_total_100_and_target_crosses_90(self) -> None:
        self.assertEqual(sum(item.weight for item in CAPABILITIES), 100)
        self.assertLess(alignment_score(), 90)
        self.assertGreaterEqual(alignment_target_score(), 90)


if __name__ == "__main__":
    unittest.main()
