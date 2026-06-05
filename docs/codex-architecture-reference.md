# Codex CLI 源码架构深度分析 — 夸父设计参考

> 调研日期：2026-06-05
> 基于：openai/codex (main, commit ecae412) — Apache-2.0, 88.7k stars, 7.1k commits
> 源码文件：46 个核心源文件已保存至 `/home/asus/.hermes/codex-analysis/`
> 主语言：Rust 95.6%（Cargo workspace, ~350 crates）

---

## 一、全局架构

```
codex-rs/  (Cargo workspace)
├── core/                    # 核心：agent 循环、tool 系统、exec、session
│   └── src/
│       ├── agent/           # AgentControl + AgentRegistry + 多 agent 树
│       ├── tools/           # ToolRegistry + Orchestrator + Router + SpecPlan
│       ├── session/         # Codex 结构 + Session + Submission/Event 队列
│       ├── codex_thread.rs  # Thread 管理（Fork + 生命周期）
│       ├── codex_delegate.rs# 任务委托（子 agent）
│       ├── exec.rs          # 进程执行引擎
│       └── exec_policy.rs   # 命令审批规则
├── cli/                     # CLI 入口（codex / codex exec）
├── tui/                     # 终端 TUI (Ink React)
├── exec/                    # exec 子命令
├── sandboxing/              # 平台通用沙箱抽象
├── linux-sandbox/           # Linux Landlock + seccomp-bpf
├── process-hardening/       # 进程加固
├── mcp-server/              # Codex 作为 MCP server
├── codex-mcp/               # MCP client 集成
├── hooks/                   # 10 个生命周期钩子
├── memories/                # 两阶段记忆系统
├── config/                  # 分层配置
├── core-plugins/            # 插件系统
├── core-skills/             # 内置 skills
├── skills/                  # 用户 skills
├── prompts/                 # prompt 模板
└── state/                   # session 运行时状态
```

---

## 二、Agent Control — 分布式 agent 树系统

### 核心数据结构

```rust
// agent/control.rs (1340行)
struct AgentControl {
    session_id: SessionId,          // 所属 session
    manager: Weak<ThreadManagerState>,  // 弱引用回指全局状态
    state: Arc<AgentRegistry>,      // 本 session 的 agent 注册表
}

struct LiveAgent {
    thread_id: ThreadId,
    metadata: AgentMetadata,
    status: watch::Sender<AgentStatus>,  // 状态通知通道
}

// AgentPath 路径系统 — 类似文件系统路径
// 根: "/"
// 子agent: "/child1"
// 孙agent: "/child1/grandchild"
struct AgentPath(Vec<AgentPathSegment>);
impl AgentPath {
    fn resolve("..") -> Option<AgentPath>;          // 父路径
    fn resolve("/sibling") -> Option<AgentPath>;    // 其他路径
    fn exists_in(&self, registry: &AgentRegistry) -> bool;
}
```

### 关键设计模式

1. **`Weak<ThreadManagerState>`**：子 agent 的 AgentControl 通过弱引用回指全局状态，避免循环引用。Session 销毁后自动失效。

2. **`watch::Sender/Receiver` 状态订阅**：每个 LiveAgent 自带一个 watch channel，外部通过 `watch::Receiver<AgentStatus>` 监听状态变化。轻量级、类型安全、天然支持 `.changed().await`。

3. **`completion_watcher` 模式**：父 agent 启动后台 tokio::spawn，在子 agent 完成时自动注入通知消息，非阻塞。

4. **`SpawnAgentForkMode`**：子 agent fork 时灵活控制历史携带量：
   - `FullHistory` — 完整上下文
   - `LastNTurns(N)` — 最近 N 轮
   - `None` — 只带系统 prompt

5. **`try_start_turn_if_idle` 两阶段启动**：区分「用户触发」和「扩展自动触发」，扩展发起的 idle turn 可以被用户事件抢占。

### → 对夸父的启发

