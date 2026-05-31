# 夸父 (Kuafu)

> **夸父逐日，不息，自我超越。**
> 永不停止地追逐目标，每一次执行都是进化的一步。

夸父是一个自我进化的 AI Agent 框架。每次任务完成后，它自动反思、学习、优化自己的能力。

**夸父不是一个被动的工具，它是一个活的 agent。**

---

## 核心理念

- **进化 = 工作的自然产物**，不是额外操作
- **核心不可破坏** — `core/` 目录只读保护区，任何 agent 实例都不可修改
- **身份感知** — 知道自己是谁、用户是谁、边界在哪里
- **零依赖哲学** — 核心仅依赖 `pyyaml`，网络操作直接用 Python 标准库

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

## 架构

```
kuafu/
├── core/                          ← 只读保护区
│   ├── agent_loop.py              # Agent 执行循环 (ReAct)
│   ├── main.py                    # Agent 入口 — CLI + 编排
│   ├── llm.py                     # LLM 客户端 — 多后端支持
│   ├── evolution.py               # 进化引擎 — 即兴进化
│   ├── memory_api.py              # 记忆系统 — file/hindsight 双模
│   ├── safety.py                  # 安全体系 — 命令分级 + 路径保护
│   ├── identity.py                # 身份系统
│   ├── subagent.py                # 子 Agent 系统
│   ├── approval.py                # 审批系统
│   ├── session_store.py           # 会话存储 (SQLite)
│   ├── tool_registry.py           # 三级工具注册中心
│   ├── context_compress.py        # 上下文压缩
│   ├── prompt_template.py         # 结构化 Prompt 组装
│   ├── hooks.py                   # 事件钩子系统
│   ├── observer.py                # 运行时观察者
│   ├── cron_scheduler.py          # 定时任务调度
│   ├── gateway.py                 # HTTP Gateway
│   ├── cli.py                     # CLI 子命令
│   ├── feishu_bot.py              # 飞书 API 发消息
│   ├── mcp_bridge.py              # MCP 协议桥
│   ├── webhook_server.py          # WebHook 事件驱动
│   ├── budget_allocator.py        # Token 预算分配
│   ├── evolution_state.py         # 进化状态管理
│   ├── evolution_rules.py         # 进化规则引擎
│   ├── judge.py                   # 进化评判器
│   ├── skill_resolver.py          # 技能解析
│   ├── skill_manager.py           # 技能管理器
│   ├── model_manager.py           # 模型配置管理
│   ├── channel/                   # 消息通道层
│   │   ├── base.py                # 通道抽象基类
│   │   ├── manager.py             # 通道管理器
│   │   ├── gateway_loop.py        # 消息消费循环
│   │   ├── feishu_ws.py           # 飞书 WebSocket 直连
│   │   └── wechat_ilink.py        # 微信 iLink 通道
│   └── memory/                    # 记忆子系统
│       ├── memory_manager.py      # 记忆管理器
│       ├── hindsight_lite.py      # Hindsight 引擎
│       ├── sqlite_backend.py      # SQLite 后端
│       ├── episodic_buffer.py     # 情景缓冲
│       └── encoding_gate.py       # 编码门控
├── autonomous/                    ← 可选增强
│   ├── strategy_loader.py         # 策略加载器
│   ├── learner.py                 # 自主学习
│   ├── web_learner.py             # 网络学习
│   ├── self_health.py             # 健康检查
│   ├── reviewer.py                # 代码审查
│   ├── skill_extractor.py         # 技能提取
│   └── prioritizer.py             # 优先级排序
├── tests/                         # 测试
│   ├── test_all.py                # 核心测试
│   ├── test_comprehensive.py      # 21 项综合测试
│   ├── test_fix_beats.py          # 修复验证
│   └── test_evolution_pipeline.py # 进化管道测试
├── strategy/                      # 策略文件
├── skills/                        # 技能库
├── memory/                        # 运行时数据
├── mobile/                        # 移动端 Web 服务器
├── kuafu.sh                       # 启动入口
├── setup_wizard.py                # 配置向导
└── pyproject.toml                 # pip 安装配置
```

