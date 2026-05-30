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
├── core/                    ← 只读保护区
│   ├── identity.py          # 身份系统 — 我是谁
│   ├── sandbox.py           # 沙盒安全 — 能做什么/不能做什么
│   ├── memory_api.py        # 记忆系统 — 支持 file / hindsight 双模式
│   ├── evolution.py         # 进化引擎 — D 方案（即兴进化）
│   ├── llm.py               # LLM 客户端 — DeepSeek Chat API 调用
│   ├── agent_loop.py        # Agent 执行循环 — ReAct 循环引擎
│   ├── main.py              # Agent 入口 — CLI + 系统 prompt 组装
│   ├── subagent.py          # 子 Agent 系统 — 隔离上下文，同步执行
│   ├── mcp_bridge.py        # MCP 协议桥 — stdio 子进程集成
│   ├── webhook_server.py    # WebHook 服务器 — HTTP 事件驱动
│   └── channel/             # 消息通道层
│       ├── base.py          # MessageChannel 抽象基类
│       ├── feishu.py        # 飞书通道实现
│       └── __init__.py      # ChannelManager 管理器
├── autonomous/              ← 可选增强（自我审查/健康检查）
│   ├── strategy_loader.py   # 策略加载器
│   ├── reviewer.py          # Reviewer 线程
│   └── self_health.py       # 健康检查线程
├── strategy/                ← 可进化区
│   ├── prompts.yaml         # 任务提示模板
│   ├── task_strategies.yaml
│   └── quality.yaml
├── skills/                  ← 可进化区（可复用技能包）
├── memory/                  ← 记忆数据
├── tests/
│   └── test_all.py          # 14 个核心测试
├── scripts/
│   ├── start.sh             # 启动脚本
│   ├── stop.sh              # 停止脚本
│   └── status.sh            # 状态查看
├── install.sh               # 一键安装脚本
├── kuafu.sh                 # 快速启动入口
└── pyproject.toml            # pip 安装配置
```

### 核心引擎

| 组件 | 文件 | 职责 |
|------|------|------|
| AgentLoop | `core/agent_loop.py` | LLM + 工具调用的循环引擎，最大 15 轮交互 |
| LLMClient | `core/llm.py` | DeepSeek Chat API 客户端（urllib 直连） |
| MemoryAPI | `core/memory_api.py` | 双模式记忆系统（file / hindsight） |
| EvolutionEngine | `core/evolution.py` | D 方案进化引擎（即兴进化，无等级预设） |

### 可用工具（14 个夸父工具 + 记忆系统）

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
| `delegate_task` | 子 Agent 委托 | 隔离上下文，受限工具集，同步执行 |
| `mcp_toolify` | MCP 工具代理 | stdio 子进程转发到 LLM |
| `feishu_send` | 飞书消息发送 | HTTP API → 朝堂群 |
| `feishu_read` | 飞书文档读取 | HTTP API 获取文档内容 |
| `cron_tool` | Cron 任务管理 | 调度/查询异步任务 |
| `read_chat` | 读取当前 session 消息历史 | 回顾本轮完整内容 |

### 核心层不可变

`core/` 下的代码是夸父的宪法：
- 任何 agent 实例**禁止**修改 `core/`
- `sandbox.py` 在每次文件操作前检查路径白名单
- 身份声明 `IDENTITY.md` 固定在 system prompt 最上层

### 进化引擎（D 方案）

夸父 v0.4 使用 **D 方案（即兴进化）** — 废除旧版 L0-L5 分级：

- 每轮任务完成后，LLM 当场判断「值不值得学」
- 值得学的内容当场生成 `SKILL.md`
- 不需要提前预设进化等级

> 进化不再是「触发条件满足后升级」，而是**任务完成时顺带的自然行为**。

### 子 Agent 系统

夸父支持通过 `delegate_task` 工具创建隔离的子 Agent：

- **隔离上下文** — 子 Agent 有自己的 conversation + terminal + toolset
- **同步执行** — 在父 Agent 的 ReAct 循环中同步等待结果
- **受限工具** — 可指定子 Agent 可用的工具集
- **不可递归** — 子 Agent 不能创建更深的子 Agent
- **最大 3 并发** — 一次可并行执行多个子任务

子 Agent 使用函数 API（`get_delegate_schema` + `handle_delegate`），无 SubAgentPool 类。

### MCP 协议桥

夸父通过 `core/mcp_bridge.py` 支持 MCP（Model Context Protocol）：

- **stdio 子进程** — 不破坏 `core/` 安全规则
- **`mcp_toolify` 工具** — 将 MCP Server 的工具暴露给 LLM 调用
- **配置文件** — `core/mcp_config.yaml` 管理 MCP Server 注册

### WebHook 事件驱动

夸父支持 HTTP WebHook 触发：

- 通过 `--webhook-port 8765 --webhook-token xxx` 启动
- 接收 JSON payload，在线程中执行任务
- 适合 CI/CD、GitHub WebHook、飞书回调等场景

### 消息通道（Channel）

夸父支持多平台消息通道：

- **`channel/base.py`** — 统一抽象（MessageChannel）
- **`channel/feishu_ws.py`** — 飞书 WebSocket 直连（lark-oapi）
- **`channel/wechat_personal.py`** — 个人微信 Wechaty 通道
- **`ChannelManager`** — 多通道注册 + 消息分发

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

## 安装

### 一键安装（推荐）

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/zhugezihou/kuafu/main/install.sh | bash

# 或克隆后本地安装
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu
bash install.sh
```

