# AGENTS.md

本仓是飞书工作伙伴（`feishu`）。开 `[全]` 先读本文；分层与主路径见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 分工（勿推翻）

| 角色 | 谁 | 边界 |
|---|---|---|
| 脑 | 本机 Hermes 档案 `feishupartner` | 禁 `--yolo`；MCP 无写工具 |
| 手 | 官方 `lark-cli`（user OAuth） | 经本仓白名单 MCP |
| 闸 | `feishu serve` + office | 短指令硬路径；「写进去」才写云文档 |

P2P 认不出意图 → Hermes 分类/归纳，禁止文档关键词澄清。范围对标：`AILY_ALIGNMENT.md` / `feishu aily`。新机器走 README「最快上手」，不要另写 init。

## 校验

全力改可断言行为（规则 / 契约 / 变换 / 校验）时，写盘前先绊线（同一 argv，必须先红后绿；胶水/改文案跳过）：

```sh
bash scripts/check.sh red <探针>
# …产品补丁…
bash scripts/check.sh green <同一探针>
```

改 `partner/` 或路由后，宣称完成前：

```sh
bash scripts/check.sh
```

即 `python3 -m unittest discover -s tests -q`，再 `bin/feishu smoke`（默认不写飞书；真写加 `--live-write`）。扩样例：`partner/ops/smoke.py`、`partner/eval_cases.json`。

`feishu serve` 不热加载。LaunchAgent 已装时改完代码必须：

```sh
launchctl kickstart -k gui/$(id -u)/com.feishu.partner.serve
```

## 红线

- 应用名 / App ID / open_id / `oc_` / Secret **不进 git**；只在 `~/.feishu-partner/` 与 `~/.lark-cli/`
- 对照截图/样卡：第一刀是发出去的形态（卡 vs 纯文本），禁止先改意图词表
- 新办公能力 → `office/` 经 `actions.py`；新工具 → `runtime/tool_registry.py`；路径用 `partner.paths`

`CLAUDE.md` 是本文软链。
