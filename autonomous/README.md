# autonomous/ — 主动进化模块

夸父从"被动应答"迈向"主动进化"的能力扩展。

## P0: 自我复盘（Self-Review）

- 后台线程（daemon），3600s 间隔
- 扫描 `memory/evolution_log.json` 增量事件
- 调用 LLM 生成结构化复盘总结 → 写入记忆
- 只读不写 `core/` 任何文件

### 复盘输出格式

每条复盘总结包含：
- 趋势: 整体进化方向
- 亮点: 有效学习
- 问题: 重复失败模式
- 建议: 可改进方向

### 文件结构

- `reviewer.py` — Reviewer 类 + ReviewerThread
- `__init__.py` — 包标识符