夸父目前是单 agent 直接处理请求，没有 agent 树。如果需要 subagent 能力，**不要从零设计 agent 间通信协议**，直接参考 Codex 的 `AgentPath + AgentRegistry + watch::Receiver` 模式。

---

## 三、Session 系统 — 消息驱动的异步中枢

`session/mod.rs` (3418行) 是整个 Codex 的中枢。

```rust
pub struct Codex {
    tx_sub: Sender<Submission>,       // 外部提交通道
    rx_event: Receiver<Event>,        // 事件接收通道
    agent_status: watch::Receiver<AgentStatus>,
}

// 提交操作
enum Op {
    UserInput { message: Message },
    Interrupt,
    Shutdown,
    SpawnAgent(SpawnAgentRequest),
    // ...
}

// Session 服务聚合
struct SessionServices {
    mcp_manager: McpManager,
    exec_policy_manager: ExecPolicyManager,
    hooks: HooksManager,
    auth: AuthManager,
    skills_manager: SkillsManager,
    plugins_manager: PluginsManager,
    analytics: AnalyticsEventsClient,
    // ...
}
```

### 关键设计模式

1. **`Submission + Event` 队列**：外部通过 `tx_sub` 发送操作，内部 `submission_loop` 先入先出处理。非阻塞。

2. **`SessionServices` 聚合**：所有子系统统一初始化，`Arc<SessionServices>` 共享引用。

3. **`SessionState` 状态机**：独立管理运行时状态（active turn、waiting turn），与 Session 结构分离。

4. **`SessionLoopTermination`**：`Shared<BoxFuture>` 允许多个调用者同时 await session 关闭。

### → 对夸父的启发

夸父的 `TaskManager`/`GatewayLoop` 可以考虑改为 **Submission/Event 队列模式**，将输入、中断、子 agent 事件统一为操作类型，避免 callback 和事件总线的复杂度。

---

## 四、Tool 系统 — 最精华的部分

Codex 的 tool 系统包含 7 个子模块，是最值得深入学习的设计。

### 4a. ToolOrchestrator — 四阶段编排 (`tools/orchestrator.rs`)

```
Phase 1: Approval（权限审批）
  ├── Skip（自动放行）
  ├── NeedsApproval（弹审批请求）
  └── Forbidden（直接阻止）

Phase 2: Select sandbox（选择执行沙箱）
  ├── Auto（自动决策）
  ├── Require（强制沙箱）
  └── Forbid（禁止沙箱）

Phase 3: First attempt（首次执行）
  ├── 网络审批：immediate / deferred 模式
  └── execute → success / failure

Phase 4: Retry with escalation（失败重试）
  ├── sandbox 拒绝 → 降级到 unsandboxed 重试
  └── 网络拒绝 → 弹审批请求
```

### 4b. ToolRegistry — 统一的 handler trait (`tools/registry.rs`)

```rust
trait CoreToolRuntime: ToolExecutor<ToolInvocation> {
    fn matches_kind(&self, payload: &ToolPayload) -> bool;
    fn telemetry_tags(...) -> BoxFuture<'_, ToolTelemetryTags>;
    fn pre_tool_use_payload(...) -> Option<PreToolUsePayload>;
    fn post_tool_use_payload(...) -> Option<PostToolUsePayload>;
    fn with_updated_hook_input(...) -> Result<ToolInvocation, FunctionCallError>;
    fn create_diff_consumer(...) -> Option<Box<dyn ToolArgumentDiffConsumer>>;
}

struct ToolRegistry {
    // Vec<(ToolName, Arc<dyn CoreToolRuntime>)>
}
```

### 4c. 动态 Tool Spec 构建 (`tools/spec_plan.rs`)

```rust
fn build_tool_specs_and_registry(turn_context, params) -> (Vec<ToolSpec>, ToolRegistry) {
    // 1. 收集所有 tool handler（内置 + MCP + extension + dynamic）
    // 2. 按 Exposure 策略过滤
    // 3. 去重（seen_tool_names HashSet）
    // 4. 构建 namespace 分组
    // 5. 构建 dispatch 用的 ToolRegistry
}
```

