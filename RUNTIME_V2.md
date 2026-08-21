# Agent Runtime v2（思考型）

> 2026-08-21 · 替换弱 `runner` 线性模板，目标：**Agent 自己想下一步**，少靠修 intent/正则。  
> 2026-08-21 修订：**Hermes 飞书脑优先**；LangGraph / OpenAI 兼容作后备。

## 产品边界

| 路径 | 行为 |
|---|---|
| 短指令（今天/待办/帮助/销账…） | 仍走 `office` + `actions.dispatch`（快、确定） |
| 复杂句 / 规划拆解（单聊） | **优先 Hermes**（`feishupartner` 档案 + 飞书 MCP 白名单，多轮取数） |
| Hermes 不可用或空答 | **Agent Runtime v2**（LangGraph：想→工具→观察→再想） |
| 写飞书 / 跟进账 | **确认闸**：到写步骤暂停，等「确认写入」；Hermes **永不** `--yolo`、MCP 无写工具 |

## Hermes 飞书对接

- 档案：`~/.hermes/profiles/feishupartner`（`feishu setup` / doctor 会刷新）
- MCP 只读：`feishu_today` / `tasks` / `digest` / `day_recap` / `memory` / `knowledge` / `identity` / `chats` / `person` / …
- 写类工具不进 MCP；需要写入时用户说短指令或「确认写入」
- **产品钉死**：不改成 Hermes 直连全量 `lark-*` 可写网关；CLI 能力经白名单 MCP 暴露给 Hermes

## 配置

本机文件（不进 git）：

| 文件 | 用途 |
|---|---|
| `~/.feishu-partner/config.json` | 身份（`feishu setup`） |
| `~/.feishu-partner/agent.json` | Agent 脑/步数/是否强制 LLM（`feishu agent init`） |

仓内模板：`agent.example.json`。环境变量可覆盖同名项。

| `brain` | 含义 | 要 key 吗 |
|---|---|---|
| `auto`（默认） | 有 Hermes 用 Hermes，否则启发式；若配了 OpenAI key 也可作后备 | Hermes 通常不用再配；openai 后备才要 |
| `hermes` | 只用 Hermes | 否（用本机 Hermes 登录） |
| `heuristic` | 不用模型 | 否 |
| `openai` | OpenAI 兼容 HTTP | **要** `openai_api_key` 或 `OPENAI_API_KEY` |

`require_llm: true` 时：没有可用模型就结束并说明，不静默掉启发式。

## 失败态矩阵

| 步骤 | 失败时本地任务 | 飞书权威 | 用户侧 |
|---|---|---|---|
| 决策 LLM 失败 | 记 trace，改启发式再试；仍失败 → `failed` | 不写 | 明示「决策失败」 |
| 读工具失败 | step/消息记错误，再想（换工具或结束） | 不写 | 摘要里带权威失败原文 |
| 写工具未确认 | `blocked/confirmation`，pending_writes 持久化 | **不写** | 提示「确认写入」 |
| 确认后飞书失败 | step `failed`，不装成功 | 库/飞书以飞书为准 | 回传错误，不说已写上 |
| 取消 | `cancelled` | 不追加写 | 可新建任务 |

## 模块

```text
partner/runtime/agent/
  brain.py    # 决策：LLM JSON 或启发式
  graph.py    # LangGraph StateGraph
  service.py  # start/continue/confirm/status/cancel（对外）
  store.py    # 任务检查点（复用 ~/.feishu-partner/tasks，schema 3）
```

旧 `runner.py`：保留给单测与 `FEISHU_PARTNER_LEGACY_RUNNER=1`；默认 `start_task` 走 v2。

## 合格线

1. 「规划/拆解」默认进 v2，能多轮选工具并结束  
2. 写工具必须确认后才 `execute_tool(..., confirmed=True)`  
3. 飞书权威失败不报成功  
4. 单测：mock brain 金路径 + 确认闸；全量原有测试仍绿  
