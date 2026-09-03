from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from partner.office.rag import (
    append_chunks,
    chunk_markdown,
    expand_query,
    hashed_vector,
    index_stats_text,
    rag_answer,
    replace_chunks,
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

    def test_hashed_vector_is_unit_length(self) -> None:
        vec = hashed_vector("A6 预发验证")
        self.assertEqual(len(vec), 64)
        self.assertAlmostEqual(sum(x * x for x in vec), 1.0, places=5)

    def test_replace_chunks_drops_old_text(self) -> None:
        append_chunks(
            doc_id="doc-sync",
            title="方案",
            url="https://example.com/sync",
            markdown="旧稿只谈预发验证。",
        )
        replace_chunks(
            doc_id="doc-sync",
            title="方案",
            url="https://example.com/sync",
            markdown="新稿只谈回滚策略。",
        )
        hits = retrieve("回滚", top_k=3)
        self.assertTrue(any("回滚" in hit.text for hit in hits))
        self.assertFalse(any("预发" in hit.text for hit in hits))

    def test_bm25_idf_monotonic(self) -> None:
        from partner.office.rag import bm25_idf

        # 词越稀有 idf 越高；全 corpus 都出现则趋近 0
        self.assertGreater(bm25_idf(1, 100), bm25_idf(50, 100))
        self.assertGreaterEqual(bm25_idf(100, 100), 0.0)
        self.assertEqual(bm25_idf(0, 0), 0.0)

    def test_bm25_score_prefers_relevant(self) -> None:
        from partner.office.rag import bm25_score

        df = {"预发": 1, "验证": 2}
        doc_hit = ["A6", "预发", "验证", "上线"]
        doc_miss = ["周报", "会议", "总结"]
        hit = bm25_score(["预发"], doc_hit, df, n_docs=10, avgdl=4.0)
        miss = bm25_score(["预发"], doc_miss, df, n_docs=10, avgdl=4.0)
        self.assertGreater(hit, 0.0)
        self.assertEqual(miss, 0.0)

    def test_bm25_length_normalization(self) -> None:
        from partner.office.rag import bm25_score

        df = {"目标": 1}
        short = ["目标", "甲"]
        long_doc = ["目标"] + ["填充"] * 200
        s_short = bm25_score(["目标"], short, df, n_docs=10, avgdl=2.0)
        s_long = bm25_score(["目标"], long_doc, df, n_docs=10, avgdl=2.0)
        self.assertGreater(s_short, s_long)

    def test_rrf_fusion_merges_rankings(self) -> None:
        from partner.office.rag import rrf_fusion

        fused = rrf_fusion(["a", "b", "c"], ["b", "d"])
        # b 两路都上榜（1/62+1/61）最高；a 第一路第 1（1/61）次之
        # d 第二路第 2（1/62）再次；c 第一路第 3（1/63）最低
        self.assertGreater(fused["b"], fused["a"])
        self.assertAlmostEqual(fused["a"], 1.0 / 61, places=6)
        self.assertAlmostEqual(fused["d"], 1.0 / 62, places=6)
        self.assertGreater(fused["d"], fused["c"])

    def test_hybrid_retrieve_ranks_keyword_and_vector(self) -> None:
        append_chunks(
            doc_id="doc-bm25",
            title="发布手册",
            url="https://example.com/1",
            markdown="预发环境验证清单：权限、回滚、监控。",
        )
        append_chunks(
            doc_id="doc-vec",
            title="上线流程",
            url="https://example.com/2",
            markdown="上线前必须做 staging 检查，确认无误后再推全量。",
        )
        hits = retrieve("预发验证", top_k=5)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].doc_id, "doc-bm25")
        # 融合分是 RRF 量纲
        self.assertGreater(hits[0].score, 0.01)


if __name__ == "__main__":
    unittest.main()
