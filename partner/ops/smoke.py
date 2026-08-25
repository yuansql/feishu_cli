"""Smoke as a test identity: exercise partner like a P2P user without waiting for bugs.

Default is safe: routing fixtures + local `dispatch` with quality gates.
`--live-write` is opt-in (can mutate Feishu docs / send).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..core.ids import P2P_CHAT_ID, display_name
from ..routing.intents import parse_intent
from .eval import run_fixture_eval


@dataclass(frozen=True)
class SmokeCase:
    id: str
    text: str
    expect_action: str = ""
    expect_action_not: str = ""
    expect_contains: tuple[str, ...] = ()
    expect_not_contains: tuple[str, ...] = ()
    run_dispatch: bool = True
    needs_write: bool = False
    # When False, do not apply global bad-snippet bans (e.g. chat history may quote old bugs).
    ban_global_bad: bool = True


_BAD_SNIPPETS = (
    "仍在执行中",
    "jsonschema",
    "'local' is not of type",
    "litellm.InternalServerError",
    "LLM provider internal error",
    "对上好几块",
    "回个序号",
    "Traceback",
)


def _tester_name() -> str:
    return display_name() or "测试用户"


def smoke_cases() -> list[SmokeCase]:
    name = _tester_name()
    wiki = "https://it82yw7fgr.feishu.cn/wiki/QLg1wnATIiVTuXkUiHecNag7nSh"
    return [
        SmokeCase("help", "帮助", expect_action="help", expect_contains=("写周报",)),
        SmokeCase(
            "identity",
            "你知道我是谁吗",
            expect_action="identity",
            expect_contains=(name,) if name else ("你是",),
            expect_not_contains=("对上好几块", "回个序号", "科沃斯"),
        ),
        SmokeCase("today", "今天", expect_action="today", expect_not_contains=_BAD_SNIPPETS),
        SmokeCase("tasks", "待办", expect_action="tasks", expect_not_contains=_BAD_SNIPPETS),
        SmokeCase(
            "search",
            "搜 本周周报",
            expect_action="search",
            expect_not_contains=_BAD_SNIPPETS,
        ),
        SmokeCase(
            "chat-history",
            "读取和吴梦晨的飞书 CLI的聊天记录",
            expect_action="chat_history",
            expect_not_contains=("【补充·", "FETCH:", "feishu_"),
            ban_global_bad=False,
        ),
        SmokeCase(
            "chat-history-msg",
            "读一下消息 吴梦晨的飞书 CLI",
            expect_action="chat_history",
            expect_not_contains=("FETCH:", "不要输出思考"),
            ban_global_bad=False,
        ),
        SmokeCase(
            "chats-list",
            "读取我的聊天,还是不行",
            expect_action="chats",
        ),
        SmokeCase(
            "today-recap",
            "我今天干了什么?",
            expect_action="today_recap",
            expect_action_not="unknown",
        ),
        SmokeCase(
            "weekly-tasks-msg",
            "本周任务, 输出到消息中",
            expect_action="weekly_tasks",
            expect_contains=("本周任务",),
            expect_not_contains=("正文空", "个人内容消费", "给改写用"),
        ),
        SmokeCase(
            "weekly-tasks-all",
            "本周的全部任务",
            expect_action="weekly_tasks",
            run_dispatch=False,  # routing only; live dispatch already covered by weekly-tasks-msg
        ),
        SmokeCase(
            "doc-edit-append",
            "https://it82yw7fgr.feishu.cn/docx/JQmIdVhCmoQLbRxHMnbcO7sMnkc\n\n本周的一些活需要添加到第二个月中",
            expect_action="write_doc",
            run_dispatch=False,  # prepare drafts live; routing gate here
            needs_write=True,
        ),
        SmokeCase(
            "who-is",
            "邱俊立是谁",
            expect_action="who",
            run_dispatch=False,  # live Hermes+search; routing gate here
        ),
        SmokeCase(
            "who-self",
            "吴梦晨是谁?",
            expect_action="identity",
            expect_contains=("吴梦晨",),
            expect_not_contains=("最近怎么说", "收件箱里"),
        ),
        SmokeCase(
            "fill-weekly-url",
            f"{wiki} 填写周报",
            expect_action="write_weekly",
            run_dispatch=False,
            needs_write=True,
        ),
        SmokeCase(
            "write-weekly-plain",
            "写个周报",
            expect_action="write_weekly",
            run_dispatch=False,  # creates a new doc — opt-in only
            needs_write=True,
        ),
    ]


def _check_text(body: str, case: SmokeCase) -> list[str]:
    fails: list[str] = []
    for needle in case.expect_contains:
        if needle and needle not in body:
            fails.append(f"missing:{needle!r}")
    for banned in case.expect_not_contains:
        if banned and banned in body:
            fails.append(f"banned:{banned!r}")
    if case.ban_global_bad:
        for bad in _BAD_SNIPPETS:
            if bad in body:
                if f"banned:{bad!r}" not in fails:
                    fails.append(f"banned:{bad!r}")
    return fails


def run_smoke(*, allow_write: bool = False) -> dict[str, Any]:
    """Return structured smoke report. Exit-worthy when failed>0."""
    os.environ.setdefault("FEISHU_PARTNER_NO_LLM", "1")
    fixtures = run_fixture_eval()
    rows: list[dict[str, Any]] = []
    passed = 0

    from ..actions import dispatch

    for case in smoke_cases():
        intent = parse_intent(case.text)
        detail = "ok"
        ok = True
        body = ""
        if case.expect_action and intent.action != case.expect_action:
            ok = False
            detail = f"action={intent.action} want {case.expect_action}"
        elif case.expect_action_not and intent.action == case.expect_action_not:
            ok = False
            detail = f"action should not be {case.expect_action_not}"
        elif case.run_dispatch and (not case.needs_write or allow_write):
            try:
                body = dispatch(
                    intent,
                    user_text=case.text,
                    channel="p2p",
                    chat_id=P2P_CHAT_ID or "oc_smoke_local",
                    force_facts=True,
                )
            except Exception as exc:  # noqa: BLE001 — smoke must not die
                ok = False
                detail = f"dispatch:{type(exc).__name__}:{exc}"
                body = ""
            if ok:
                text_fails = _check_text(body or "", case)
                if text_fails:
                    ok = False
                    detail = ";".join(text_fails)
        elif case.needs_write and not allow_write:
            detail = "skip-write (pass routing only)"
            # routing already checked
        if ok:
            passed += 1
        rows.append(
            {
                "id": case.id,
                "ok": ok,
                "detail": detail,
                "action": intent.action,
                "text": case.text[:60],
                "preview": (body or "")[:120].replace("\n", " / "),
            }
        )

    total = len(rows)
    rate = round(100.0 * passed / total, 1) if total else 0.0
    return {
        "tester": _tester_name(),
        "fixtures": fixtures,
        "passed": passed,
        "total": total,
        "rate": rate,
        "allow_write": allow_write,
        "results": rows,
    }


def smoke_text(*, allow_write: bool = False, report: dict[str, Any] | None = None) -> str:
    report = report or run_smoke(allow_write=allow_write)
    fx = report["fixtures"]
    lines = [
        f"【测试身份冒烟】tester={report['tester']} allow_write={report['allow_write']}",
        f"路由 fixtures：{fx['passed']}/{fx['total']}（{fx['rate']}%）",
        f"行为用例：{report['passed']}/{report['total']}（{report['rate']}%）",
        "",
    ]
    fails = [r for r in report["results"] if not r["ok"]]
    if fails:
        lines.append("失败：")
        for row in fails:
            lines.append(f"- {row['id']}: {row['detail']} ← {row['text']}")
    else:
        lines.append("行为用例全部通过。")
    lines.append("")
    lines.append("说明：默认不写飞书文档；真写模板/新建周报加 --live-write。")
    lines.append("扩样例：partner/ops/smoke.py + partner/eval_cases.json。")
    return "\n".join(lines)
