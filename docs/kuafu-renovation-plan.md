# 夸父（Kuafu）改造计划 — 让夸父变成聪明、能干活、能进化、有对外认知的优秀 AI Agent

> 基于 Codex CLI (openai/codex) 架构参考文档（`codex-architecture-reference.md`）
> 分析日期：2026-06-05

---

## 一、总览：四维度改造路线图

| 维度 | 目标 | 涉及模块 | P0 项 | P1 项 | P2 项 |
|------|------|---------|-------|-------|-------|
| 🧠 **聪明** | 更智能的决策、上下文管理和记忆 | agent_loop, context_compress, memory, tool_selection | 3 | 4 | 3 |
| ⚡ **能干活** | 更强执行力、工具系统、安全沙箱 | tool_registry, exec, approval, mcp, subagent, sandbox | 2 | 4 | 3 |
| 🧬 **能进化** | 自我改进、技能系统、反馈闭环 | evolution, skill_system, feedback_loop, hooks | 2 | 2 | 2 |
| 👁️ **有对外认知** | 感知外部世界、持久化、搜索、调度 | session_store, config, search, cron | 1 | 3 | 2 |

---

## 二、模块级改造方案

### 2.1 Agent Loop — `core/agent_loop.py`

**夸父当前实现总结：**
- 单 agent 串行循环：组装 system prompt → LLM 调用 → 工具执行 → 递归
- 手动拼接 system prompt（通过 PromptManager 分 section），prompt_cache 做 L1/L2/L3 分层缓存
- 后处理（反思、进化、偏好学习）在后台线程执行，不阻塞主流程
- 已有 BudgetAllocator、ContextCompressor、Microcompact 等先进机制

**Codex 可借鉴的关键设计：**
- **TurnContext 不可变快照**（Codex §13.1）：每次 turn 构建不可变上下文，函数式克隆更新，自描述快照支持 resume
- **Submission/Event 队列**（Codex §3）：外部通过 tx_sub 提交操作，内部 submission_loop 非阻塞处理
- **Agent 树 + AgentPath 寻址**（Codex §2）：AgentRegistry + watch::Receiver 状态订阅，Weak<GlobalState> 避免循环引用
- **EventMapping 类型安全事件流**（Codex §13.2）：parse_turn_item() 将 API ResponseItem 映射为业务 TurnItem

**具体改造方案：**
1. **引入结构化 TurnContext**：将 `build_system_prompt()` + `context_compress.py` 手动拼字符串改为不可变 TurnContext 对象。每次 turn 开始时构建快照，with_model() 模式克隆更新。压缩时保留 TurnContext 快照供后续 resume。(P1, 中)
2. **Agent 树而非单 agent**：在当前 AgentLoop 外层包裹一个 AgentRegistry，支持多 agent 并行。当前 agent_loop 作为根 agent，subagent 通过 AgentPath 寻址。注册 `watch::Receiver` 模式监听子 agent 状态。(P2, 大)
3. **Event 队列替代 callback 海洋**：将 `on_step`, `on_llm_start`, `on_tool_end` 等 12 个回调形参改为统一 Event 队列。agent_loop 内部使用 `Submission/Event` 模式处理输入、中断、子 agent 事件。(P1, 中)
4. **保留并增强现有机制**：BudgetAllocator → 改为继承 Codex 的 TurnMultiAgentRuntime 模式；PromptCache L1/L2/L3 → 标准化为 `Config → PerTurnConfig → TurnContext` 三层递进。(P0, 小)

**优先级：P0（PromptCache 标准化 + BudgetAllocator 增强）/ P1（TurnContext + Event 队列）/ P2（Agent 树）**
**预估工作量：P0 小(2d)，P1 中(5d)，P2 大(10d)**

---

### 2.2 Tool Registry — `core/tool_registry.py`

**夸父当前实现总结：**
- 三级工具架构：核心（全量 schema）、紧凑（名称+描述，首次调用自动提升）、延迟（隐藏，通过 tool_search 发现）
- 工具注册为 (schema_dict, handler_function) 对，execute() 统一分派
- compact/deferred 设计精巧，ToolSearch 元工具实现了工具发现

**Codex 可借鉴的关键设计：**
- **ToolOrchestrator 四阶段**（Codex §4a）：Approval → Select sandbox → First attempt → Retry with escalation
- **CoreToolRuntime trait**（Codex §4b）：统一的 handler trait，含 pre_tool_use_payload / post_tool_use_payload / hooks
- **SpecPlan 动态构建**（Codex §4c）：运行时根据 turn_context + params 动态裁剪 tool 列表，ToolExposure 三级控制
- **并行执行控制**（Codex §4d）：RwLock 读写锁控制串行/并行，CancellationToken 支持取消
- **Tool Events**（Codex §4e）：统一 start/success/failure 事件发射

