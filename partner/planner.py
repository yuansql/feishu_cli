from __future__ import annotations

import re

from .llm import rewrite_plan


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


def plan_text(goal: str, facts: str = "", *, allow_llm: bool = True) -> str:
    cleaned = _clean_goal(goal)
    if not cleaned:
        return "用法：`feishu plan <目标>`，例如：`feishu plan 拆解 A6 上线前检查`。"
    if allow_llm:
        rewritten = rewrite_plan(cleaned, facts)
        if rewritten:
            return rewritten
    return _fallback_plan(cleaned, facts)
