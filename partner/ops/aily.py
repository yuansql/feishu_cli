from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    weight: int
    local_score: int
    target_score: int
    target: str
    current: str
    gap: str
    source: str


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "agent_runtime",
        "Agentic 循环与动态规划",
        14,
        92,
        100,
        "观察事实后规划，工具执行、验证、失败重规划并交付。",
        "Agent v2 支持 observe→plan→act→verify→replan；卡片/文字双通道写前确认；blocked 状态可超时撤销、可转让；SQLite 持久化 + 租约恢复。",
        "待补：超长任务断点续跑；group owner 列表精细化权限。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "async_sandbox",
        "异步长任务与隔离环境",
        10,
        78,
        100,
        "后台运行数小时，具备独立文件/网络/代码环境和完成通知。",
        "后台 worker + 本地沙箱（读写隔离、命令白名单、可配置超时与并发配额）。",
        "待补：Playwright 浏览器；仍非云 VM。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "multi_agent",
        "Multi-Agent / 子智能体",
        7,
        86,
        100,
        "按办公领域调用专门子智能体协作完成复杂任务。",
        "本地 researcher/executor/writer 并行生成 brief；同 chat 任务支持多人 intent-patch、写权限转让「我来确认」、冲突澄清卡片。",
        "待补：复杂任务并行子规划；更细粒度的群成员角色与权限。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "feishu_tools",
        "飞书工具与办公闭环",
        8,
        86,
        100,
        "深度操作文档、日历、任务、多维表、纪要、审批等。",
        "读写工具、权威失败判断、文档回读验真；飞书交互式确认卡片 + 文字回复 fallback 形成零摩擦办公闭环。",
        "待补：富文档块编辑、通用多维表写回、更多连接器；卡片按钮回调待开放平台事件订阅验证。",
        "https://www.feishu.cn/content/51zo70vs",
    ),
    Capability(
        "workflow",
        "确定性 Workflow 编排",
        9,
        84,
        100,
        "分支、循环、代码、HTTP、知识和连接器节点的稳定流程。",
        "JSON Workflow DSL：when/unless、repeat/until 循环、http 步骤；混合模式路由；内置晨间/会议/审批/点名跟进。",
        "待补：可视化编辑、连接器市场。",
        "https://www.feishu.cn/content/8u02e8ub",
    ),
    Capability(
        "triggers",
        "定时/Webhook/飞书事件触发",
        7,
        88,
        100,
        "环境变化主动触发技能并推送结果。",
        "schedule/message/webhook 三类声明式触发器；serve 轮询 schedule、消息关键词匹配、webhook 路径路由；统一 trigger_event_log 幂等审计；CLI `feishu triggers` 管理。",
        "待补：更复杂的事件源（多维表/日程变更）、可视化触发器管理。",
        "https://www.feishu.cn/content/xb36mver",
    ),
    Capability(
        "knowledge",
        "企业知识与数据问答",
        9,
        70,
        100,
        "多源知识/RAG/NL2SQL、术语、召回参数和持续调优。",
        "轻量 RAG：本地切片、术语扩展、关键词 + hashed n-gram 向量召回；`feishu rag sync` 增量刷新。",
        "待补：神经网络向量、NL2SQL、调参 CLI。",
        "https://www.feishu.cn/content/euuvns8t",
    ),
    Capability(
        "eval",
        "评测与效果调优",
        7,
        72,
        100,
        "线上日志转用例、批量评测、人工评分和 Badcase 调优。",
        "feishu eval：高风险路由 fixtures + 本地 trace 完成/失败统计。",
        "待补：线上日志一键转用例、人工评分台。",
        "https://www.feishu.cn/content/bpp8g117",
    ),
    Capability(
        "observability",
        "运行日志与可观测性",
        5,
        90,
        100,
        "查看输入、命中技能、节点 I/O、状态、耗时和反馈。",
        "JSONL traces + feishu eval + feishu runs + 统一 trigger_event_log 审计；状态机/触发/群上下文均可查。",
        "待补：token 汇总与 Web 仪表盘。",
        "https://www.feishu.cn/content/qrb2teig",
    ),
    Capability(
        "artifacts",
        "多形态产物交付",
        7,
        78,
        100,
        "报告、云文档、表格、网页、图片、音频等可继续编辑的产物。",
        "本地 HTML 报告 + SVG 图表 + WAV 提示音 + 云文档/卡片/ArtifactTask；上传飞书仍走确认闸。",
        "待补：真实语音合成、批量上传与更多富媒体。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "mcp",
        "MCP 与企业系统连接",
        5,
        88,
        100,
        "官方/自定义 MCP 可被 Agent 自主规划调用。",
        "ToolRegistry 统一 runner/MCP 工具面；stdio + 可选 HTTP 网关。",
        "待补：外部 MCP 插件注册与 Planner 自动发现。",
        "https://www.feishu.cn/content/article/7597739180457806780",
    ),
    Capability(
        "publish",
        "发布、版本、Diff 与回滚",
        4,
        72,
        100,
        "变更先发布，保留版本记录、差异和一键回滚。",
        "feishu versions publish/list/diff/rollback 快照 Workflow、知识源、术语表。",
        "待补：技能提示词版本与二进制产物回滚。",
        "https://www.feishu.cn/content/kjtmg83u",
    ),
    Capability(
        "governance",
        "权限、审计与组织治理",
        4,
        66,
        95,
        "可用范围、角色、权限自动检测、审计和企业统一管控。",
        "user/bot 隔离、doctor scope 探针、本机身份闸（feishu setup）；写前审批状态机持久化 SQLite，含 token 幂等、超时自动撤销、操作者审计。",
        "单用户 CLI：企业 RBAC 标 N/A；可扩展 group owner 列表与更细粒度审批策略。",
        "https://www.feishu.cn/content/kjtmg83u",
    ),
    Capability(
        "memory",
        "上下文、记忆与经验沉淀",
        4,
        88,
        100,
        "用户/群上下文隔离，运行经验可复用并持续进化。",
        "Agents.md + experience.jsonl；Agent v2 任务运行前注入群聊隐式上下文（TTL 24h）；任务支持多人 intent-patch；feishu memory CLI 可清空聊天上下文。",
        "待补：自动从 Badcase 提炼 Agents.md 段落；跨任务经验关联与检索增强。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
)