**具体改造方案：**
1. **封装 ToolOrchestrator**：将 approval.py + safety.py + tool_registry.execute() 整合为四阶段编排。第一阶段调用 `pretooluse_check`，第二阶段决定是否沙箱（暂不实现真正的 sandbox 但保留接口），第三阶段首次执行，第四阶段失败重试。(P0, 中)
2. **SpecPlan 动态裁剪**：在每轮 LLM 调用前，根据 turn_context（当前任务类型、已调用的工具）动态决定暴露哪些工具。隐藏不相关的延迟工具，减少 LLM 决策空间。(P1, 中)
3. **并行执行控制**：给工具声明 parallel/serial 属性，用 threading.Lock/Rlock 实现串行化。Codex 用 RwLock 读写锁，Python 等效方案可用 `threading.Lock` 或 `asyncio.Lock`。(P1, 小)
4. **统一 Tool Events**：每个工具执行统一发射 start/success/failure 事件，连接到 hooks.py 的事件系统。替换当前手动拼接的 `self.on_tool_start/end` 回调。(P1, 小)

**优先级：P0（ToolOrchestrator）/ P1（SpecPlan + 并行控制 + Tool Events）**
**预估工作量：P0 中(5d)，P1 小+中(6d)**

---

### 2.3 Approval System — `core/approval.py`

**夸父当前实现总结：**
- 三层审批：Layer 1 DenyRules（静态黑名单）、Layer 2 AutoMode（历史批准率自动决策）、Layer 3 人工审批
- 终端交互审批（锁机制）+ 非交互轮询
- 命令安全前缀预检查（_is_safe_terminal）
- 审批请求持久化到 JSON 文件

**Codex 可借鉴的关键设计：**
- **ExecPolicyManager**（Codex §5b）：ArcSwap 原子策略更新 + Semaphore 写锁防并发
- **命令降级解析**：bash -lc "cmd" → 实际执行的 cmd，再匹配策略
- **`.rules` 文件**替代硬编码正则列表
- **PermissionRequest Hook**（Codex §7）：钩子允许第三方插件拦截和执行权限决策

**具体改造方案：**
1. **重写为 ExecPolicyManager**：将 DenyRules + AutoMode + ApprovalManager 合并为一个 PolicyManager。使用类似 ArcSwap 模式（Python 可用 `contextvars` + `threading.Lock` 替代）。(P0, 中)
2. **引入命令降级解析**：在 terminal 工具中增加解析层，`bash -lc "rm -rf /"` → 解析出 `rm -rf /` 再匹配策略。(P1, 小)
3. **权限决策 Hook**：将 `on_permission_check` / `on_approval_result` 钩子连接到 hooks.py，允许第三方插件扩展安全策略。(P1, 小)
4. **从文件存储升级到 SQLite**：当前 JSON 文件存储审批请求，规模和性能有限。改为 SQLite 存储，支持更高效的查询和过期。(P2, 小)

**优先级：P0（PolicyManager 合并）/ P1（命令降级 + Hooks）/ P2（SQLite）**
**预估工作量：P0 中(4d)，P1 小(2d)，P2 小(2d)**

---

### 2.4 Safety Layer — `core/safety.py`

**夸父当前实现总结：**
- 路径白名单（PROTECTED_DIRS / ALLOWED_WRITE_DIRS）
- 命令危险分级（SAFE/ATTENTION/DANGEROUS/FORBIDDEN）
- DenialTracker：用户拒绝跟踪，连续 N 次自动降级
- API key / 敏感信息脱敏

**Codex 可借鉴的关键设计：**
- **SandboxManager**（Codex §6a）：平台沙箱抽象（Landlock/seccomp-bpf/macOS Seatbelt）
- **进程加固**（Codex §6b）：取消 capabilities、no_new_privs
- **ExecCapturePolicy**：区分 shell 工具（输出限制）和内部调用（全缓冲）

**具体改造方案：**
1. **引入 SandboxManager 抽象**：在目前纯 Python 路径保护基础上，增加对 WSL Landlock/seccomp-bpf 的可选支持。当检测到 Linux 内核时，通过 `ctypes` 或子进程调用 `seccomp-bpf` 做额外保护。(P2, 大)
2. **ExecCapturePolicy**：为 terminal 工具增加输出大小限制。Codex 区分 ShellTool（限制 1000 行）和 FullBuffer（无限制），夸父当前无此区分。(P1, 小)
3. **DenialTracker 升级为规则文件系统**：从内存 JSON 升级为类似 Codex 的 `.rules` 文件目录，支持按项目/用户级别分规则。(P2, 中)
4. **脱敏增强**：目前防 API key 泄漏做的好，可增加 `safety-lock` 文件系统（已有雏形），禁止写入特定规则定义的路径。(P1, 小)

