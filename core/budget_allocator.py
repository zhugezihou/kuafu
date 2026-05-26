"""
夸父 Token 预算分配器 (Budget Allocator)

职责：
1. 将上下文 token 划分为 5 类预算：系统提示 / 对话 / 工具结果 / 记忆 / 技能
2. 在每次 LLM 调用前，计算各类别 token 占用，预警即将超限
3. 当某类别超预算时，自动触发对应的压缩/降级策略
4. 提供预算状态快照，供 Hook 系统和 Observer 记录

设计原则：
- 零依赖，仅标准库
- 所有阈值可配置，支持本地（28K）和云端（64K+）两套默认值
- 非侵入式：只读不写 messages，预算违规仅返回建议 action
- 与 ContextCompressor / ContextCollapse / ToolResultStore 协作

参考：Claude Code budget-allocator.ts — 五类预算 + 自动策略选择
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

# token 估算常数（与 context_compress.py 保持一致）
CHARS_PER_TOKEN = 1.6


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数。"""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN)


# ─────────────────────────────────────────────────────────────────────
# 预算类别
# ─────────────────────────────────────────────────────────────────────

class BudgetCategory:
    """预算类别常量。"""
    SYSTEM   = "system"      # 系统提示（身份、规则）
    DIALOGUE = "dialogue"    # 对话历史（user + assistant + tool）
    TOOLS    = "tools"       # 工具结果（大块输出）
    MEMORY   = "memory"      # 记忆注入（MemoryAPI/Whiteboard 记忆）
    SKILLS   = "skills"      # 技能注入（Skill 定义）
    RESERVED = "reserved"    # 保留空间（LLM 输出 + 函数调用）


ALL_CATEGORIES = [
    BudgetCategory.SYSTEM,
    BudgetCategory.DIALOGUE,
    BudgetCategory.TOOLS,
    BudgetCategory.MEMORY,
    BudgetCategory.SKILLS,
    BudgetCategory.RESERVED,
]


@dataclass
class BudgetPolicy:
    """预算配置策略。

    Args:
        total_budget: 总 token 预算（= LLM context window - 输出冗余）
        system_ratio: 系统提示占比
        dialogue_ratio: 对话历史占比
        tools_ratio: 工具结果占比
        memory_ratio: 记忆注入占比
        skills_ratio: 技能注入占比
        reserved_ratio: 保留空间占比（LLM 输出 + 函数调用 arguments）
        warning_threshold: 预警阈值比例（0-1），超过则触发预警，默认 0.85
        critical_threshold: 危险阈值比例（0-1），超过则强制压缩，默认 0.95
    """
    total_budget: int = 28000

    # 各类别占比（总计应 ≈ 1.0）
    system_ratio: float = 0.15    # ~4200 tokens (28K) / ~9600 (64K)
    dialogue_ratio: float = 0.35  # ~9800
    tools_ratio: float = 0.20     # ~5600
    memory_ratio: float = 0.08    # ~2240
    skills_ratio: float = 0.07    # ~1960
    reserved_ratio: float = 0.15  # ~4200 (保留给 LLM 输出)

    warning_threshold: float = 0.85
    critical_threshold: float = 0.95

    def __post_init__(self):
        total = (self.system_ratio + self.dialogue_ratio + self.tools_ratio
                 + self.memory_ratio + self.skills_ratio + self.reserved_ratio)
        if abs(total - 1.0) > 0.01:
            # 自动归一化
            factor = 1.0 / total
            self.system_ratio *= factor
            self.dialogue_ratio *= factor
            self.tools_ratio *= factor
            self.memory_ratio *= factor
            self.skills_ratio *= factor
            self.reserved_ratio *= factor

    def get_budget(self, category: str) -> int:
        """获取指定类别的预算上限（tokens）。"""
        ratio = getattr(self, f"{category}_ratio", 0.0)
        return int(self.total_budget * ratio)

    @classmethod
    def for_backend(cls, total_budget: int) -> "BudgetPolicy":
        """根据总预算创建合适的配置。

        Args:
            total_budget: LLM 上下文窗口（如 28000 本地, 64000 云端）
        """
        return cls(total_budget=total_budget)


# ─────────────────────────────────────────────────────────────────────
# 预算快照
# ─────────────────────────────────────────────────────────────────────

@dataclass
class CategoryUsage:
    """单个类别的预算使用状态。"""
    category: str
    budget: int           # 分配的预算上限
    used: int             # 当前已用 token
    ratio: float = 0.0    # used / budget
    status: str = "ok"    # ok / warning / critical / over

    def __post_init__(self):
        if self.budget > 0:
            self.ratio = round(self.used / self.budget, 3)
        if self.ratio >= 1.0:
            self.status = "over"
        elif self.ratio >= 0.95:
            self.status = "critical"
        elif self.ratio >= 0.85:
            self.status = "warning"
        else:
            self.status = "ok"


