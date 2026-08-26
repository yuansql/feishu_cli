# feishu_cli → 飞书 aily 对齐路线图（PR 计划）

> 生成时间：2026-08-26
> 目标：把 feishu_cli 从「单人冷启动 CLI」向「群聊感知、零摩擦确认、多人共享」的 aily 体验推进。
> 当前对齐分约 77.4/100（`feishu aily`）；本路线图画出从 77.4 → ≥90 的可执行 PR 路径。

---

## 设计原则

1. **不推倒架构**：继续「脑 Hermes + 手 lark-cli + 闸 feishu serve/office」三层分工。
2. **不写 Y/n 阻塞**：所有用户确认点必须能在飞书 IM 内用交互卡片完成。
3. **飞书权威失败不装成功**：写操作无论走卡片还是文字，都保留回读验真。
4. **可逆可审计**：确认状态进 SQLite + 任务 JSON，支持超时撤销与幂等重放。
5. **先单点突破，再串成主线**：PR1 卡片确认 → PR2 群聊上下文 → PR3 多人共享运行时。

---

## PR1：交互式卡片确认闸（Card Consent）

### Why
当前 Agent v2 写操作被 `blocked` 后，用户必须回复「确认写入」文字。这是 CLI 思维，不是 aily 思维。飞书里一击 Approve/Decline 的卡片才是零摩擦。

### What
- 把 `tool_registry` 中 `confirmation == "required"` 的写操作阻塞点，改成由 `feishu serve` 推送一张交互式确认卡片。
- 卡片展示：写操作类型、目标（摘要）、影响范围 diff、即将执行命令原文。
- 用户点击「确认」/「取消」后，飞书回调驱动任务恢复，无需终端轮询。
- 确认状态持久化到 `runtime.db` + 任务 JSON，支持超时自动撤销、重复点击幂等。

### 改动模块

| 文件 | 改动 |
|------|------|
| `partner/runtime/tool_registry.py` | 增加 `approval_gate_info(tool, args) -> dict`：生成卡片需要展示的元数据。保留 `execute_tool(..., confirmed=True)` 语义不变。 |
| `partner/runtime/agent/graph.py` | `act_node` 中写操作未确认时，把 `pending_write` enriched 为 `{tool, args}` 而不是只存 reason。 |
| `partner/runtime/agent/service.py` | 新增 `_maybe_request_approval(task)`；`_run_task` 检测到 `blocked` 且 `pending_write` 时调用；新增 `confirm/decline_agent_writes_by_message(message_id)`。 |
| `partner/office/approval_card.py` | 新建：统一确认卡片生成器，支持 `approve/decline` 按钮、diff 展示、超时时间。 |
| `partner/core/events.py` | `CardAction` 增加 `task_id` / `message_id` / `token` 字段；`extract_card_action` 兼容新的 approval value。 |
| `partner/ops/serve.py` | 处理 `act in {"approve", "decline"}` 卡片回调；新增文本回复 fallback：用户回复确认/取消类关键词也能审批当前聊天的 pending approval。 |
| `partner/core/run_store.py` | 新建 `approvals` 表（含 `chat_id`、`expires_at`、`token` 及迁移），支持 request/approve/decline/get/expire 与按聊天查询。 |
| `tests/test_approval_card.py` | 覆盖卡片 payload、审批状态机、回调解析、超时撤销。 |
| `tests/test_serve_approval_text.py` | 覆盖文字回复审批 fallback。 |
| `scripts/check.sh` | 检测项目 venv（`~/.workbuddy/binaries/python/envs/feishu_cli`），存在则优先使用，避免依赖缺失导致检查失败。 |

### 数据模型

任务 JSON 新增：

```jsonc
{
  "pending_write": {
    "tool": "task_create",
    "args": {"summary": "..."},
    "display": {"title": "创建飞书待办", "body": "摘要：...", "risk": "low"},
    "approval": {
      "message_id": "om_xxx",
      "requested_at": "2026-08-26T12:00:00+08:00",
      "expires_at": "2026-08-26T12:05:00+08:00",
      "operator_id": "ou_xxx"
    }
  }
}
```