## 核心能力

### AI Agent 循环
- **ReAct 循环引擎** — LLM + 工具调用，最大 20 轮交互
- **上下文管理** — 自动压缩、预算分配、工具结果磁盘化
- **结构化 Prompt** — 身份/工具/进化/记忆/规则多段组装

### 工具系统
- 15+ 内置工具：`terminal`、`read_file`、`write_file`、`patch`、`search_files`、`web_search`、`finish`、`delegate_task` 等
- 三级架构：核心工具 / 紧凑工具（按需提升）/ 延迟发现工具

### 进化引擎
夸父使用 **D 方案（即兴进化）**：
- 每轮任务完成后，LLM 当场判断「值不值得学」
- 值得学的内容当场生成 `SKILL.md`
- 进化规则引擎基于 Hindsight 置信度自适应调整

### 消息通道
- **飞书** — WebSocket 直连，@夸父 即可交互，审批通知推送
- **微信** — 腾讯官方 iLink 协议，扫码登录
- **Gateway HTTP API** — REST 接口，支持 `/api/task` 等端点

### 子 Agent 系统
- 隔离上下文，同步执行
- 每个子 Agent 有独立对话 + 终端 + 工具集
- 最大 3 并发，不可递归

### 记忆系统
| 模式 | 后端 | 能力 |
|------|------|------|
| `file` | JSON 文件 | 关键词匹配，零依赖，离线可用 |
| `hindsight` | Hindsight Cloud API | 语义搜索、实体图谱、综合推理 |

### 定时任务
- 支持间隔调度（`30m`、`2h`）和 cron 表达式
- 任务结果投递到飞书/微信
- 支持脚本模式和无 agent 模式

## 开发

```bash
source venv/bin/activate

# 运行全部测试
python -m pytest tests/ -v

# 运行核心测试
python tests/test_all.py

# 运行综合测试（21 项）
python -m pytest tests/test_comprehensive.py -v
```

### 测试覆盖

```
21 项综合测试覆盖：
├── identity         — 身份声明加载
├── sandbox          — 路径 + 命令安全检查
├── memory_api       — 写入/检索/反思
├── evolution        — 进化引擎
├── llm              — 客户端初始化
├── agent_loop       — 核心循环 + 工具
├── subagent         — 子 Agent 委托
├── main             — Agent 初始化
├── session_store    — 会话存储
├── approval         — 审批系统
├── safety           — 安全体系
├── hooks            — 事件钩子
├── observer         — 运行时观察
├── whiteboard       — 白板模式
├── context_compress — 上下文压缩
├── cron_scheduler   — 定时调度
├── prompt_template  — Prompt 组装
├── gateway/channel  — 消息通道
├── autonomous       — 自主学习
└── 进化管道         — 端到端进化
```

## 状态

✅ **v0.4 — 完整功能版**

| 模块 | 状态 |
|------|------|
| Agent 循环 | ✅ ReAct + 结构化 Prompt |
| LLM 多后端 | ✅ DeepSeek / OpenAI / 本地 |
| 记忆系统 | ✅ file + hindsight 双模 |
| 进化引擎 | ✅ D 方案（即兴进化）|
| 安全体系 | ✅ 命令分级 + 路径保护 |
| 飞书通道 | ✅ WebSocket 直连 + @过滤 |
| 微信通道 | ✅ iLink 扫码登录 |
| Gateway API | ✅ HTTP REST 接口 |
| 子 Agent | ✅ 隔离上下文，同步执行 |
| 定时任务 | ✅ 间隔/cron/一次性 |
| MCP 协议 | ✅ stdio 子进程集成 |
| WebHook | ✅ HTTP 事件驱动 |
| 测试覆盖 | ✅ 47+ 测试全部通过 |

## License

Apache 2.0
