# 架构说明（feishu_cli）

> 2026-08-20 重构落地。2026-08-21 **钉死分工**：Hermes 脑 + 飞书 CLI 手 + 本仓闸。

## 结论：本机 Hermes × 吴梦晨飞书 CLI（不是推倒成纯 Hermes 网关）

| 角色 | 谁干 | 说明 |
|---|---|---|
| **脑** | 本机 Hermes（`feishupartner`） | 复杂句多轮想；只读 MCP |
| **手** | 官方 `lark-cli`（user OAuth） | 经本仓白名单 MCP 调用，即已授权飞书能力 |
| **闸 / 壳** | 本仓 `feishu serve` + office | 收消息、短指令、ack、确认写入、幂等 |
| **后备脑** | LangGraph Agent v2 | Hermes 不可用时；写仍确认闸 |

**不做**：把伙伴改成「Hermes 直连飞书、全技能可写」的第二网关（能力面大、失控面也大）。

短指令（今天 / 待办 / 简报 / 帮助 / 销账 / 写入确认…）仍走 `office` 快路径。  
**材料类**（读消息 / 搜索 / 读文档 / 收件箱 / 某人怎么说 / 哪个群…）单聊经 Hermes 归纳后再回，禁止整段原文甩料。长任务细节见 [RUNTIME_V2.md](RUNTIME_V2.md)。

| 层 | 实际技术 |
|---|---|
| 语言运行时 | Python 3 + 标准库 + **langgraph / langchain-core**（后备长任务） |
| 飞书手 | 官方 [`lark-cli`](https://github.com/larksuite/cli)（user 办事 / bot 收发） |
| 主脑 | **Hermes** 隔离档案 + `feishu mcp` 只读白名单（禁 `--yolo`、禁终端） |
| 后备脑 | LangGraph think→act（`partner/runtime/agent/`） |
| 进程 | `feishu serve` + macOS LaunchAgent；定时 brief/followup |
| 状态 | `~/.feishu-partner/`（session / followups / traces / config） |
| 入口 | `bin/feishu` → `python -m partner` → `partner.ops.cli` |
| 飞书技能正文 | 仓内 `skills/lark-*`；Cursor 经 `.cursor/skills/` 软链；`scripts/sync-lark-skills.sh` |

产品对标飞书 Aily **能力清单**，**不对接** Aily 接口。计分见 `feishu aily` / `AILY_ALIGNMENT.md`。

## 包分层（物理目录）

```text
partner/
  paths.py          # REPO_ROOT / PACKAGE_DIR / WORKFLOWS_DIR（禁 Path(__file__) 爬层级）
  actions.py        # 对外 facade：re-export office + IM dispatch / partner_reply
  workflows/        # 内置 Workflow JSON
  core/             # 平台原语：lark / ids / session / events / ack / inbox / trace / run_store
  routing/          # 理解用户话：intents / mode_router / resolved
  runtime/          # Agent：agent/(LangGraph v2) + runner 兼容壳 / planner / tool_registry / …
  office/           # 办公域：brief / followup / recap / bitable / calendar_views / tasks_io / docs_io / …
  compose/          # 生成与呈现：llm / hermes_setup / formatters
  ops/              # 运维入口：cli / serve / setup / eval / versions / aily / webhook / mcp_*
```

依赖方向（硬）：`ops → actions/office/runtime → compose/routing/core`；**禁止** core 依赖 office。

## 主路径

```text
飞书消息
  → ops/serve（WS event）
  → routing/intents + actions.dispatch
  → office_* 取数 或 runtime/runner（任务模式）
  → compose/llm（单聊润色，可关）
  → core/lark（bot 回复）
```

## 已删除的废弃物

| 路径 | 原因 |
|---|---|
| `AILY_AGENT_PROMPT.md` | 已标明废弃的 Aily 控制台提示词 |
| 仓根 `serve.log` | 运行日志；应在 `~/.feishu-partner/`，已加入 `.gitignore` |

## 扩展约定

1. 新办公能力 → `office/`，经 `actions.py` re-export（保持 `partner.actions` 稳定）。
2. 新 Agent 步骤/工具 → `runtime/tool_registry.py`，不要在 serve 里直接 subprocess。
3. 路径一律 `partner.paths`。
4. 身份 / open_id **不进 git**；`feishu setup` → `~/.feishu-partner/config.json`。
