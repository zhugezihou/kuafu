# 夸父 (Kuafu) v0.3.0 — Gap 分析

> 分析基准：夸父 v0.3.0 "自我进化的 AI Agent 框架"
> 对比对象：Hermes Agent、Claude Code、LangGraph、Dify、Coze、n8n、Cline/Kilo Code、Google ADK、OpenAI Agents SDK
> 分析日期：2026-05-25

---

## 一、夸父已有能力（不缺失）

以下能力在夸父 v0.3.0 已验证实现，与主流框架在同一水平线或具有独特性：

| 能力 | 夸父实现 | 对比说明 |
|------|---------|---------|
| **Agent Loop 执行引擎** | ReAct 循环，最大 15 轮，含任务类型检测（7 类）、白板模式、上下文压缩 | 与 Cline、Claude Code 同级 |
| **工具系统** | 14 个工具：terminal/read_file/write_file/patch/search_files/web_search/web_fetch/github_search/github_get_repo/tavily_search/finish/finish_step/whiteboard_read/whiteboard_write | 覆盖主流 Agent 工具集 |
| **双 LLM 后端** | 云端 DeepSeek + 本地 llama-server Qwen3.5-9B | 比大多数只支持单一后端的框架强（如 Cline 只 cloud、Ollama 只本地） |
| **双记忆后端** | file (JSON 关键词) + Hindsight Cloud (语义搜索/实体图谱) | 与 Hermes hindsight、Claude Code memories 同级 |
| **自我进化系统** | 即兴进化 D 方案：LLM 当场判断→提取→写入 skills/ | 夸父独有优势，主流框架几乎无此能力 |
| **自主优化四模块** | P1 prioritizer → P2 learner → P3 skill_extractor → P4 self_health/reviewer | 领先于行业（主流框架依赖人工优化） |
| **安全/沙盒/审批** | 三级风险 L0-L3，三种审批模式（终端/飞书/自动过期），核心目录保护 | 与 Hermes、Claude Code 同级 |
| **Cron 定时任务** | YAML 驱动，支持 cron 表达式和间隔语法，持久化可恢复 | 与 Hermes cron、Claude Code Routines 同级 |
| **飞书 Bot 通道** | 轮询式 @bot 检测，消息去重持久化 | 单一通道但实现完整 |
| **白板系统** | 外部推理状态分区存储（whiteboard_read/whiteboard_write） | 夸父独特设计，LangGraph 也用外部 State |
| **上下文压缩** | 超限时自动压缩摘要 | 与 Hermes context_compress 同级 |
| **会话管理** | SQLite 持久化，WAL 模式，Token 估算（1.6 chars/token） | 与 Hermes session_store 同级 |
| **任务分解** | finish_step + 白板模式实现步骤分解 | 支持但不如 Dify/Coze 可视化 |
| **技能系统** | YAML 声明式，skill_resolver 匹配注入，15 个 YAML 文件 | 夸父特色，n8n 用 JSON，Claude Code 用 markdown |
| **策略配置文件** | prompts.yaml / task_strategies.yaml / quality.yaml | 夸父特色，主流框架无此粒度 |
| **身份系统/防冒充** | IDENTITY.md，core/ 只读保护区，沙盒路径白名单 | 与 Hermes 身份系统同级 |

---

## 二、缺失能力清单

### P0 核心缺失 — 必须补，不然无法作为现代 Agent 框架

