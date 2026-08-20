# 飞书 Aily 功能对齐（本地独立 · 功能不低于 Aily）

日期：2026-08-20  
口径：以飞书 Aily / 豆包工作伙伴公开能力为**对标清单**；**不对接 Aily**；**本地功能不得低于对标项**。总权重 100。

## 结论

- **当前本地实现：约 70.0/100**（随 P1+ 开发更新，运行 `feishu aily` 查看）
- **目标：≥90/100（误差 ≤10%，且单项能力不低于官方对标行为）**
- **路径：全部在本仓库单独开发**，不依赖 Aily 运行时

`feishu aily` 从 `partner/aily.py` 输出 14 项加权矩阵与每项待开发差距。

## 产品定位

```text
飞书客户端（单聊 / 群 @）
        │
        ▼
本机 feishu serve / CLI / Cron / Webhook
        │
        ▼
Local Agent Runtime（唯一内核 · 深化 runner.py）
  ├─ 模型推理 / Workflow / 知识问答 / 混合调度
  ├─ 任务模式：后台 worker + 持久状态 + 通知
  ├─ Multi-Agent：本地 researcher/executor/writer 子角色（已落地）
  ├─ 本地沙箱：隔离目录 + 命令白名单（已落地）
  ├─ 本地 HTML 报告：report_write + feishu report（已落地）
  └─ ToolRegistry：runner / MCP / Hermes 统一工具契约（已落地）
        │
        ▼
lark-cli（user 读办事 / bot 收发）+ 确认闸 + 回读验真
```

MCP HTTP（`feishu mcp-http`）仅作**可选对外扩展**（Cursor/Hermes 等），不是 Aily 桥接，也不是达标前提。

## 官方能力基线（对标用，非依赖）

1. Agentic 循环、Multi-Agent、智能体电脑、异步长任务  
   https://www.feishu.cn/content/article/7585126677299137754
2. 四种对话模式：模型推理、Workflow、知识问答、混合调度  
   https://www.feishu.cn/content/kdvbmrpn
3. Agent 技能与工具调用  
   https://www.feishu.cn/content/51zo70vs
4. Workflow 节点编排  
   https://www.feishu.cn/content/8u02e8ub
5. 定时、Webhook、飞书事件触发  
   https://www.feishu.cn/content/xb36mver
6. 企业知识问答 / RAG  
   https://www.feishu.cn/content/euuvns8t
7. 评测与调优  
   https://www.feishu.cn/content/bpp8g117
8. 运行日志与可观测性  
   https://www.feishu.cn/content/qrb2teig

## 已落地（本地）

- **TaskRunner v2**：observe→plan→act→verify→replan；确认闸、取消、恢复
- **异步 worker**：飞书收消息不阻塞；任务队列 + 完成通知
- **运行轨迹**：`~/.feishu-partner/traces/<task>.jsonl`（脱敏）
- **办公闭环**：DayRecap、ArtifactTask、简报/跟进、飞书读写工具、**本地 HTML 报告**
- **MCP**：stdio + 可选 HTTP 网关（外部 Agent 用，非 Aily）
- **Clone 即用**：`feishu setup --name 名字` → `~/.feishu-partner/config.json`（不进 git）

## 待单独开发（本地路线图）

| 阶段 | 模块 | 预期加分 |
|------|------|----------|
| P1 | SQLite `run_store` + **`tool_registry`** + **`webhook`** | ~55 |
| P2 | **本地 Multi-Agent**（researcher/executor/writer · 已落地） | ~58 |
| P3 | **Workflow DSL + 混合模式路由**（已落地） | ~62 |
| P4 | **轻量 RAG**（切片/术语/召回 · 已落地） | ~68 |
| P5 | **本地沙箱**（目录隔离 + 命令白名单 · 已落地） | ~72 |
| P6 | **`feishu eval` + `feishu versions`**（已落地） | ~70 |
| P7 | **Agents.md / 经验档案 + Workflow when/unless**（已落地） | ~70 |

企业计费、租户管理员 UI、应用市场审核标 **N/A**，不参与单用户 CLI 计分。

## 不做

- **不对接 Aily**（API、控制台、官方 MCP 绑定、verified_at 验收）
- 不把密钥/token 提交进仓库
- 不让群消息触发任意 shell（无 `--yolo`）
- 不假装已有云 VM 级沙箱（本地沙箱须明示边界）

## 最新验收 · 2026-08-20

| 项 | 结果 |
|---|---|
| 全量单测 | **297 passed**（`python3 -m unittest discover -s tests -q`） |
| `feishu setup` / `feishu doctor` | 身份闸 + OAuth 探针 |
| `feishu report` | 本地 HTML 落盘 `~/.feishu-partner/reports/` |