**优先级：P1（ExecCapturePolicy + safety-lock 增强）/ P2（SandboxManager + 规则文件）**
**预估工作量：P1 小(2d)，P2 大(8d)**

---

### 2.5 Session Store — `core/session_store.py`

**夸父当前实现总结：**
- SQLite 存储会话历史，含 sessions + messages 两张表
- 自动截断到 max_tokens（从最旧消息开始丢弃）
- token 估算基于字符数（1.6 chars/token）
- JSONL 持久化支持原始消息归档（ContextCollapse 用）
- 已有搜索（LIKE）、归档、清理功能

**Codex 可借鉴的关键设计：**
- **SessionServices 聚合**（Codex §3）：所有子系统统一初始化，Arc<SessionServices> 共享引用
- **SessionState 状态机**：独立管理运行时状态（active turn、waiting turn），与 Session 结构分离
- **SessionLoopTermination**：Shared<BoxFuture> 允许多个调用者同时 await 会话关闭

**具体改造方案：**
1. **SessionServices 聚合**：将 SessionStore、ToolRegistry、MCPBridge、SkillsManager、HooksManager 统一为一个 `KuafuServices` 对象，共享引用传递，避免初始化散落在 agent_loop 各处。(P1, 中)
2. **Session 状态机**：在 session 基础上增加运行时状态（active_turn / waiting_approval / paused），支持状态切换事件通知。(P2, 中)
3. **FTS5 全文搜索**：当前 `search_sessions` 使用 LIKE 搜索，改为 SQLite FTS5 全文索引，大幅提升搜索速度和精度。(P1, 小)
4. **Session 快照/恢复**：在关键节点（压缩前、中断前）保存会话快照，支持从快照恢复而不丢失上下文。(P2, 中)

**优先级：P1（KuafuServices 聚合 + FTS5）/ P2（状态机 + 快照恢复）**
**预估工作量：P1 中(3d)，P2 中(5d)**

---

### 2.6 Context Compression — `core/context_compress.py`

**夸父当前实现总结：**
- 三层压缩策略：工具结果清理（Snip）→ BudgetReduction（零 token）→ LLM 摘要
- PinnedContentManager 保护关键消息不被压缩
- ToolResultStore 做 Microcompact（大结果存磁盘，上下文放摘要）
- 本地 llama-server 做智能摘要

**Codex 可借鉴的关键设计：**
- **TurnContext 不可变快照**（Codex §13.1）：压缩前保存完整上下文快照，压缩后仍可通过快照恢复
- **`PreCompact` / `PostCompact` Hooks**（Codex §7）：压缩生命周期事件，供插件扩展
- **上下文片段识别**（Codex §13.2）：CONTEXTUAL_DEVELOPER_PREFIXES 区分"框架注入"和"用户真实内容"

**具体改造方案：**
1. **Pre/PostCompact Hooks**：在压缩前后触发 hooks，允许第三方插件扩展压缩策略（如自定义摘要格式、特殊上下文保护）。(P0, 小)
2. **TurnContext 快照存储**：每次压缩前将完整消息列表快照存储到 JSONL（已有雏形），标记时间戳和轮次，支持后续查询。(P1, 小)
3. **上下文片段标记**：标记 system prompt 注入部分和用户真实内容，压缩时只压缩用户内容，保留系统注入的完整性。(P1, 小)
4. **两级摘要模型**：Codex 用低成本模型（gpt-5.4-mini）+ 高精度模型（gpt-5.4）两阶段。夸父可用本地 Qwen（低成本）+ 云端 DeepSeek（高精度）。(P2, 中)

**优先级：P0（Pre/Post Hooks）/ P1（快照 + 片段标记）/ P2（双模型摘要）**
**预估工作量：P0 小(1d)，P1 小(2d)，P2 中(4d)**

---

### 2.7 Memory System — `core/memory/memory_manager.py`

**夸父当前实现总结：**
- 四网络存储：World / Experience / Observation / Opinion
- OpinionEngine 置信度演化：store() 时 reinforce/weaken/contradict
- CacheRing (L0) + SQLite FTS (L1) + EpisodicBuffer
- LLM 萃取事实类型（降级到 rule-based 检测）

