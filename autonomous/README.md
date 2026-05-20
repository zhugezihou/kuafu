# autonomous/ — 主动进化模块

夸父从"被动应答"迈向"主动进化"的能力扩展。

## P0: 自我复盘（Self-Review）

- 后台线程（daemon），3600s 间隔
- 扫描 `memory/evolution_log.json` 增量事件
- 调用 LLM 生成结构化复盘总结 → 写入记忆
- 只读不写 `core/` 任何文件

### 文件结构

- `reviewer.py` — Reviewer 类 + ReviewerThread
- `__init__.py` — 包标识符

## P1: 主动学习（Active Learning）

- 每轮任务完成后自动检测 5 种学习信号
- 通过 `Learner.detect()` 无缝嵌入 `agent_loop.py` 的 `run()` 方法
- 学习信号自动写入记忆，带 `["learning", "signal", <type>, <priority>]` 标签

### 5 种学习信号

| 信号 | 级别 | 触发条件 | 响应 |
|------|------|----------|------|
| 用户纠正 (user_correction) | A | 用户输入包含纠正/指导 | 写入记忆 + log 通知 |
| 重复失败 (repeat_failure) | A | 同类型任务连续失败 ≥2 次 | 写入记忆 + log 通知 |
| 未知错误 (unknown_error) | B | 遇到不在已知错误库的错误 | 写入记忆，更新已知错误库 |
| 知识缺口 (knowledge_gap) | B | 工具频繁出错/重试 | 写入记忆 |
| 新模式 (new_pattern) | S | 成功完成复杂任务，发现新模式 | 自动记录，不打扰 |

### 相关文件

- `learner.py` — Learner 类：信号检测主逻辑
- `__init__.py` — 包标识符，导出 Learner

## P2: 自主决策（Prioritizer）

夸父在空闲状态下能自主决策「下一步最有价值的事情」。

### 三大能力

1. **IdlePrioritizer（空闲决策）** — 没有待处理任务时，根据当前状态选择最优主动行动
   - 决策信号：学习信号（P1）、进化事件（L2+）、维护需求、知识盲区
   - 评分 0-100，综合考量 信号强度/类别权重/成本
   - 最低决策间隔 5 分钟，防止频繁空转
   - 每次决策写入记忆 + 日志（可审计回溯）

2. **TaskPrioritizer（任务优先级）** — 多任务排队时动态排序
   - 考虑因素：任务类型权重 + 等待时间 + 历史相似任务
   - 用户任务 > 学习信号 > 进化 > 维护

3. **EvolutionScheduler（进化时机决策）** — L2+ 进化事件不立即执行，调度到空闲时
   - L0/L1：仍然立即执行（轻量不影响体验）
   - L2+：进入调度队列，由 prioritizer 在空闲时选择执行时机
   - 同一级别 2 分钟内不重复执行

### 集成方式

- `prioritizer.py` — 三个核心类（纯 autonomous/ 模块，不依赖 core/）
- `main.py` — 在 KuafuAgent.init() 中启动 PrioritizerThread（daemon）
- 每 5 分钟检查一次空闲状态
- 如果 `__init__.py` 导入失败，自动降级跳过（不影响核心功能）

### 决策记录

所有决策持久化到 `memory/priority_log.json`（最近 100 条），可通过 `get_status()` 查询。
