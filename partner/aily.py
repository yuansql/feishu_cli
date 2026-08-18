from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    target: str
    current: str
    gap: str
    next_step: str
    status: str


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "飞书内对话办事",
        "在飞书里自然语言发起任务、查看进展、验收结果。",
        "已有 `feishu serve`、单聊/群聊路由、OnIt 反馈、JSON 2.0 卡片。",
        "卡片按钮仍依赖开放平台订阅；群里只做安全范围内的回复和记账。",
        "继续补卡片回调与失败重发覆盖。",
        "接近",
    ),
    Capability(
        "企业知识问答",
        "连接文档/表格/业务系统，检索后结构化回答并可持续调优。",
        "已有文档搜索、文档读取、单文档要点分析、Hermes 改写。",
        "没有完整 RAG 索引、术语库、评测台、多源召回排序。",
        "先把常用知识源做成可配置清单，再加答案质量样例集。",
        "部分",
    ),
    Capability(
        "任务规划/任务模式",
        "把复杂目标拆成多步子任务，有序执行、调用工具并跟踪结果。",
        "本轮新增 `feishu plan <目标>` 和“规划/拆解”入口，结合今天、待办、跟进账生成计划。",
        "当前只生成计划；没有异步沙箱、自动执行或结果跟踪，飞书写操作仍走现有安全工具。",
        "经用户确认后，把计划步骤写入跟进账或飞书待办。",
        "部分",
    ),
    Capability(
        "工作流与定时任务",
        "对话、Webhook、定时任务都能触发 AI 自动化。",
        "已有 09:00 简报、今日待跟进、周一任务、多维表轮询、LaunchAgent。",
        "没有通用可视化工作流编排和 Webhook 触发器。",
        "抽象 workflow 配置，让 brief/followup 成为可复用技能。",
        "部分",
    ),
    Capability(
        "飞书工具操作",
        "能操作文档、多维表格、任务等高频模块。",
        "已有日程、待办、文档、会话、纪要、审批、多维表任务。",
        "写操作仍很少，且都必须以飞书权威结果为准。",
        "优先补安全的 task/create、doc/create 模板、bitable record 操作。",
        "接近",
    ),
    Capability(
        "数据表/多维表数据",
        "数据表可读写，可由技能查询，用于业务数据管理。",
        "已有本周任务表、例行模板、表格艾特扫描。",
        "没有通用数据表 schema、OQL/BI、Excel 导入能力。",
        "把 bitable 配置扩展成多表 schema + 查询模板。",
        "部分",
    ),
    Capability(
        "记忆与隔离",
        "每个用户/群有独立记忆，凭证中心化且安全隔离。",
        "已有会话 session、跟进账、Hermes 隔离档案、MCP allowlist。",
        "不是企业级记忆系统；没有后台可视化管理和回滚。",
        "补每群/每人偏好摘要，但只存必要工作事实。",
        "部分",
    ),
    Capability(
        "多渠道发布",
        "发布到飞书机器人、服务台、Web、自建系统。",
        "已有 CLI、飞书机器人、MCP stdio。",
        "没有服务台、Web 入口和开放 API 服务。",
        "如果需要外部入口，再加只读 HTTP API。",
        "缺口",
    ),
    Capability(
        "效果调优与评测",
        "术语、规则、评测反馈、变更记录、回滚。",
        "已有单元测试、泄漏过滤、缺权限显式失败。",
        "没有产品化 eval 集、答案评分、版本 diff/rollback。",
        "建立 eval fixtures，覆盖典型飞书问题和禁止误搜场景。",
        "缺口",
    ),
    Capability(
        "管理后台/额度/模型",
        "模型、权限、额度、日志、企业管理后台。",
        "已有 `doctor` 权限探针和本地配置文件。",
        "不做 Aily 工作台、计费额度、企业后台克隆。",
        "保持 doctor 可诊断，不把后台做进自建 CLI。",
        "不做",
    ),
    Capability(
        "文档/PPT/网页生成",
        "能生成文档、PPT、网页原型等内容资产。",
        "已有写周报并创建飞书云文档。",
        "没有 PPT/网页生成器，也没有素材资产管线。",
        "先补常用文档模板，再评估是否接 PPT/网页工具。",
        "缺口",
    ),
)


def status_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for item in CAPABILITIES:
        out[item.status] = out.get(item.status, 0) + 1
    return out


def alignment_text() -> str:
    counts = status_counts()
    lines = [
        "飞书 Aily / 豆包工作伙伴功能对齐（自建版）",
        "",
        "口径：对齐办公闭环和可用能力，不克隆官方工作台、额度、企业后台。",
        "当前统计："
        + " / ".join(f"{key} {counts[key]}" for key in sorted(counts)),
        "",
    ]
    for item in CAPABILITIES:
        lines.extend(
            [
                f"【{item.status}】{item.name}",
                f"- Aily 目标：{item.target}",
                f"- 本项目：{item.current}",
                f"- 差距：{item.gap}",
                f"- 下一步：{item.next_step}",
                "",
            ]
        )
    lines.extend(
        [
            "本轮优先补齐：",
            "- `feishu plan <目标>`：把复杂目标拆成可执行计划，并带上今天/待办/跟进上下文。",
            "- 飞书内自然语言：说「规划 A6 上线」或「拆解 写周报」会走同一能力。",
        ]
    )
    return "\n".join(lines).rstrip()
