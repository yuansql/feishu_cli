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
        70,
        100,
        "观察事实后规划，工具执行、验证、失败重规划并交付。",
        "TaskRunner v2 已支持 observe→plan→act→verify→replan、确认闸、取消和恢复。",
        "待补：SQLite 持久与租约恢复；规划质量依赖本地模型可用性。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "async_sandbox",
        "异步长任务与隔离环境",
        10,
        68,
        100,
        "后台运行数小时，具备独立文件/网络/代码环境和完成通知。",
        "后台 worker + 本地沙箱目录（读写隔离、python3/ls 白名单、超时截断）。",
        "待补：Playwright 浏览器、更长超时与进程配额；仍非云 VM。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "multi_agent",
        "Multi-Agent / 子智能体",
        7,
        62,
        100,
        "按办公领域调用专门子智能体协作完成复杂任务。",
        "本地 researcher/executor/writer 三角色在动态规划前协作，结果写入 trace 与任务状态。",
        "待补：按目标类型自动选角色子集；复杂任务并行子规划。",
        "https://www.feishu.cn/content/article/7585126677299137754",
    ),
    Capability(
        "feishu_tools",
        "飞书工具与办公闭环",
        8,
        80,
        100,
        "深度操作文档、日历、任务、多维表、纪要、审批等。",
        "已有读写工具、权威失败判断、写前确认和文档回读验真。",
        "待补：富文档块编辑、通用多维表写回、更多连接器。",
        "https://www.feishu.cn/content/51zo70vs",
    ),
    Capability(
        "workflow",
        "确定性 Workflow 编排",
        9,
        58,
        100,
        "分支、循环、代码、HTTP、知识和连接器节点的稳定流程。",
        "JSON Workflow DSL（内置 + ~/.feishu-partner/workflows.json）；混合模式路由 workflow/knowledge/task/model。",
        "待补：条件/循环节点、HTTP 步骤、可视化编辑。",
        "https://www.feishu.cn/content/8u02e8ub",
    ),
    Capability(
        "triggers",
        "定时/Webhook/飞书事件触发",
        7,
        72,
        100,
        "环境变化主动触发技能并推送结果。",
        "LaunchAgent、消息/卡片事件、多维表轮询；Webhook HMAC 入队后台任务。",
        "待补：任务配置 CLI、周期任务声明式配置。",
        "https://www.feishu.cn/content/xb36mver",
    ),
    Capability(
        "knowledge",
        "企业知识与数据问答",
        9,
        58,
        100,
        "多源知识/RAG/NL2SQL、术语、召回参数和持续调优。",
        "轻量 RAG：本地切片索引、术语扩展、keyword 召回；知识问答模式优先命中索引。",
        "待补：向量召回、自动增量同步、调参 CLI。",
        "https://www.feishu.cn/content/euuvns8t",
    ),
    Capability(
        "eval",
        "评测与效果调优",
        7,
        35,
        100,
        "线上日志转用例、批量评测、人工评分和 Badcase 调优。",
        "已有回归 fixtures、全量单测和高风险路由样例。",
        "待开发：feishu eval 读 trace/fixtures 批量跑分。",
        "https://www.feishu.cn/content/bpp8g117",
    ),
    Capability(
        "observability",
        "运行日志与可观测性",
        5,
        70,
        100,
        "查看输入、命中技能、节点 I/O、状态、耗时和反馈。",
        "Agent 任务写入脱敏 JSONL：计划版本、步骤 I/O、耗时、失败。",
        "待补：按 run 查询 CLI、耗时/token 汇总（无 Web 台）。",
        "https://www.feishu.cn/content/qrb2teig",
    ),
    Capability(
        "artifacts",
        "多形态产物交付",
        7,
        50,
        100,
        "报告、云文档、表格、网页、图片、音频等可继续编辑的产物。",
        "已有云文档、卡片、ArtifactTask 原地续改与验真。",
        "待开发：本地生成 HTML/图片/音频产物并上传飞书。",
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
        20,
        100,
        "变更先发布，保留版本记录、差异和一键回滚。",
        "本地仅有 Git 与部署文档。",
        "待开发：技能/Workflow 版本目录 + diff/rollback CLI。",
        "https://www.feishu.cn/content/kjtmg83u",
    ),
    Capability(
        "governance",
        "权限、审计与组织治理",
        4,
        35,
        95,
        "可用范围、角色、权限自动检测、审计和企业统一管控。",
        "已有 user/bot 隔离、doctor scope 探针、确认闸和脱敏日志。",
        "单用户 CLI：企业 RBAC 标 N/A；保留 scope 探针与写前确认。",
        "https://www.feishu.cn/content/kjtmg83u",
    ),
    Capability(
        "memory",
        "上下文、记忆与经验沉淀",
        4,
        55,
        100,
        "用户/群上下文隔离，运行经验可复用并持续进化。",
        "已有 session、跟进账、任务/工件状态和证据上下文。",
        "待开发：本地 Agents.md / 经验档案与跨会话复用。",
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