def alignment_score() -> float:
    points = sum(item.weight * item.local_score / 100 for item in CAPABILITIES)
    return round(points, 1)


def alignment_target_score() -> float:
    points = sum(item.weight * item.target_score / 100 for item in CAPABILITIES)
    return round(points, 1)


def aily_config_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_AILY_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "parity.json"


def load_parity_config() -> dict[str, object]:
    path = aily_config_path()
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return blob if isinstance(blob, dict) else {}


def record_mcp_probe(*, mcp_url: str, client: str = "") -> None:
    """Optional local MCP gateway smoke record; not an Aily integration gate."""
    path = aily_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcp_url": (mcp_url or "").strip(),
        "client": (client or "").strip()[:200],
        "probed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(
            timespec="seconds"
        ),
        "transport": "streamable-http",
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# Backward-compatible alias for existing imports/tests.
load_aily_config = load_parity_config
record_aily_verification = record_mcp_probe


def alignment_text() -> str:
    current = alignment_score()
    target = alignment_target_score()
    lines = [
        "飞书 Aily 能力对标（本地完全独立，功能不低于 Aily）",
        "",
        f"- 当前实现：{current:.1f}/100，差距 {100 - current:.1f}%",
        f"- 本地目标：{target:.1f}/100（逐项开发直至不低于官方对标能力）",
        "- 不对接 Aily；缺口全部在本仓库实现",
        "",
        "评分规则：14 项总权重 100；每项需官方对标、本地代码与行为验收。",
        "达标：适用能力得分 ≥ 对应官方项，总体误差 ≤10%。",
        "",
    ]
    for item in CAPABILITIES:
        lines.extend(
            [
                f"【{item.id} · {item.weight}分 · 当前{item.local_score}% → 目标{item.target_score}%】{item.name}",
                f"- 对标目标：{item.target}",
                f"- 本项目：{item.current}",
                f"- 待开发：{item.gap}",
                f"- 官方：{item.source}",
                "",
            ]
        )
    lines.extend(
        [
            "达标门槛：",
            "- 按 AILY_ALIGNMENT.md 路线图逐项本地实现；每项验收通过才计满分。",
            "- 不以接入飞书 Aily 为条件；功能不得低于官方公开对标能力。",
        ]
    )
    return "\n".join(lines).rstrip()
