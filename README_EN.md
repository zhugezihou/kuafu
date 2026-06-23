# Kuafu (夸父)

> **Kuafu Chases the Sun — Relentless, Self-Transcending.**
> Forever chasing the goal, every execution is a step toward evolution.

Kuafu is a self-evolving AI Agent framework. After every task, it automatically reflects, learns, and optimizes its own capabilities.

**Kuafu is not a passive tool — it's a living agent.**

---

## Quick Start

### Installation

```bash
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu
python3 -m venv venv
source venv/bin/activate
pip install -e .
python setup_wizard.py
```

### Usage

```bash
# Interactive mode
bash kuafu.sh

# Single task
bash kuafu.sh 'search for the latest Python release info'

# Gateway mode (Feishu/WeChat)
bash kuafu.sh gateway start

# Check status
bash kuafu.sh status

# Cron jobs
bash kuafu.sh cron list
```

### Python API

```python
from core.main import KuafuAgent

agent = KuafuAgent()
result = agent.run('search for the latest Python release info')
print(result['result'])
```

---

## Architecture Highlights (v1.1)

### Dual Backend LLM Engine (new in v1.1)

Kuafu supports **auto-fallback between cloud + local models**:

```
KUAFU_PROVIDERS=deepseek,qwen
```

- **Primary (DeepSeek)**: complex reasoning, tool calling, multi-turn dialogue
- **Fallback (local Qwen)**: automatically switches when cloud is unavailable
- **LocalHelper layer**: memory classification, dialogue summarization, sub-agent result refinement — zero API cost

No single point of failure — all degradation paths are fully covered.

### Four-Phase Tool Execution

```
ToolOrchestrator.execute()
  ├── Phase 1: PolicyManager.decide()
  │   ├── Pre-check: blacklist / read-only / safe commands
  │   ├── Layer 1: DenyRules — hard reject
  │   ├── Layer 2: AutoMode — auto-classification
  │   ├── Layer 3: Manual approval
  │   └── → emits on_permission_check / on_tool_rejected hooks
  ├── Phase 2: SafetyLayer.get_tri_state()
  │   └── Allow / Block / Escalate tri-state decision
  ├── Phase 3: ToolRegistry.execute()
  └── Phase 4: Retry (configurable)
```

### Memory System (Hindsight-Lite + NMM)

Three-layer memory architecture:
- **L0 Cache Ring**: hot memory for current session
- **L1 Four-Network Store**: World / Experience / Observation / Opinion
- **L2 NMM Neural Memory**: semantic associative recall (optional, 0.6+ confidence threshold prevents false associations)

### Context Management

```
ContextCompressor + ContextCollapse + ToolResultStore + Microcompact
  ├── Budget Allocator: token budget allocation → warning → compact → degrade
  ├── ContextCollapse: non-destructive context projection
  ├── Microcompact: large tool results → disk summary (save 40-60% tokens)
  └── BudgetReduction: zero-cost in-place trimming
```

### Expert System

10+ domain experts (code / research / data / security, etc.), invoked via `invoke_expert` tool, independent inference, results injected into main context.

### Event-Driven Persistence

```
RolloutLog (JSONL event log) + SessionStore (SQLite fast query)
  ├── Cursor-based pagination
  ├── Event-type filtering
  └── Archive + restore
```

### Agent Tree

```
AgentPath addressing (/root/child/grandchild)
AgentRegistry global registry
LiveAgent status subscription (IDLE → RUNNING → COMPLETED/FAILED)
```

---

## Configuration

Kuafu supports three-layer configuration: environment variables, YAML files, CLI parameters.

```bash
# Environment variables
export KUAFU_DISABLE_APPROVAL=1   # disable approval
export KUAFU_GATEWAY_RUNNING=1    # Gateway mode

# LLM backend fallback order (v1.1)
export KUAFU_PROVIDERS=deepseek,qwen
export QWEN_BASE_URL=http://localhost:8080
export QWEN_MODEL=Qwen3.5-9B-DeepSeek-V4-Flash-IQ4_XS.gguf

# Config file (~/.kuafu/config.yaml)
cat ~/.kuafu/config.yaml
approval:
  timeout: 300
  mode: gateway
model:
  provider: deepseek
  name: deepseek-chat
```

---

## Project Structure

```
kuafu/
├── core/                          # Core execution engine
│   ├── agent_loop.py              # Agent main loop (~2720 lines)
│   ├── llm.py                     # LLM client — N backends + auto-fallback
│   ├── local_helper.py            # Local model helper (memory/summary) [v1.1]
│   ├── tool_registry.py           # 3-level tool registration
│   ├── tool_orchestrator.py       # 4-phase tool orchestration
│   ├── policy_manager.py          # Unified policy management
│   ├── turn_context.py            # Immutable context
│   ├── rollout_log.py             # Event log
│   ├── exec_policy.py             # Command execution policy
│   ├── agent_tree.py              # Agent tree system
│   ├── config.py                  # Layered configuration
│   ├── agents_md.py              # AGENTS.md discovery
│   ├── compact_hooks.py          # Compression hook interface
│   ├── turn_diff_tracker.py       # File change tracking
│   ├── skill_resolver.py          # Skill resolution
│   ├── approval.py                # Approval system
│   ├── safety.py                  # Tri-state safety
│   ├── context_compress.py        # Context compression pipeline
│   ├── session_store.py           # Session storage (SQLite)
│   ├── hooks.py                   # 29 hook event points
│   ├── memory/                    # Memory system
│   ├── subagent.py                # Sub-agent system
│   ├── cli.py                     # CLI entry
│   └── main.py                    # Agent entry (v1.1.0)
├── autonomous/                    # Autonomous learning
├── tests/                         # ~1900+ tests
├── kuafu.sh                       # Launch script
├── Dockerfile                     # Docker build
└── install.sh                     # Install script
```

---

## Docker

```bash
# Start Gateway mode (Feishu/WeChat)
docker compose up -d

# Interactive mode
docker compose run --rm kuafu bash kuafu.sh

# With local model (requires NVIDIA GPU + container toolkit)
docker compose --profile local up -d
```

Configuration via `.env` file:
```
KUAFU_PROVIDERS=deepseek,qwen
DEEPSEEK_API_KEY=sk-xxx
```

---

## Core Principles

- **Evolution = natural byproduct of work**, not an extra operation
- **Core is inviolable** — `core/` directory is read-only; no agent instance can modify it
- **Identity-aware** — knows who it is, who the user is, and where the boundaries lie

---

## Tech Stack

- **Python 3.10+** — zero external dependencies (stdlib + pyyaml)
- **Architecture reference** — OpenAI Codex CLI (Apache-2.0)

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| v1.1.0 | 2026-06-23 | Local LLM integration, NMM associative filtering, output truncation fix, dynamic max_tokens |
| v1.0 | — | Initial release: Codex CLI architecture migration, 14 core upgrades |

---

## License

Apache-2.0
