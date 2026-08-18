"""Seam: 群聊防遗忘 — 截止日 / 对接人 B / 转交 / 待跟进账 / 表格艾特。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from partner.events import extract_card_action, extract_inbound_message
from partner.followup import (
    apply_action,
    digest_buckets,
    extract_assignee_b,
    extract_due,
    format_digest_text,
    followups_for_command,
    format_assign_push,
    format_open_followups,
    in_chat_watch_window,
    ingest,
    load_items,
    looks_like_work_assign,
    record_mentions_user,
    save_items,
    should_scan_bitable,
    should_sync_user_chats,
    weekly_rows,
)
from partner.followup_card import digest_card
from partner.intents import parse_intent
from partner.llm import should_partner


CN = timezone(timedelta(hours=8))
USER = "ou_user_wmc"
BOT = "ou_bot"
WANG = "ou_wang"
LI = "ou_li"
MON = date(2026, 8, 17)  # Monday


def _group(
    text: str,
    *,
    mentions: list | None = None,
    sender_id: str = "ou_other",
    sender_name: str = "张三",
    message_id: str = "om_x",
):
    return extract_inbound_message(
        {
            "chat_id": "oc_g",
            "chat_type": "group",
            "chat_name": "研发群",
            "content": text,
            "message_id": message_id,
            "sender_type": "user",
            "sender_id": sender_id,
            "sender_name": sender_name,
            "mentions": mentions or [],
        }
    )


class DueDateTests(unittest.TestCase):
    def test_relative_and_weekday(self) -> None:
        self.assertEqual(extract_due("明天给我答复", MON), date(2026, 8, 18))
        self.assertEqual(extract_due("后天确认", MON), date(2026, 8, 19))
        self.assertEqual(extract_due("今天给个说法", MON), MON)
        self.assertEqual(extract_due("这周五前处理", MON), date(2026, 8, 21))
        self.assertEqual(extract_due("周一给我答复", MON), MON)
        self.assertEqual(extract_due("下周一同步", MON), date(2026, 8, 24))
        self.assertEqual(extract_due("8月20日给答复", MON), date(2026, 8, 20))
        self.assertEqual(extract_due("8月20号看一下", MON), date(2026, 8, 20))

    def test_plain_weekday_rolls_forward(self) -> None:
        friday = date(2026, 8, 21)
        self.assertEqual(extract_due("周一给我答复", friday), date(2026, 8, 24))

    def test_no_due_is_none(self) -> None:
        self.assertIsNone(extract_due("请王五处理一下设备", MON))


class AssigneeBTests(unittest.TestCase):
    def test_please_handle_not_asker(self) -> None:
        name, oid = extract_assignee_b(
            "请王五处理退换货",
            mentions=(),
            user_open_id=USER,
            bot_open_id=BOT,
        )
        self.assertEqual(name, "王五")
        self.assertEqual(oid, "")

    def test_mention_binds_id(self) -> None:
        name, oid = extract_assignee_b(
            "@_user_1 请王五处理",
            mentions=((WANG, "王五"), (USER, "吴梦晨")),
            user_open_id=USER,
            bot_open_id=BOT,
        )
        self.assertEqual(name, "王五")
        self.assertEqual(oid, WANG)

    def test_at_user_without_b_is_self(self) -> None:
        name, oid = extract_assignee_b(
            "@_user_1 看一下这个",
            mentions=((USER, "吴梦晨"),),
            user_open_id=USER,
            bot_open_id=BOT,
        )
        self.assertEqual(oid, USER)


class IngestLedgerTests(unittest.TestCase):
    def test_assign_b_not_asker(self) -> None:
        msg = _group(
            "请王五处理，周一给我答复",
            mentions=[{"id": WANG, "name": "王五"}],
            message_id="om_b",
        )
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            item = ingest(
                msg,
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 17, 10, 0, tzinfo=CN),
            )
            assert item is not None
            self.assertEqual(item["kind"], "assign_b")
            self.assertEqual(item["assignee_id"], WANG)
            self.assertEqual(item["assignee_name"], "王五")
            self.assertEqual(item["asker_id"], "ou_other")
            self.assertEqual(item["due"], "2026-08-17")
            self.assertEqual(item["status"], "open")

    def test_self_transfer_when_user_ats_other(self) -> None:
        msg = _group(
            "@_user_1 这事你跟一下",
            mentions=[{"id": LI, "name": "李四"}],
            sender_id=USER,
            sender_name="吴梦晨",
            message_id="om_t",
        )
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            item = ingest(
                msg,
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 17, 11, 0, tzinfo=CN),
            )
            assert item is not None
            self.assertEqual(item["kind"], "self_transfer")
            self.assertEqual(item["assignee_id"], LI)
            self.assertEqual(item["assignee_name"], "李四")

    def test_own_plain_message_does_not_invent_item(self) -> None:
        msg = _group("收到", sender_id=USER, message_id="om_plain")
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            self.assertIsNone(
                ingest(msg, user_open_id=USER, bot_open_id=BOT, path=path)
            )
            self.assertEqual(load_items(path), [])

    def test_assignee_reply_then_week_chase(self) -> None:
        ask = _group(
            "请王五处理设备",
            mentions=[{"id": WANG, "name": "王五"}],
            message_id="om_ask",
        )
        reply = _group(
            "方案可以",
            sender_id=WANG,
            sender_name="王五",
            message_id="om_rep",
        )
        assert ask is not None and reply is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            ingest(
                ask,
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 10, 9, 0, tzinfo=CN),
            )
            ingest(
                reply,
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 14, 15, 0, tzinfo=CN),
            )
            items = load_items(path)
            due, chase, _other = digest_buckets(items, MON)
            self.assertEqual(due, [])
            self.assertEqual(len(chase), 1)
            self.assertEqual(chase[0]["assignee_name"], "王五")
            text = format_digest_text(due, chase, [])
            self.assertIn("这周要去催谁", text)
            self.assertIn("王五", text)

    def test_due_today_unanswered(self) -> None:
        msg = _group(
            "请王五处理，今天给我答复",
            mentions=[{"id": WANG, "name": "王五"}],
            message_id="om_due",
        )
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            ingest(
                msg,
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 17, 8, 0, tzinfo=CN),
            )
            due, chase, _other = digest_buckets(load_items(path), MON)
            self.assertEqual(len(due), 1)
            self.assertEqual(chase, [])
            text = format_digest_text(due, chase, [])
            self.assertIn("今天要去问谁为什么没给答复", text)
            self.assertIn("王五", text)

    def test_user_followup_clears_week_chase(self) -> None:
        ask = _group(
            "请王五处理",
            mentions=[{"id": WANG, "name": "王五"}],
            message_id="om_a2",
        )
        their = _group("好的", sender_id=WANG, message_id="om_r2")
        mine = _group("催一下进度", sender_id=USER, message_id="om_u2")
        assert ask and their and mine
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            ingest(ask, user_open_id=USER, bot_open_id=BOT, path=path,
                   now=datetime(2026, 8, 10, 9, 0, tzinfo=CN))
            ingest(their, user_open_id=USER, bot_open_id=BOT, path=path,
                   now=datetime(2026, 8, 14, 15, 0, tzinfo=CN))
            ingest(mine, user_open_id=USER, bot_open_id=BOT, path=path,
                   now=datetime(2026, 8, 17, 9, 30, tzinfo=CN))
            _due, chase, _other = digest_buckets(load_items(path), MON)
            self.assertEqual(chase, [])

    def test_card_buttons_done_snooze_ignore(self) -> None:
        msg = _group(
            "请王五处理",
            mentions=[{"id": WANG, "name": "王五"}],
            message_id="om_btn",
        )
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            item = ingest(msg, user_open_id=USER, bot_open_id=BOT, path=path)
            assert item is not None
            key = item["id"]
            self.assertIn("不再催", apply_action("fu_done", key, path=path))
            self.assertEqual(load_items(path)[0]["status"], "done")

            item2 = ingest(
                _group("请李四确认", mentions=[{"id": LI, "name": "李四"}], message_id="om_s"),
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 17, 12, 0, tzinfo=CN),
            )
            assert item2 is not None
            self.assertIn("明天再催", apply_action("fu_snooze", item2["id"], path=path, today=MON))
            self.assertEqual(load_items(path)[1]["status"], "snooze")
            self.assertEqual(load_items(path)[1]["snooze_until"], "2026-08-18")

            item3 = ingest(
                _group("请王五看一下", mentions=[{"id": WANG, "name": "王五"}], message_id="om_i"),
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
            )
            assert item3 is not None
            self.assertIn("已忽略", apply_action("fu_ignore", item3["id"], path=path))
            self.assertEqual(load_items(path)[2]["status"], "ignore")

    def test_digest_card_has_three_buttons(self) -> None:
        card = digest_card(
            due=[{"id": "fu:1", "assignee_name": "王五", "text": "设备", "chat_name": "研发群"}],
            chase=[{"id": "fu:2", "assignee_name": "李四", "text": "方案", "chat_name": "研发群"}],
            other=[],
            today=MON,
        )
        blob = str(card)
        self.assertIn("已完成", blob)
        self.assertIn("明天再说", blob)
        self.assertIn("忽略", blob)
        self.assertIn("fu_done", blob)
        self.assertIn("fu_snooze", blob)
        self.assertIn("fu_ignore", blob)
        self.assertNotIn("column_set", blob)
        self.assertNotIn('"tag": "note"', blob)
        self.assertNotIn("wide_screen_mode", blob)
        self.assertEqual(card.get("schema"), "2.0")
        self.assertNotIn("elements", card)
        self.assertIn("body", card)

    def test_digest_card_strips_html(self) -> None:
        card = digest_card(
            due=[],
            chase=[],
            other=[
                {
                    "id": "fu:html",
                    "assignee_name": "邱俊立",
                    "chat_name": "邱俊立",
                    "text": "<p>好好体验下M8p</p>",
                }
            ],
            today=MON,
        )
        blob = str(card)
        self.assertNotIn("<p>", blob)
        self.assertIn("好好体验下M8p", blob)

    def test_extract_card_keeps_fu_act(self) -> None:
        act = extract_card_action(
            {
                "chat_id": "oc_p2p",
                "operator_id": USER,
                "event_id": "ev_1",
                "action_value": {"act": "fu_done", "key": "fu:om_btn"},
            }
        )
        assert act is not None
        self.assertEqual(act.act, "fu_done")
        self.assertEqual(act.key, "fu:om_btn")


class BitableScanTests(unittest.TestCase):
    def test_record_ats_user_by_name_or_id(self) -> None:
        self.assertTrue(
            record_mentions_user(
                {"标题": "请@吴梦晨看缺陷", "负责人": "张三"},
                names=("吴梦晨",),
                user_open_id=USER,
            )
        )
        self.assertTrue(
            record_mentions_user(
                {"person": [{"id": USER, "name": "吴梦晨"}]},
                names=("吴梦晨",),
                user_open_id=USER,
            )
        )
        self.assertFalse(
            record_mentions_user(
                {"标题": "请王五处理"},
                names=("吴梦晨",),
                user_open_id=USER,
            )
        )

    def test_scan_interval(self) -> None:
        self.assertTrue(should_scan_bitable(0.0, 180.0))
        self.assertFalse(should_scan_bitable(10.0, 100.0))

    def test_weekly_rows_mix_template_and_open(self) -> None:
        rows = weekly_rows(
            templates=[
                {"标题": "填写任务清单", "启用": "是"},
                {"标题": "同步上周未闭环", "启用": "是"},
                {"标题": "核对表格艾特", "启用": "是"},
            ],
            followups=[
                {
                    "assignee_name": "王五",
                    "text": "设备邮寄",
                    "due": "2026-08-17",
                    "status": "open",
                    "kind": "assign_b",
                }
            ],
            today=MON,
        )
        titles = [row["标题"] for row in rows]
        self.assertIn("填写任务清单", titles)
        self.assertIn("同步上周未闭环", titles)
        self.assertIn("核对表格艾特", titles)
        self.assertTrue(any("王五" in title for title in titles))

    def test_matrix_record_list_shape(self) -> None:
        from partner.bitable import _fields_of, _records_of

        recs = _records_of(
            {
                "ok": True,
                "data": {
                    "data": [["填写任务清单", ["是"], None], ["同步上周未闭环", ["是"], None]],
                    "fields": ["标题", "启用", "说明"],
                    "record_id_list": ["rec1", "rec2"],
                },
            }
        )
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["record_id"], "rec1")
        self.assertEqual(_fields_of(recs[0])["标题"], "填写任务清单")
        self.assertTrue(
            record_mentions_user(
                {"标题": "@吴梦晨 看缺陷", "负责人": [{"id": USER, "name": "吴梦晨"}]},
                names=("吴梦晨",),
                user_open_id=USER,
            )
        )


class IntentAndPartnerTests(unittest.TestCase):
    def test_digest_and_weekly_task_intents(self) -> None:
        self.assertEqual(parse_intent("今日待跟进").action, "digest")
        self.assertEqual(parse_intent("催办").action, "digest")
        self.assertEqual(parse_intent("生成本周任务").action, "weekly_tasks")
        self.assertEqual(parse_intent("本周任务").action, "weekly_tasks")
        self.assertEqual(parse_intent("今日待办").action, "today")
        self.assertEqual(parse_intent("帮助").action, "help")

    def test_digest_skips_hermes(self) -> None:
        self.assertFalse(should_partner("p2p", "digest"))
        self.assertFalse(should_partner("p2p", "weekly_tasks"))
        self.assertFalse(should_partner("p2p", "today"))
        self.assertFalse(should_partner("p2p", "tomorrow"))


class WorkAssignTests(unittest.TestCase):
    def test_informal_direct_is_assign_not_chatter(self) -> None:
        long = (
            "研究下这个品技术方案，看下录音、转写，会议纪要当前实现链路，"
            "想想如果都切换到AI中台，能不能走通，后边要加快AI中台能力的建设，你和少华一起做"
        )
        self.assertTrue(looks_like_work_assign(long))
        self.assertTrue(looks_like_work_assign("好好体验下M8p，写份体验总结给我"))
        self.assertFalse(looks_like_work_assign("我给你的m8plus还在吗"))
        self.assertFalse(looks_like_work_assign("在的"))
        self.assertFalse(looks_like_work_assign("OK,看到这个我很高兴啊"))

    def test_p2p_direct_lands_in_ledger(self) -> None:
        msg = extract_inbound_message(
            {
                "chat_id": "oc_li",
                "chat_type": "p2p",
                "chat_name": "立哥",
                "content": "好好体验下M8p，写份体验总结给我",
                "message_id": "om_li1",
                "sender_type": "user",
                "sender_id": "ou_li",
                "sender_name": "立哥",
            }
        )
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            item = ingest(
                msg,
                user_open_id=USER,
                bot_open_id=BOT,
                path=path,
                now=datetime(2026, 8, 17, 17, 14, tzinfo=CN),
            )
            assert item is not None
            self.assertEqual(item["kind"], "direct")
            self.assertEqual(item["assignee_id"], USER)
            self.assertEqual(item["asker_name"], "立哥")

    def test_group_ni_research_is_yours(self) -> None:
        msg = _group(
            "研究下这个品技术方案，你和少华一起做",
            sender_id="ou_li",
            sender_name="立哥",
            message_id="om_g1",
        )
        assert msg is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            item = ingest(msg, user_open_id=USER, bot_open_id=BOT, path=path)
            assert item is not None
            self.assertEqual(item["kind"], "direct")
            self.assertEqual(item["assignee_id"], USER)

    def test_command_scans_chats_then_summarizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fu.json"
            save_items(
                [
                    {
                        "kind": "direct",
                        "asker_name": "立哥",
                        "text": "好好体验下M8p，写份体验总结给我",
                        "status": "open",
                    }
                ],
                path,
            )
            with patch("partner.followup.scan_recent_p2p", return_value=1) as scan:
                text = followups_for_command(path=path)
        scan.assert_called()
        self.assertIn("体验总结", text)
        self.assertIn("立哥", text)

    def test_hourly_watch_window_nine_to_six(self) -> None:
        self.assertTrue(
            in_chat_watch_window(datetime(2026, 8, 17, 9, 0, tzinfo=CN))
        )
        self.assertTrue(
            in_chat_watch_window(datetime(2026, 8, 17, 17, 59, tzinfo=CN))
        )
        self.assertFalse(
            in_chat_watch_window(datetime(2026, 8, 17, 8, 59, tzinfo=CN))
        )
        self.assertFalse(
            in_chat_watch_window(datetime(2026, 8, 17, 18, 0, tzinfo=CN))
        )
        self.assertTrue(should_sync_user_chats(0.0, 3600.0))
        self.assertFalse(should_sync_user_chats(10.0, 3599.0))
        line = format_assign_push(
            {
                "asker_name": "立哥",
                "text": "好好体验下M8p，写份体验总结给我",
            }
        )
        self.assertIn("派你的活", line)


class OpenFollowupFormatTests(unittest.TestCase):
    def test_direct_work_stays_when_tasks_empty(self) -> None:
        text = format_open_followups(
            [
                {
                    "kind": "direct",
                    "asker_name": "立哥",
                    "text": "好好体验下M8p，写份体验总结给我",
                    "status": "open",
                },
                {
                    "kind": "direct",
                    "asker_name": "立哥",
                    "text": "研究下这个品技术方案，你和少华一起做",
                    "status": "done",
                },
            ],
            today=MON,
        )
        self.assertIn("立哥", text)
        self.assertIn("体验总结", text)
        self.assertNotIn("技术方案", text)


class DigestPushOnceTests(unittest.TestCase):
    def test_concurrent_push_digest_sends_one_card(self) -> None:
        import threading
        import time

        from partner.followup import CN_TZ, push_digest

        now = datetime(2026, 8, 18, 9, 0, tzinfo=CN_TZ)
        barrier = threading.Barrier(2)
        sends: list[str] = []

        def slow_card(*_a: object, **_k: object) -> str:
            time.sleep(0.05)
            sends.append("card")
            return "已发送。"

        def run() -> None:
            barrier.wait(timeout=2)
            push_digest(now=now)

        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "digest-sent.on"
            with patch("partner.followup.DIGEST_STAMP", stamp):
                with patch("partner.followup.scan_recent_p2p"):
                    with patch(
                        "partner.followup.digest_payload",
                        return_value={
                            "today": "2026-08-18",
                            "due": [],
                            "chase": [],
                            "other": [{"id": "fu:1", "text": "体验总结", "assignee_name": "邱俊立"}],
                            "text": "今日待跟进\n还在跟\n- 邱俊立：体验总结",
                        },
                    ):
                        with patch("partner.actions.send_card", side_effect=slow_card):
                            with patch("partner.actions.send_text"):
                                threads = [
                                    threading.Thread(target=run)
                                    for _ in range(2)
                                ]
                                for thread in threads:
                                    thread.start()
                                for thread in threads:
                                    thread.join(timeout=3)
        self.assertEqual(sends, ["card"])


if __name__ == "__main__":
    unittest.main()
