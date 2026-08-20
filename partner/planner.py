from __future__ import annotations

import json
import os
import re
from typing import Any, Collection

from .llm import _invoke_hermes, rewrite_plan


def _clean_goal(goal: str) -> str:
    text = re.sub(r"\s+", " ", goal or "").strip(" ：:，,")
    return text


def _compact_facts(facts: str, *, limit: int = 12) -> list[str]:
    lines: list[str] = []
    for raw in (facts or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) > 120:
            line = line[:120] + "..."
        if line.startswith(("【", "-", "未完成", "今日日程", "明日日程", "飞书权威失败")):
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _keyword(goal: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,10}", goal)
    skip = {"帮我", "一下", "一个", "这个", "那个", "怎么", "如何", "规划", "计划", "拆解", "任务"}
    for tok in tokens:
        if tok not in skip:
            return tok
    return goal[:12]


def _step_templates(goal: str) -> list[str]:
    if any(key in goal for key in ("周报", "总结", "复盘")):
        return [
            "先确认时间范围和交付口径，避免把本周/下周混在一起。",
            "读取日程、待办、跟进账和相关周报材料，整理成事实清单。",
            "按【工作内容】【问题风险】【下周计划】输出初稿。",
            "核对是否有编造、漏掉待推进项，再决定是否创建飞书云文档。",
        ]
    if any(key in goal for key in ("会议", "纪要", "评审")):
        return [
            "先确认会议主题、参会人、要追的决策和待办。",
            "读取最近会议纪要和相关文档，只保留能落地的结论。",
            "拆出负责人、截止时间、风险点和下一次同步节点。",
            "把需要你跟的项写入跟进账或飞书待办。",
        ]
    if any(key in goal for key in ("数据", "多维表", "表格", "分析")):
        return [
            "先确认数据源、字段口径、统计周期和输出形式。",
            "检查多维表配置和可读权限，失败时明确缺哪个权限。",
            "按问题拆成筛选、聚合、异常项、行动建议四步。",
            "把需要持续看的指标沉淀为定时扫描或周报输入。",
        ]
    if any(key in goal for key in ("回复", "沟通", "催", "跟进")):
        return [
            "先确认对象、群/单聊、期望语气和截止时间。",
            "读取最近相关会话和跟进账，区分未回复、已追问未答完、已答完。",
            "给出可直接发送的短回复，并标出是否需要继续催。",
            "发出后把结果写回跟进账，避免明早重复提醒。",
        ]
    return [
        "先确认交付物、截止时间、相关人和成功标准。",
        "读取今天日程、未完成待办、跟进账和明确点名的文档。",
        "把任务拆成 3-5 个可验证动作，每个动作都有产出。",
        "先做阻塞最少、对后续依赖最大的动作。",
        "完成后回到飞书权威结果验收，不用本地猜成功。",
    ]


def _command_hints(goal: str) -> list[str]:
    key = _keyword(goal)
    hints = ["`feishu today`", "`feishu tasks`"]
    if any(word in goal for word in ("文档", "资料", "制度", "周报", "总结", "方案")):
        hints.append(f"`feishu search {key}`")
    if any(word in goal for word in ("会议", "纪要", "评审")):
        hints.append("`feishu ask 会议纪要`")
    if any(word in goal for word in ("审批", "流程")):
        hints.append("`feishu ask 审批`")
    if any(word in goal for word in ("跟进", "催", "回复", "谁找我")):
        hints.append("`feishu digest`")
    if any(word in goal for word in ("多维表", "本周任务", "表格")):
        hints.append("`feishu followup --weekly-once`")
    return list(dict.fromkeys(hints))


def _fallback_plan(goal: str, facts: str) -> str:
    context = _compact_facts(facts)
    lines = [
        f"任务规划：{goal}",
        "",
        "【当前上下文】",
    ]
    if context:
        lines.extend(context)
    else:
        lines.append("- 还没有可用上下文；先按目标本身拆解。")
    lines.extend(["", "【执行步骤】"])
    for index, step in enumerate(_step_templates(goal), 1):
        lines.append(f"{index}. {step}")
    lines.extend(["", "【可直接用的飞书动作】"])
    lines.extend(f"- {hint}" for hint in _command_hints(goal))
    lines.extend(
        [
            "",
            "【需要确认】",
            "- 最晚什么时候要交付？",
            "- 交付给谁，发到哪个群或文档？",
            "- 是否允许我把拆出来的事项写入飞书待办/跟进账？",
        ]
    )
    return "\n".join(lines)


