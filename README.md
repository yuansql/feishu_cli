# 飞书工作伙伴

自建、不付费。目标是对齐 **飞书豆包工作伙伴** 的办公闭环：在飞书里问、在飞书里办事。  
**不是** 官方 aily / 豆包企业版：没有 AI 额度、智能体工作台、多维表格 AI 字段。

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
| `feishu <lark-cli 原生命令>` | 原样转交，如 `feishu wiki +space-list` |

## 飞书里怎么用

1. 飞书里打开本机那个机器人单聊，或把机器人拉进群。  
2. 本机执行 `feishu serve`（应用已是 WebSocket 回调，不用隧道）。  
3. 单聊直接发「帮助」「今天」「待办」「本周计划」。**只有「搜 关键词」才列文档**；其余会读材料再分析。群里请 @机器人，或以「工作伙伴」开头。

```text
飞书消息
    │
    ▼
lark-cli event consume im.message.receive_v1 --as bot
    │
    ▼
意图路由（帮助/今天/待办/搜/读/群/发）
    │
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
- 克隆 aily 工作台 / 计费 / AI 字段  
- 群消息驱动本机任意命令（Hermes `--yolo`）  
- 把 App Secret 提交进 git
