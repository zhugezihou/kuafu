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

## 架构

```
kuafu/
├── core/                 ← 只读保护区
│   ├── identity.py       # 身份系统 — 我是谁
│   ├── sandbox.py        # 沙盒安全 — 能做什么/不能做什么
│   ├── memory_api.py     # 记忆系统 — 记得什么
│   ├── evolution.py      # 进化引擎 — 五级进化（L1-L5）
│   └── main.py           # Agent 入口 — 系统 prompt + 反思循环
├── strategy/             ← 可进化区
│   ├── prompts.yaml      # 任务提示模板
│   ├── task_strategies.yaml
│   └── quality.yaml
├── skills/               ← 可进化区（可复用技能包）
├── memory/               ← 记忆数据
└── tests/
    └── test_all.py       # 7 个核心测试
```

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

## 快速开始

```bash
# 克隆
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu

# 安装
pip install -r requirements.txt

# 跑测试
python tests/test_all.py

# 使用
python -c "
from kuafu import KuafuAgent
agent = KuafuAgent()
result = agent.run('你好，夸父')
print(result)
"
```

## 状态

🚧 **V0.1 — 核心框架已完成**

- [x] 身份系统（`core/identity.py`）
- [x] 沙盒安全（`core/sandbox.py`）
- [x] 记忆系统（`core/memory_api.py`）
- [x] 进化引擎 L1/L2（`core/evolution.py`）
- [x] Agent 入口 + 反思循环（`core/main.py`）
- [x] 7/7 测试通过
- [ ] V2：自动技能提取（L3）
- [ ] V3：prompt 进化（L4）
- [ ] V4：元学习（L5）

## 项目来源

夸父原本是 Hermes Agent 的一个扩展项目，后来独立发展为一个通用 self-improving agent 框架。项目最初受 Memento-Skills、EvoSkill、Reflexion 等研究成果启发。

## License

Apache 2.0
