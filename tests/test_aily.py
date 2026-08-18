from __future__ import annotations

import unittest

from partner.aily import alignment_text, status_counts


class AilyAlignmentTests(unittest.TestCase):
    def test_alignment_mentions_scope_and_plan(self) -> None:
        text = alignment_text()
        self.assertIn("飞书 Aily / 豆包工作伙伴", text)
        self.assertIn("不克隆官方工作台", text)
        self.assertIn("任务规划/任务模式", text)
        self.assertIn("只生成计划", text)
        self.assertIn("异步沙箱", text)
        self.assertIn("`feishu plan <目标>`", text)

    def test_status_counts_are_non_empty(self) -> None:
        counts = status_counts()
        self.assertGreater(counts.get("接近", 0), 0)
        self.assertGreater(counts.get("部分", 0), 0)
        self.assertGreater(counts.get("缺口", 0), 0)


if __name__ == "__main__":
    unittest.main()
