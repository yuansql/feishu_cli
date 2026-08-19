# 飞书 Aily / 豆包工作伙伴功能对齐

日期：2026-08-17  
口径：飞书 aily 已更名为「豆包工作伙伴」；本项目只对齐办公闭环和可落地能力，不克隆官方工作台、额度、企业后台。

## 公开功能面

- 企业级智能体平台：AI 技能编排、知识数据处理、效果调优、持续运营，多渠道发布到飞书、Web 等。
- 工作伙伴：进入飞书工作环境，处理任务、协作、24 小时在线。
- 任务模式：把复杂目标拆成多步子任务，有序执行、调用工具并跟踪结果；可在异步沙箱中长时间运行。
- 自定义智能体：团队共享、关联知识空间、操作飞书文档/多维表格/任务，支持定时任务、记忆隔离、后台发布与回滚。
- 四类应用场景：模型推理、工作流、知识问答、混合调度。
- 数据能力：连接飞书文档、多维表格、数据库、业务系统；数据表可读写，可由 workflow 查询。

主要来源：
- https://www.feishu.cn/content/article/7585126677299137754
- https://www.feishu.cn/content/3d5z9ttt
- https://www.feishu.cn/content/article/7631864469689240764
- https://www.feishu.cn/content/0vi1z25i1
- https://www.feishu.cn/content/euuvns8t
- https://www.feishu.cn/content/vl1lf3v5

## 本项目现状

已接近：
- 飞书内对话办事：`feishu serve`、单聊/群聊路由、OnIt 反馈、JSON 2.0 卡片。
- 高频飞书工具：日程、待办、文档、会话、纪要、审批、多维表任务。
- 定时任务：09:00 简报、今日待跟进、周一任务、表格扫描。
- **任务模式 v0.2**：`partner/runner.py` 持久任务、读工具链、汇总、**写回跟进账/飞书待办（确认闸）**、续跑/进度/确认写入。

部分具备：
- 企业知识问答：文档搜索 + `~/.feishu-partner/knowledge.json` 知识源优先级；无完整 RAG/评测台。
- 任务规划：单聊「规划/拆解」走 TaskRunner；CLI `feishu plan` 仍只打印文本。
- 数据表：有本周任务表和表格艾特扫描，但没有通用 schema/OQL/BI。
- 记忆隔离：有 session、跟进账、Hermes 隔离档案，但不是企业级记忆系统。

缺口：
- 通用工作流编排、Webhook 触发器、多渠道 Web/服务台发布。
- 效果调优台、答案评分、变更 diff/rollback。
- PPT/网页/多模态生成。

刻意不做：
- Aily 工作台、计费额度、企业管理后台。
- 群消息驱动本机任意命令。
- 提交任何 App Secret、open_id、应用 ID 或人员隐私信息。

## 下一步优先级

1. ~~把 `feishu plan` 的步骤和飞书待办/跟进账打通。~~ **已落地**：TaskRunner 写回 `followup_add` + `task_create`，需「确认写入」。
2. ~~将常用知识源做成配置清单，回答时按知识源优先级取材料。~~ **已落地**：`partner/knowledge.py` + `~/.feishu-partner/knowledge.json`。
3. ~~建立 eval fixtures~~ **基线已落地**：`tests/test_eval_fixtures.py`（意图/resolve 高风险短语）；后续扩权限/泄漏用例。
4. 把 brief/followup 抽象成可复用 workflow 配置。
5. 任务模式：LLM 动态改 plan、异步长跑；~~写云文档（`docs_create` 确认闸）~~ **已接入默认 plan**（周报/文档类目标）。
