---
name: feishu-work-partner
description: >-
  吴梦晨的飞书工作伙伴。在飞书里读文档、待办、知识库、发消息，或用终端 feishu/lark-cli。
  当用户提到飞书、云文档、Wiki、日程、待办、群消息、工作伙伴、豆包办公时使用。
---

# 飞书工作伙伴

凭证已在本机 `~/.lark-cli/`，应用 `cli_aaf077d53d389d2d`，用户 **吴梦晨**。不要读/打印 secret，不要 `auth login`（本人登录红线）。飞书单聊走 Hermes 隔离档案 `feishupartner` + `feishu mcp`，**禁止** `--yolo`；群里只润色。不要 `hermes profile use` 切日常档案。

## 优先用本仓命令

```bash
feishu doctor
feishu today
feishu weekly   # 读《吴梦晨》周报正文写成【工作内容】【重点项目】【下周】
feishu tasks
feishu search <关键词>
feishu read <文档 URL 或 token>
feishu chats
feishu ask <短指令>
feishu brief          # 昨天小结+今天规划；可 Hermes 润色；--install 装 09:00；--push 推单聊
```

入口：`/Users/wumm/学习/@Agent/feishu_cli/bin/feishu`。找不到命令时：

```bash
PYTHONPATH="/Users/wumm/学习/@Agent/feishu_cli" python3 -m partner <子命令>
```

## 更深的飞书操作

`feishu` 不认识的子命令会原样交给官方 `lark-cli`。先看 help / schema，再调用：

```bash
lark-cli <domain> --help
lark-cli schema <service>.<resource>.<method>
lark-cli skills read lark-doc
```

写操作看 risk：`high-risk-write` 必须用户确认后再加 `--yes`。

## 身份

| 场景 | 身份 |
|------|------|
| 读自己的待办/文档/知识库/日历 | `--as user` |
| 收消息、以机器人回复 | `--as bot` |

## 缺权限

若返回 `missing_scope`，把缺失 scope 告诉用户，请 **用户本人** 跑 `lark-cli auth login --scope "..."`。Agent 不得代完成浏览器授权。

## 飞书内机器人

`feishu serve` 消费 `im.message.receive_v1`。@机器人或「工作伙伴」前缀才在群里回答。群里 @吴梦晨 或点名指派会写入 inbox，该推的推到单聊。不做跨群 messages-search 轮询。不要把 Hermes YOLO 接到这条链路。机器人必须在那个群里。
