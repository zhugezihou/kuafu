"""
夸父 (Kuafu) — 自我进化的 AI Agent。

项目结构：
    kuafu/
    ├── core/              ← 只读保护区
    │   ├── identity.py    # 身份系统
    │   ├── sandbox.py     # 沙盒安全
    │   ├── memory_api.py  # 记忆系统
    │   ├── evolution.py   # 进化引擎
    │   └── main.py        # Agent 入口
    ├── strategy/          ← 可进化区
    │   ├── prompts.yaml   # 提示模板
    │   ├── task_strategies.yaml
    │   └── quality.yaml
    ├── skills/            ← 可进化区（L3 技能自动写入）
    ├── memory/            ← 记忆数据
    ├── CORE_CHARTER.md    # 核心宪章（不可违）
    ├── IDENTITY.md        # 身份声明（不可改）
    └── README.md

快速开始：
    python -m kuafu --status
    python -m kuafu "写一个 Python 函数计算 Fibonacci"
"""
