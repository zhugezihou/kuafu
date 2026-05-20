# 夸父 (Kuafu)

> **夸父逐日，不息，自我超越。**
> 永不停止地追逐目标，每一次执行都是进化的一步。

夸父是一个自我进化的 AI Agent 框架。每次任务完成后，它自动反思、学习、优化自己的能力。用户感觉不到进化的过程，只知道夸父越来越好用。

**夸父不是一个被动的工具，它是一个活的 agent。**

---

## 核心理念

- **进化 = 工作的自然产物**，不是额外操作
- **核心不可破坏** — `core/` 目录只读保护区，任何 agent 实例都不可修改
- **身份感知** — 知道自己是谁、用户是谁、边界在哪里（`IDENTITY.md`）
- **向后兼容** — V1 接口永久不变，进化不破坏已有集成
- **先能用再进化** — V1 能干活，V2 开始自我改进
- **零依赖哲学** — 核心仅依赖 `pyyaml`，网络操作直接用 Python 标准库

## 架构

```
kuafu/
├── core/                 ← 只读保护区
│   ├── identity.py       # 身份系统 — 我是谁
│   ├── sandbox.py        # 沙盒安全 — 能做什么/不能做什么
│   ├── memory_api.py     # 记忆系统 — 记得什么（支持 file / hindsight 双模式）
│   ├── evolution.py      # 进化引擎 — 五级进化（L1-L5）
│   ├── llm.py            # LLM 客户端 — DeepSeek Chat API 调用
│   ├── agent_loop.py     # Agent 执行循环 — ReAct 循环引擎 + 8 个工具
│   └── main.py           # Agent 入口 — CLI + 系统 prompt 组装
├── strategy/             ← 可进化区
│   ├── prompts.yaml      # 任务提示模板
│   ├── task_strategies.yaml
│   └── quality.yaml
├── skills/               ← 可进化区（可复用技能包）
├── memory/               ← 记忆数据
└── tests/
    └── test_all.py       # 11 个核心测试
```

### 核心引擎

夸父 V0.2 引入 **AgentLoop** — 一个完整的 ReAct 循环引擎：

| 组件 | 文件 | 职责 |
|------|------|------|
| AgentLoop | `core/agent_loop.py` | LLM + 工具调用的循环引擎，最大 15 轮交互 |
| LLMClient | `core/llm.py` | DeepSeek Chat API 客户端（urllib 直连） |
| MemoryAPI | `core/memory_api.py` | 双模式记忆系统（file / hindsight） |
| EvolutionEngine | `core/evolution.py` | 事件驱动进化引擎（L1-L5） |

### 可用工具（8 个夸父工具 + 记忆系统）

| 工具 | 描述 | 实现 |
|------|------|------|
| `terminal` | 终端命令执行 | subprocess + sandbox 安全检查 |
| `read_file` | 读取文件 | Python 文件操作 |
| `write_file` | 写入文件 | 含 `core/` 写保护检查 |
| `patch` | 精确文本替换 | 仅支持唯一匹配 |
| `search_files` | 文件内容/名称搜索 | ripgrep / find |
| `web_search` | 互联网搜索 | DuckDuckGo Lite → Bing fallback |
| `web_fetch` | 网页内容抓取 | urllib + HTML 文本提取 |
| `finish` | 完成任务 | 提交最终结果 + 触发进化 |

### 核心层不可变

`core/` 下的代码是夸父的宪法：
- 任何 agent 实例**禁止**修改 `core/`
- `sandbox.py` 在每次文件操作前检查路径白名单
- 身份声明 `IDENTITY.md` 固定在 system prompt 最上层

### 进化等级

| 等级 | 触发条件 | 动作 |
|------|---------|------|
| L1 | 重复出现相同错误 | 优化当前任务策略 |
| L2 | 同类型任务成功 5 次 / 连续失败 3 次 | 更新策略模板 |
| L3 | L2 进化后 + 有可复用的成功模式 | 提取为技能包 |
| L4 | 进化达 10 次 | 重构 system prompt |
| L5 | 积累足够经验 | 元学习：自我调整进化参数 |

所有进化都是**事件驱动**的，不使用 cron 定时触发。每一次任务完成是进化的自然契机。

### 记忆系统

夸父支持双模记忆后端，通过 `.env` 文件配置：

