# 夸父开发者手册

> 面向贡献者和内部开发者的技术文档。

---

## 目录

1. [环境搭建](#环境搭建)
2. [项目结构](#项目结构)
3. [核心架构](#核心架构)
4. [测试体系](#测试体系)
5. [编码规范](#编码规范)
6. [CI/CD](#cicd)
7. [贡献流程](#贡献流程)

---

## 环境搭建

### 要求

- Python 3.10+
- Git
- (可选) 本地 LLM：llama-server (port 8080) 用于上下文压缩
- (可选) 飞书 / 微信 Bot Token（用于通道测试）

### 安装

```bash
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu
python3 -m venv venv
source venv/bin/activate

# 开发模式安装
pip install -e .
pip install pytest pytest-cov  # 测试依赖

# 运行测试验证
python -m pytest tests/ -x --tb=short -q
```

---

## 项目结构

```
kuafu/
├── core/                    # ◄ 核心模块（只读保护区）
│   ├── __init__.py
│   ├── agent_loop.py        # Agent 执行循环
│   ├── approval.py          # 三层审批系统
│   ├── budget_allocator.py  # Token 预算
│   ├── channel/             # 消息通道
│   ├── cli.py               # CLI 入口
│   ├── context_compress.py  # 上下文压缩
│   ├── cron_scheduler.py    # 定时任务
│   ├── downloader.py        # 网络下载
│   ├── evolution.py         # 进化引擎
│   ├── evolution_rules.py   # 进化规则（Hindsight 驱动）
│   ├── evolution_state.py   # 进化状态管理
│   ├── evolution_tracker.py # SQLite 进化追踪
│   ├── evolution_viz.py     # 进化可视化
│   ├── executor.py          # 白板执行器
│   ├── gateway.py           # HTTP Gateway
│   ├── gepa_engine.py       # GEPA 适应度评估
│   ├── hooks.py             # 事件钩子
│   ├── identity.py          # 身份系统
│   ├── judge.py             # 进化评判器
│   ├── llm.py               # LLM 客户端
│   ├── main.py              # Agent 入口
│   ├── mcp_bridge.py        # MCP 协议桥
│   ├── memory/              # 记忆子系统
│   ├── memory_api.py        # 记忆 API
│   ├── model_manager.py     # 模型管理
│   ├── observer.py          # 运行时观察
│   ├── prompt_template.py   # Prompt 组装
│   ├── safety.py            # 安全体系
│   ├── session_store.py     # 会话存储
│   ├── skill_deps.py        # 技能依赖
│   ├── skill_manager.py     # 技能管理
│   ├── skill_publisher.py   # 技能发布
│   ├── skill_repo.py        # 技能仓库
│   ├── skill_resolver.py    # 技能解析
│   ├── subagent.py          # 子 Agent
│   ├── tool_registry.py     # 工具注册
│   ├── webhook_server.py    # WebHook
│   └── whiteboard/          # 白板模式
├── tests/                    # 测试
│   ├── test_bulk.py         # 综合 bulk 测试（~22500 行，1860+ 用例）
│   ├── test_all.py          # 14 项端到端测试
│   ├── test_comprehensive.py # 21 项综合测试
│   ├── test_evolution_pipeline.py # 进化管道
│   ├── test_tool_integration.py    # 工具集成
│   └── test_*.py            # 各模块独立测试（~25 个文件）
├── strategy/                 # 策略文件
├── skills/                   # 技能库
├── memory/                   # 运行时数据
├── .github/workflows/ci.yml  # GitHub Actions CI
├── kuafu.sh                  # 启动入口
├── setup_wizard.py           # 配置向导
└── pyproject.toml            # pip 安装配置
```

---

## 核心架构

### Agent 执行循环

夸父的核心是 `agent_loop.py` 中的 `AgentLoop` 类。每次任务执行流程：

```
用户输入
  → 构建 System Prompt（身份 + 工具 + 规则 + 记忆 + 进化状态）
  → ReAct 循环（最多 N 轮）:
      → LLM 生成响应（可能含工具调用）
      → 执行工具（terminal / read_file / web_search / ...）
      → 上下文检查 / 预算紧缩 / 工具结果磁盘化
      → 记录工具结果
  → 后处理:
      → 进化评估（值不值得学？）
      → 进化规则分析（成功/失败模式）
      → 自检质量评分
      → 生成报告
  → 返回结果
```

### 工具注册中心

`tool_registry.py` 实现三级工具架构：

1. **核心工具** — 始终以全量 schema 暴露给 LLM（terminal, finish, tool_search）
2. **紧凑工具** — 仅名称+描述在提示词中，首次调用后自动注入 schema（read_file, write_file, patch, search_files）
3. **延迟工具** — 对 LLM 隐藏，通过 ToolSearch 元工具发现后注入（web_search, github, browser, image_gen 等）

### 审批系统

三层安全架构 (`approval.py`):

```
工具调用 → Layer1: Deny 规则（硬拒绝）→ Layer2: Auto 分类器 → Layer3: 人工审批
                ↓                      ↓                       ↓
          直接拒绝                 自动通过/拒绝           终端提示 / 通道通知
```

### 进化引擎

D 方案（即兴进化）流程：

1. 任务完成后 `evaluate_and_evolve(result)` 被调用
2. Judge 评估结果（无 LLM：静态分析错误/成功/工具调用数）
3. Observer 记录观察数据
4. `run_pipeline()` 执行完整进化管道：
   - GEPA 适应度评估（6 维打分）
   - 技能提取（Judge 决定是否值得学）
   - 规则分析（Hindsight 置信度）
   - 退化检测 + 自动回滚

### 上下文压缩

四级管线 (`context_compress.py`):

```
P0: BudgetReduction — 就地裁剪超大工具结果（零 token 成本）
P1: ToolResultStore — 大结果写入磁盘，上下文只留路径
P2: Pin 保护 — 关键消息（system、最近提问、决策）不压缩
P3: LLM 摘要 — 旧轮次用本地模型生成摘要
```

---

## 测试体系

### 测试架构

夸父的测试分三层：

| 层级 | 位置 | 特点 | 数量 |
|:----|:-----|:-----|:----:|
| **单元测试** | `tests/test_*.py` | mock 隔离，覆盖每行代码 | 30+ 文件，~1800+ 用例 |
| **集成测试** | `test_all.py`, `test_comprehensive.py` | 真实对象，不依赖外部服务 | 35 项 |
| **端到端** | `test_evolution_pipeline.py`, `test_tool_integration.py` | 多模块配合 | 17 项 |

### 运行测试

```bash
# 全部测试
python -m pytest tests/ -x --tb=short

# 特定模块
python -m pytest tests/test_approval_denyrules.py -x --tb=short -v

# 集成测试
python tests/test_all.py

# 带覆盖率
coverage run --source=core -m pytest tests/test_xxx.py --tb=short -q
coverage report -m --include="core/xxx.py"
```

### 覆盖率目标

所有 `core/` 下**非外部依赖的模块**必须保持 100% 覆盖率：
- 纯逻辑模块（approval, safety, evolution, context_compress 等）→ 100%
- 外部依赖 handler（terminal, web_search, browser 等）→ `# pragma: no cover`

### 测试规范

- 使用 `pytest`，4 空格缩进
- 测试文件命名：`test_<module_name>.py`
- 新模块必须附带完整测试
- 使用 `tmp_path`/`monkeypatch` 隔离文件操作
- 使用 `unittest.mock` mock 外部 API/网络
- 不修改源文件来实现测试覆盖（允许加 `# pragma: no cover`）

---

## 编码规范

### Python 风格

- Python 3.10+ 类型注解（使用 `from __future__ import annotations`）
- 4 空格缩进
- 中文注释（面向中文开发者）
- 优先标准库，零新增依赖
- 日志使用 `logging.getLogger(__name__)`

### 代码组织

```
# 模块文档字符串（中文）
# 导入（标准库 → 第三方 → 本地）
# 常量 & 配置
# 工具函数
# 主类定义
# Schema 定义（工具注册用）
# Handler 实现
```

### 提交规范

- 提交消息中文/英文均可
- 关联 issue 号（如果有）
- 不破坏已有测试

---

## CI/CD

GitHub Actions CI 配置在 `.github/workflows/ci.yml`。

### Pipeline

```
push/PR → 3 × Python版本(3.10/3.11/3.12)
  → unit-tests（并行覆盖率收集）
  → integration-tests（端到端测试）
  → coverage-report（汇总报告）
```

### 本地模拟

```bash
pip install pytest pytest-cov pyyaml

# 单元测试
python -m pytest tests/test_*.py -x --tb=short

# 集成测试
KUAFFU_API_KEY=test-dummy-key python -m pytest \
  tests/test_all.py \
  tests/test_comprehensive.py \
  tests/test_evolution_pipeline.py \
  tests/test_tool_integration.py \
  --tb=short
```

---

## 贡献流程

1. Fork 仓库
2. 创建特性分支：`git checkout -b feat/xxx`
3. 修改代码 + 写/更新测试
4. 确保全部测试通过：`python -m pytest tests/ -x --tb=short`
5. 确保覆盖率不降低：`coverage run --source=core ... && coverage report`
6. 提交 PR

### 代码审查重点

- 是否引入新依赖？→ 尽量标准库解决
- 是否修改了 `core/` 下模块？→ 只读保护区，需额外注意
- 测试覆盖率是否保持 100%？
- 审批/安全逻辑是否有变更？
