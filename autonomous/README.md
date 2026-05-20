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