**Codex 可借鉴的关键设计：**
- **两阶段记忆系统**（Codex §8）：Phase 1 低成本模型从 rollout 中提取知识点 → Phase 2 高精度模型合并去重
- **增量触发**：仅在 workspace 发生变化（diff 驱动）时更新，非每轮都写
- **文件系统持久化**：Markdown 文件便于用户审查编辑

**具体改造方案：**
1. **文件系统持久化层**：在 SQLite 基础上增加 Markdown 文件同步。`raw_memories.md` 文件人类可读可编辑，与 SQLite 双向同步。(P2, 中)
2. **增量触发优化**：当前 memory.store() 每次任务完成都调用。改为只在"有关键变化"时才触发：对比当前任务结果与前一次结果的关键 diff。(P1, 小)
3. **双模型分层**：用低成本模型（本地 Qwen）做第一遍事实类型检测和初步提取，用高精度模型（云端 DeepSeek）做合并去重和 Opinion 演化。(P2, 中)
4. **记忆冷却期增强**：当前有 30 秒冷却期，改为基于内容相似度的动态冷却，避免重复存储相似内容。(P1, 小)

**优先级：P1（增量触发 + 冷却期增强）/ P2（文件同步 + 双模型）**
**预估工作量：P1 小(2d)，P2 中(5d)**

---

### 2.8 Evolution — `core/evolution.py`

**夸父当前实现总结：**
- 三阶段管道：Observer 收集信号 → EvolutionState 更新计数 → Judge 判断+提取 → SkillWriter 写入
- GEPA 遗传规划引擎（SkillGenome 适应度评估）
- EvolutionState：SQLite 存储任务类型统计、错误模式、技能版本链
- 后台线程执行，不阻塞主流程

**Codex 可借鉴的关键设计：**
- **两阶段记忆**（Codex §8）：低成本 + 高精度模型分层
- **Hooks 系统**（Codex §7）：生命周期事件，进化前后触发
- **增量式触发**：Codex 不每轮都评估，只在有明显信号时触发

**具体改造方案：**
1. **进化 Hooks**：在 Observer → Judge → SkillWriter 各阶段前后触发 hooks（on_evolution_before / on_evolution_after / on_skill_create）。(P0, 小)
2. **质量反馈闭环**：当前 `evolution_state.record_skill_quality()` 已在记录质量分数。可增加主动反馈机制：当某个 skill 被多次调用且结果不佳时，自动触发重新评估或回退。(P1, 中)
3. **进化日志可视化**：将 `evolution_log.json` 的结构化数据通过 CLI/Web 展示，支持回溯查看每个技能的生命周期。(P2, 小)
4. **Skill 测试框架**：在 skills/ 目录保留测试用例，每次 skill 进化后自动运行测试验证正确性。(P2, 中)

**优先级：P0（进化 Hooks）/ P1（质量反馈闭环）/ P2（进化日志可视化 + 测试框架）**
**预估工作量：P0 小(1d)，P1 中(4d)，P2 中(4d)**

---

### 2.9 Hooks System — `core/hooks.py`

**夸父当前实现总结：**
- 28 个钩子事件点（覆盖 Agent、LLM、工具、记忆、任务、进化、系统、审批生命周期）
- 4 种执行类型：shell / llm / webhook / subagent
- JSON 配置持久化，支持优先级、异步/同步、超时、重试

**Codex 可借鉴的关键设计：**
- **10 个生命周期钩子**（Codex §7）：PreToolUse / PermissionRequest / PostToolUse / PreCompact / PostCompact / SessionStart / UserPromptSubmit / SubagentStart / SubagentStop / Stop
- **PermissionRequest 钩子**允许第三方插件拦截和执行权限决策，返回 Allow/Deny/传递给用户

**具体改造方案：**
1. **增加 PermissionRequest 钩子集成**：hooks.py 已有 26 个 on_permission_check 事件，但未真正连接到 approval.py 的权限决策流程。将 `on_permission_check` 钩子结果纳入 ApprovalManager 决策链。(P0, 小)
2. **subagent 执行器增强**：当前 hooks 支持 subagent 类型（验证器），但未实际实现。补全 subagent 执行器，在事件触发时创建子 agent 执行指定验证任务。(P1, 中)
3. **Webhook 执行器增强**：添加 OAuth 签名、重试退避、请求体 JSON Schema 校验。(P2, 小)
4. **条件过滤**：支持事件匹配器，只匹配特定 tool、特定 session 类型。(P1, 小)

**优先级：P0（PermissionRequest 集成）/ P1（subagent 执行器 + 条件过滤）/ P2（webhook 增强）**
**预估工作量：P0 小(1d)，P1 中(3d)，P2 小(1d)**