`install.sh` 会自动完成：
1. ✅ 检测 Python 3.10+、pip、git
2. ✅ 创建虚拟环境并 `pip install -e .` 安装
3. ✅ 启动交互式配置向导（选择云端/本地后端）
4. ✅ 测试 LLM 连接
5. ✅ 运行全部测试
6. ✅ 给出下一步指引

### 手动安装

```bash
# 1. 克隆
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu

# 2. 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 3. 安装夸父
pip install -e .

# 4. 运行配置向导
python setup_wizard.py

# 5. 验证安装
python -m pytest tests/test_all.py -v
```

### pip 安装

```bash
pip install kuafu
```

### Docker 部署

```bash
# 构建镜像
docker build -t kuafu .

# 运行（挂载配置和记忆）
docker run -it --rm \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/memory:/app/memory \
  kuafu
```

---

### 选择后端

| 模式 | 优点 | 要求 |
|------|------|------|
| **云端** (DeepSeek) | 免 GPU，开箱即用 | DeepSeek API Key |
| **本地** (Qwen3.5-9B) | 免费，隐私，低延迟 | NVIDIA GPU 8GB+，llama.cpp |

#### 云端模式（推荐新手）

```bash
# 运行配置向导
python setup_wizard.py
# → 选择 cloud → 输入 API Key → 自动测试
```

#### 本地模式（需 GPU）

```bash
# 1. 编译 llama-server（或下载 Release）
git clone https://github.com/ggml-ai/llama.cpp
cd llama.cpp && cmake -B build && cmake --build build --config Release
# 或: pip install llama-cpp-python

# 2. 下载模型（约 5.8GB）
bash scripts/download_model.sh
# 自动下载 Qwen3.5-9B-UD-Q4_K_XL.gguf

# 3. 启动推理服务器
build/bin/llama-server \
  -m models/Qwen3.5-9B-UD-Q4_K_XL.gguf \
  -c 8192 --port 8080

# 4. 夸父配置
echo 'KUAFFU_BACKEND=local' >> .env

# 5. 启动夸父
bash kuafu.sh
```

---

### 快速启动

```bash
# 交互模式
bash kuafu.sh

# 单次命令
bash kuafu.sh '帮我搜索最新的 Python 发布信息'

# 查看状态
bash scripts/status.sh

# 后台运行
bash scripts/start.sh --daemon
# → 停止: bash scripts/stop.sh
```

