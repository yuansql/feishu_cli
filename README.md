# 飞书工作伙伴

自建**完全本地独立**的飞书办公智能体，功能对标 **飞书 Aily / 豆包工作伙伴**：在飞书里问、后台执行、在飞书里交付。**不对接 Aily 任何接口**，缺口在本仓库内单独开发。
公开能力、当前覆盖和开发路线图见 [AILY_ALIGNMENT.md](AILY_ALIGNMENT.md)。部署见 [DEPLOY.md](DEPLOY.md)。

手：官方 [`lark-cli`](https://github.com/larksuite/cli)（本机已登录；应用名/ID 勿写入文档）。  
脑：Cursor 对话（本仓 `SKILL.md`）。飞书单聊走本机 Hermes 隔离档案 `feishupartner`：只有白名单飞书取数 MCP，从不 `--yolo`，不给终端。群里只润色。

旧仓 `~/学习/@Agent/agent_feishu`（小飞 + Cloudflare 隧道）不再扩展。

**换人 / 换机器部署**：看 [DEPLOY.md](DEPLOY.md)。

## 先确认本机

```bash
feishu doctor
feishu today
feishu tasks
```

`doctor` 会列出缺的 OAuth 权限。补权限必须 **本人** 浏览器授权：

```bash
lark-cli auth login --scope "calendar:calendar.event:read search:docs:read"
```

妾身不能代登。

## 命令

| 命令 | 作用 |
|------|------|
| `feishu status` / `feishu doctor` | 身份、连通、缺权限 |
| `feishu today` | 日程 + 未完成待办（日程缺权限会明示，不装成功） |
| `feishu tasks` | 未完成待办 |
| `feishu search 关键词` | 文档搜索；缺权限则按知识库空间名筛选 |
| `feishu read <链接或 token>` | 读云文档 Markdown |
| `feishu chats` | 会话列表 |
| `feishu send oc_xxx 文本` | 机器人发消息 |
| `feishu ask 今天` | 自然语言短指令 |
| `feishu serve` | 飞书内收消息（WebSocket） |
| `feishu brief` | 昨天小结 + 今天规划；`--install` 装 09:00 定时，`--push` 推单聊 |
| `feishu plan <目标>` | CLI 输出计划；飞书内「任务模式/规划」会先观察、动态规划并后台执行 |
| `feishu rag stats` / `index` / `query` | 本地 RAG 索引与召回 |
| `feishu sandbox` | 查看本地沙箱路径与允许命令 |
| `feishu workflow` | 列出本地 Workflow 及触发词 |
| `feishu webhook` | Webhook 触发后台任务（HMAC，默认 127.0.0.1:8766） |
| `feishu mcp-http` | 可选 MCP HTTP 网关（外部 Agent 扩展） |
| `feishu aily` | 对标 Aily 能力差距与本地开发路线图 |
| `feishu <lark-cli 原生命令>` | 原样转交，如 `feishu wiki +space-list` |

## 飞书里怎么用

1. 飞书里打开本机那个机器人单聊，或把机器人拉进群。  
2. 本机执行 `feishu serve`（应用已是 WebSocket 回调，不用隧道）。  
3. 单聊直接发「帮助」「今天」「待办」「本周计划」「拆解 A6 上线」。**只有「搜 关键词」才列文档**；其余会读材料再分析。群里请 @机器人，或以「工作伙伴」开头。

```text
飞书消息
    │
    ▼
lark-cli event consume im.message.receive_v1 --as bot
    │
    ▼
意图路由（简单对话 / 任务模式）
    │
    ▼
TaskRunner v2（观察 → 动态规划 → 执行 → 验真 → 重规划）
    │
    ├─ 后台 worker / 取消 / 恢复 / 脱敏运行轨迹
    ├─ 本地 Multi-Agent：调研 → 执行 → 交付（规划前协作）
    ▼
lark-cli 读用户身份办事 / 机器人身份回复
    │
    ▼
飞书会话
```

## 安装到 PATH

仓库入口：`bin/feishu`。可链到 `~/.local/bin`：

```bash
ln -sf "$PWD/bin/feishu" ~/.local/bin/feishu
ln -sf ~/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli ~/.local/bin/lark-cli
```

凭证只在 `~/.lark-cli/`，**不要**写进本仓。

## 刻意不做

- 重写 OpenAPI SDK  
- **对接飞书 Aily 平台**（API、控制台绑定、以接入 Aily 作为达标路径）
- 企业计费 / 租户管理员 UI / 应用市场审核（单用户 CLI 标 N/A）
- 群消息驱动本机任意命令（Hermes `--yolo`）  
- 把 App Secret 提交进 git