```env
# 可选值: 'file' (默认) 或 'hindsight'
KUAFU_MEMORY_MODE=file

# Hindsight 模式需配置（cloud API）
HINDSIGHT_API_KEY=your_api_key_here
HINDSIGHT_BANK_ID=default
```

| 模式 | 后端 | 能力 |
|------|------|------|
| `file` | JSON 文件 | 关键词匹配搜索，零依赖，离线可用 |
| `hindsight` | Hindsight Cloud API | 语义搜索、实体图谱、综合推理 |

夸父自动在 system prompt 中注入最近记忆，让 agent 感知上下文。

### 网络搜索

夸父内置的 `web_search` 工具使用两级 fallback：
1. **DuckDuckGo Lite** — 速度快，无需 API key
2. **Bing** — DDG 不可用时自动切换（urllib 直接抓取搜索结果）

在国内 WSL 环境中自动适配。无需额外配置。

## 快速开始

```bash
# 克隆
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu

# 安装（仅 pyyaml）
pip install -r requirements.txt

# 配置 API key
echo 'DEEPSEEK_API_KEY=sk-your-key-here' >> .env

# 跑测试
python tests/test_all.py

# 交互使用
python -m core.main
```

### CLI 模式

```bash
# 单次任务
python -c "
from kuafu import KuafuAgent
agent = KuafuAgent()
result = agent.run('帮我搜索最新的 Python 发布信息')
print(result['result'])
"

# 查看状态
python -c "
from kuafu import KuafuAgent
agent = KuafuAgent()
status = agent.get_status()
print(f\"版本: {status['version']}\")
print(f\"记忆条数: {status['memory']['total']}\")
print(f\"进化次数: {status['evolution']['total_evolutions']}\")
"
```

## 开发

```bash
# 运行全部测试
python tests/test_all.py

# 验证核心模块
python -c "
from core.identity import load_identity_statement
from core.sandbox import validate_command
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine
print('所有核心模块加载成功')
"
```

### 测试覆盖

```
11 个测试用例覆盖：
├── identity          — 身份声明加载
├── sandbox           — 路径 + 命令安全检查
├── memory_api        — 写入/检索/反思
├── evolution         — 触发条件 & 统计
├── main (agent_repr) — Agent 初始化
├── main (prompt)     — 系统 prompt + 状态查询
├── full_flow         — memory + evolution 联合
├── core_charter      — 核心文件完整性
├── llm               — 客户端导入
├── agent_loop (tools) — 8 个工具定义完整
└── agent_loop (prompt) — 系统 prompt 组装
```

## 状态

✅ **V0.2 — 核心引擎已就绪**

| 模块 | 状态 | 说明 |
|------|------|------|
| 身份系统 | ✅ | `core/identity.py` |
| 沙盒安全 | ✅ | `core/sandbox.py` |
| 记忆系统 | ✅ | `core/memory_api.py` — 双模（file/hindsight） |
| 进化引擎 | ✅ | `core/evolution.py` — L1/L2 实现 |
| LLM 客户端 | ✅ | `core/llm.py` — DeepSeek API |
| Agent 循环 | ✅ | `core/agent_loop.py` — ReAct + 8 工具 |
| Agent 入口 | ✅ | `core/main.py` — CLI + 系统 prompt |
| 搜索引擎 | ✅ | web_search（DDG → Bing fallback） |
| 网页抓取 | ✅ | web_fetch（urllib + HTML 提取） |
| 测试覆盖 | ✅ | 11/11 全部通过 |
| GitHub 集成 | ✅ | 已推送至 `zhugezihou/kuafu` |

**路线图：**

- [ ] V2.1：自动技能提取（L3）
- [ ] V2.2：prompt 进化（L4）
- [ ] V3：元学习（L5）
- [ ] V3.1：多模型支持
- [ ] V3.2：飞书集成

## 项目来源

夸父原本是 Hermes Agent 的一个扩展项目，后来独立发展为一个通用 self-improving agent 框架。项目最初受 Memento-Skills、EvoSkill、Reflexion 等研究成果启发。

## 反馈

夸父在不同环境下会有不同的进化和 bug。发现有趣的进化或遇到问题，欢迎来 **[kuafu-feedback](https://github.com/zhugezihou/kuafu-feedback)** 提 issue，帮助我们一起改进。

## License

Apache 2.0
