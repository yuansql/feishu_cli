"""表格分析技能：取数 → 统计 → 结论 → 图表 → 回写。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.office import sheet_analysis as sa


def _sheets_read_payload(values, truncated=False):
    return {
        "ok": True,
        "data": {"values": values, "truncated": truncated, "total_rows": len(values)},
    }


def _sheets_info_payload(sheet_id="abc123", title="销售明细"):
    return {
        "ok": True,
        "data": {
            "spreadsheet": {"token": "shtcnTESTTOKEN1"},
            "sheets": [{"sheet_id": sheet_id, "title": title, "index": 0}],
        },
    }


SALES = [
    ["区域", "销售", "金额", "备注"],
    ["华东", "张三", 1200, "大单"],
    ["华东", "李四", 800, ""],
    ["华北", "王五", 500, "续费"],
    ["华南", "赵六", 1, "退款"],
    ["华北", "钱七", 300, ""],
    ["华南", "孙八", 700, ""],
]


class TestParseTarget(unittest.TestCase):
    def test_url_with_sheet_and_range(self):
        t = sa.parse_target(
            "https://example.larksuite.com/sheets/shtcnABC123?sheet=xyz789&range=A1:C10"
        )
        self.assertEqual(t["kind"], "sheets")
        self.assertEqual(t["token"], "shtcnABC123")
        self.assertEqual(t["sheet_id"], "xyz789")
        self.assertEqual(t["range"], "A1:C10")

    def test_url_fragment_style(self):
        t = sa.parse_target("https://x.feishu.cn/sheets/shtcnDEF456#sheet=aaa")
        self.assertEqual(t["sheet_id"], "aaa")

    def test_bare_token(self):
        t = sa.parse_target("shtcnGHI789")
        self.assertEqual(t["kind"], "sheets")
        self.assertEqual(t["token"], "shtcnGHI789")
        self.assertEqual(t["sheet_id"], "")

    def test_token_with_range(self):
        t = sa.parse_target("shtcnJKL012!B2:D9")
        self.assertEqual(t["token"], "shtcnJKL012")
        self.assertEqual(t["range"], "B2:D9")

    def test_bitable_spec(self):
        t = sa.parse_target("bitable:OUDhbl3qZarm:tblwwvOxUA")
        self.assertEqual(t["kind"], "bitable")
        self.assertEqual(t["token"], "OUDhbl3qZarm")
        self.assertEqual(t["table"], "tblwwvOxUA")

    def test_garbage_returns_empty(self):
        self.assertEqual(sa.parse_target(""), {})
        self.assertEqual(sa.parse_target("随便一段话"), {})


class TestCoerceNumber(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(sa.coerce_number(42), 42.0)
        self.assertEqual(sa.coerce_number("3.14"), 3.14)

    def test_formatted(self):
        self.assertEqual(sa.coerce_number("1,234"), 1234.0)
        self.assertEqual(sa.coerce_number("¥12.5"), 12.5)
        self.assertEqual(sa.coerce_number("45%"), 45.0)
        self.assertEqual(sa.coerce_number("-8"), -8.0)

    def test_rejects_text_and_dates(self):
        self.assertIsNone(sa.coerce_number("2026-08-24"))
        self.assertIsNone(sa.coerce_number("张三"))
        self.assertIsNone(sa.coerce_number(""))
        self.assertIsNone(sa.coerce_number(None))
        self.assertIsNone(sa.coerce_number(True))


class TestProfilesAndGroups(unittest.TestCase):
    def setUp(self):
        self.headers = SALES[0]
        self.rows = SALES[1:]

    def test_column_profiles(self):
        profiles = sa.column_profiles(self.headers, self.rows)
        by_name = {p["name"]: p for p in profiles}
        self.assertEqual(by_name["金额"]["kind"], "numeric")
        self.assertEqual(by_name["金额"]["count"], 6)
        self.assertAlmostEqual(by_name["金额"]["sum"], 3501.0)
        self.assertEqual(by_name["区域"]["kind"], "text")
        self.assertEqual(by_name["区域"]["distinct"], 3)

    def test_group_stats(self):
        groups = sa.group_stats(self.headers, self.rows, "区域", "金额")
        self.assertEqual(groups[0]["label"], "华东")
        self.assertAlmostEqual(groups[0]["sum"], 2000.0)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(len(groups), 3)

    def test_group_stats_bad_col(self):
        self.assertEqual(sa.group_stats(self.headers, self.rows, "不存在的列", "金额"), [])

    def test_pick_chart_series(self):
        profiles = sa.column_profiles(self.headers, self.rows)
        group_col, value_col, groups = sa.pick_chart_series(self.headers, self.rows, profiles)
        self.assertEqual(group_col, "区域")
        self.assertEqual(value_col, "金额")
        self.assertGreaterEqual(len(groups), 2)


class TestFetchTable(unittest.TestCase):
    def test_fetch_sheets(self):
        calls = []

        def fake_run_lark(argv, **_kw):
            calls.append(argv)
            if argv[1] == "+info":
                return _sheets_info_payload()
            return _sheets_read_payload(SALES)

        with patch.object(sa, "run_lark", side_effect=fake_run_lark):
            table = sa.fetch_table("shtcnTESTTOKEN1")
        self.assertEqual(table["kind"], "sheets")
        self.assertEqual(table["title"], "销售明细")
        self.assertEqual(table["headers"], SALES[0])
        self.assertEqual(len(table["rows"]), 6)
        self.assertEqual(calls[0][1], "+info")
        self.assertEqual(calls[1][1], "+read")

    def test_fetch_sheets_error(self):
        with patch.object(
            sa, "run_lark", return_value={"ok": False, "error": {"message": "no scope"}}
        ):
            table = sa.fetch_table("shtcnTESTTOKEN1")
        self.assertIn("no scope", table["error"])

    def test_fetch_bitable(self):
        payload = {
            "ok": True,
            "data": {
                "fields": ["姓名", "分数"],
                "data": [["张三", 90], ["李四", 60]],
                "record_id_list": ["rec1", "rec2"],
            },
        }
        with patch.object(sa, "run_lark", return_value=payload):
            table = sa.fetch_table("bitable:BASE123:TBL456")
        self.assertEqual(table["kind"], "bitable")
        self.assertEqual(table["headers"], ["分数", "姓名"])
        self.assertEqual(len(table["rows"]), 2)

    def test_fetch_bad_source(self):
        table = sa.fetch_table("不是表格链接")
        self.assertIn("没认出", table["error"])


class TestConclusion(unittest.TestCase):
    def _table(self):
        profiles = sa.column_profiles(SALES[0], SALES[1:])
        group_col, value_col, groups = sa.pick_chart_series(SALES[0], SALES[1:], profiles)
        table = {"title": "销售明细", "headers": SALES[0], "rows": SALES[1:]}
        return table, profiles, groups, group_col, value_col

    def test_llm_conclusion(self):
        table, profiles, groups, gc, vc = self._table()
        material = sa.build_material(table, profiles, groups, gc, vc)
        with patch.object(
            sa,
            "_conclusion",
            return_value="华东合计 2,000 最高，建议加大投入。",
        ):
            text = sa._conclusion("哪个区域最高", material)
        self.assertIn("华东", text)

    def test_no_llm_env_disables(self):
        old = os.environ.get("FEISHU_PARTNER_NO_LLM")
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        try:
            self.assertEqual(sa._conclusion("q", "material"), "")
        finally:
            if old is None:
                os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
            else:
                os.environ["FEISHU_PARTNER_NO_LLM"] = old

    def test_fallback_conclusion(self):
        table, profiles, groups, gc, vc = self._table()
        text = sa.fallback_conclusion(table, profiles, groups, gc, vc)
        self.assertIn("6 行", text)
        self.assertIn("华东", text)
        self.assertIn("建议", text)


class TestChart(unittest.TestCase):
    def test_write_chart_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sa, "CHART_DIR", Path(tmp)):
                groups = [
                    {"label": "华东", "count": 2, "sum": 2000.0, "mean": 1000.0},
                    {"label": "华北", "count": 2, "sum": 800.0, "mean": 400.0},
                ]
                path = sa.write_chart_svg(
                    title="销售明细", group_col="区域", value_col="金额", groups=groups
                )
                self.assertTrue(path.exists())
                svg = path.read_text(encoding="utf-8")
                self.assertIn("<svg", svg)
                self.assertIn("华东", svg)
                self.assertIn("2,000", svg)
                self.assertIn("区域 → 金额", svg)

    def test_svg_to_png_missing_tool(self):
        with patch.object(sa.shutil, "which", return_value=None):
            self.assertIsNone(sa.svg_to_png(Path("/tmp/x.svg")))


class TestWriteBack(unittest.TestCase):
    def test_write_back_ok(self):
        calls = []

        def fake_run_lark(argv, **_kw):
            calls.append(argv)
            if argv[1] == "+create-sheet":
                return {
                    "ok": True,
                    "data": {"sheet": {"sheet_id": "newsheet1", "title": "分析结果"}},
                }
            return {"ok": True, "data": {"updated_range": "newsheet1!A1"}}

        with patch.object(sa, "run_lark", side_effect=fake_run_lark):
            result = sa.write_back("shtcnTESTTOKEN1", [["列", "总和"], ["金额", "3,301"]])
        self.assertIn("已回写", result)
        self.assertEqual(calls[0][1], "+create-sheet")
        self.assertEqual(calls[1][1], "+write")
        self.assertIn("newsheet1", " ".join(calls[1]))

    def test_write_back_create_fails(self):
        with patch.object(
            sa, "run_lark", return_value={"ok": False, "error": {"message": "denied"}}
        ):
            result = sa.write_back("shtcnTESTTOKEN1", [["a"]])
        self.assertIn("denied", result)

    def test_write_back_no_sheet_id(self):
        payload = {"ok": True, "data": {"spreadsheet": {"token": "shtcnTESTTOKEN1"}}}
        with patch.object(sa, "run_lark", return_value=payload):
            result = sa.write_back("shtcnTESTTOKEN1", [["a"]])
        self.assertIn("sheet_id", result)


class TestAnalyze(unittest.TestCase):
    def test_analyze_full_pipeline_no_llm(self):
        old = os.environ.get("FEISHU_PARTNER_NO_LLM")
        os.environ["FEISHU_PARTNER_NO_LLM"] = "1"
        try:
            with (
                patch.object(
                    sa,
                    "run_lark",
                    side_effect=lambda argv, **_kw: (
                        _sheets_info_payload()
                        if argv[1] == "+info"
                        else _sheets_read_payload(SALES)
                    ),
                ),
                tempfile.TemporaryDirectory() as tmp,
                patch.object(sa, "CHART_DIR", Path(tmp)),
            ):
                text = sa.analyze(
                    "shtcnTESTTOKEN1", question="哪个区域金额最高", write_back_flag=False
                )
        finally:
            if old is None:
                os.environ.pop("FEISHU_PARTNER_NO_LLM", None)
            else:
                os.environ["FEISHU_PARTNER_NO_LLM"] = old
        self.assertIn("销售明细·分析", text)
        self.assertIn("华东", text)  # fallback conclusion mentions top group
        self.assertIn("图表：", text)

    def test_analyze_write_back(self):
        calls = []

        def fake_run_lark(argv, **_kw):
            calls.append(argv)
            if argv[1] == "+info":
                return _sheets_info_payload()
            if argv[1] == "+create-sheet":
                return {"ok": True, "data": {"sheet": {"sheet_id": "ns1"}}}
            if argv[1] == "+write":
                return {"ok": True, "data": {"updated_range": "ns1!A1"}}
            return _sheets_read_payload(SALES)

        with (
            patch.object(sa, "run_lark", side_effect=fake_run_lark),
            tempfile.TemporaryDirectory() as tmp,
            patch.object(sa, "CHART_DIR", Path(tmp)),
            patch.object(sa, "_conclusion", return_value=""),
        ):
            text = sa.analyze("shtcnTESTTOKEN1", write_back_flag=True, with_chart=False)
        self.assertIn("已回写", text)
        verbs = [c[1] for c in calls]
        self.assertIn("+create-sheet", verbs)
        self.assertIn("+write", verbs)

    def test_analyze_bitable_no_write_back_support(self):
        payload = {
            "ok": True,
            "data": {"fields": ["组", "值"], "data": [["A", 10], ["B", 20]]},
        }
        with (
            patch.object(sa, "run_lark", return_value=payload),
            patch.object(sa, "_conclusion", return_value=""),
            tempfile.TemporaryDirectory() as tmp,
            patch.object(sa, "CHART_DIR", Path(tmp)),
        ):
            text = sa.analyze("bitable:B1:T1", write_back_flag=True, with_chart=False)
        self.assertIn("多维表暂不支持自动回写", text)

    def test_analyze_fetch_error(self):
        with patch.object(sa, "run_lark", return_value={"ok": False, "error": {"message": "boom"}}):
            text = sa.analyze("shtcnTESTTOKEN1")
        self.assertIn("读表格信息失败", text)


class TestCli(unittest.TestCase):
    def test_cli_dispatch(self):
        with (
            patch.object(sa, "analyze", return_value="【表格·分析】\n结论") as fake_analyze,
            patch("builtins.print") as fake_print,
        ):
            code = sa.sheet_analysis_cli(
                ["shtcnTESTTOKEN1", "--question", "哪个区域最高", "--no-chart"]
            )
        self.assertEqual(code, 0)
        fake_analyze.assert_called_once_with(
            "shtcnTESTTOKEN1",
            question="哪个区域最高",
            write_back_flag=False,
            chat_id="",
            with_chart=False,
        )
        fake_print.assert_called_once_with("【表格·分析】\n结论")


if __name__ == "__main__":
    unittest.main()