@dataclass
class BudgetSnapshot:
    """完整的预算使用快照。"""
    total_budget: int
    total_used: int
    categories: dict[str, CategoryUsage] = field(default_factory=dict)
    timestamp: float = 0.0

    @property
    def overall_ratio(self) -> float:
        if self.total_budget > 0:
            return round(self.total_used / self.total_budget, 3)
        return 0.0

    @property
    def needs_action(self) -> bool:
        """是否需要触发压缩/降级。"""
        return any(
            cat.status in ("warning", "critical", "over")
            for cat in self.categories.values()
        )

    @property
    def critical_categories(self) -> list[str]:
        """返回所有 warning 及以上状态的类别名。"""
        return [
            name for name, cat in self.categories.items()
            if cat.status in ("warning", "critical", "over")
        ]

    def to_dict(self) -> dict:
        return {
            "total_budget": self.total_budget,
            "total_used": self.total_used,
            "overall_ratio": self.overall_ratio,
            "needs_action": self.needs_action,
            "categories": {
                name: {
                    "budget": cat.budget,
                    "used": cat.used,
                    "ratio": cat.ratio,
                    "status": cat.status,
                }
                for name, cat in self.categories.items()
            },
            "critical_categories": self.critical_categories,
        }


# ─────────────────────────────────────────────────────────────────────
# 建议动作（当预算超限时返回给 AgentLoop 的策略建议）
# ─────────────────────────────────────────────────────────────────────

@dataclass
class BudgetAction:
    """预算超限时建议的动作。"""
    category: str
    severity: str               # warning / critical / over
    action_type: str            # compress / trim / microcompact / summarize / defer
    description: str = ""
    priority: int = 0           # 0=low, 1=normal, 2=high, 3=critical


# ─────────────────────────────────────────────────────────────────────
# BudgetAllocator — 主分配器
# ─────────────────────────────────────────────────────────────────────