**ToolExposure 三级控制**：
- `Direct` — 正常暴露给模型
- `DirectModelOnly` — 仅对指定模型暴露
- `Hidden` — 不暴露但可 dispatch（内部 tool）

### 4d. 并行执行控制 (`tools/parallel.rs`)

```rust
struct ToolCallRuntime {
    parallel_execution: Arc<RwLock<()>>,  // 写锁=串行，读锁=并行
}
```

通过 `RwLock` 控制并行度：声明支持并发的 tool 获取读锁（可同时执行多个），声明不支持并发的获取写锁（串行化）。配合 `tokio::select!` + `CancellationToken` 支持取消。

### 4e. Tool Events (`tools/events.rs`)

三种事件发射器类型：`Shell`、`ApplyPatch`、`UnifiedExec`。每个发射器统一处理 start/success/failure 事件的 Telemetry 发射。

### → 对夸父的启发

| 夸父当前 | Codex 参考 | 改进方向 |
|---------|-----------|---------|
| 直通执行，无审批 | ToolOrchestrator 四阶段 | 插入 Approval → Sandbox → Retry 层 |
| 静态 tool 列表 | SpecPlan 动态构建 | 运行时根据上下文裁剪 tool |
| Python 函数直接注册 | CoreToolRuntime trait | 统一接口 + 钩子扩展点 |
| 无并行控制 | RwLock 读写锁 | 工具级并行/串行声明 |
| 简单 try/except | 沙箱降级重试 | 结构化 retry 策略 |

---

## 五、Exec 系统 — 进程执行与命令审批

### 5a. 进程执行引擎 (`exec.rs`, 1570行)

```rust
struct ExecParams {
    command: Vec<String>,
    cwd: AbsolutePathBuf,
    expiration: ExecExpiration,         // Timeout / Cancellation
    capture_policy: ExecCapturePolicy,  // ShellTool / FullBuffer
    env: HashMap<String, String>,
    network: Option<NetworkProxy>,
    sandbox_permissions: SandboxPermissions,
}

enum ExecExpiration {
    Timeout(Duration),
    DefaultTimeout,
    Cancellation(CancellationToken),
    TimeoutOrCancellation { timeout, cancellation },
}
```

设计要点：
- **`ExecExpiration`** 统一超时和取消，内部用 `tokio::select!` 等待
- **`ExecCapturePolicy`** 区分 shell 工具（有输出大小限制）和内部调用（全缓冲）
- **进程组杀**：`kill_child_process_group` 防止孙子进程泄露
- **IO drain 超时**：解决孙子进程继承 fd 导致挂起

### 5b. 命令审批策略 (`exec_policy.rs`, 1047行)

```rust
struct ExecPolicyManager {
    policy: ArcSwap<Policy>,   // 原子更新，无锁读
    update_lock: Semaphore,    // 写锁防并发
}
```

规则来源：从 `rules/` 目录读取 `.rules` 文件 + 内置 banned prefix（`python -c`, `bash -c`, `git` 等）。

**命令降级解析**：`bash -lc "cmd"` → 实际执行的 `cmd`，再匹配策略。

**`ArcSwap` 模式**：适合频繁读取、低频率写入的配置/策略。

### → 对夸父的启发

夸父的 `safety.py` + `approval.py` 可以重构成 `ExecPolicyManager` + 规则系统：
- `ArcSwap` 替代全局变量/锁
- `.rules` 文件替代硬编码的正则列表
- 命令降级解析解决 `bash -c "rm -rf /"` 绕过

---

## 六、Sandbox 系统 — 平台抽象沙箱

### 6a. 沙箱管理器

```rust
enum SandboxType { None, MacosSeatbelt, LinuxSeccomp, WindowsRestrictedToken }
enum SandboxablePreference { Auto, Require, Forbid }

struct SandboxManager;

impl SandboxManager {
    fn select_initial(fs_policy, net_policy, pref) -> SandboxType;
    fn transform(request: SandboxTransformRequest) -> Result<SandboxExecRequest, Error>;
}
```

