# 夸父 v1.0 — 开发计划（已完成 ✅）

> 目标：参考 OpenAI Codex CLI 源码架构，对夸父进行全面架构升级
> 状态：**全部完成** — 14 项改造，693 个测试通过

---

## 完成清单

| 阶段 | # | 模块 | 文件 | 测试 | 状态 |
|------|---|------|------|------|------|
| **P0** | 1 | ToolOrchestrator 四阶段编排 | `core/tool_orchestrator.py` | 14 | ✅ |
| **P0** | 2 | TurnContext 不可变快照 | `core/turn_context.py` | 14 | ✅ |
| **P0** | 3 | PolicyManager 三层统一 | `core/policy_manager.py` | 15 | ✅ |
| **P0** | 4 | Hook→Approval 贯通 | 改 3 个文件 | — | ✅ |
| **P1** | 5 | Rollout 事件日志 | `core/rollout_log.py` | 19 | ✅ |
| **P1** | 6 | ExecPolicy 规则文件+降级 | `core/exec_policy.py` | 18 | ✅ |
| **P1** | 7 | Skill 隐式触发+Metadata | ~~`core/skill_discovery.py`~~（已删除） | &nbsp; | ❌ |
| **P2** | 8 | Config 分层堆叠 | `core/config.py` | 9 | ✅ |
| **P2** | 9 | AGENTS.md 层次发现 | `core/agents_md.py` | 8 | ✅ |
| **P2** | 10 | 两阶段记忆提取 | `core/memory/two_phase_extract.py` | 7 | ✅ |
| **P2** | 11 | Agent 树系统 | `core/agent_tree.py` | 28 | ✅ |
| **P3** | 12 | Safety 三态决策 | 改 safety.py + orchestrator | 6 | ✅ |
| **P3** | 13 | CompactHook 接口 | `core/compact_hooks.py` | 6 | ✅ |
| **P3** | 14 | TurnDiffTracker | `core/turn_diff_tracker.py` | 14 | ✅ |

---

## v2.0 规划（未来方向）

| 方向 | 说明 |
|------|------|
| **Agent 树 → AgentRun** | 子 agent 通过 `completion_watcher` 非阻塞通知父 agent |
| **两阶段记忆 → 生产化** | 配置不同成本模型，diff 驱动自动提取 |
| **Safety Escalate 处理** | 在 agent_loop 中实现 Escalate 后的审批弹窗流程 |
| **Rollout 重建** | 从 JSONL 事件日志重建 session 状态（逆序扫描+正向 replay） |
| **MCP 工具审批模板** | 为 MCP 工具动态生成审批提示 |
| **Git Worktree 隔离生产化** | 子 agent 在独立 worktree 中运行 |
