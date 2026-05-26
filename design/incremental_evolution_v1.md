# 夸父增量式进化引擎 v1 — 设计文档

> 阶段：设计草案供 review
> 日期：2026-05-26
> 状态：Draft

---

## 1. 现状分析

### 当前架构（D 方案「即兴进化」）

夸父目前有两条并行的进化路径，每次任务完成后先后执行：

```
任务完成 → [1] P1 Learner 信号检测 (autonomous/learner.py) → [2] EvolutionEngine (core/evolution.py)
```

### 现存问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | **LLM 调用冗余**：Learner + EvolutionEngine 各自独立调 LLM，每次任务结束最多 4 次 LLM 调用（判断+提取），本地 Qwen3.5-9B 带宽被挤占 | 响应变慢，8GB VRAM 本地模型推理排队 |
| 2 | **Observer 模式缺失**：两次 LLM 调用都是在任务结束后用 task_result 做后验分析，任务执行过程中收集的信号（工具报错、重试次数、实际 tool chain）没有被利用 | 丢了一半信息 |
| 3 | **`quality.yaml` 残留旧教训**：规则中包含 `参考历史教训: 「generic」连续 3 次失败` 这种旧版本硬编码，没有自动清理机制 | 静态配置退化 |
| 4 | **Learner 与 EvolutionEngine 功能重叠**：两者都做“检测-提取-写入”的事，只是粒度和优先级不同 | 维护双份代码，逻辑不一致 |

### 原始 D 方案的正确设计目标

2026-05-24 的 D 方案文档说：
> 「废除 L0-L5 分级体系，改为每次任务完成后让 LLM 当场判断是否值得学」

这个目标本身是对的。问题出在实现层面——**Learner 和 EvolutionEngine 被独立开发，没有统一为一条管道**。

### 设计目标

1. **合并两条路径为一条管道**：Observer → Judge → Writer
2. **LLM 调用 ≤ 1 次/任务**：90% 的任务不应触发 LLM 进化调用
3. **零成本 Observer**：运行时信号收集不用 LLM，纯规则
4. **真正增量**：维护进化状态，只处理新的信号
5. **兼容现有沙盒**：只写 `skills/`、`strategy/`、`memory/`、`logs/`

---

## 2. 架构设计

### 2.1 整体架构

```
任务执行中 ─────────────────────→ 任务完成
     │                                    │
     ▼                                    ▼
┌─────────────┐                  ┌──────────────┐
│ Observer    │                  │ Observer     │
│ (运行时)    │                  │ (后验)       │
└──────┬──────┘                  └──────┬───────┘
       │                                 │
       └──────────┬──────────────────────┘
                  │ Observation 对象
                  ▼
         ┌──────────────────┐
         │ 有足够信号?       │──── 无信号 ──→ 安静跳过
         │ (纯规则判断)      │
         └──────┬───────────┘
                │ 有信号
                ▼
         ┌──────────────────┐
         │ EvolutionJudge   │ ← 1 次 LLM 调用
         │ (判断+提取)      │
         └──────┬───────────┘
                │ Skill 对象
                ▼
         ┌──────────────────┐
         │ SkillWriter      │ ← 0 LLM 写入 yaml
         │ (去重+写文件)    │
         └──────────────────┘
```

### 2.2 关键设计决策

**决策 1：运行中 Observer + 后验 Observer → 合并为一个 Observation**

运行时 Observer 实时监听 tool call 结果；后验 Observer 在任务结束后收集 task_result。两者合并为一个 Observation 对象。Observer 合并了原来 Learner 的全部信号检测（零 LLM 调用），原来 Learner 调 LLM 做判断的部分移入 Judge。

**决策 2：Judge 合并原来的「判断+提取」两步为一步**

原来的 Learner 在检测到 user_correction/new_pattern 时会分别调 LLM（最多 2 次），然后 EvolutionEngine 又调 LLM 判断+提取（2 次）。新设计将这些信号**打包为一个 Observation**，Judge 一次性处理，输出可以是 skill 或 null。

**决策 3：增量状态文件**

维护 `.evolution_state.json` 记录已处理的最后任务索引和已有 skill 列表，避免重复提取。

---

## 3. 组件详细设计

### 3.1 Observer — `core/observer.py`（新建）

**零 LLM 成本**，纯规则收集信号。