| # | 缺失能力 | 为什么 P0 | 参考实现 | 实现思路 |
|---|---------|---------|---------|---------|
| 1 | **MCP 协议支持** | Anthropic 发起的行业标准，OpenAI/Google/MS 均已采纳。2026 年没有 MCP 的 Agent = 没有「AGI 时代的 USB」= 无法接入社区 2000+ MCP Server（数据库、浏览器、地图、设计工具等） | [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) / Hermes Agent 的 native-mcp 模块 | 在 tool_registry 外层加一层 MCP 桥接：`MCPBridge` 类动态加载 MCP Server 的 tools 并注册到 Registry。支持 stdio 和 HTTP 传输 |
| 2 | **WebHook 事件驱动** | 夸父目前只能主动轮询（飞书 cron），无法被外部事件触发。Hermes、n8n、Dify 都支持 WebHook → 与 CI/CD、监控、CRM 等集成是基础能力 | [Hermes webhook-subscriptions skill](https://hermes-agent.nousresearch.com/docs) / n8n Webhook node | 加一个轻量 HTTP server（Python http.server / FastAPI），`/webhook/<token>` 解析 payload 后注入 AgentLoop 执行任务。可复用现有 cron_scheduler 的任务执行逻辑 |
| 3 | **多平台消息通道** | 夸父只有飞书。Telegram/ Discord/ Slack 是用户面覆盖率前三的平台，企业场景还需邮箱（微信/企微视区域而定） | [Hermes 跨平台 gateway](https://hermes-agent.nousresearch.com) / [himalaya 邮件 skill](https://github.com/soywod/himalaya) | 抽象 `MessageChannel` 接口（send / poll / listen），各平台独立实现。Telegram 用 python-telegram-bot，Discord 用 discord.py，Slack 用 slack-sdk。邮件用 IMAP/SMTP |
| 4 | **子 Agent 系统** | Claude Code Subagent 证明：隔离子上下文可显著提升 2x 以上的多文件/多任务吞吐。夸父单 Agent 遇到 15 轮上限时只能截断，无法并行。任务分解后应能 spawn 子 Agent | [Claude Code subagent](https://docs.anthropic.com/en/docs/claude-code/subagents) — 上下文隔离、文件隔离、权限继承 | 新建 `subagent.py`: `SubAgent(task, parent_context)` → 创建隔离会话 → 调用 LLM + 有限工具集 → 返回结果。工具集继承父权限 |

### P1 重要缺失 — 影响用户体验和可观测性，建议 0.4 补充

| # | 缺失能力 | 为什么 P1 | 参考实现 | 实现思路 |
|---|---------|---------|---------|---------|
| 5 | **可视化工作流/Debug UI** | 当前全黑箱操作：用户看不到 Agent 内部状态、推理过程、工具调用栈。LangGraph Studio（图形化 workflow）、Dify（拖拽编辑器）、Cline（浏览器 DevTools）都提供可视化 Debug | [LangGraph Studio](https://github.com/langchain-ai/langgraph-studio) / [Dify workflow editor](https://dify.ai) / [Cline DevTools](https://github.com/cline/cline) | 使用白板数据 + session_store 数据，提供一个 Web Debug UI（Flask/FastAPI + 简单 React/Svelte）：显示当前 Agent 计划、已执行步骤、工具调用记录、上下文压缩事件 |
| 6 | **Human-in-the-Loop 审批 UI** | 目前审批只有终端输入和飞书按钮，没有专用的审批面板。Hermes 有 /approve 命令、OpenAI SDK 有 structured output 审批、Cline 有内嵌对话框 | [Hermes 审批 UI](https://hermes-agent.nousresearch.com) / [OpenAI SDK 审批流](https://platform.openai.com/docs/assistants) | 在 Web Debug UI 上加审批面板 + 消息通道审批（Telegram 按钮卡片 / 邮件回复审批）。或复用 HTTP server 提供 RESTful 审批 |
| 7 | **记忆自动管理/清理** | 当前记忆纯靠 append，没有自动清理/去重/过期机制。长期运行后记忆膨胀 → 噪声增多 → search 精度下降 | [Claude Code auto memory](https://docs.anthropic.com/en/docs/claude-code/memories) — 自动管理记忆生命周期 / [Hermes hindsight](https://hermes-agent.nousresearch.com) — 自动语义压缩 | 在 memory_api.py 添加：1) 记忆自动摘要合并（同主题 3 条→1 条）；2) 过期删除（30 天无匹配）；3) 相关性阈值（低分自动丢弃） |
| 8 | **知识库/RAG 管道** | 夸父只能搜索 web 和本地文件，无法对用户私有文档做 RAG。Dify 的 RAG pipeline 可接入 PDF/HTML/Notion 等 30+ 格式 | [Dify RAG pipeline](https://dify.ai) — 文档上传→分块→向量化→检索 / [LangChain RAG](https://python.langchain.com/docs/use_cases/question_answering/) | 引入本地向量化（sentence-transformers 或 llama.cpp embedding）→ SQLite 向量扩展 / ChromaDB → 加 `knowledge_retrieve` 工具 |
| 9 | **内联代码补全** | 夸父编辑代码只能 write_file 全量写入，无法流式逐行补全。Kilo Code 的内联补全是编码场景用户最爱的功能 | [Kilo Code inline completions](https://github.com/Kilo-Org/kilocode) — 在 IDE 内实时流式补全 | 前端策略：在 Web UI 或 VS Code extension 中集成 tab-completion。后端策略：LLM 流式输出 + 结构化 diff 插入 |
| 10 | **可观测性/Telemetry（OTel）** | 当前零遥测。无法回答「上周跑了多少任务」「哪些工具最常用」「失败率多少」。企业级部署必须有 | [LangSmith](https://smith.langchain.com) / [LangFuse](https://langfuse.com) — OpenTelemetry 标准，traces + metrics + evaluations | 在 agent_loop 关键节点埋点（任务开始/结束、工具调用、LLM 调用、进化事件），输出 OTel 事件，可接入 LangFuse 自托管 |

### P2 锦上添花 — 有更好，无也可，优先级最低

| # | 缺失能力 | 为什么 P2 | 参考实现 | 实现思路 |
|---|---------|---------|---------|---------|
| 11 | **Agent 技能市场/模板库** | 夸父有 15 个 YAML 技能文件，但只有本地库，没有社区市场。Coze 技能市场 1000+、n8n 4000+ 模板是拉新利器 | [Coze Skills Store](https://www.coze.com) / [n8n templates](https://n8n.io/workflows/) / [Claude Code Skills](https://docs.anthropic.com/en/docs/claude-code/skills) | 简单方案：skills/ 目录支持从 Git 远程仓库同步（`git pull`）。进阶方案：搭建技能市场 API（用 GitHub Issues 做简易商店） |
| 12 | **浏览器自动化** | 夸父已有 Playwright skill（非核心工具），但未集成为核心能力。Cline、Claude Code 都内置了 browser 工具 | [Cline browser tool](https://github.com/cline/cline) — Playwright 集成 | 加 `browser` 工具调用 Playwright（JavaScript 截图 + 点击 + 表单）。可用 `subprocess` + npx playwright |
| 13 | **评估系统/合成测试** | 夸父自省进化不评估「进化是否有效」。Google ADK Evaluation Suite 提供自动合成测试 | [Google ADK Evaluation Suite](https://developers.google.com/adk-evaluation) — synthetic test generation | 每次进化后，自动生成测试用例 → 回放验证 → 进化前后的 success rate 对比 |
| 14 | **状态检查点/时间旅行** | 当前 AgentLoop 执行无法回退。LangGraph 支持 checkpoint 和时间旅行 | [LangGraph checkpointing](https://langchain-ai.github.io/langgraph/concepts/persistence/) — 自动保存每步状态，可回溯 | 在 agent_loop 每步调用时序列化状态到 SQLite → 提供 `checkpoint_list` / `checkpoint_rollback` 命令 |
| 15 | **跨运行时通信 A2A** | 夸父是单进程，无法与其他 Agent 框架通信。Microsoft Agent Framework 打通 n8n ↔ LangGraph 的 A2A 协议是 2026 新趋势 | [Microsoft Agent Framework A2A](https://github.com/microsoft/agent-framework) / [Agent-to-Agent Protocol](https://github.com/google/A2A) | MCP 已覆盖 A2A 的大部分场景。先补 MCP 后再评估 A2A 需求 |
| 16 | **MCP Tunnel / Secure Tunnel** | OpenAI SDK 2026.5 企业功能：安全穿透企业防火墙连接 MCP Server | [OpenAI SDK MCP Tunnel](https://platform.openai.com/docs/agents/mcp-tunnel) | 优先级极低，仅当有企业部署需求时才需要 |
| 17 | **定时 Routine（高级版）** | 夸父已有 cron scheduler，但缺少 Claude Code 的 "每天 9 点检查 issue" 这类自然语言定义 Routine | [Claude Code Routines](https://docs.anthropic.com/en/docs/claude-code/routines) — NL → cron 自动转换 | 在 cron_scheduler 上加 NL 解释器：LLM 转译 "每天早 9 点检查 issue" → cron 表达式 |
| 18 | **模型间切换/fallback 策略** | 双后端是手工切换的，不是自动 fallback（云端失败→自动切本地） | [OpenAI SDK model fallback](https://platform.openai.com/docs/guides/error-handling) / [OpenRouter multi-model](https://openrouter.ai) | 在 llm.py 加 fallback 链：primary 失败 3 次→自动切 backup。支持配置多级 fallback |

---

## 三、优先级汇总

| 优先级 | # | 缺失能力 | 估计工作量 | 依赖 |
|-------|---|---------|-----------|------|
| **P0** | 1 | MCP 协议支持 | 2-3 天 | 无 |
| **P0** | 2 | WebHook 事件驱动 | 1-2 天 | 无 |
| **P0** | 3 | 多平台消息通道 | 3-5 天 (Telegram + Discord) | 无 |
| **P0** | 4 | 子 Agent 系统 | 3-5 天 | 无 |
| **P1** | 5 | 可视化工作流/Debug UI | 5-7 天 | P0#2 (HTTP server 复用) |
| **P1** | 6 | HITL 审批 UI | 2-3 天 | P0#2 + P1#5 |
| **P1** | 7 | 记忆自动管理/清理 | 2-3 天 | 无 |
| **P1** | 8 | 知识库/RAG 管道 | 5-7 天 | 需引入向量库依赖 |
| **P1** | 9 | 内联代码补全 | 3-5 天 | 需前端集成 |
| **P1** | 10 | 可观测性 OTel | 2-3 天 | 无 |
| **P2** | 11 | 技能市场/模板库 | 2-3 天 | 无 |
| **P2** | 12 | 浏览器自动化 | 1-2 天 | 需安装 Playwright |
| **P2** | 13 | 评估系统/合成测试 | 3-5 天 | 无 |
| **P2** | 14 | 状态检查点/时间旅行 | 3-5 天 | 无 |
| **P2** | 15 | 跨运行时通信 A2A | 3-5 天 | P0#1 (MCP 前置) |
| **P2** | 16 | MCP Tunnel | 5-7 天 | P0#1 (MCP 前置) |
| **P2** | 17 | 定时 Routine (NL) | 1-2 天 | 无 |
| **P2** | 18 | 模型自动 fallback | 1 天 | 无 |

---

## 四、关键发现总结

### 夸父的独特优势（主流框架没有）
1. **自我进化系统** — 夸父最核心的差异化能力。Claude Code 有 memories 但不会自动提取 skill，Hermes 有 hindsight 但不会造工具
2. **自主优化四模块（P1-P4）** — 自适应学习、优先级排序、健康检查，主流框架没有对标物
3. **策略配置文件体系** — prompts.yaml / task_strategies.yaml / quality.yaml 的细粒度策略分层是夸父独特设计
4. **零依赖哲学** — 核心仅依赖 pyyaml，比 LangGraph（langchain 全家桶）、Dify（Flask + 向量库）轻量得多

### 必须优先补的 Gap
1. **MCP 协议** — 没有 MCP = 没有生态接入能力。夸父 14 个工具 vs MCP 社区 2000+ 个 Server
2. **WebHook** — 当前只能被动轮询，无法被外部事件触发，限制了 CI/CD/自动化集成
3. **多平台消息通道** — 只有飞书严重限制了用户覆盖

### 建议的路线图
- **v0.4 目标**：补完 P0 全部 4 项 + P1 中的 5-7
- **v0.5 目标**：P1 全部 8-10 + P2 部分（11-12）
- **v0.6 目标**：P2 剩余 + 稳定性/性能优化