SQLite `approvals` 表（幂等 + 审计）：

```sql
CREATE TABLE IF NOT EXISTS approvals (
    task_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    expires_at TEXT NOT NULL DEFAULT '',
    operator_id TEXT,
    token TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES runs(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_approvals_message ON approvals(message_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_approvals_expires ON approvals(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_approvals_chat ON approvals(chat_id, status);
```

### 验收
- [x] 单元测试覆盖卡片生成、审批状态机、回调解析与超时撤销（`tests/test_approval_card.py`）。
- [x] 真实飞书机器人单聊收到确认卡片（heuristic 模式下也能触发的通用写权限卡片）。
- [x] `decline_agent_writes_by_message` + `approval_result_card` 链路手动验证通过，任务状态变为 `cancelled`。
- [x] 文字回复 fallback：用户回复「确认写入」/「取消」等关键词即可审批当前 pending approval，已单测覆盖，无需依赖卡片按钮回调。
- [ ] 飞书卡片按钮回调 + 文字消息事件尚未到达 `feishu serve`：`lark-cli event consume card.action.trigger` 与 `im.message.receive_v1` 均能启动并 ready，但实际点击/发送后未收到 payload。需检查飞书开放平台对应自建应用的 **事件与回调** 订阅（确认已勾选 `card.action.trigger`、`im.message.receive_v1`）、机器人 **读取/发送消息** 权限，并重新发布 + 重新授权安装。
- [x] 5 分钟未处理自动撤销：`_maybe_expire_approvals()` 已挂到 `feishu serve` 主循环（60 秒窗口），状态变为 `expired` 并发送过期通知卡片。
- [x] 同一任务连续点击不会出现重复写入（approval token + status 校验）。
- [x] `bash scripts/check.sh` 全绿（依赖安装到独立 venv）。

### 风险与缓解
- **回调跨进程不可靠**：approval 状态以 SQLite 为准，回调只负责转换状态；任务恢复时再次从 SQLite 读取状态。
- **越权操作**：卡片 `operator_id` 默认只允许任务发起者；群场景可后续扩展 owner list。
- **重放攻击**：卡片 `value` 中嵌入一次性 `token`（UUID），serve 校验 `record_trigger` / `approvals` 已存在才生效。

---

## PR2：群聊隐式上下文（Ambient Context）

### Why
当前每次 CLI/Agent 运行都是冷启动。aily 赢在能继承「谁在群里、谁 @ 谁、最近决策了什么」。

### What
- `feishu serve` 可选订阅目标群事件，把聊天元数据沉淀为「会话工作记忆」。
- 工作记忆有 TTL（默认 24h 衰减），用户可 `feishu setup --ambient-context off` 关闭。
- 记忆内容注入 `brain/Hermes` 与 `runtime/agent` 的 prompt，减少重复确认。

### 改动模块

| 文件 | 改动 |
|------|------|
| `partner/core/chat_context.py` | 新建：采集、衰减、查询聊天上下文；只存元数据（sender、@、message_id、timestamp、text），默认 TTL 24h。 |
| `partner/ops/serve.py` | 收到非 bot 消息时调用 `chat_context.ingest_inbound_message(msg)`；主循环每小时衰减一次旧上下文。 |
| `partner/runtime/agent/service.py` | `_run_task` 前把 `chat_context.recent_context(chat_id)` 注入 task observations。 |
| `partner/ops/setup.py` | `feishu setup` 增加 `--ambient-context {on,off}`，默认写入 `ambient_context: on`。 |
| `tests/test_chat_context.py` | 覆盖开关、采集、衰减、渲染。 |