```python
@dataclass
class Observation:
    """Observer 收集的所有运行时和后验信号。"""
    # 运行时信号（agent_loop 执行过程中实时收集）
    tool_errors: list[ToolError]         # 工具调用失败（含重试次数）
    tool_chains: list[list[str]]         # 工具链模式
    user_input_raw: str                  # 原始用户输入
    
    # 后验信号（任务结束后从 task_result 收集）
    task_type: str
    success: bool
    tool_call_count: int
    errors: list[str]
    duration: float
    
    # 增量状态
    is_novel_task: bool                  # 首次遇到的 task_type
    is_repeated_failure: bool            # 同类任务连续失败 ≥ 2 次
    is_user_correction: bool             # 用户输入含纠正信号
    has_unknown_error: bool              # 错误不在已知错误库


class Observer:
    """运行时观察者 — 在 agent_loop 执行过程中收集信号。"""
    
    def on_tool_call(self, tool_name: str, args: dict, result: dict):
        """每个 tool call 完成后调用（零成本）。"""
    
    def on_task_complete(self, task_result: dict, user_input: str) -> Observation:
        """任务完成后整合所有信号 → Observation。"""
    
    @staticmethod
    def _detect_user_correction(text: str) -> bool:
        """纯规则检测纠正信号（不用 LLM）。"""
        return any(kw in text for kw in ["不要", "别用", "换成", "注意", "记住", ...])
```

### 3.2 State — `core/evolution_state.py`（新建）

增量状态文件管理，删除旧的 `strategy/quality.yaml` 硬编码教训。

```python
class EvolutionState:
    """增量进化状态管理。"""
    
    def __init__(self):
        self.state_file = ROOT_DIR / "memory" / ".evolution_state.json"
        self._state = self._load()
    
    @property
    def known_skills(self) -> list[str]:
        """已有 skill 列表（按 name+task_type 去重 key）。"""
        return list(self._state.get("skills_index", {}).keys())
    
    def is_skill_known(self, name: str, task_type: str) -> bool:
        key = f"{task_type}:{name}"
        return key in self._state.get("skills_index", {})
    
    def record_skill(self, name: str, task_type: str, filepath: str):
        """记录一个新技能。"""
        key = f"{task_type}:{name}"
        self._state.setdefault("skills_index", {})[key] = {
            "created_at": time.time(),
            "filepath": filepath,
            "usage_count": 0,
        }
        self._save()
    
    def should_judge(self, observation: Observation) -> bool:
        """纯规则判断是否值得调 LLM。
        
        返回 True 的条件（或关系）：
        - 有 tool error → True
        - 用户纠正信号 → True
        - 首次遇到的 task_type → True
        - 连续失败 ≥ 2 次 → True
        - tool_calls ≥ 5 → True
        - 以上都不满足 → False（安静跳过）
        """
```

### 3.3 Judge — `core/judge.py`（新建）

**1 次 LLM 调用**，判断有没有价值 + 直接提取。

```python
class EvolutionJudge:
    """一次 LLM 调用完成判断+提取。"""
    
    def __init__(self, llm, memory, state: EvolutionState):
        self._llm = llm
        self._memory = memory
        self._state = state
    
    def evaluate(self, obs: Observation) -> Optional[SkillResult]:
        """一次 LLM 调用。
        
        Prompt 结构（紧凑，针对本地模型优化）：
        
        [System]
        你是一个进化判断器。根据观察信号，判断是否有值得保存的经验。
        
        [Observations]
        - 任务类型: {obs.task_type}
        - 成功: {obs.success}
        - 工具调用: {obs.tool_call_count}次
        - 错误: {obs.errors}
        - 用户纠正: {obs.is_user_correction}
        - 首次任务: {obs.is_novel_task}
        
        [已有技能]
        - {已有技能 key 列表，不传完整内容}
        
        [判断标准]
        1. 有具体的技术/工具操作 → 值得学
        2. 用户纠正 → 值得学
        3. 遇到新错误 → 值得学
        4. 5次以上工具调用 → 值得学
        5. 纯闲聊/问候 → 不值得学
        
        [输出格式]
        {"has_value": true/false, "name": "...", "description": "...", "steps": [...], "pitfalls": [...]}
        或 null
        """
        
    def _maybe_record_skill(self, result: Optional[SkillResult]) -> bool:
        """如果有 skill 输出，写入文件。"""
```

### 3.4 SkillWriter — 改造 `core/evolution.py`

将现有的 `_write_skill_yaml()` 换掉（放弃暴力 frontmatter 解析，用 pyyaml）。改为：

