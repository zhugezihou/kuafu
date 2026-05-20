"""P2 — 自主决策 Prioritizer。

让夸父能根据当前状态自主决策「下一步最有价值的事情」。

三大能力：
1. IdlePrioritizer（空闲决策）— 没有待处理任务时，自主决定该做什么
2. TaskPrioritizer（任务优先级）— 多任务排队时动态排序
3. EvolutionScheduler（进化时机决策）— 进化事件不立即执行，由调度器决定最佳时机

核心设计原则：
- 所有决策都基于「当前状态 + 历史模式」，不做盲目行动
- 优先级分数 0-100，综合考虑 紧急度/价值/依赖/风险
- 每次决策结果写入记忆（可审计、可回溯）
"""

import json
import time
import hashlib
from pathlib import Path
from typing import Any, Optional, Callable
from dataclasses import dataclass, field, asdict

ROOT_DIR = Path(__file__).resolve().parent.parent
PRIORITY_LOG = ROOT_DIR / "memory" / "priority_log.json"


# ──────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────

@dataclass
class ActionItem:
    """一个可执行的行动项。"""
    title: str                       # 简短标题
    description: str                 # 具体做什么
    id: str = ""                     # 唯一标识（空则自动生成）
    priority_score: float = 50.0     # 0-100，越高越优先
    category: str = "generic"        # idle / task / evolution / maintenance
    source: str = ""                 # 触发源（"learner:repeat_failure" 等）
    estimated_cost: str = "medium"   # low / medium / high（预计耗时）
    dependencies: list[str] = field(default_factory=list)  # 前置行动 ID
    context: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    decided_at: float = 0.0
    executed: bool = False

    def __post_init__(self):
        if not self.id:
            raw = f"{self.title}|{self.source}|{time.time()}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass
class DecisionRecord:
    """一次自主决策的记录。"""
    timestamp: float = field(default_factory=time.time)
    trigger: str = ""                # 什么触发了这次决策
    context_snapshot: dict = field(default_factory=dict)  # 决策时的状态快照
    candidates: list[dict] = field(default_factory=list)   # 候选行动列表（评分前）
    chosen: Optional[dict] = None    # 最终选择的行动
    reasoning: str = ""              # 决策理由


# ──────────────────────────────────────────
# 空闲决策器
# ──────────────────────────────────────────