选择策略：
- `Forbid` → 不用沙箱
- `Require` → 强制平台沙箱
- `Auto` → 根据 `FileSystemSandboxPolicy` 和 `NetworkSandboxPolicy` 自动判断

### 6b. Linux 沙箱

- `codex-linux-sandbox` 可执行文件：Landlock + seccomp-bpf
- 可选 bwrap（bubblewrap）沙箱
- 进程级加固（`process-hardening/src/lib.rs`）：取消能力（capabilities）、设置 no_new_privs

### → 对夸父的启发

夸父在 WSL 上运行，Landlock 可用（WSL2 内核 5.x+ 支持）。可以引入 `SandboxManager` 作为可选安全层，至少在 Linux 端实现 seccomp-bpf 限制。

---

## 七、Hooks 系统 — 10 个生命周期钩子

```rust
enum HookEventName {
    PreToolUse,          // tool 调用前
    PermissionRequest,   // 权限决策（可返回 Allow/Deny/传递给用户）
    PostToolUse,         // tool 调用后
    PreCompact,          // 上下文压缩前
    PostCompact,         // 上下文压缩后
    SessionStart,        // session 启动
    UserPromptSubmit,    // 用户提交 prompt
    SubagentStart,       // 子 agent 启动
    SubagentStop,        // 子 agent 停止
    Stop,                // session 停止
}
```

每个事件有对应的 `Request`/`Outcome` 结构。钩子配置支持：
- `command` 字段：调用外部脚本
- 事件匹配器：只匹配特定 tool、特定 session 类型

**亮点**：`PermissionRequest` 钩子允许第三方插件拦截和执行权限决策，返回 `Allow` / `Deny` / 传递给用户。这意味着安全策略可以由插件扩展，不硬编码在核心中。

---

## 八、记忆系统 — 两阶段生成

```rust
// memories/write/src/lib.rs (136行)
// Phase 1: 用 gpt-5.4-mini（低成本模型）从 rollout 中提取知识点
// Phase 2: 用 gpt-5.4（高精度模型）合并、去重、格式化到 raw_memories.md
```

存储结构：
```
~/.codex/memories/
├── raw_memories.md             # 合并后的记忆文件（人类可读可编辑）
├── extensions/
│   └── <name>/instructions.md  # 扩展提供的额外记忆信号
├── rollout_summaries/          # 每轮对话的总结
```

记忆触发条件：workspace 发生变化（diff 驱动），非每轮都写。

### → 对夸父的启发

夸父的 `memory_manager.py` + `hindsight_lite.py` + `episodic_buffer.py` 已经有两阶段概念。可以借鉴：
- **多模型分层**：Codex 用不同成本模型做不同阶段
- **文件系统持久化**：Markdown 文件便于用户审查编辑
- **增量触发**：仅在有关键变化时更新，减少 token 消耗

---

## 九、配置系统 — 多层堆叠

```
Cloud Config → App Requirements → Profile
    → User config.toml → Project config.toml → CLI overrides
```

```
Layer Source     Precedence
Cloud Config     Lowest （云端默认值）
App Requirements 次低 （应用安全要求，如 sandbox mode）
Profile          中间 （命名 profile）
User config      较高 （~/.codex/config.toml）
Project config   更高 （.codex/config.toml，仅信任项目）
CLI overrides   最高 （--config key=value）
```

关键概念：
- `ConfigLayerSource`：标记每个值的来源
- `Constrained<T>`：带约束的类型，跨层合并时校验冲突
- `ConfigRequirements`：应用级要求（如必须使用特定 sandbox mode）
- `ConfigLockfile`：锁定关键配置项，防止项目 config 覆盖安全设置

---

## 十、MCP 支持

Codex 同时是 **MCP client** 和 **MCP server**（实验性）：