def plan_steps(goal: str, facts: str = "") -> list[dict[str, str | dict[str, str]]]:
    """Structured steps for TaskRunner (tool + args)."""
    cleaned = _clean_goal(goal)
    if not cleaned:
        return []
    key = _keyword(cleaned)
    no_write = any(
        phrase in cleaned
        for phrase in ("不写入", "不要写", "只读", "不要创建", "无需写入", "不落盘")
    )
    steps: list[dict[str, str | dict[str, str]]] = [
        {"title": "读取今天日程、待办和要跟的活", "tool": "today", "args": {}},
        {"title": "读取未完成待办", "tool": "tasks", "args": {}},
    ]
    if any(word in cleaned for word in ("文档", "资料", "制度", "周报", "总结", "方案")):
        steps.append(
            {
                "title": f"搜索相关文档：{key}",
                "tool": "search",
                "args": {"query": key},
            }
        )
    if any(word in cleaned for word in ("会议", "纪要", "评审")):
        steps.append({"title": "读取最近会议纪要", "tool": "minutes", "args": {}})
    if any(word in cleaned for word in ("审批", "流程")):
        steps.append({"title": "读取待办审批", "tool": "approval", "args": {}})
    if any(word in cleaned for word in ("回复", "沟通", "催", "跟进", "谁找我")):
        steps.append({"title": "读取待跟进与谁找我", "tool": "inbox", "args": {}})
    if key and not any(str(s.get("tool") or "") == "search" for s in steps):
        steps.append(
            {
                "title": f"搜索相关材料：{key}",
                "tool": "search",
                "args": {"query": key},
            }
        )
    steps.append({"title": "汇总材料并给出建议", "tool": "summarize", "args": {}})
    if any(word in cleaned for word in ("沙箱", "脚本", "python", "本地计算", "跑一下代码")):
        steps.insert(
            -1,
            {
                "title": "查看本地沙箱",
                "tool": "sandbox_ls",
                "args": {"path": "."},
            },
        )
    want_html = any(
        word in cleaned
        for word in ("html", "HTML", "本地报告", "本地 html", "工作报告", "生成本地")
    )
    want_upload = any(
        word in cleaned for word in ("上传飞书", "云文档", "写到飞书", "发到飞书")
    )
    if not no_write and want_html:
        steps.append(
            {
                "title": f"生成本地 HTML 报告：{cleaned}",
                "tool": "report_write",
                "args": {"goal": cleaned},
            }
        )
    if not no_write and any(
        word in cleaned for word in ("文档", "周报", "总结", "方案", "复盘")
    ) and (not want_html or want_upload):
        steps.append(
            {
                "title": f"创建云文档初稿：{cleaned}",
                "tool": "docs_create",
                "args": {"query": cleaned},
                "requires_confirm": True,
            }
        )
    if not no_write and any(
        phrase in cleaned
        for phrase in ("写入跟进", "加入跟进", "记到跟进", "创建跟进", "催办")
    ):
        steps.append(
            {
                "title": "写入本地跟进账（需确认）",
                "tool": "followup_add",
                "args": {"goal": cleaned},
                "requires_confirm": True,
            }
        )
    if not no_write and any(
        phrase in cleaned
        for phrase in (
            "创建待办",
            "新建待办",
            "写入待办",
            "同步待办",
            "加入待办",
            "创建飞书任务",
        )
    ):
        steps.append(
            {
                "title": "创建飞书待办（需确认）",
                "tool": "task_create",
                "args": {"summary": cleaned},
                "requires_confirm": True,
            }
        )
    return steps


def _extract_plan_json(raw: str) -> dict[str, Any] | None:
    blob = (raw or "").strip()
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def agent_plan_steps(
    goal: str,
    facts: str,
    *,
    available_tools: Collection[str],
    write_tools: Collection[str] = (),
    previous_steps: list[dict[str, Any]] | None = None,
    failure: str = "",
    timeout: int = 45,
) -> list[dict[str, Any]]:
    """Plan from observed facts; return [] so the runtime can use its safe fallback."""
    cleaned = _clean_goal(goal)
    allowed = {str(item).strip() for item in available_tools if str(item).strip()}
    writes = {str(item).strip() for item in write_tools if str(item).strip()}
    no_write = any(
        phrase in cleaned
        for phrase in ("不写入", "不要写", "只读", "不要创建", "无需写入", "不落盘")
    )
    if not cleaned or not allowed or os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return []
    previous = json.dumps(previous_steps or [], ensure_ascii=False)[:5000]
    prompt = f"""你是飞书工作伙伴的任务规划器，只负责输出 JSON，不执行工具。
目标：{cleaned}

已观察事实：
{(facts or "暂无")[:12000]}

上版步骤：
{previous}

失败原因：
{(failure or "无")[:1000]}

可用工具（只能从这里选）：{", ".join(sorted(allowed))}
写工具（仅目标明确要求写入/创建时使用，运行时还会再次向用户确认）：{", ".join(sorted(writes)) or "无"}

输出严格 JSON：
{{"reason":"一句规划理由","steps":[{{"title":"可验证动作","tool":"工具名","args":{{"参数":"值"}}}}]}}

规则：
1. 基于已观察事实规划 1-8 步，不重复已经成功且结果仍有效的动作。
2. 每步只能调用一个可用工具；参数只能是短字符串。
3. 遇到权限、缺失对象或用户必须决定的分叉，不要虚构补全。
4. 最后用 summarize 汇总；不要输出 Markdown、思考过程或额外文字。
"""
    raw = _invoke_hermes(prompt, timeout=timeout)
    payload = _extract_plan_json(raw)
    rows = payload.get("steps") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for item in rows[:8]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool not in allowed or (no_write and tool in writes):
            continue
        raw_args = item.get("args")
        args = (
            {
                str(key): str(value)[:500]
                for key, value in raw_args.items()
                if isinstance(value, (str, int, float, bool))
            }
            if isinstance(raw_args, dict)
            else {}
        )
        out.append(
            {
                "title": str(item.get("title") or tool).strip()[:160],
                "tool": tool,
                "args": args,
                "requires_confirm": tool in writes,
            }
        )
    if out and not any(str(step.get("tool") or "") == "summarize" for step in out):
        if "summarize" in allowed:
            out.append({"title": "汇总结果并验收目标", "tool": "summarize", "args": {}})
    return out


def plan_text(goal: str, facts: str = "", *, allow_llm: bool = True) -> str:
    cleaned = _clean_goal(goal)
    if not cleaned:
        return "用法：`feishu plan <目标>`，例如：`feishu plan 拆解 A6 上线前检查`。"
    if allow_llm:
        rewritten = rewrite_plan(cleaned, facts)
        if rewritten:
            return rewritten
    return _fallback_plan(cleaned, facts)
