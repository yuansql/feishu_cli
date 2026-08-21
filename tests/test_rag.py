from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from partner.office.rag import (
    append_chunks,
    chunk_markdown,
    expand_query,
    index_stats_text,
    rag_answer,
    retrieve,
)


class RagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = Path(self.tmp.name) / "rag"
        self.terms = Path(self.tmp.name) / "terminology.json"
        os.environ["FEISHU_PARTNER_RAG_DIR"] = str(self.rag)
        os.environ["FEISHU_PARTNER_TERMINOLOGY"] = str(self.terms)
        self.terms.write_text(
            """
            {"terms":[{"term":"A6","aliases":["a6项目"],"definition":"测试项目A6"}]}
            """,
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        os.environ.pop("FEISHU_PARTNER_RAG_DIR", None)
        os.environ.pop("FEISHU_PARTNER_TERMINOLOGY", None)
        self.tmp.cleanup()

    def test_chunk_markdown_splits_paragraphs(self) -> None:
        text = "第一段内容。\n\n第二段内容，包含更多文字。"
        chunks = chunk_markdown(text, chunk_size=20, overlap=5)
        self.assertGreaterEqual(len(chunks), 2)

    def test_expand_query_with_terminology(self) -> None:
        _, notes = expand_query("a6项目进度")
        self.assertTrue(any("A6" in n for n in notes))

    def test_index_and_retrieve(self) -> None:
        append_chunks(
            doc_id="doc1",
            title="A6方案",
            url="https://example.com/doc",
            markdown="A6 上线前需要完成预发验证。\n\n风险项包括权限与回滚。",
        )
        hits = retrieve("A6 预发", top_k=3)
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("预发", hits[0].text)

    def test_rag_answer_formats_evidence(self) -> None:
        append_chunks(
            doc_id="doc2",
            title="制度",
            url="",
            markdown="报销制度：差旅需提前申请。",
        )
        body = rag_answer("报销制度")
        self.assertIn("召回片段", body)
        self.assertIn("差旅", body)

    def test_stats_empty(self) -> None:
        self.assertIn("0 个切片", index_stats_text())


if __name__ == "__main__":
    unittest.main()
