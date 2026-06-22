# Changelog

> 夸父 (Kuafu) 版本历史

---

## v1.0.0 (2026-06-05)

### 🏗️ 架构重构：从 Codex CLI 学来的 14 项核心改造

夸父 v1.0 是一次全面的架构升级，参考 OpenAI Codex CLI 的 Rust 源码设计，
在保持 Python 生态优势的前提下，引入了一系列生产级 Agent 框架必备的能力。

#### P0 — 核心架构（4 项）

| 改造 | 文件 | 说明 |
|------|------|------|
| **ToolOrchestrator** | `core/tool_orchestrator.py` | Approval → Safety → Execute → Retry 四阶段编排 |
| **TurnContext** | `core/turn_context.py` | 不可变上下文快照 + 函数式更新 + 序列化 |
| **PolicyManager** | `core/policy_manager.py` | DenyRules + AutoMode + ApprovalManager 三层统一 |
| **Hook→Approval 贯通** | 改 policy + orchestrator | 权限决策自动发射 Hook 事件 |

#### P1 — 功能增强（3 项）

| 改造 | 文件 | 说明 |
|------|------|------|
| **Rollout 事件日志** | `core/rollout_log.py` | JSONL 事件源 + 游标查询 + 归档恢复 |
| **ExecPolicyManager** | `core/exec_policy.py` | 规则文件 + 命令降级解析（`bash -c "rm"` → `rm`） |
| **Skill 隐式触发** | ~~`core/skill_discovery.py`~~（已删除） |

#### P2 — 智能化（4 项）

| 改造 | 文件 | 说明 |
|------|------|------|
| **Config 分层堆叠** | `core/config.py` | 用户 → 项目 → CLI 层叠 + 热加载 |
| **AGENTS.md 发现** | `core/agents_md.py` | 全局 → 项目 → 本地三级级联 + 分段解析 |
| **两阶段记忆提取** | `core/memory/two_phase_extract.py` | 低成本预筛 + 高精度精炼 |
| **Agent 树系统** | `core/agent_tree.py` | AgentPath 寻址 + Registry + 状态订阅 |

#### P3 — 锦上添花（3 项）

| 改造 | 文件 | 说明 |
|------|------|------|
| **Safety 三态决策** | 改 safety.py + orchestrator | Allow / Block / Escalate + Hook |
| **CompactHook 接口** | `core/compact_hooks.py` | pre/post_compact 插件协议 |
| **TurnDiffTracker** | `core/turn_diff_tracker.py` | 纯内存文件变更追踪 |

#### 测试

- **新增 170+ 个测试**覆盖所有新模块
- **693 个测试全部通过**，零回归
- 所有新模块 100% 测试覆盖

---

## v0.4.1 (2026-06-04)

### 测试覆盖全面升级

核心模块全部达到 **100% 测试覆盖**：

| 模块 | 覆盖 | 测试数 |
|:----|:----:|:-----:|
| agent_loop.py — Agent 循环 | 100% | 256 |
| evolution.py — 进化引擎 | 100% | 61 |
| evolution_state.py — 进化状态 | 100% | 32 |
| evolution_rules.py — 规则引擎 | 100% | 37 |
| evolution_tracker.py — 进化追踪 | 100% | 160 |
| approval.py — 审批系统 | 100% | 242 |
| safety.py — 安全体系 | 100% | 215 |
| session_store.py — 会话存储 | 100% | 101 |
| llm.py — LLM 客户端 | 100% | 84 |
| context_compress.py — 上下文压缩 | 100% | 166 |
| tool_registry.py — 工具注册 | 100% | 198 |
| aggregate_search.py | 100% | 27 |
| browser.py | 100% | 54 |
| downloader.py | 100% | 37 |
| judge.py | 100% | 13 |
| main.py — Agent 入口 | 100% | 29 |
| mcp_bridge.py — MCP 协议 | 100% | 42 |
| subagent.py — 子 Agent | 100% | 34 |
| skill_deps.py | 100% | 53 |
| whiteboard/decomposer/executor | 100% | 99 |
| identity/observer | 100% | 44 |

### 新增

- **GitHub Actions CI** — `.github/workflows/ci.yml`，push/PR 自动测试
- **开发者文档** — `DEVELOPER.md` 详细架构/测试/编码规范
- **Setup Wizard** — 交互式初始化配置（`python setup_wizard.py`）

---

## v0.3.x 早期版本

请参阅 git history。