**作为 MCP client**：启动时连接 MCP server，tool 自动暴露给模型。
**作为 MCP server**：`codex mcp-server`，允许其他 MCP client 把 Codex 当 tool 用。

`rmcp-client/` 和 `codex-mcp/` 是 MCP 协议实现的 Rust crate。

---

## 十一、对夸父的 P0-P2 改进建议

### 🏆 P0 — 立即进行的架构改进

| 改进 | Codex 参考文件 | 夸父对应模块 |
|------|--------------|-----------|
| **Tool Orchestrator 四阶段** | `core/src/tools/orchestrator.rs` | `tool_registry.py` + `safety.py` + `approval.py` |
| `Weak<GlobalState>` 生命周期 | `core/src/agent/control.rs` | 各模块间的强引用耦合 |
| `watch::Receiver` 状态订阅 | `core/src/agent/control.rs` | callback/事件总线 |

### 🥈 P1 — 短期引入

| 改进 | Codex 参考 | 夸父对应 |
|------|-----------|---------|
| **分层配置系统** | `config/src/loader.rs` | `config.yaml`（单层） |
| **动态 Tool Spec** | `core/src/tools/spec_plan.rs` | 静态 tool 列表 |
| **Tool Exposure 三级控制** | `core/src/tools/spec_plan.rs` | 无 |
| **ExecPolicyManager 命令规则** | `core/src/exec_policy.rs` | `safety.py` 硬编码规则 |
| **AgentPath 寻址系统** | `core/src/agent/control.rs` | 无 |

### 🥉 P2 — 中长期

| 改进 | Codex 参考 | 说明 |
|------|-----------|------|
| **Hooks 系统** | `hooks/src/lib.rs` | 10 个生命周期钩子 |
| **两阶段记忆系统** | `memories/write/src/lib.rs` | 不同成本模型分层 |
| **SandboxManager 抽象** | `sandboxing/src/manager.rs` | 平台沙箱抽象 |
| **Agent 树** | `core/src/agent/` | 多 agent 层级 |
| **InterAgentCommunication** | `core/src/codex_thread.rs` | agent 间消息通道 |

---

## 十二、源码文件索引

所有源码文件已下载到 `/home/asus/.hermes/codex-analysis/`：

```
agent_control.rs        # AgentControl 系统 (50KB)
agent_mod.rs            # agent 模块入口
agent_registry.rs       # AgentRegistry 注册表 (11KB)
agent_resolver.rs       # AgentPath 解析器
agent_role.rs           # AgentRole 定义 (16KB)
agent_status.rs         # AgentStatus 枚举
cli_lib.rs / cli_main.rs  # CLI 入口 (133KB)
codex_delegate.rs       # 任务委托 (30KB)
codex_mcp_lib.rs        # MCP 集成
codex_thread.rs         # Thread 管理 (22KB)
config_lib.rs           # 配置系统 (6KB)
core_lib.rs             # core crate 入口 (6KB)
core_plugins_lib.rs     # 插件系统
core_skills_lib.rs      # 内置 skills
exec.rs                 # 进程执行引擎 (56KB)
exec_policy.rs          # 命令审批策略 (37KB)
hooks_lib.rs            # Hooks 系统
mcp_server_lib.rs       # MCP server
memories_*.rs           # 记忆系统 (4.7KB)
process_hardening_lib.rs # 进程加固 (5.9KB)
sandboxing_lib.rs       # 沙箱抽象
session_mod.rs          # Session 中枢 (132KB)
skills_lib.rs           # Skills 系统
tools_events.rs         # Tool 事件 (23KB)
tools_mod.rs            # Tool 模块入口
tools_orchestrator.rs   # Tool 四阶段编排 (25KB)
tools_parallel.rs       # 并行执行 (21KB)
tools_registry.rs       # Tool 注册表 (26KB)
tools_router.rs         # Tool 路由 (7.5KB)
tools_spec_plan.rs      # 动态 Tool Spec (37KB)
turn_context.rs         # Turn 上下文 (38KB)
```

