# 夸父策略层

> 此目录下的文件**可以被进化引擎修改**。
> core/ 永远不会改这里。
> evolution.py 负责更新这些策略。

---

## 文件说明

| 文件 | 用途 | 可被进化 |
|------|------|---------|
| `prompts.yaml` | 任务提示模板 | ✅ L2 |
| `task_strategies.yaml` | 各类任务的默认策略 | ✅ L2 |
| `quality.yaml` | 质量标准 | ✅ L1 |

## 进化规则

- evolution.py 调用此目录下的文件时使用 `sandbox.register_allowed_dir()`
- 每次进化前自动备份为 `*.bak`
