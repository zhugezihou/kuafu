
---

## 六、补充：从 Codex §13 中提取的额外改进点

以下是从 Codex 参考文档 §13（补充优秀设计）中提取的、未在以上模块中覆盖的重要改进点：

### 6.1 Rollout — 事件驱动会话持久化 (§13.3)

**夸父现状：** `session_store.py` 是快照存储（每次更新覆盖写入 messages）。不保留事件日志，无法回放历史。

**Codex 方案：** `RolloutRecorder` + `Cursor` — 游标分页，增量读取。JSONL 事件日志（非快照）：
```json
{"meta": {"name": "session", "started_at": "..."}}
{"turn_start": {"id": "t1"}}
{"tool_call": {"id": "tc1", "name": "read_file"}}
{"tool_result": {"id": "tr1"}}
```

**改造方案：** 在 `session_store.py` 中增加事件日志层（JSONL append-only event log），`RolloutRecorder` + `Cursor` 支持游标分页增量读取，支持从事件日志重建完整会话状态（replay）
**优先级：P1**，预估工作量：中(4d)

### 6.2 AGENTS.md 层次化发现 (§13.4)

**夸父现状：** 无。仅通过 `IDENTITY.md` 加载身份声明。

**Codex 方案：** 三级 AGENTS.md：`~/.codex/AGENTS.md` → `<project>/.codex/AGENTS.md` → `<project>/AGENTS.md`。结构化解析注入到 turn_context。

**改造方案：** 设计类似的三级 AGENTS.md 加载机制，结构化解析后注入到 system prompt 的 user_instructions section
**优先级：P2**，预估工作量：小(2d)

### 6.3 Skills 隐式触发 (§13.6)

**夸父现状：** skill 通过将步骤注入 prompt 的方式工作，LLM 需要手动调用。

**Codex 方案：** `maybe_emit_implicit_skill_invocation()` 检测 user prompt 自然语言触发 skill，不依赖 /skill 命令。

**改造方案：** 基于 kfskill 的 keywords 匹配（类同 ToolSearch 机制）检测 user prompt 中的自然语言，自动注入匹配的技能步骤到 prompt 中
**优先级：P2**，预估工作量：中(4d)

### 6.4 TurnDiffTracker — 纯内存 diff (§13.7)

**夸父现状：** 每轮都写记忆到 SQLite，无 diff 判断。

**Codex 方案：** 跟踪 tool 调用文件变更，仅在有 diff 时更新记忆——避免每轮都写记忆浪费 token。

**改造方案：** 在 `agent_loop.py` 中跟踪每轮 tool 调用后的 workspace 文件状态，仅在实际有 diff 时才调用 `memory.store()`，大幅减少记忆写入频率和 token 消耗
**优先级：P1**，预估工作量：小(2d)

### 6.5 Safety 三态决策树 (§13.8)

**夸父现状：** `approval.py` 返回 allowed=True/False/None（二态 + 待审批）。

**Codex 方案：** `enum SafetyDecision { Allow, BlockWithReason(String), Escalate { reason, suggestions } }` — 不是二态，Escalate 给用户展示安全建议列表。

**改造方案：** 改为三态决策：`Allow` / `BlockWithReason` / `Escalate(reason, suggestions)`。`Escalate` 状态允许 LLM 在看到建议后自动修正（如"命令需要 sudo 权限，建议：以非 sudo 方式重写"）
**优先级：P2**，预估工作量：小(2d)

### 6.6 Spawn 三保险 (§13.10)

**夸父现状：** `terminal` 工具有 timeout 参数，但无进程组杀和 IO drain 超时。

**Codex 方案：** 每个子进程：①超时 ②进程组杀 ③IO drain 超时。三层防止子进程泄露。

**改造方案：** 在 `tool_registry.py` 的 `_handle_terminal` 中增加进程组杀（`os.killpg()`）和 IO drain 超时机制
**优先级：P1**，预估工作量：小(2d)

### 6.7 Compact Hook 接口 (§13.5)

**夸父现状：** 已实现 Pre/PostCompact 的钩子事件点但未实现真正的 Hook handler 接口。

**Codex 方案：** `trait CompactHook { fn pre_compact(&mut self, ctx: &CompactContext); fn post_compact(...); }`

**改造方案：** 为 ContextCompressor 增加 `pre_compact(ctx)` / `post_compact(ctx)` 接口方法，允许插件在压缩前保护关键消息、压缩后检查摘要质量
**优先级：P1**，预估工作量：小(1d)

---

## 七、快速启动指南

改造分为三个并行工作流，建议团队按此分配开发任务：

| 工作流 | 职责 | Phase 1 | Phase 2 | Phase 3 |
|--------|------|---------|---------|---------|
| **A: 核心架构组** | agent_loop, 事件系统, config | ToolOrchestrator + PolicyManager + 事件队列 | SpecPlan + ConfigLoader + KuafuServices | Agent 树 + AgentPath |
| **B: 安全与执行组** | 审批, 沙箱, MCP, subagent | PermissionRequest Hook + Spawn 三保险 + ExecCapturePolicy | 命令降级 + 并行控制 + MCP ToolExposure | SandboxManager + MCP Server |
| **C: 记忆与进化组** | 记忆, 进化, hooks, skills | 进化 Hooks + Pre/PostCompact + 技能分层 | TurnDiffTracker + Rollout + 质量闭环 | 双模型记忆 + 测试框架 + 隐式触发 |

---

> **核心思想：** 夸父的代码质量已经很高，很多设计已经在向 Codex 靠拢（如三级 Tool 架构、进化管道、Hook 系统）。改造的核心不是"重写"，而是：
> 1. **结构化**：把分散的配置/逻辑整合为统一框架（ToolOrchestrator、ConfigLoader、KuafuServices）
> 2. **事件化**：用 Event 队列 + Hook 系统替代 callback 和紧耦合
> 3. **树形化**：从单 Agent 升级到 Agent 树
> 4. **分层化**：内置/用户分离、低成本/高精度模型分层
