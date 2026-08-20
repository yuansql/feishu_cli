# 飞书工作伙伴 · 部署说明

给**另一台 Mac / 另一个飞书账号**把本项目跑起来用。照着做即可，不必改业务代码。

本仓是**完全本地独立**的飞书办公智能体：手是官方 [`lark-cli`](https://github.com/larksuite/cli)，本地运行时是 `partner/`。**不对接飞书 Aily 平台**；Aily 级能力在本仓内单独开发，路线图见 [AILY_ALIGNMENT.md](AILY_ALIGNMENT.md)。

## 部署完能做什么

| 能力 | 说明 |
|------|------|
| 单聊问答 | 「今天 / 明天 / 待办 / 本周的周报 / 会议纪要 / 审批 / 搜 关键词」 |
| 飞书内回复 | 本机跑 `feishu serve`，机器人在单聊（或群里被 @）时回答 |
| 一句话作答 | 对上 1 份文档就读重点；对上多份先问哪件，不甩搜索列表 |
| 监视 @你 | **仅**机器人所在群：@你 / 点名指派 → 记 inbox，该推的推单聊；说「谁找我」 |
| 周报 | 按人名搜个人周报文档再改写成【工作内容】【重点项目】【下周】 |
| 每天 09:00 简报 | **必装** LaunchAgent：昨天小结 + 今天规划，推到部署者与机器人的单聊 |
| 今天工作回顾 | 「我今天干了什么 / 读今天消息」→ 分页读取当天跨会话消息 → 按证据归纳已做/推进中/待确认；「继续确认」沿用上下文 |
| 任务模式 | 单聊「任务模式/规划/拆解 …」→ 后台先观察 → 动态规划 → 执行/验真/重规划；支持进度、取消、恢复和确认写入 |
| 现有文档续改 | 贴链接说「写到…下面」→ 先看草稿 →「写进去」原地更新并回读；「多一点」续改同一 block |

电脑睡觉或没开 `feishu serve` 时，飞书里**不会回、也不会监视**。

### 任务模式与知识源（可选）

Agent 任务落在 `~/.feishu-partner/tasks/`，脱敏轨迹落在 `~/.feishu-partner/traces/`；文档工件任务落在 `~/.feishu-partner/artifacts.json`；当天消息回顾的证据与结论只在 `~/.feishu-partner/session.json` 保留 12 小时。写操作（跟进账、飞书待办、云文档）**必须**用户明确说「确认写入」或「写进去」后才执行。

知识源优先级（默认：云文档 → 知识库空间）可在 `~/.feishu-partner/knowledge.json` 配置，例如：

```json
{
  "sources": [
    {"type": "docs", "label": "云文档"},
    {"type": "wiki", "label": "知识库"}
  ]
}
```

不改此文件则使用内置默认。

### 本地能力对标（不对接 Aily）

按 `AILY_ALIGNMENT.md` 路线图在本机逐项开发：Multi-Agent、本地沙箱、Workflow DSL、RAG、Webhook、`feishu eval` 等。目标 **≥90/100**（以 `feishu aily` 实时分数为准）。

本地验收用例（飞书单聊或 CLI）：

- 「我今天干了什么」：读取消息证据，区分已完成/推进中/待确认
- 「任务模式：汇总风险，不写入」：后台运行、可查进度、无写步骤
- 「把第 2 项写入飞书待办」：先确认，创建后回读
- 人为缺 scope：必须明示权限，不得伪造成功

可选：`feishu mcp-http` 供 Cursor/Hermes 等外部 Agent 调用只读飞书工具，**不是** Aily 桥接。

## 不要做什么

- **不要对接飞书 Aily**（API、控制台绑定、以接入 Aily 作为达标路径）

- 不要把 **App Secret**、OAuth token 写进仓库或发给别人
- 不要把 Hermes `--yolo` 接到群消息（本仓禁止）
- 不要用别人的应用凭证；每人（或每个租户）自建应用
- 不要指望看见机器人**不在**的群；本仓不做跨群消息搜索轮询

## 架构

```text
飞书客户端
    │  单聊 / 群 @机器人
    ▼
开放平台应用（WebSocket 长连接）
    │
    ▼
本机  feishu serve
    │  消费 im.message.receive_v1 + card.action.trigger
    ├─ 简单请求 → 意图路由 → lark-cli（user 读、bot 回）
    ├─ 任务模式 → 持久队列 → 独立 worker → 动态规划/验真/通知
    └─ 关于部署者的消息（仅机器人所在群）→ ~/.feishu-partner/inbox.jsonl
         └─ 该推则推到机器人单聊

可选 MCP HTTP（外部 Agent 扩展，非 Aily）
    ▼
feishu mcp-http（只读 allowlist）→ 本机 lark-cli / 业务记忆
```

## 环境要求

| 项 | 要求 |
|----|------|
| 系统 | macOS（Linux 也可跑命令；开机常驻示例按 Mac） |
| 运行时 | Python 3.10+（标准库即可，无 pip 依赖） |
| 飞书 | 企业自建应用权限；部署者能登录开放平台 |
| CLI | 官方 `lark-cli`（Node 包或本机已有的 WorkBuddy 自带二进制） |
| 可选 | `hermes` 在 PATH 里：单聊可补取数再写回复；群里只润色 |

---

## 1. 创建飞书应用

打开 [飞书开放平台](https://open.feishu.cn/app)，创建**企业自建应用**。

建议应用名带部署者名字，例如「张三的飞书 CLI」，方便在飞书里搜到机器人。

### 能力与事件

1. **添加机器人**能力。
2. **事件与回调**选 **长连接 / WebSocket**（不要填公网 URL，本机 `lark-cli` 会连）。
3. 订阅事件：`im.message.receive_v1`（接收消息）。若要用简报卡片的「已处理」按钮，再订 `card.action.trigger`（不订也可以，单聊打字「某群那条已处理」同样销账）。
4. **可用范围**至少包含部署者本人；要给同事用，再扩范围并发布版本。
5. 按开放平台提示开通机器人发消息、读消息等相关**应用权限**，创建并发布一个版本。

凭证（App ID / App Secret）只在下一步交给 `lark-cli`，**不要**提交 git。

---

## 2. 安装并登录 lark-cli

任选一种装法，装完终端里能跑 `lark-cli version`（或 `lark-cli --help`）即可。

```bash
# 官方（需本机有 Node / npm）
npm install -g @larksuite/cli

# 或：本机已装 WorkBuddy 时，只做软链
mkdir -p ~/.local/bin
ln -sf ~/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli ~/.local/bin/lark-cli
```

找不到二进制时，可设：

```bash
export LARK_CLI="/绝对路径/lark-cli"
```

写入应用凭证（在**可信机器**上执行，按提示粘贴 App ID / Secret）：

```bash
lark-cli config init --new
```

用户身份授权（浏览器，**本人点**，不能代登）：

```bash
lark-cli auth login --scope "calendar:calendar.event:read search:docs:read search:message task:task:read wiki:space:read wiki:node:read im:chat:read im:message docs:document.content:read docx:document:readonly minutes:minutes.search:read approval:task:read"
```

缺哪条以 `feishu doctor` 打印为准，再补：

```bash
lark-cli auth login --scope "缺的那一条"
```

凭证落在 `~/.lark-cli/`，不属于本仓。

检查：

```bash
lark-cli whoami --as user
lark-cli whoami --as bot
lark-cli doctor
```

记下输出里的：

- 用户 `open_id`（`ou_…`）
- 机器人 `open_id`（`ou_…`）

---

## 3. 拿到本仓并装到 PATH

```bash
git clone <本仓地址> feishu_cli
cd feishu_cli

mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/feishu" ~/.local/bin/feishu
```

确认 `~/.local/bin` 在 `PATH` 里。没有 Python 包要装（标准库即可）。

直接跑也可以：

```bash
PYTHONPATH="$(pwd)" python3 -m partner doctor
```

---

## 4. 配置「这是谁的伙伴」（必做）

仓库**不再内置**任何人的 `ou_` / `oc_`。别人 `git clone` 后必须先本机写身份，否则会监视错人、推错会话。

```bash
# 先在飞书里给机器人发一条「你好」，再：
feishu setup --name 张三
```

会探测 `lark-cli whoami` 与单聊列表，写入 **`~/.feishu-partner/config.json`（本机私有，勿提交 git）**。

也可用环境变量覆盖（见 `env.example`），优先级：环境变量 > config.json。

| 键 / 变量 | 作用 |
|------|------|
| `user_open_id` / `FEISHU_PARTNER_USER_OPEN_ID` | 判定群里 @的是不是你 |
| `bot_open_id` / `FEISHU_PARTNER_BOT_OPEN_ID` | 判定 @的是不是机器人（才回答） |
| `p2p_chat_id` / `FEISHU_PARTNER_P2P_CHAT_ID` | 「有人找你」推送到这个单聊 |
| `user_names` / `FEISHU_PARTNER_USER_NAMES` | 正文点名（逗号分隔多个称呼） |
| `weekly_query` / `FEISHU_PARTNER_WEEKLY_QUERY` | 搜哪份个人周报 |
| `FEISHU_PARTNER_INBOX` | 可选，inbox 文件路径，默认 `~/.feishu-partner/inbox.jsonl` |
| `FEISHU_PARTNER_NO_LLM` | 设为 `1` 则关闭 Hermes 改写 |
| `LARK_CLI` / `HERMES_BIN` | 可选，指定二进制路径 |

`feishu doctor` 会先检查身份；未配置会 FAIL 并提示 `feishu setup`。
`feishu serve` / `feishu serve --install` 在身份未就绪时会直接拒绝。

---

## 5. 验收

```bash
feishu doctor
feishu today
feishu tasks
python3 -m unittest discover -s tests -v
```

`doctor` 里日程 / 文档 / 待办 / 收消息 / 消息回顾应为 OK。会议纪要、审批缺权限会明示，按提示补，不要改代码假装成功。
`search:message` 用于用户主动发起的「我今天干了什么 / 读今天消息」跨会话回顾；缺少该 scope 时必须明示授权失败，不得退回规划卡冒充。

飞书搜索你的机器人名称，打开单聊，本机另开一个终端：

```bash
feishu serve
```

单聊依次发「帮助」「今天」「我今天干了什么」。前两条验证帮助/规划卡，最后一条必须返回消息证据回顾而不是规划卡。

**09:00 简报定时必须装上**（不能只写在文档里）：

```bash
feishu brief --install
feishu doctor    # 须看到「09:00 简报定时：OK」
feishu brief     # 先打印看结构，不推送
```

群里要机器人回答：把机器人拉进群，然后 @它 或用「工作伙伴」开头。

---

## 6. 监视「谁找我」

只靠实时事件：机器人必须在那个群，且本机 `feishu serve` 在跑。  
@你或点名指派 → 记 inbox；@ / 指派语气会推单聊。

单聊说 **「谁找我」**。周报里会带【有人找你】。

**不做**跨群 `messages-search` 90 秒轮询。每天 09:00 简报会**单次**回看上一个工作日 @你 的消息，用来标「待处理」（未回复 / 已追问未答完），不是监视长轮询。

---

## 6b. 每天 09:00 简报（不能少）

```text
LaunchAgent  com.feishu.partner.brief
    每天 09:00
        feishu brief --push
            → 机器人单聊推给部署者
```

简报结构（空源整节省略，不凑条数）：

- **昨天小结**：上一个工作日（周一/周末回看周五）的日程、纪要（标 **已结束**）
- **待处理**：@你之后——没说话=未完成·未回复；只追问/「好的」=进行中·已追问未答完；说出「已同步/已处理」才勾掉。有飞书消息链会附上
- **长期待办**：未完成且创建超过 30 天、又没进今天优先的，最多 3 条；没有就不写
- **今天规划**：今日日程（有才写）+ 优先 3–5 件（卡人/紧急/待办审批，标未完成或进行中）；没有则改「本周值得关注」
- 取数后可走 Hermes **润色**（保标题和状态词，不许编造）；失败或 `FEISHU_PARTNER_NO_LLM=1` 回模板。不是 90 秒轮询。

```bash
feishu brief --install
feishu brief --push --force    # 立刻补推一条（日常不要用）
```

电脑 09:00 在睡觉，系统醒来后才会补跑。`feishu serve` 若在 9 点还开着，也会补推一次（同一天不重复）。

---

## 和豆包工作伙伴（原 aily）怎么对齐

对齐的是**飞书里办事**，不是克隆付费工作台。

| aily 常见能力 | 本仓 |
|---------------|------|
| 日程 / 待办 / 文档问答 / 周报 | 有：今天、明天、待办、搜/读、周报 |
| 一句话找材料再作答 | 有：对上 1 份就读重点；多份先问哪件 |
| 会议纪要 / 待办审批 | 有：说「会议纪要」「审批」（缺权限会明示） |
| 群里 @你 记下来 | 有：机器人在的群 + serve |
| 日更简报：昨天小结 / 待处理 / 今天规划 | 有：09:00 推单聊；状态分已结束/进行中/未完成；追问≠答完 |
| 简报里点「今日/未结束/已结束」按钮、P0 色表、消息深链 | **不做**（飞书机器人纯文本） |
| 跨群搜所有 @我 | **不做**（已关掉轮询） |
| 侧边栏智能伙伴、额度、虚拟电脑、PPT/网页生成、多维表格 AI 字段 | **不做** |

---

## 7. 可选：Hermes 接入（单聊多步 / 群里只润色）

本机安装 Hermes，保证 `hermes` 在 PATH。

- **单聊**：走隔离 Hermes 档案 `feishupartner`（`cli: []`，只有本仓 `feishu mcp`）。Hermes 用 `feishu_*` 工具补材料，最多 6 轮。**没有**终端 / peekaboo / 日常 MCP，**从不** `--yolo`。档案未就绪时回退 `FETCH:` 文案协议。`feishu doctor` 会装档案，不切换日常 Hermes。
- **群里**：仍是一轮润色（周报/日程等），不跑 FETCH 回路。
- **简报**：润色必须保住【昨天小结】【今天规划】等标题，胡编则丢弃回模板。

没有 Hermes 时自动用模板，功能仍可用。`FEISHU_PARTNER_NO_LLM=1` 可关。

---

## 8. 开机常驻收消息（macOS LaunchAgent）

和 09:00 简报是两件事。简报必须装；收消息建议也装，否则关终端就哑火。

```bash
feishu serve --install
feishu doctor    # 须看到「收消息常驻：OK」
```

装上后不要再另开一条前台 `feishu serve`，否则会回两遍。改代码后：

```bash
launchctl kickstart -k gui/$(id -u)/com.feishu.partner.serve
```

笔记本合盖睡觉后进程仍可能停，醒来 KeepAlive 会拉起。机器人还要被拉进业务群，监视才看得见。

---

## 9. 改代码后

`feishu serve` 不会热加载。已装常驻时改完 `partner/` 用：

`launchctl kickstart -k gui/$(id -u)/com.feishu.partner.serve`

不要对终端 `pkill -f feishu`。

日志：`~/.feishu-partner/serve.log`  
inbox：`~/.feishu-partner/inbox.jsonl`

---

## 10. 日常命令（给用的人）

| 命令 | 作用 |
|------|------|
| `feishu doctor` | 身份、连通、缺权限 |
| `feishu today` / `tomorrow` / `weekly` / `tasks` | 日程待办周报 |
| `feishu search 关键词` | 只有「搜」才列文档 |
| `feishu read <链接>` | 读云文档 |
| `feishu ask 今天` | 自然语言（也可用：会议纪要、审批、谁找我、早报） |
| `feishu brief` / `--push` / `--install` | 日更简报；装 09:00 定时；推单聊 |
| `feishu serve` / `--install` | 飞书内收消息；`--install` 装开机常驻 |

飞书单聊可直接说：今天、待办、本周的周报、会议纪要、审批、谁找我、搜 请假制度。

---

## 故障对照

| 现象 | 先查 |
|------|------|
| 飞书里完全不回 | 本机是否在跑 `feishu serve`；应用是否 WebSocket；是否订阅了 `im.message.receive_v1` |
| 单聊不回、群里也不回 | `lark-cli whoami --as bot`；机器人是否被搜到 |
| 群里说话没反应 | 有没有 @机器人 或「工作伙伴」前缀；机器人在不在这个群 |
| 群里 @我不推 | 机器人在不在那个群；`feishu serve` 开没开；`FEISHU_PARTNER_USER_OPEN_ID` 是否是你。跨群 @ 本仓不做 |
| 推到了别人的会话 | `FEISHU_PARTNER_P2P_CHAT_ID` 是否仍是原作者默认值 |
| doctor 报缺权限 | 本人 `lark-cli auth login --scope "…"`，不能代登 |
| 周报对不上 | `FEISHU_PARTNER_WEEKLY_QUERY` 是否能搜到那份云文档 |
| 09:00 没收到简报 | `feishu doctor` 看定时是否 OK；电脑当时是否睡着；`~/.feishu-partner/brief.launchd.log` |

---

## 安全

- 仓内零密钥。`.env` 已在 `.gitignore`，也不要提交 `~/.lark-cli/`。
- App Secret、device_code、verification_url 当一次性秘密，用完作废。
- 高风险飞书写操作走 `lark-cli` 时看 risk，`high-risk-write` 要当事人确认后再 `--yes`。