---

### 2.10 Skill System — `core/skill_manager.py` + `core/kfskill.py`

**夸父当前实现总结：**
- kfskill YAML 格式（名称/描述/步骤/关键词/依赖/陷阱）
- 本地技能 CRUD（skills/ 目录）
- 远程技能市场（URL 下载索引）
- 已有版本管理（1.0.0 等）

**Codex 可借鉴的关键设计：**
- **core-skills/ + skills/** 分层（Codex §1）：内置 skills（core-skills）不可修改，用户 skills（skills/）可覆盖
- **prompts/ 模板目录**（Codex §1）：prompt 模板与代码分离

**具体改造方案：**
1. **内置/用户技能分层**：将现有 skills/ 拆分为 `core_skills/`（不可修改，由框架维护）和 `skills/`（用户自定义，可被进化覆盖）。(P0, 中)
2. **Skill 版本链增强**：当前版本管理较简单（1.0.0）。改为 Codex 风格的完整版本链，支持回退、diff 查看、合并。(P1, 中)
3. **Skill 依赖解析**：已有雏形（kfskill.py 的 dependencies 字段），补全为完整的依赖安装流程（auto-install missing packages）。(P1, 中)

**优先级：P0（内置/用户分层）/ P1（版本链增强 + 依赖解析）**
**预估工作量：P0 中(3d)，P1 中(5d)**

---

### 2.11 LLM Client — `core/llm.py`

**夸父当前实现总结：**
- N 后端 + 自动降级（deepseek → openai → qwen → claude → openrouter）
- 每个后端独立 API Key / Base URL / 模型名
- 运行时 switch() 热切换
- 超时 / 重试 / 错误处理

**Codex 可借鉴的关键设计：**
- **ExecExpiration**（Codex §5a）：统一超时和取消，内部用 select! 等待
- **MultiAgent 模型路由**（Codex §2）：不同 agent 使用不同模型

**具体改造方案：**
1. **统一超时/取消**：当前 LLMClient 超时参数是固定的（timeout=15）。改为 ExecExpiration 模式，支持 Timeout / Cancellation / CancellationToken 三种过期机制。(P1, 小)
2. **模型路由**：允许不同 task_type 路由到不同模型（如 coding 用 deepseek-chat，research 用 claude-sonnet，简单任务用本地 qwen）。(P2, 中)
3. **Streaming 增强**：当前 LLM 调用在 tool_calls 模式似乎不是全流式。增强为 Codex 式的流式事件推送。(P2, 中)

**优先级：P1（统一超时）/ P2（模型路由 + Streaming）**
**预估工作量：P1 小(2d)，P2 中(4d)**

---

### 2.12 Subagent — `core/subagent.py`

**夸父当前实现总结：**
- delegate_task 工具：在隔离的 AgentLoop 中执行子任务
- 子 Agent 拥有独立 ToolRegistry 和对话上下文
- 支持 YAML Frontmatter 配置（subagent_profiles/）
- 侧链隔离：完整对话转录写入 sidechain_data/
- worktree 支持：git worktree 文件系统隔离

**Codex 可借鉴的关键设计：**
- **AgentPath 寻址系统**（Codex §2）：类似文件系统路径（"/" → "/child1" → "/child1/grandchild"）
- **AgentRegistry**（Codex §2）：全局 agent 注册表，弱引用避免循环引用
- **completion_watcher**：父 agent 启动后台线程监听子 agent 完成事件
- **SpawnAgentForkMode**（Codex §2）：控制子 agent fork 时历史携带量（FullHistory / LastNTurns / None）

**具体改造方案：**
1. **AgentPath 寻址**：为每个子 agent 分配 AgentPath（如 "/delegate/1"），支持通过路径查找和监听状态。替换当前 `_active_subagents` 计数器的简单管理方式。(P2, 大)
2. **completion_watcher**：父 agent 在 spawn 子 agent 时启动后台线程，通过事件队列接收完成通知，非阻塞等待。(P1, 中)
3. **SpawnAgentForkMode**：当前子 agent 上下文为空（"记忆是空的"）。新增 ForkMode 参数，支持 FullHistory（携带全部上下文）、LastN（只带最近 N 轮）、None（只带系统 prompt）。(P1, 中)
4. **资源限制**：当前 MAX_CONCURRENT=3，但不支持子 agent 级别的 token 预算、超时控制。引入 PerAgent 的资源限制。(P2, 小)

**优先级：P1（completion_watcher + ForkMode）/ P2（AgentPath 寻址 + 资源限制）**
**预估工作量：P1 中(5d)，P2 大(10d)**

---

### 2.13 MCP Bridge — `core/mcp_bridge.py`

**夸父当前实现总结：**
- 基于 JSON-RPC 2.0 over stdio
- 支持动态发现、连接、调用 MCP Server 工具
- 进程管理（重启、超时）

**Codex 可借鉴的关键设计：**
- **双模式 MCP**（Codex §10）：MCP client + MCP server
- **ToolExposure 控制**：MCP 工具的暴露策略

**具体改造方案：**
1. **MCP Server 模式**：让夸父自身可以作为 MCP Server 被外部调用。暴露 `run_task` 工具，允许其他 MCP Client 把夸父当作 agent 使用。(P2, 大)
2. **ToolExposure 集成**：MCP 桥接注册的工具自动进入 SpecPlan 的 Dynamic Tool 池，受 ToolExposure 三级控制（Direct/Hidden/DirectModelOnly）。(P1, 中)
3. **连接健康检测**：当前 MCPServer 仅在调用时检测连接状态。增加后台心跳检测，自动重连断开的 MCP server。(P1, 小)
4. **多 server 负载均衡**：支持同一工具由多个 MCP server 提供，自动负载均衡。(P2, 中)

**优先级：P1（ToolExposure + 健康检测）/ P2（MCP Server 模式 + 负载均衡）**
**预估工作量：P1 中(3d)，P2 大(8d)**

---

### 2.14 Config System — 当前无统一配置文件

**夸父当前实现总结：**
- 配置分散在 `.env`（API keys）、`memory_config.yaml`（记忆系统）、`mcp_config.yaml`（MCP）、`memory/` 下的 JSON 文件（hooks、approval 等）
- 无统一的配置加载器

**Codex 可借鉴的关键设计：**
- **分层配置系统**（Codex §9）：Cloud Config → App Requirements → Profile → User config → Project config → CLI overrides
- **Constrained<T>**：带约束的类型，跨层合并时校验冲突
- **ConfigLockfile**：锁定关键配置项

**具体改造方案：**
1. **统一配置加载器**：设计 `ConfigLoader` 类，按 Codex 的 6 层优先级加载配置。从 `.env` 读取最低层（环境变量），从 `kuafu.yaml` 读取项目配置，CLI 参数为最高层。(P1, 大)
2. **Constrained 配置校验**：为关键配置项（sandbox mode、approval policy、max_tokens）定义约束和冲突解决策略。(P2, 中)
3. **ConfigLockfile**：防止项目级配置覆盖安全要求。(P2, 小)

**优先级：P1（ConfigLoader）/ P2（约束校验 + Lockfile）**
**预估工作量：P1 大(6d)，P2 中(4d)**

---

### 2.15 CLI — `core/cli.py` / `core/main.py`

**夸父当前实现总结：**
- 支持交互模式 / 单次执行 / 子命令（cron, sessions, skill, status）
- KuafuAgent 封装了完整的 agent 组装流程

**Codex 可借鉴的关键设计：**
- **TUI (Ink React)**（Codex §1）：终端 TUI
- **codex exec 子命令**：单次命令执行模式

**具体改造方案：**
1. **Streaming TUI**：当前 CLI 输出方式是逐行打印。增加 Streaming 模式，实时显示 LLM 思考内容和工具执行过程。(P2, 中)
2. **exec 子命令**：类似 Codex 的 `codex exec`，直接执行一条命令并返回结果，不启动完整 agent 循环。(P1, 小)
3. **状态面板**：`kuafu status` 增强为实时状态面板，显示活跃会话、待审批请求、运行中任务、记忆统计。(P2, 中)

**优先级：P1（exec 子命令）/ P2（TUI + 状态面板）**
**预估工作量：P1 小(2d)，P2 中(6d)**

---

### 2.16 Batch Engine — `core/batch_engine.py`

**夸父当前实现总结：**
- SQLite 持久化的批量任务队列
- 并发控制（max_concurrent）
- 进度追踪、状态持久化

**Codex 可借鉴的关键设计：**
- **Submission/Event 队列**（Codex §3）：统一操作类型
- **SessionLoopTermination**：可关闭的循环

**具体改造方案：**
1. **Event 驱动替代轮询**：当前 `get_status()` 是轮询模式。改为 Event 驱动，任务完成时发射事件，消费者等待事件。(P2, 中)
2. **任务依赖关系**：支持 DAG 式任务依赖（任务 B 需要在任务 A 完成后执行）(P2, 中)
3. **批量审批**：一批任务共享审批策略，提高效率。(P1, 小)

**优先级：P1（批量审批）/ P2（Event 驱动 + 依赖关系）**
**预估工作量：P1 小(1d)，P2 中(4d)**

---

### 2.17 Aggregate Search — `core/aggregate_search.py`

**夸父当前实现总结：**
- 并行请求 DDG + Bing + Tavily（线程池）
- URL 去重 + 智能合并
- LLM 汇总

**Codex 可借鉴的关键设计：**
- **WebSearch**（Codex §13.2）：TurnItem 枚举的一种，纳入事件流

**具体改造方案：**
1. **搜索纳入事件流**：将 aggregate_search 结果纳入 TurnItem 事件流，支持 undo/redo。(P2, 小)
2. **搜索缓存层**：相同 query 的搜索结果缓存 5 分钟，减少重复网络请求。(P1, 小)
3. **搜索结果质量评分**：LLM 对搜索结果做质量评分，丢弃低质量结果再喂给主 LLM。(P2, 小)

**优先级：P1（搜索缓存）/ P2（事件流集成 + 质量评分）**
**预估工作量：P1 小(1d)，P2 小(2d)**

---

### 2.18 Cron Scheduler — `core/cron_scheduler.py`

**夸父当前实现总结：**
- YAML 配置文件驱动（cron/schedule.yaml）
- 支持一次性/周期性/定时任务
- 线程级调度（非进程级）

**Codex 可借鉴的关键设计：**
- **Hooks 集成**（Codex §7）：on_cron_tick 事件

**具体改造方案：**
1. **Cron 事件 Hook**：已有 `on_cron_tick` 事件但未充分利用。每次 cron 任务触发时发射事件，允许 hooks 拦截、记录、通知。(P1, 小)
2. **持久化任务历史**：当前只记录 run_count，不保留每次执行的结果。增加 `cron_history` 表存储执行历史。(P1, 小)
3. **多调度器实例支持**：当前是单例模式。扩展为支持多个调度器实例并行运行。(P2, 小)

**优先级：P1（Cron Hook + 执行历史）/ P2（多实例）**
**预估工作量：P1 小(2d)，P2 小(1d)**

---

## 三、整体实施路线图

### Phase 1：基础加固（第 1-3 周）

**重点：** P0 项 + 核心 P1 项 — 让现有系统更稳定、更智能

| 实施项 | 模块 | 天数 | 依赖 |
|--------|------|------|------|
| ToolOrchestrator 四阶段封装 | tool_registry + approval | 5d | 无 |
| PolicyManager 合并 | approval | 3d | 无 |
| PermissionRequest Hook 集成 | hooks + approval | 1d | 无 |
| Pre/PostCompact Hooks | context_compress | 1d | 无 |
| 进化 Hooks | evolution | 1d | 无 |
| PromptCache 标准化 | agent_loop | 2d | 无 |
| 事件队列替代 callback | agent_loop | 3d | 无 |
| 内置/用户技能分层 | skill_manager | 3d | 无 |

**预计产出：**
- 重构的 ToolOrchestrator + PolicyManager，统一审批流程
- Hook 系统打通所有 P0 事件点
- agent_loop 回调改为事件队列
- 技能系统分层

**风险：** 
- ToolOrchestrator 重写可能影响现有 approval 调用链（低风险，有兼容接口）
- 事件队列可能引入性能开销（低风险，异步线程安全需注意）

### Phase 2：能力增强（第 4-7 周）

**重点：** 剩余 P1 项 + 轻量 P2 项 — 让夸父更聪明、更能干

| 实施项 | 模块 | 天数 | 依赖 |
|--------|------|------|------|
| SpecPlan 动态 Tool 裁剪 | tool_registry | 3d | Phase 1 |
| 并行执行控制 | tool_registry | 2d | Phase 1 |
| KuafuServices 聚合 | session_store | 3d | 无 |
| TurnContext 快照存储 | context_compress | 1d | 无 |
| 上下文片段标记 | context_compress | 2d | 无 |
| completion_watcher + ForkMode | subagent | 5d | 无 |
| MCP ToolExposure 集成 | mcp_bridge | 3d | Phase 1 |
| 统一 ConfigLoader | config | 6d | 无 |
| 质量反馈闭环 | evolution | 4d | Phase 1 |
| exec 子命令 | cli | 2d | 无 |
| Subagent 执行器增强 | hooks | 3d | 无 |
| 模型路由 | llm | 2d | 无 |

**预计产出：**
- 动态 Tool 裁剪，LLM 每轮只看相关工具
- 统一的 KuafuServices 架构
- 子 Agent 携带 ForkMode，更灵活的任务委托
- 分层配置系统
- 进化质量闭环

**风险：**
- SpecPlan 动态裁剪可能影响 LLM 发现新工具的能力（需保留 tool_search 入口，低风险）
- ConfigLoader 范围较广，需逐步迁移（中风险）

### Phase 3：进化飞跃（第 8-12 周）

**重点：** P2 项 — 完整的 Agent 生态

| 实施项 | 模块 | 天数 | 依赖 |
|--------|------|------|------|
| Agent 树 + AgentPath | agent_loop + subagent | 10d | Phase 1+2 |
| SandboxManager 抽象 | safety | 8d | 无 |
| 双模型记忆系统 | memory | 5d | Phase 2 |
| 文件系统记忆同步 | memory | 3d | Phase 2 |
| MCP Server 模式 | mcp_bridge | 8d | Phase 2 |
| Streaming TUI | cli | 4d | 无 |
| 状态面板 | cli | 2d | 无 |
| Skill 测试框架 | evolution | 4d | Phase 1 |
| Event 驱动批处理 | batch_engine | 4d | 无 |
| 任务 DAG 依赖 | batch_engine | 3d | 无 |

**预计产出：**
- 完整的 Agent 树系统，支持复杂的多 agent 协作
- 沙箱安全层（Linux 平台）
- 双模型记忆系统
- 夸父可作为 MCP Server 被外部调用
- 完整的状态监控和可视化

**风险：**
- Agent 树实现复杂度高，需要处理并发和生命周期（高风险）
- SandboxManager 依赖 Linux 内核特性（Landlock/seccomp-bpf），WSL 兼容性需测试（中风险）
- MCP Server 模式涉及网络暴露，需安全审计（中风险）

---

## 四、总结：关键指标对比

| 指标 | 当前夸父 | 改造后夸父 | 提升 |
|------|---------|-----------|------|
| 上下文管理 | 手动拼字符串 + 三阶段压缩 | TurnContext 快照 + 双模型摘要 + Hooks | 结构化、可恢复 |
| Tool 系统 | 三级架构 + 直通执行 | 四阶段编排 + 动态裁剪 + 并行控制 | 更安全、更精确 |
| 审批系统 | 三层审批 + JSON 持久化 | PolicyManager + Hooks + 命令降级 | 更灵活、可扩展 |
| 子 Agent | 隔离 AgentLoop + 简单限制 | AgentPath 寻址 + ForkMode + watcher | 可追踪、可控 |
| 记忆系统 | 四网络 + SQLite + 置信度 | + 文件同步 + 双模型 + 增量触发 | 更持久、更智能 |
| 进化系统 | 三阶段管道 + GEPA | + Hooks + 质量闭环 + 测试框架 | 可观测、可验证 |
| 配置系统 | 分散 `.env` + YAML + JSON | 6 层分层配置 + 约束校验 | 统一、安全 |
| MCP 集成 | 仅 Client 模式 | Client + Server 双模式 | 双向集成 |
| Hooks 系统 | 28 个事件点 + 4 种执行器 | + PermissionRequest 集成 + subagent 执行器 | 打通核心流程 |
| 对外认知 | cron + 搜索 + 批处理 | + Event 驱动 + 状态面板 + 搜索缓存 | 更实时、更高效 |

---

## 五、文件组织建议

改造后的目录结构建议：

```
kuafu/
├── core/
│   ├── agent/                  # Agent 树（AgentRegistry + AgentControl）
│   ├── tools/                  # Tool 系统（Orchestrator + Registry + SpecPlan）
│   ├── session/                # Session 系统（Session + State + Services）
│   ├── exec/                   # 执行引擎（ExecPolicy + ExecParams）
│   ├── agent_loop.py           # 主循环（精简，只做编排）
│   ├── tool_registry.py        # 保留（核心工具注册）
│   ├── approval.py → tools/    # 迁移到 tools/orchestrator.py
│   ├── safety.py               # 保留（脱敏 + 路径安全）
│   ├── hooks.py                # 保留（Hook 系统）
│   ├── config.py               # 新增：统一配置加载器
│   └── ...
├── core_skills/                # 内置技能（不可修改）
├── skills/                     # 用户技能（可修改）
├── subagent_profiles/          # 子 Agent 配置
├── prompts/                    # Prompt 模板目录
├── config/                     # 分层配置文件
│   ├── user_config.yaml
│   ├── project_config.yaml
│   └── config.lock
└── docs/
    ├── codex-architecture-reference.md
    └── kuafu-renovation-plan.md  # 本文件
```
