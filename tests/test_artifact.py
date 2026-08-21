from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner.actions import dispatch
from partner.runtime.artifact import (
    _fallback_draft,
    apply_document_edit,
    artifact_turn,
    close_artifact,
    load_artifact,
    locate_target_block,
    observe_document,
    prepare_document_edit,
    save_artifact,
)
from partner.routing.intents import parse_intent


DOC_URL = "https://example.feishu.cn/docx/trial"
SOURCE_XML = """
<fragment>
  <h1 id="month1">入职第一个月</h1>
  <p id="owner1">完成人：<cite user-name="吴梦晨"></cite></p>
  <h1 id="month2">入职第二个月</h1>
  <h3 id="done2">二、工作完成情况</h3>
  <p id="owner2">完成人：<cite user-name="吴梦晨"></cite></p>
  <ol><li id="item2">A6,A8 bug + UI 调整</li></ol>
  <h1 id="month3">入职第三个月</h1>
</fragment>
""".strip()


class ArtifactTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_ARTIFACTS"] = str(
            Path(self.tmp.name) / "artifacts.json"
        )
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_ARTIFACTS", None)

    def test_observe_document_persists_target(self) -> None:
        observe_document("oc_x", DOC_URL, "# 入职第二个月\n原文")
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "observed")
        self.assertEqual(task["doc_url"], DOC_URL)
        self.assertIn("入职第二个月", task["source_snapshot"])

    def test_locate_target_within_named_section(self) -> None:
        target = locate_target_block(
            SOURCE_XML,
            marker="吴梦晨",
            section="入职第二个月",
        )
        self.assertEqual(target.block_id, "owner2")
        self.assertIn("吴梦晨", target.label)

    def test_locate_subsection_within_month(self) -> None:
        target = locate_target_block(
            SOURCE_XML,
            marker="工作完成情况",
            section="入职第二个月",
        )
        self.assertEqual(target.block_id, "done2")

    def test_prepare_builds_draft_without_writing(self) -> None:
        observe_document("oc_x", DOC_URL, "# 入职第二个月\n原文")
        payload = {
            "ok": True,
            "data": {
                "document": {
                    "content": SOURCE_XML,
                    "revision_id": 8,
                }
            },
        }
        with patch("partner.runtime.artifact.run_lark", return_value=payload) as run:
            with patch(
                "partner.runtime.artifact.draft_doc_edit",
                return_value="1. 完成 A6/A8 问题修复\n2. 推进终端套餐查询",
            ):
                reply = prepare_document_edit(
                    "oc_x",
                    "写到入职第二个月吴梦晨下面，先写2句话看看",
                    work_facts="本周：A6/A8 问题修复、终端套餐查询",
                )
        self.assertIn("草稿", reply)
        self.assertIn("回复「写进去」", reply)
        self.assertFalse(
            any(call.args[0][1] == "+update" for call in run.call_args_list)
        )
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["anchor_block_id"], "owner2")

    def test_fallback_draft_does_not_pad_with_unrelated_doc_text(self) -> None:
        draft = _fallback_draft(
            "写4条",
            "入职第一个月\n交接人：张三",
            "- 完成 A6 修复\n- 推进套餐查询",
        )
        self.assertIn("A6", draft)
        self.assertIn("套餐查询", draft)
        self.assertNotIn("入职第一个月", draft)
        self.assertNotIn("张三", draft)

    def test_ambiguous_target_stays_needs_target(self) -> None:
        payload = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 8}},
        }
        with patch("partner.runtime.artifact.run_lark", return_value=payload):
            with patch("partner.runtime.artifact.draft_doc_edit") as draft:
                reply = prepare_document_edit(
                    "oc_x",
                    f"{DOC_URL} 写到吴梦晨下面",
                    work_facts="本周事实",
                )
        draft.assert_not_called()
        self.assertIn("出现多次", reply)
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "needs_target")

    def test_new_section_in_same_doc_does_not_replace_previous_block(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "done",
                "doc_url": DOC_URL,
                "section": "入职第一个月",
                "marker": "吴梦晨",
                "anchor_block_id": "owner1",
                "anchor_label": "完成人：吴梦晨",
                "draft": "上一节草稿",
                "inserted_block_id": "old1",
                "source_snapshot": "原文",
            },
        )
        payload = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 9}},
        }
        with patch("partner.runtime.artifact.run_lark", return_value=payload):
            with patch(
                "partner.runtime.artifact.draft_doc_edit",
                return_value="新一节草稿",
            ) as draft:
                prepare_document_edit(
                    "oc_x",
                    "入职第二个月的工作完成情况给我写一下",
                    work_facts="新事实",
                )
        self.assertEqual(draft.call_args.kwargs["previous_draft"], "")
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["anchor_block_id"], "done2")
        self.assertEqual(task["inserted_block_id"], "")

    def test_explicit_new_section_does_not_reuse_old_marker(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "done",
                "doc_url": DOC_URL,
                "section": "入职第二个月",
                "marker": "吴梦晨",
                "anchor_block_id": "owner2",
                "draft": "旧草稿",
                "inserted_block_id": "old1",
            },
        )
        payload = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 9}},
        }
        with patch("partner.runtime.artifact.run_lark", return_value=payload):
            with patch(
                "partner.runtime.artifact.draft_doc_edit",
                return_value="第三个月草稿",
            ):
                prepare_document_edit(
                    "oc_x",
                    "入职第三个月给我写一下",
                    work_facts="新事实",
                )
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["marker"], "")
        self.assertEqual(task["anchor_block_id"], "month3")
        self.assertEqual(task["inserted_block_id"], "")

    def test_confirm_updates_then_verifies_before_done(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "ready",
                "doc_url": DOC_URL,
                "instruction": "写到吴梦晨下面",
                "section": "入职第二个月",
                "marker": "吴梦晨",
                "anchor_block_id": "owner2",
                "anchor_label": "完成人：吴梦晨",
                "draft": "1. 完成 A6/A8 问题修复\n2. 推进终端套餐查询",
                "source_snapshot": "原文",
                "inserted_block_id": "",
            },
        )
        locate = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 8}},
        }
        updated = {
            "ok": True,
            "data": {
                "result": "success",
                "updated_blocks_count": 1,
                "document": {"new_blocks": [{"block_id": "new1"}]},
            },
        }
        verified = {
            "ok": True,
            "data": {
                "document": {
                    "content": '<fragment><p id="new1">1. 完成 A6/A8 问题修复<br/>'
                    "2. 推进终端套餐查询</p></fragment>"
                }
            },
        }
        with patch(
            "partner.runtime.artifact.run_lark",
            side_effect=[locate, updated, verified],
        ) as run:
            reply = apply_document_edit("oc_x")
        update_args = run.call_args_list[1].args[0]
        self.assertEqual(update_args[:2], ["docs", "+update"])
        self.assertIn("block_insert_after", update_args)
        self.assertNotIn("+create", update_args)
        self.assertIn("已写入并回读确认", reply)
        self.assertIn("任务结束", reply)
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["inserted_block_id"], "new1")

    def test_existing_inserted_block_is_replaced(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "ready",
                "doc_url": DOC_URL,
                "instruction": "再多一点",
                "section": "入职第二个月",
                "marker": "吴梦晨",
                "anchor_block_id": "owner2",
                "anchor_label": "完成人：吴梦晨",
                "draft": "扩充后的内容",
                "source_snapshot": "原文",
                "inserted_block_id": "old1",
            },
        )
        locate = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 9}},
        }
        updated = {
            "ok": True,
            "data": {
                "result": "success",
                "updated_blocks_count": 1,
                "document": {"new_blocks": [{"block_id": "new2"}]},
            },
        }
        verified = {
            "ok": True,
            "data": {
                "document": {
                    "content": '<fragment><p id="new2">扩充后的内容</p></fragment>'
                }
            },
        }
        with patch(
            "partner.runtime.artifact.run_lark",
            side_effect=[locate, updated, verified],
        ) as run:
            apply_document_edit("oc_x")
        update_args = run.call_args_list[1].args[0]
        self.assertIn("block_replace", update_args)
        self.assertIn("old1", update_args)

    def test_update_failure_keeps_ready_draft(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "ready",
                "doc_url": DOC_URL,
                "instruction": "写到吴梦晨下面",
                "section": "入职第二个月",
                "marker": "吴梦晨",
                "anchor_block_id": "owner2",
                "anchor_label": "完成人：吴梦晨",
                "draft": "两条内容",
                "source_snapshot": "原文",
                "inserted_block_id": "",
            },
        )
        locate = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 8}},
        }
        failed = {"ok": False, "error": {"message": "forbidden"}}
        with patch("partner.runtime.artifact.run_lark", side_effect=[locate, failed]):
            reply = apply_document_edit("oc_x")
        self.assertIn("写入失败", reply)
        self.assertNotIn("任务结束", reply)
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["draft"], "两条内容")

    def test_verify_failure_never_claims_done(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "ready",
                "doc_url": DOC_URL,
                "instruction": "写到吴梦晨下面",
                "section": "入职第二个月",
                "marker": "吴梦晨",
                "anchor_block_id": "owner2",
                "anchor_label": "完成人：吴梦晨",
                "draft": "两条内容",
                "source_snapshot": "原文",
                "inserted_block_id": "",
            },
        )
        locate = {
            "ok": True,
            "data": {"document": {"content": SOURCE_XML, "revision_id": 8}},
        }
        updated = {
            "ok": True,
            "data": {
                "result": "success",
                "updated_blocks_count": 1,
                "document": {"new_blocks": [{"block_id": "new1"}]},
            },
        }
        missing = {
            "ok": True,
            "data": {"document": {"content": "<fragment></fragment>"}},
        }
        with patch(
            "partner.runtime.artifact.run_lark",
            side_effect=[locate, updated, missing],
        ):
            reply = apply_document_edit("oc_x")
        self.assertIn("未验真", reply)
        self.assertNotIn("任务结束", reply)
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "verify_failed")
        self.assertEqual(task["inserted_block_id"], "new1")

    def test_close_marks_task_closed(self) -> None:
        observe_document("oc_x", DOC_URL, "原文")
        reply = close_artifact("oc_x")
        self.assertEqual(reply, "任务结束。")
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "closed")

    def test_done_artifact_does_not_steal_runner_confirmation(self) -> None:
        self.assertEqual(
            artifact_turn({"status": "done"}, "确认写入"),
            "",
        )
        self.assertEqual(
            artifact_turn({"status": "observed"}, "多一点"),
            "",
        )
        self.assertEqual(
            artifact_turn({"status": "ready"}, "多一点"),
            "revise",
        )


class ArtifactDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FEISHU_PARTNER_ARTIFACTS"] = str(
            Path(self.tmp.name) / "artifacts.json"
        )
        os.environ["FEISHU_PARTNER_SESSION"] = str(
            Path(self.tmp.name) / "session.json"
        )
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_ARTIFACTS", None)
        self.addCleanup(os.environ.pop, "FEISHU_PARTNER_SESSION", None)

    def test_read_observes_document_for_next_turn(self) -> None:
        asked = f"{DOC_URL} 读这个"
        with patch("partner.actions.read_text", return_value="# 入职第二个月\n原文"):
            with patch("partner.actions.partner_reply", return_value=""):
                reply = dispatch(
                    parse_intent(asked),
                    user_text=asked,
                    channel="p2p",
                    chat_id="oc_x",
                )
        self.assertIn("入职第二个月", reply)
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "observed")
        self.assertEqual(task["doc_url"], DOC_URL)

    def test_section_write_uses_observed_doc_not_create(self) -> None:
        observe_document("oc_x", DOC_URL, "# 入职第二个月\n原文")
        asked = "入职第二个月的工作完成情况给我写一下"
        with patch(
            "partner.actions.prepare_document_edit",
            return_value="【草稿】\n两条内容",
        ) as prepare:
            with patch("partner.actions.weekly_text", return_value="本周事实"):
                with patch("partner.actions.write_doc_text") as create:
                    reply = dispatch(
                        parse_intent(asked),
                        user_text=asked,
                        channel="p2p",
                        chat_id="oc_x",
                    )
        prepare.assert_called_once()
        create.assert_not_called()
        self.assertIn("草稿", reply)

    def test_linked_edit_prepares_existing_doc_not_new_doc(self) -> None:
        asked = f"{DOC_URL} 先读后给我写到吴梦晨下面，先写2句话看看"
        with patch(
            "partner.actions.prepare_document_edit",
            return_value="【草稿】\n两条内容",
        ) as prepare:
            with patch("partner.actions.weekly_text", return_value="本周事实"):
                with patch("partner.actions.write_doc_text") as create:
                    reply = dispatch(
                        parse_intent(asked),
                        user_text=asked,
                        channel="p2p",
                        chat_id="oc_x",
                    )
        prepare.assert_called_once()
        create.assert_not_called()
        self.assertIn("草稿", reply)

    def test_write_in_applies_active_artifact(self) -> None:
        save_artifact(
            "oc_x",
            {
                "kind": "doc_edit",
                "status": "ready",
                "doc_url": DOC_URL,
                "draft": "两条内容",
            },
        )
        with patch(
            "partner.actions.apply_document_edit",
            return_value="已写入并回读确认。\n任务结束。",
        ) as apply:
            reply = dispatch(
                parse_intent("写进去"),
                user_text="写进去",
                channel="p2p",
                chat_id="oc_x",
            )
        apply.assert_called_once_with("oc_x")
        self.assertIn("任务结束", reply)

    def test_end_closes_active_artifact(self) -> None:
        observe_document("oc_x", DOC_URL, "原文")
        reply = dispatch(
            parse_intent("结束"),
            user_text="结束",
            channel="p2p",
            chat_id="oc_x",
        )
        self.assertEqual(reply, "任务结束。")
        task = load_artifact("oc_x")
        assert task is not None
        self.assertEqual(task["status"], "closed")


if __name__ == "__main__":
    unittest.main()