```python
class SkillWriter:
    """将 LLM 输出的 skill 结构化写入文件系统。"""
    
    SKILLS_DIR = ROOT_DIR / "skills"
    
    def write(self, skill: SkillResult) -> str:
        safe_name = self._sanitize_name(skill.name)
        filepath = self.SKILLS_DIR / f"{safe_name}.yaml"
        
        # 如果同名文件已存在 → usage_count++，不覆盖
        if filepath.exists():
            existing = yaml.safe_load(filepath.read_text())
            existing["usage_count"] = existing.get("usage_count", 0) + 1
            filepath.write_text(yaml.dump(existing, allow_unicode=True))
            return str(filepath)
        
        # 新文件
        data = {
            "name": skill.name,
            "description": skill.description,
            "task_type": skill.task_type,
            "keywords": skill.keywords,
            "steps": skill.steps,
            "pitfalls": skill.pitfalls,
            "usage_count": 0,
            "created_at": int(time.time()),
            "source": "kuafu_incremental_evolution",
        }
        filepath.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
        return str(filepath)
```

### 3.5 调用点改造 — `core/agent_loop.py`

**当前代码**（两条路径并行）：

```python
# Line 587-605 (普通任务)
# [1] Learner.detect() → 可能调 LLM 0-2 次
learning_signals = self._learner.detect(task_result, task, messages)

# [2] EvolutionEngine.evaluate_and_evolve() → LLM 2 次
evolution_event = self.evolution.evaluate_and_evolve(task_result)
```

**改为**：

```python
# Observer + Judge → 1 条管道
observation = self._observer.on_task_complete(task_result, task)

# 只有 Observer 判断有信号时才调 LLM
if self._evo_state.should_judge(observation):
    result = self._judge.evaluate(observation)  # 1 次 LLM
    if result:
        filepath = self._writer.write(result)
        # 写 memory
        self.memory.remember(key=..., content=..., tags=...)
```

---

## 4. 文件变更清单

| 文件 | 操作 | 变更说明 |
|------|------|---------|
| `core/observer.py` | **新建** | 运行时+后验观察者 |
| `core/evolution_state.py` | **新建** | 增量进化状态管理 |
| `core/judge.py` | **新建** | 单次 LLM 调用判断+提取 |
| `core/evolution.py` | **重写** | 精简为 SkillWriter |
| `core/agent_loop.py` | **修改** | 替换两条调用链为一条 |
| `autonomous/learner.py` | **标记 deprecated** | 不删，加注释说明由 Observer 替代 |
| `strategy/quality.yaml` | **清理** | 删除旧版本硬编码教训 |

---

## 5. 进化（LLM 调用）阈值矩阵

Observer 收集完信号后，`EvolutionState.should_judge()` 的判定逻辑：

| 条件 | 判定 | 说明 |
|------|------|------|
| 没有工具调用 + 任务成功 | ❌ 不进化 | 纯问答，无可学 |
| 有工具调用且成功 | ✅ 进化 | 可提取工作流 |
| 有工具调用且失败 | ✅ 进化 | 可提取错误教训 |
| 用户纠正信号 | ✅ 进化 | 最高优先级 |
| 首次 task_type | ✅ 进化 | 新领域探索 |
| 连续失败 ≥ 2 次 | ✅ 进化 | 需要修复 |
| 纯问候/闲聊 | ❌ 不进化 | 无技术内容 |

预估效果：
- **日常简单任务**：约 90% 被判不进化，0 次 LLM 调用
- **轻度任务**（简单代码/文件操作）：约 50% 进化，1 次 LLM 调用
- **复杂任务**（多工具协作/报错）：约 100% 进化，1 次 LLM 调用
- **平均 LLM 调用的降**：从当前 0.8-2.0 次/任务 → 0.3-0.5 次/任务

---

## 6. 兼容性与迁移

| 项 | 说明 |
|----|------|
| 现有 skills/*.yaml | 不删不改，正常使用 |
| 现有 evolution_log.json | 保留，读取接口不改 |
| 现有 memory 记录 | 保留 |
| Learner 运行时 | 如果 Observer 没读到信号，fallback 到 Learner（兼容期） |
| 配置项 | 无需新增 config |
| 环境变量 | 不变 |

---

## 7. 未解决的问题（留待 review）

1. **Observer 的 tool_chain 检测**：需要 AgentLoop 在每次 tool call 时抛一个事件给 Observer。当前 agent_loop.py 的 tool_execute 方法里是在 `_execute_tool()` 内完成的，需要找一个钩子点不污染 core/ 安全逻辑。—— **建议方案**：在 `agent_loop.py` 的 `_handle_tool_call()` 末尾加一行 `self._observer.on_tool_call(...)`。

2. **Learner 的 deprecated 策略**：直接删怕有地方引用，标记 deprecated 后提供多长时间的双轨并行期？—— **建议**：标记 deprecated 后保留 2 周（下一个版本迭代时删除），代码中加 `warnings.warn("deprecated")`。

3. **增量状态文件的 TTL**：.evolution_state.json 理论上可以无限增长，是否需要限制大小？—— **建议**：上限 500 条已知 skill，超过时删除最旧/使用最少的。