### Python API

```python
from core.main import KuafuAgent

agent = KuafuAgent()

# 单次任务
result = agent.run('帮我搜索最新的 Python 发布信息')
print(result['result'])

# 查看状态
status = agent.get_status()
print(f"版本: {status['version']}")
print(f"记忆条数: {status['memory']['total']}")
print(f"进化次数: {status['evolution']['total_evolutions']}")
```

## 开发

```bash
# 运行全部测试（14 个）
python -m pytest tests/test_all.py -v

# 运行全部测试（纯 Python）
python tests/test_all.py

# 验证核心模块
python -c "
from core.identity import load_identity_statement
from core.sandbox import validate_command
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine
from core.subagent import get_delegate_schema, handle_delegate
from core.mcp_bridge import MCPBridge
from core.webhook_server import WebhookServer
from core.channel import ChannelManager
print('所有核心模块加载成功')
"
```

### 测试覆盖

```
14 个测试用例覆盖：
├── identity              — 身份声明加载
├── sandbox               — 路径 + 命令安全检查
├── memory_api            — 写入/检索/反思
├── evolution             — 进化引擎（D 方案）
├── main (agent_repr)     — Agent 初始化
├── main (prompt)         — 系统 prompt + 状态查询
├── full_flow             — memory + evolution 联合
├── core_charter          — 核心文件完整性
├── llm                   — 客户端导入
├── agent_loop (tools)    — 全部工具定义完整
├── agent_loop (prompt)   — 系统 prompt 组装
├── subagent              — 子 Agent 委托函数
├── feishu_channel        — 飞书通道集成
└── mcp_bridge            — MCP 协议桥连接
```

## 状态

✅ **v0.4 — MCP / 子 Agent / WebHook / Channel / D-进化 完整实现**

| 模块 | 状态 | 说明 |
|------|------|------|
| 身份系统 | ✅ | `core/identity.py` |
| 沙盒安全 | ✅ | `core/sandbox.py` |
| 记忆系统 | ✅ | `core/memory_api.py` — 双模（file/hindsight） |
| 进化引擎 | ✅ | `core/evolution.py` — D 方案（即兴进化） |
| LLM 客户端 | ✅ | `core/llm.py` — DeepSeek API |
| Agent 循环 | ✅ | `core/agent_loop.py` — ReAct + 14 工具 |
| Agent 入口 | ✅ | `core/main.py` — CLI + 系统 prompt |
| 子 Agent 系统 | ✅ | `core/subagent.py` — 隔离上下文，同步执行 |
| MCP 协议桥 | ✅ | `core/mcp_bridge.py` — stdio 子进程集成 |
| WebHook 服务器 | ✅ | `core/webhook_server.py` — HTTP 事件驱动 |
| 消息通道 | ✅ | `core/channel/` — 飞书消息通道 |
| 搜索引擎 | ✅ | web_search（DDG → Bing fallback） |
| 网页抓取 | ✅ | web_fetch（urllib + HTML 提取） |
| 测试覆盖 | ✅ | 14/14 全部通过 |
| pip 安装 | ✅ | pyproject.toml build-metadata 修复 |
| GitHub 集成 | ✅ | 已推送至 `zhugezihou/kuafu` |

**规划中：**

- [ ] 飞书交互式消息卡片
- [ ] 多模型支持（开关自由切换）
- [ ] 分布式 Worker 模式

## 项目来源

夸父原本是 Hermes Agent 的一个扩展项目，后来独立发展为一个通用 self-improving agent 框架。项目最初受 Memento-Skills、EvoSkill、Reflexion 等研究成果启发。

## 反馈

夸父在不同环境下会有不同的进化和 bug。发现有趣的进化或遇到问题，欢迎来 **[kuafu-feedback](https://github.com/zhugezihou/kuafu-feedback)** 提 issue，帮助我们一起改进。

## License

Apache 2.0