### 隐私边界
- 只记录用户已加入的群。
- 元数据不进任何云端，只存本机 `~/.feishu-partner/chat_context/`。
- 提供 `feishu memory --clear-chat-context` 一键清空。

### 验收
- [ ] 在群里决策「下周一前把方案发了」后，私聊 Agent「跟进那个方案」能自动链接上下文。
- [ ] TTL 过期后旧上下文不再注入 prompt。
- [ ] 关闭配置后不再采集任何群事件元数据。
- [ ] 单元测试覆盖上下文衰减与注入。

---

## PR3：多人共享运行时（Shared Runtime / intent-patch）

### Why
当前 Agent 任务是单用户的。ily aily 的终极体验是「群里每个人都可以 @ 伙伴，接力推进同一件事」。

### What
- 把一条 Agent 任务变成可追加 `intent-patch` 的运行图。
- 多个用户在群里的 @、回复、卡片点击都能追加 patch。
- Hermes 担任主持人，出现冲突时发澄清卡片。
- 确认闸可转让：`holder_open_id` 超时后可由他人回复「我来确认」接管。

### 改动模块

| 文件 | 改动 |
|------|------|
| `partner/runtime/agent/store.py` | 增加 `intent_patches: list[dict]` schema：每个 patch 含 `author_open_id`、`ts`、`text`、`action`（append/override/cancel）。 |
| `partner/runtime/agent/service.py` | 新增 `append_intent_patch(task_id, patch)`；`continue_agent_task` 先合并 patches 再 `run_loop`。 |
| `partner/runtime/agent/graph.py` | `AgentState` 增加 `patches`；`think_node` 合并 patches 到 goal/observations。 |
| `partner/routing/p2p_router.py` | 冲突仲裁：当同一任务的 patches 不兼容时，生成多选澄清卡片发到群聊。 |
| `partner/ops/serve.py` | 群聊 @ 伙伴时，如果已有同 `chat_id` 运行中任务，则追加 patch 而不是新建任务。 |
| `partner/compose/formatters.py` | 新增「任务进度简报」卡片：定时/手动广播「谁在等什么 / 下一步由谁决定」。 |

### 验收
- [ ] 用户 A 在群里说「写个下周计划」，用户 B 回复「再加一条风控复盘」，同任务自动追加 patch。
- [ ] 用户 C 在任务卡住时点击「我来确认」可接管写操作。
- [ ] 两人同时给出矛盾 patch 时，Hermes 在群里发澄清卡片。
- [ ] `feishu runs` 新增 `--patches` 查看运行图。

---

## PR4：Aily 对齐分刷新与能力补齐

每完成一个 PR 后，更新 `partner/ops/aily.py` 的 `CAPABILITIES` 分数与 gap 描述。

预期得分走势：

| PR | 预期提升 | 关键能力项 |
|----|---------|-----------|
| PR1 | +3~5 | `feishu_tools`, `agent_runtime`, `governance` |
| PR2 | +4~6 | `memory`, `agent_runtime` |
| PR3 | +5~8 | `multi_agent`, `agent_runtime`, `memory` |

---

## 执行顺序

1. **PR1**（卡片确认闸）→ 立即提升体验，风险最低。
2. **PR2**（群聊上下文）→ 为 PR3 铺垫，单独也能提升「懂上下文」体验。
3. **PR3**（多人共享运行时）→ 架构改动最大，放到最后。
4. **PR4**（对齐分刷新）→ 每个 PR 合并时同步更新。

---

## 附录：与现有 AGENTS.md 红线的关系

- **不改 Hermes 直连飞书写网关**：写操作仍经 `lark-cli`，只是确认方式改成卡片。
- **不泄露 `open_id`/secret**：卡片 value 中可以用任务 UUID 代替 open_id。
- **不对接 Aily 官方 API**：所有能力本地实现。
- **群消息不触发任意 shell**：PR1/PR2/PR3 都不引入 `--yolo`。确认卡片只映射到 `confirm_agent_writes` / `cancel_agent_task`。