---

## 十三、补充优秀设计（之前未覆盖）

### 13.1. TurnContext — 不可变上下文快照

TurnContext 是每次 turn 开始时构建的不可变快照。关键模式：

- **函数式克隆更新**：`with_model()` 返回新的 `Self`，不修改原对象
- **自描述快照**：`to_turn_context_item()` 序列化为 rollout 项，支持 resume 时重建
- **三层递进**：`Config → PerTurnConfig → TurnContext`
- **`TurnMultiAgentRuntime::ResolveAndStore | Preview`**：共享代码路径但结果不同

→ 夸父的 `context_compress.py` + `prompt_template.py` 手动拼字符串，可改为结构化不可变 `TurnContext`

### 13.2. EventMapping — 类型安全的事件流

`parse_turn_item()` 将 API `ResponseItem` 映射为业务 `TurnItem`：

```rust
enum TurnItem {
    UserMessage, AgentMessage, Reasoning,
    WebSearch, ToolCall, ToolResult, Compact, ...
}
```

**上下文片段识别**：通过 `CONTEXTUAL_DEVELOPER_PREFIXES` 标记区分"框架注入"和"用户真实内容"，是压缩/rollback 策略的基础。

### 13.3. Rollout — 事件驱动会话持久化

**JSONL 事件日志**（非快照）：

```
{"meta": {"name": "session", "started_at": "..."}}
{"turn_start": {"id": "t1"}}
{"tool_call": {"id": "tc1", "name": "read_file"}}
{"tool_result": {"id": "tr1"}}
```

- `RolloutRecorder` + `Cursor` — 游标分页，增量读取
- `ThreadStore` trait — 可替换存储引擎
- `ArchivedSessions` — 归档机制

→ 夸父 `session_store.py` 是快照存储，事件日志更灵活

### 13.4. AGENTS.md 层次化发现

```
~/.codex/AGENTS.md → 全局
<project>/.codex/AGENTS.md → 项目
<project>/AGENTS.md → 项目根
```

`LoadedAgentsMd` 结构化解析，注入到 `turn_context.user_instructions`

### 13.5. Compact Hook 接口

```rust
trait CompactHook {
    fn pre_compact(&mut self, ctx: &CompactContext) -> Result<(), CompactError>;
    fn post_compact(&mut self, ctx: &CompactContext) -> Result<(), CompactError>;
}
```

保留最近 N 轮，中间压缩为摘要。pre/post hook 允许插件干预。

### 13.6. Skills 隐式触发

`maybe_emit_implicit_skill_invocation()` 检测 user prompt 自然语言触发 skill，不依赖 `/skill` 命令。

### 13.7. TurnDiffTracker — 纯内存 diff

跟踪 tool 调用文件变更，仅在有 diff 时更新记忆——避免每轮都写记忆浪费 token。

### 13.8. Safety — 三态决策树

```rust
enum SafetyDecision { Allow, BlockWithReason(String), Escalate { reason, suggestions } }
```

不是 allow/block 二态，`Escalate` 给用户展示安全建议列表。

### 13.9. ConfigLock — 配置锁

`approval_mode` / `sandbox_policy` 等安全关键配置可锁定，防止项目 `.codex/config.toml` 覆盖。

### 13.10. Spawn 三保险

每个子进程：①超时 ②进程组杀 ③IO drain 超时。三层防止子进程泄露。

### 13.11. 其他小模式

| 模式 | 说明 |
|------|------|
| `clippy::print_stdout` deny | 编译期禁止库代码直接写 stdout |
| API 追踪 header | `X_CODEX_TURN_METADATA_HEADER` |
| Deprecated 类型别名 | `#[deprecated] ConversationManager = ThreadManager` 做平滑迁移 |
| MCP Approval 模板 | 为 MCP 工具动态生成审批提示 |
| Rollout 逆序扫描重建 | 从末尾扫描找快照点，正向 replay |

---

*最后更新：2026-06-05*