class BudgetAllocator:
    """Token 预算分配器。

    工作流程：
    1. scan(messages, memory_size, skills_size) → BudgetSnapshot
    2. 根据 snapshot 判断是否需压缩
    3. get_actions(snapshot) → list[BudgetAction]（供 AgentLoop 执行）
    4. 支持自定义预警回调

    与现有系统的协作：
    - ContextCompressor: 当 DIALOGUE 超限时触发全局压缩
    - ContextCollapse: 当 DIALOGUE critical 时触发非破坏性投影
    - ToolResultStore: 当 TOOLS 超限时对尚未 microcompact 的大结果做立即存储
    - MemoryAPI: 当 MEMORY 超限时减少记忆注入量
    - SkillResolver: 当 SKILLS 超限时仅注入最相关的技能
    """

    def __init__(
        self,
        policy: Optional[BudgetPolicy] = None,
        on_warning: Optional[callable] = None,
        on_critical: Optional[callable] = None,
    ):
        """
        Args:
            policy: 预算策略，默认使用本地 28K
            on_warning: 预警回调（可选）
            on_critical: 危险回调（可选）
        """
        self.policy = policy or BudgetPolicy()
        self.on_warning = on_warning
        self.on_critical = on_critical

        # 历史记录
        self._last_snapshot: Optional[BudgetSnapshot] = None
        self._history: list[BudgetSnapshot] = []

    def scan(
        self,
        messages: list[dict],
        memory_token_size: int = 0,
        skills_token_size: int = 0,
    ) -> BudgetSnapshot:
        """扫描当前上下文，生成预算使用快照。

        Args:
            messages: 当前准备发给 LLM 的消息列表
            memory_token_size: 记忆注入部分的 token 数（来自 MemoryAPI）
            skills_token_size: 技能注入部分的 token 数

        Returns:
            BudgetSnapshot
        """
        # 按 role 分类计算 token
        system_tokens = 0
        dialogue_tokens = 0
        tools_tokens = 0

        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            content_str = str(content) if not isinstance(content, str) else content
            tokens = estimate_tokens(content_str)

            # tool_calls 也占 token
            tc_tokens = 0
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                tc_tokens += estimate_tokens(
                    json.dumps(fn.get("arguments", {}), ensure_ascii=False)
                )
            tokens += tc_tokens

            if role == "system":
                system_tokens += tokens
            elif role == "tool":
                tools_tokens += tokens
            else:
                dialogue_tokens += tokens

        # 工具结果中可能包含大段输出——把它们识别为 TOOLS 类别的
        # 实际已经在 role='tool' 中统计了，所以 tools_tokens 已经准确

        total_used = system_tokens + dialogue_tokens + tools_tokens + memory_token_size + skills_token_size

        categories = {}
        for cat_name in ALL_CATEGORIES:
            if cat_name == BudgetCategory.SYSTEM:
                used = system_tokens
            elif cat_name == BudgetCategory.DIALOGUE:
                used = dialogue_tokens
            elif cat_name == BudgetCategory.TOOLS:
                used = tools_tokens
            elif cat_name == BudgetCategory.MEMORY:
                used = memory_token_size
            elif cat_name == BudgetCategory.SKILLS:
                used = skills_token_size
            elif cat_name == BudgetCategory.RESERVED:
                # 保留空间：不"已用"，但统计的是"已被其他抢占"
                # 实际 available = total_budget - total_used
                # 只是用来显示
                used = 0
            else:
                used = 0

            cat_budget = self.policy.get_budget(cat_name)
            if cat_name == BudgetCategory.RESERVED:
                # 保留空间：USED 显示的是被其他类别侵占的
                reserved_budget = cat_budget
                actual_used = total_used
                if actual_used > reserved_budget:
                    used = actual_used - reserved_budget
                else:
                    used = 0

            categories[cat_name] = CategoryUsage(
                category=cat_name,
                budget=cat_budget,
                used=used,
            )

        import time
        snapshot = BudgetSnapshot(
            total_budget=self.policy.total_budget,
            total_used=total_used,
            categories=categories,
            timestamp=time.time(),
        )

        self._last_snapshot = snapshot
        self._history.append(snapshot)
        if len(self._history) > 50:
            self._history = self._history[-50:]

        # 触发回调
        if snapshot.needs_action:
            critical = snapshot.critical_categories
            if critical and self.on_critical:
                self.on_critical(snapshot, critical)
            elif self.on_warning:
                self.on_warning(snapshot, snapshot.critical_categories)

        return snapshot

    def get_actions(self, snapshot: Optional[BudgetSnapshot] = None) -> list[BudgetAction]:
        """根据预算快照生成策略建议。

        返回按优先级排序的动作列表。
        """
        snap = snapshot or self._last_snapshot
        if not snap:
            return []

        actions: list[BudgetAction] = []

        for cat_name in ALL_CATEGORIES:
            usage = snap.categories.get(cat_name)
            if not usage or usage.status == "ok":
                continue

            if usage.status == "over" or usage.status == "critical":
                priority = 3 if usage.status == "over" else 2
            else:
                priority = 1

            action = self._suggest_action(cat_name, usage)
            if action:
                action.priority = priority
                actions.append(action)

        # 按优先级排序
        actions.sort(key=lambda a: a.priority, reverse=True)
        return actions

    def _suggest_action(self, category: str, usage: CategoryUsage) -> Optional[BudgetAction]:
        """为单个类别建议压缩动作。"""
        if category == BudgetCategory.DIALOGUE:
            if usage.status in ("over", "critical"):
                return BudgetAction(
                    category=category,
                    severity=usage.status,
                    action_type="collapse",  # 非破坏性投影
                    description=f"DIALOGUE 超限 ({usage.used}/{usage.budget}), 执行 ContextCollapse",
                )
            elif usage.status == "warning":
                return BudgetAction(
                    category=category,
                    severity=usage.status,
                    action_type="compress",
                    description=f"DIALOGUE 预警 ({usage.used}/{usage.budget})，准备全局压缩",
                )

        elif category == BudgetCategory.TOOLS:
            if usage.status in ("over", "critical"):
                return BudgetAction(
                    category=category,
                    severity=usage.status,
                    action_type="microcompact",
                    description=f"TOOLS 超限 ({usage.used}/{usage.budget}), 强制 microcompact 大工具结果",
                )
            elif usage.status == "warning":
                return BudgetAction(
                    category=category,
                    severity=usage.status,
                    action_type="microcompact",
                    description=f"TOOLS 预警 ({usage.used}/{usage.budget})，对新工具结果开启 microcompact",
                )

        elif category == BudgetCategory.MEMORY:
            if usage.status in ("over", "critical"):
                return BudgetAction(
                    category=category,
                    severity=usage.status,
                    action_type="summarize",
                    description=f"MEMORY 超限 ({usage.used}/{usage.budget})，减少记忆注入量",
                )

        elif category == BudgetCategory.SKILLS:
            if usage.status in ("over", "critical"):
                return BudgetAction(
                    category=category,
                    severity=usage.status,
                    action_type="summarize",
                    description=f"SKILLS 超限 ({usage.used}/{usage.budget})，仅注入最相关技能",
                )

        return None

    def get_categories_summary(self, snapshot: Optional[BudgetSnapshot] = None) -> str:
        """生成人类可读的预算概览字符串。"""
        snap = snapshot or self._last_snapshot
        if not snap:
            return "预算: 未扫描"

        lines = [
            f"📊 Token 预算: {snap.total_used}/{snap.total_budget} "
            f"({snap.overall_ratio*100:.0f}%)"
        ]
        for cat_name in ALL_CATEGORIES:
            usage = snap.categories.get(cat_name)
            if not usage:
                continue
            icon = {"ok": "✅", "warning": "⚠️", "critical": "🔥", "over": "🚨"}
            mark = icon.get(usage.status, "❓")
            lines.append(
                f"  {mark} {cat_name}: {usage.used}/{usage.budget} "
                f"({usage.ratio*100:.0f}%) [{usage.status}]"
            )
        return "\n".join(lines)

    def reset(self):
        """重置历史记录。"""
        self._last_snapshot = None
        self._history = []


# ─────────────────────────────────────────────────────────────────────
# 便捷工厂
# ─────────────────────────────────────────────────────────────────────

def create_allocator_for_backend(total_budget: int) -> BudgetAllocator:
    """根据总 token 预算创建适当的 BudgetAllocator。"""
    policy = BudgetPolicy.for_backend(total_budget)
    return BudgetAllocator(policy=policy)
