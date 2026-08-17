# CONTEXT

飞书工作伙伴（自建 CLI）。用户：吴梦晨。应用：cli_aaf077d53d389d2d。

领域词：lark-cli、工作伙伴、user 身份办事、bot 身份收发、缺权限明示、不代登 OAuth。

边界：不克隆 aily；Hermes 禁止 --yolo；单聊走隔离档案 `feishupartner` + 白名单 `feishu mcp`（无终端）；群里只润色；仓内零密钥；飞书权威失败则不假装成功。
监视：只看得见机器人所在群；不做 messages-search 轮询。
换人部署：`DEPLOY.md`；身份用 `FEISHU_PARTNER_*` 环境变量，勿提交密钥。
aily：对齐日程/待办/文档/周报/纪要/审批，不克隆工作台与额度。问哪个群走会话搜索，不拿文档追问。单聊会记住上一轮（澄清列表/原句），「详细点」或序号接着问，不拿跟进句去搜文档。
每天 09:00：`feishu brief --push` + LaunchAgent `com.feishu.partner.brief`（不能少）。收消息：`feishu serve --install` → `com.feishu.partner.serve`。待处理要分未回复/已追问未答完/已答完；事项标已结束/进行中/未完成。单聊说「某群那条已处理」写入本地账，明早简报跳过；09:00 可附卡片按钮（需开放平台订阅 `card.action.trigger`，未订则文字销账仍可用）。不克隆 aily 色表/筛选按钮。
