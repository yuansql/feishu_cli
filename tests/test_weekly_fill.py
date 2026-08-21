"""Weekly template fill + curated bullets (no raw chat dumps)."""

from __future__ import annotations

import unittest

from partner.office.recap import curated_work_buckets, _fallback_summary
from partner.office.weekly_fill import (
    extract_weekly_doc_url,
    parse_weekly_slots,
    _person_heading_slice,
)
from partner.routing.intents import parse_intent


class WeeklyFillIntentTests(unittest.TestCase):
    def test_fill_with_url_is_write_weekly(self) -> None:
        raw = (
            "https://it82yw7fgr.feishu.cn/wiki/QLg1wnATIiVTuXkUiHecNag7nSh "
            "填写吴梦晨的周报"
        )
        intent = parse_intent(raw)
        self.assertEqual(intent.action, "write_weekly")
        self.assertIn("wiki/QLg1wnATIiVTuXkUiHecNag7nSh", intent.query)

    def test_identity_not_search(self) -> None:
        self.assertEqual(parse_intent("你知道我是谁吗").action, "identity")


class CuratedBucketsTests(unittest.TestCase):
    def test_no_raw_chat_quotes_in_done(self) -> None:
        ctx = """周期：测试
## 马丽敏
[E001 id=om_1][08-17 13:40][我] 后面就按照这个走, 修改的UI 入文档
[E002 id=om_2][08-17 13:46][我] 你这个第一轮就要想到的,我是根据你UI来的
## 邱俊立
[E003 id=om_3][08-19 09:00][我] 邱俊立（邱俊立）好好体验下M8p，写份体验总结给我 已经完成
"""
        text = _fallback_summary(ctx)
        self.assertNotIn("第一轮就要想到", text)
        self.assertNotIn("马丽敏：", text)
        done, progress, pending = curated_work_buckets(ctx)
        self.assertTrue(any("M8p" in x for x in done))
        blob = "\n".join(done + progress + pending)
        self.assertNotIn("第一轮就要想到", blob)


class SlotParseTests(unittest.TestCase):
    def test_parse_empty_lis(self) -> None:
        xml = """
<h3 id="doxcnPerson"><cite type="user" user-name="吴梦晨"></cite></h3>
<h4 id="doxcnD"><b>已完成</b></h4>
<ol><li id="doxcnA" seq="1"></li><li id="doxcnB"></li><li id="doxcnC">已有内容</li></ol>
<h4 id="doxcnP"><b>进行中</b></h4>
<ol><li id="doxcnD1" seq="1"></li></ol>
<h4 id="doxcnN"><span>下周计划</span></h4>
<ol><li id="doxcnE1" seq="1">  </li></ol>
"""
        slice_xml = _person_heading_slice(xml, "吴梦晨")
        slots = parse_weekly_slots(slice_xml)
        self.assertEqual(slots["done"], ["doxcnA", "doxcnB"])
        self.assertEqual(slots["progress"], ["doxcnD1"])
        self.assertEqual(slots["next"], ["doxcnE1"])

    def test_extract_url(self) -> None:
        u = extract_weekly_doc_url(
            "看这个 https://x.feishu.cn/wiki/AbCdEfGhIj 填写周报"
        )
        self.assertTrue(u.endswith("/wiki/AbCdEfGhIj"))


if __name__ == "__main__":
    unittest.main()
