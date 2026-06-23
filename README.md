# 夸父 (Kuafu)

> [English](./README_EN.md) | 中文

> **夸父逐日，不息，自我超越。**
> 永不停止地追逐目标，每一次执行都是进化的一步。

夸父是一个自我进化的 AI Agent 框架。每次任务完成后，它自动反思、学习、优化自己的能力。

**夸父不是一个被动的工具，它是一个活的 agent。**

---

## 快速开始

### 安装

```bash
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu
python3 -m venv venv
source venv/bin/activate
pip install -e .
python setup_wizard.py
```

### 使用

```bash
# 交互模式
bash kuafu.sh

# 单次任务
bash kuafu.sh '帮我搜索最新的 Python 发布信息'

# Gateway 模式（飞书/微信）
bash kuafu.sh gateway start

# 查看状态
bash kuafu.sh status

# 定时任务
bash kuafu.sh cron list
```

### Python API

```python
from core.main import KuafuAgent

agent = KuafuAgent()
result = agent.run('帮我搜索最新的 Python 发布信息')
print(result['result'])
```

---

## 架构亮点（v1.1）

### 双后端 LLM 引擎（v1.1 新特性）

夸父支持**云端 + 本地双后端自动降级**：

```
KUAFU_PROVIDERS=deepseek,qwen
```

- **主后端（DeepSeek）**：复杂推理、工具调用、多轮对话
- **备选后端（本地 Qwen）**：云端不可用时自动切换，不中断工作流
- **本地模型辅助层（LocalHelper）**：记忆分类、对话摘要、子Agent结果提炼 — 零 API 费用

本地模型缺失完全不影响夸父正常运行，降级路径全覆盖。

### 四阶段工具执行

```
ToolOrchestrator.execute()
  ├── Phase 1: PolicyManager.decide()
  │   ├── Pre-check: 硬黑名单 / 只读 / 安全命令
  │   ├── Layer 1: DenyRules — 硬拒绝
  │   ├── Layer 2: AutoMode — 自动分类
  │   ├── Layer 3: 人工审批
  │   └── → emits on_permission_check / on_tool_rejected hooks
  ├── Phase 2: SafetyLayer.get_tri_state()
  │   └── Allow / Block / Escalate 三态决策
  ├── Phase 3: ToolRegistry.execute()
  └── Phase 4: Retry (可配置)
```

### 记忆系统（Hindsight-Lite + NMM 联想引擎）

三层记忆架构：
- **L0 缓存环**：当前 session 热点记忆
- **L1 四网络存储**：World / Experience / Observation / Opinion
- **L2 NMM 神经记忆**：语义联想检索（可选，0.6+ 置信度阈值防虚假关联）

### 上下文管理

```
ContextCompressor + ContextCollapse + ToolResultStore + Microcompact
  ├── Budget Allocator：Token 预算分配，预警 → 压缩 → 降级
  ├── ContextCollapse：非破坏性上下文投影（保留关键信息）
  ├── Microcompact：大工具结果 → 磁盘摘要（节省 40-60% token）
  └── BudgetReduction：零 token 成本就地裁剪
```

### 专家系统

10+ 领域专家（代码 / 研究 / 数据 / 安全等），通过 `invoke_expert` 工具调用，独立推理，结果直接注入主上下文。

### 事件驱动持久化

```
RolloutLog (JSONL 事件日志) + SessionStore (SQLite 快速查询)
  ├── 游标分页查询
  ├── 按事件类型过滤
  └── 归档 + 恢复
```

### Agent 树

```
AgentPath 寻址系统（/root/child/grandchild）
AgentRegistry 全局注册表
LiveAgent 状态订阅（IDLE → RUNNING → COMPLETED/FAILED）
```

---

## 配置

夸父支持从环境变量、YAML 文件、CLI 参数三层配置：

```bash
# 环境变量
export KUAFFU_DISABLE_APPROVAL=1   # 禁用审批
export KUAFFU_GATEWAY_RUNNING=1    # Gateway 模式

# LLM 后端降级顺序（v1.1）
export KUAFU_PROVIDERS=deepseek,qwen
export QWEN_BASE_URL=http://localhost:8080
export QWEN_MODEL=Qwen3.5-9B-DeepSeek-V4-Flash-IQ4_XS.gguf

# 配置文件 (~/.kuafu/config.yaml)
cat ~/.kuafu/config.yaml
approval:
  timeout: 300
  mode: gateway
model:
  provider: deepseek
  name: deepseek-chat
```

---

## 核心理念

- **进化 = 工作的自然产物**，不是额外操作
- **核心不可破坏** — `core/` 目录只读保护区，任何 agent 实例都不可修改
- **身份感知** — 知道自己是谁、用户是谁、边界在哪里

---

## 项目结构

```
kuafu/
├── core/                          # 核心执行引擎
│   ├── agent_loop.py              # Agent 主循环 (~2720行)
│   ├── llm.py                     # LLM 客户端 — N 后端 + 自动降级【v1.1 增强】
│   ├── local_helper.py            # 本地模型辅助层（记忆分类/摘要）【v1.1 新增】
│   ├── tool_registry.py           # 三级工具注册系统
│   ├── tool_orchestrator.py       # 四阶段工具编排
│   ├── policy_manager.py          # 统一策略管理
│   ├── turn_context.py            # 不可变上下文
│   ├── rollout_log.py             # 事件日志
│   ├── exec_policy.py             # 命令执行策略
│   ├── agent_tree.py              # Agent 树系统
│   ├── config.py                  # 分层配置
│   ├── agents_md.py              # AGENTS.md 发现
│   ├── compact_hooks.py          # 压缩 Hook 接口
│   ├── turn_diff_tracker.py       # 文件变更追踪
│   ├── skill_resolver.py          # 技能解析
│   ├── approval.py                # 审批系统 (Layer 1~3)
│   ├── safety.py                  # 三态安全决策
│   ├── context_compress.py        # 上下文压缩管线
│   ├── session_store.py           # 会话存储 (SQLite)
│   ├── hooks.py                   # 29 个钩子事件点
│   ├── memory/                    # 记忆系统 (四网络 + 两阶段提取)
│   ├── subagent.py                # 子 Agent 系统
│   ├── cli.py                     # CLI 入口
│   └── main.py                    # Agent 入口 (v1.1.0)
├── autonomous/                    # 自主学习系统
├── tests/                         # ~1900+ 测试
├── kuafu.sh                       # 启动脚本
└── install.sh                     # 安装脚本
```

---

## 技术栈

- **Python 3.10+** — 零额外依赖（标准库 + pyyaml）
- **架构参考** — OpenAI Codex CLI (Apache-2.0)

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1.0 | 2026-06-23 | 本地大模型接入、NMM 联想过滤、输出截断修复、max_tokens 动态调整 |
| v1.0 | — | 初始发布：Codex CLI 架构迁移、14 项核心改造 |

---

## License

Apache-2.0