class IdlePrioritizer:
    """空闲状态下的自主决策。

    当夸父没有待处理任务时，根据当前状态决定最有价值的主动行动。
    决策信号来源：
    1. P0 Reviewer 的复盘结果（若已有未处理复盘）
    2. P1 Learner 的学习信号（未处理的高优先级信号）
    3. Evolution Engine 的进化事件（未执行的 L2/L3）
    4. 系统的周期性维护需求（日志清理、记忆压缩）
    5. 知识盲区探测（发现频繁失败的任务类型）
    """

    def __init__(
        self,
        memory_recall_fn: Optional[Callable] = None,
        learner_status_fn: Optional[Callable] = None,
        evolution_stats_fn: Optional[Callable] = None,
    ):
        self._memory_recall = memory_recall_fn
        self._learner_status = learner_status_fn
        self._evolution_stats = evolution_stats_fn
        self._last_decision: Optional[DecisionRecord] = None
        self._min_idle_interval = 300  # 两次空闲决策至少间隔 5 分钟

    def decide(self, force: bool = False) -> Optional[ActionItem]:
        """根据当前状态决定最有价值的主动行动。

        Args:
            force: 强制决策（忽略间隔限制）

        Returns:
            选中的行动项，或 None（无合适行动）
        """
        # 频率控制
        now = time.time()
        if not force and self._last_decision:
            elapsed = now - self._last_decision.timestamp
            if elapsed < self._min_idle_interval:
                return None

        candidates = self._collect_candidates()
        if not candidates:
            return None

        # 评分 -> 排序 -> 选择
        for c in candidates:
            c.priority_score = self._score(c)
        candidates.sort(key=lambda x: x.priority_score, reverse=True)

        chosen = candidates[0]
        chosen.decided_at = now

        # 记录决策
        self._last_decision = DecisionRecord(
            trigger="idle_check",
            context_snapshot=self._snapshot_state(),
            candidates=[asdict(c) for c in candidates],
            chosen=asdict(chosen),
            reasoning=self._build_reasoning(chosen, candidates),
        )
        self._log_decision(self._last_decision)

        return chosen

    def _collect_candidates(self) -> list[ActionItem]:
        """收集当前所有候选行动。"""
        candidates: list[ActionItem] = []

        # 1. 学习信号 -> 行动
        if self._learner_status:
            try:
                signals = self._learner_status()
                if signals:
                    for sig in signals:
                        if sig.get("priority") in ("A", "B") and not sig.get("handled"):
                            candidates.append(ActionItem(
                                title=f"处理学习信号: {sig.get('type', 'unknown')}",
                                description=sig.get("summary", sig.get("type", "")),
                                category="learning",
                                source=f"learner:{sig.get('type')}",
                                estimated_cost="low",
                                context=sig,
                            ))
            except Exception:
                pass

        # 2. 进化事件 -> 行动
        if self._evolution_stats:
            try:
                stats = self._evolution_stats()
                if stats and stats.get("total_evolutions", 0) > 0:
                    latest = stats.get("latest", {})
                    if latest and latest.get("level", 0) >= 2:
                        candidates.append(ActionItem(
                            title=f"跟进进化事件 L{latest['level']}",
                            description=latest.get("action", ""),
                            category="evolution",
                            source=f"evolution:L{latest['level']}",
                            estimated_cost="medium" if latest.get("level") <= 3 else "high",
                            context=latest,
                        ))
            except Exception:
                pass

        # 3. 内存健康检查（每 24 小时一次）
        candidates.append(ActionItem(
            title="内存健康检查",
            description="检查记忆系统状态、清理过期缓存、统计记忆使用量",
            category="maintenance",
            source="system:periodic",
            estimated_cost="low",
        ))

        # 4. 知识盲区探测
        candidates.append(ActionItem(
            title="知识盲区探测",
            description="分析最近失败任务，识别知识缺口，生成学习计划",
            category="learning",
            source="system:knowledge_gap",
            estimated_cost="medium",
        ))

        return candidates

    def _score(self, item: ActionItem) -> float:
        """对候选行动进行评分（0-100）。"""
        score = 50.0  # 基础分

        # 类别权重
        category_weights = {
            "learning": 70,      # 学习信号最优先
            "evolution": 65,     # 进化事件次之
            "maintenance": 30,   # 维护任务较低
            "task": 80,          # 用户任务最高
            "generic": 40,
        }
        score += category_weights.get(item.category, 40)

        # 成本调整
        cost_penalty = {"low": 0, "medium": -10, "high": -20}
        score += cost_penalty.get(item.estimated_cost, -10)

        # 来源信号强度
        if "learner:A" in item.source or "learner:user_correction" in item.source:
            score += 20  # 用户纠正信号最高权重
        elif "learner:repeat_failure" in item.source:
            score += 15
        elif "learner:B" in item.source:
            score += 10

        # 有依赖的降低优先级（需要先完成前置任务）
        if item.dependencies:
            score -= 10 * len(item.dependencies)

        return max(0, min(100, score))

    def _snapshot_state(self) -> dict:
        """拍一张当前状态快照。"""
        state = {
            "timestamp": time.time(),
        }
        if self._evolution_stats:
            try:
                state["evolution"] = self._evolution_stats()
            except Exception:
                pass
        if self._memory_recall:
            try:
                recent = self._memory_recall("", limit=5)
                state["recent_memories"] = len(recent) if recent else 0
            except Exception:
                pass
        return state

    @staticmethod
    def _build_reasoning(chosen: ActionItem, all_items: list[ActionItem]) -> str:
        """构建决策理由。"""
        top3 = [f"{c.title} ({c.priority_score:.0f}分)" for c in all_items[:3]]
        return (
            f"从 {len(all_items)} 个候选中选择「{chosen.title}」"
            f"（{chosen.priority_score:.0f}分）。"
            f"候选排名: {' > '.join(top3)}"
        )

    @staticmethod
    def _log_decision(decision: DecisionRecord):
        """持久化决策日志。"""
        try:
            path = PRIORITY_LOG
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                logs = json.loads(path.read_text(encoding="utf-8"))
            else:
                logs = []
            logs.append(asdict(decision))
            # 只保留最近 100 条
            if len(logs) > 100:
                logs = logs[-100:]
            path.write_text(
                json.dumps(logs, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


# ──────────────────────────────────────────
# 任务优先级排序器
# ──────────────────────────────────────────

class TaskPrioritizer:
    """多任务排队时的动态优先级排序。

    当用户连续下发多个任务时，自动计算每个任务的优先级分数。
    也可用于 AgentLoop 内部的多路径分支决策。
    """

    def __init__(self, memory_recall_fn: Optional[Callable] = None):
        self._memory_recall = memory_recall_fn

    def prioritize(self, tasks: list[dict]) -> list[dict]:
        """对多个任务打分排序。

        Args:
            tasks: 任务列表，每项格式:
                {"id": str, "description": str, "task_type": str, "request_time": float, ...}

        Returns:
            按优先级从高到低排序的任务列表（每项附加 priority_score 字段）
        """
        if not tasks:
            return []

        scored = []
        for t in tasks:
            score = self._score_task(t)
            t["priority_score"] = score
            scored.append(t)

        scored.sort(key=lambda x: x["priority_score"], reverse=True)
        return scored

    def _score_task(self, task: dict) -> float:
        """对单个任务评分。"""
        score = 50.0

        task_type = task.get("task_type", "generic")

        # 任务类型权重
        type_weights = {
            "urgent": 90,
            "fix": 80,
            "bug": 80,
            "coding": 70,
            "research": 60,
            "write": 55,
            "generic": 50,
            "query": 40,
            "maintenance": 30,
        }
        score += type_weights.get(task_type, 50)

        # 等待时间加分（排队越久越优先）
        request_time = task.get("request_time", 0)
        if request_time > 0:
            wait_minutes = (time.time() - request_time) / 60
            score += min(20, wait_minutes * 2)  # 每分钟 +2 分，最多 +20

        # 是否有历史相似失败（高优先级的反面——降低期望）
        if self._memory_recall and task.get("task_type"):
            try:
                similar = self._memory_recall(task_type, limit=3)
                if similar:
                    # 检查类似任务是否频繁失败
                    pass  # 暂不实现，避免耦合太深
            except Exception:
                pass

        return max(0, min(100, score))


# ──────────────────────────────────────────
# 进化时机调度器
# ──────────────────────────────────────────

class EvolutionScheduler:
    """进化事件的时机决策器。

    原始 EvolutionEngine 在任务完成后立即触发进化动作。
    EvolutionScheduler 接管后：
    - L1 以下进化：仍然立即执行（轻量，不影响用户体验）
    - L2 以上进化：进入调度队列，由 prioritizer 选择合适时机
        - 决策因素：当前是否有用户任务 / 系统负载 / 上次进化时间

    这样做的意义：
    - 进化动作（特别是 L3 技能提取）需要 LLM 调用，在空闲时做更合理
    - 避免在用户等待结果时"突然"执行长时间进化操作
    - 让进化变成「可规划」而非「事件触发」
    """

    def __init__(self, idle_prioritizer: IdlePrioritizer):
        self._idle = idle_prioritizer
        self._queue: list[dict] = []
        self._log_path = PRIORITY_LOG

    def enqueue(self, evolution_event: Any) -> bool:
        """将一个进化事件加入调度队列。

        返回 True（已加入队列）或 False（不需要排队，已直接放行）。
        """
        level = getattr(evolution_event, "level", 0) if not isinstance(evolution_event, dict) else evolution_event.get("level", 0)

        # L0/L1: 直接放行，不需要排队
        if level <= 1:
            return False

        # L2+: 加入队列
        event_dict = asdict(evolution_event) if hasattr(evolution_event, "__dataclass_fields__") else evolution_event
        event_dict["enqueued_at"] = time.time()
        event_dict["status"] = "pending"
        self._queue.append(event_dict)
        return True

    def get_pending(self) -> list[dict]:
        """获取所有待执行的进化事件。"""
        return [e for e in self._queue if e.get("status") == "pending"]

    def should_execute_now(self) -> Optional[dict]:
        """判断当前是否有进化事件应该执行。

        检查条件：
        1. 队列中有待处理事件
        2. 距离上一次执行足够久
        3. 当前不是用户任务高峰期

        Returns:
            应该执行的进化事件，或 None
        """
        pending = self.get_pending()
        if not pending:
            return None

        # 按 level 从高到低排序
        pending.sort(key=lambda e: e.get("level", 0), reverse=True)

        latest_evolution = self._idle._last_decision
        if latest_evolution:
            elapsed = time.time() - latest_evolution.timestamp
            if elapsed < 120:  # 2 分钟内不重复执行进化
                return None

        # 选择最高优先级的进化事件
        chosen = pending[0]
        chosen["status"] = "in_progress"
        return chosen

    def mark_done(self, event_id: str):
        """标记进化事件为已完成。"""
        for e in self._queue:
            if e.get("hash") == event_id or e.get("id") == event_id:
                e["status"] = "done"
                e["executed_at"] = time.time()
                break

    def get_queue_status(self) -> dict:
        return {
            "total": len(self._queue),
            "pending": len(self.get_pending()),
            "in_progress": sum(1 for e in self._queue if e.get("status") == "in_progress"),
            "done": sum(1 for e in self._queue if e.get("status") == "done"),
        }
