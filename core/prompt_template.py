"""
夸父结构化 Prompt 模板系统 (PromptTemplate)

职责：
1. 将 system prompt 拆分为可组合的独立 section
2. 每个 section 有唯一 ID、标题、内容、条件开关
3. 根据任务类型和上下文动态组装 prompt
4. 提供 token 估算，配合 BudgetAllocator 做预算感知
5. 支持 section 级别的条件注入（仅当有记忆时注入记忆 section）

设计原则：
- 零依赖，仅标准库
- 所有 section 纯文本，无模板引擎
- 非侵入式：build_system_prompt 仍由 AgentLoop 调用，仅重构内容组织方式
- 与 BudgetAllocator 协作：每个 section 提供 token 估算注册

参考：Claude Code prompt-builder.ts — section + 条件注入 + budget tag
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Section:
    """单个 prompt section。

    Args:
        id: 唯一标识（如 "identity", "rules", "tools"）
        title: 标题（Markdown 二级标题，如 "## 核心规则"）
        content: 正文内容（不含标题，纯文本列表）
        condition: 条件表达式，None 表示始终包含
        priority: 优先级（数字越大越靠前，仅在不指定 order 时使用）
        order: 显式排序权重（0=最前），为 None 时按 priority 排序
        budget_tag: 对应的 BudgetCategory（用于计数），默认 "system"
        enabled: 是否启用（设为 False 可快速关闭）
    """
    id: str
    title: str = ""
    content: str = ""
    condition: Optional[str] = None  # 未来扩展：条件表达式
    priority: int = 0
    order: Optional[int] = None
    budget_tag: str = "system"
    enabled: bool = True

    def render(self) -> str:
        """渲染该 section 为文本。"""
        if not self.enabled or not self.content:
            return ""
        lines = [f"## {self.title}"]
        lines.append(self.content)
        lines.append("")
        return "\n".join(lines)

    def estimate_tokens(self) -> int:
        """估算该 section 的 token 数。"""
        text = self.render()
        if not text:
            return 0
        return int(len(text) / 1.6)


@dataclass
class PromptAssembly:
    """一次完整的 prompt 组装结果。"""
    sections: list[Section] = field(default_factory=list)
    order: list[str] = field(default_factory=list)  # section id 顺序

    def assemble(self) -> str:
        """将启用的 sections 按 order 排序后拼接。"""
        # 收集所有启用的 section
        enabled = [s for s in self.sections if s.enabled and s.content]

        # 排序
        def sort_key(s: Section):
            if s.order is not None:
                return (0, s.order)
            return (1, -s.priority)

        enabled.sort(key=sort_key)

        parts = []
        for sec in enabled:
            rendered = sec.render()
            if rendered:
                parts.append(rendered)

        return "\n".join(parts)

    def count_tokens(self) -> dict[str, int]:
        """按 budget_tag 统计 token 数。"""
        stats: dict[str, int] = {}
        for sec in self.sections:
            if sec.enabled and sec.content:
                tag = sec.budget_tag
                stats[tag] = stats.get(tag, 0) + sec.estimate_tokens()
        return stats

    def get_by_id(self, section_id: str) -> Optional["Section"]:
        """按 ID 查找 section。"""
        for sec in self.sections:
            if sec.id == section_id:
                return sec
        return None

    def disable(self, section_id: str):
        """禁用指定 section。"""
        sec = self.get_by_id(section_id)
        if sec:
            sec.enabled = False

    def enable(self, section_id: str):
        """启用指定 section。"""
        sec = self.get_by_id(section_id)
        if sec:
            sec.enabled = True

    def replace_content(self, section_id: str, new_content: str):
        """替换指定 section 的内容（保留标题）。"""
        sec = self.get_by_id(section_id)
        if sec:
            sec.content = new_content


# ─────────────────────────────────────────────────────────────────────
# PromptManager — 主管理器
# ─────────────────────────────────────────────────────────────────────

class PromptManager:
    """结构化 Prompt 管理器。

    将 build_system_prompt 从"一个函数拼接字符串"改为了"section 组合"。
    每个 section 有独立的生命周期：构建、条件注入、budget 标签、启用/禁用。

    使用示例：
        pm = PromptManager(task)
        pm.add_identity_section(identity)
        pm.add_rules_section(rules)
        pm.add_tools_section(tool_descriptions)
        pm.add_memory_section(memories, enable=has_memories)
        prompt = pm.assemble()
    """

    def __init__(self, task: str = ""):
        """
        Args:
            task: 当前任务（用于条件注入，如技能匹配）
        """
        self._assembly = PromptAssembly()
        self.task = task
        self._section_count: int = 0

    def add_section(
        self,
        section_id: str,
        title: str = "",
        content: str = "",
        condition: Optional[str] = None,
        priority: int = 0,
        order: Optional[int] = None,
        budget_tag: str = "system",
        enabled: bool = True,
    ) -> "PromptManager":
        """添加一个 section。支持链式调用。

        Args:
            section_id: 唯一标识
            title: 标题
            content: 正文
            condition: 条件表达式（预留）
            priority: 优先级
            order: 显式排序（0=最前）
            budget_tag: 预算标签
            enabled: 是否启用
        """
        self._assembly.sections.append(Section(
            id=section_id,
            title=title,
            content=content,
            condition=condition,
            priority=priority,
            order=order,
            budget_tag=budget_tag,
            enabled=enabled,
        ))
        self._section_count += 1
        return self  # 链式

    def assemble(self) -> str:
        """组装所有启用的 section 为完整 system prompt。"""
        return self._assembly.assemble()

    def get_budget_stats(self) -> dict[str, int]:
        """按 budget_tag 获取 token 估算统计。"""
        return self._assembly.count_tokens()

    def get_by_id(self, section_id: str) -> Optional[Section]:
        """按 ID 查找 section。"""
        return self._assembly.get_by_id(section_id)

    def disable(self, section_id: str):
        """禁用某个 section。"""
        self._assembly.disable(section_id)

    def enable(self, section_id: str):
        """启用某个 section。"""
        self._assembly.enable(section_id)

    def replace_content(self, section_id: str, new_content: str):
        """替换某个 section 的内容。"""
        self._assembly.replace_content(section_id, new_content)

    @property
    def sections(self) -> list[Section]:
        return self._assembly.sections

    @property
    def section_count(self) -> int:
        return self._section_count

    @property
    def enabled_sections(self) -> list[Section]:
        return [s for s in self._assembly.sections if s.enabled]

    def to_summary(self) -> str:
        """生成人类可读的 Section 概览。"""
        lines = ["📋 Prompt 组装概览:"]
        for i, sec in enumerate(self._assembly.sections):
            status = "✅" if sec.enabled else "⬜"
            token_info = f"~{sec.estimate_tokens()}t"
            lines.append(f"  {status} [{sec.id}] {sec.title} ({token_info})")
        lines.append(f"  ── 总计: {len(self.enabled_sections)}/{self._section_count} sections active")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# P1-4: PromptCache — 缓存分块
# ─────────────────────────────────────────────────────────────────────

# 稳定性标签
STABILITY_L1_IMMUTABLE = "L1_immutable"   # identity, rules, format — session 级不变
STABILITY_L2_SEMI = "L2_semi"             # tools — 同一 session 固定
STABILITY_L3_VARIABLE = "L3_variable"     # memory, skills, quality — 每次变化

STABILITY_DEFAULT: dict[str, str] = {
    "identity": STABILITY_L1_IMMUTABLE,
    "user_identity": STABILITY_L1_IMMUTABLE,
    "boundary": STABILITY_L1_IMMUTABLE,
    "commitments": STABILITY_L1_IMMUTABLE,
    "rules": STABILITY_L1_IMMUTABLE,
    "format": STABILITY_L1_IMMUTABLE,
    "exec_rules": STABILITY_L1_IMMUTABLE,
    "self_cognition": STABILITY_L1_IMMUTABLE,
    "config": STABILITY_L1_IMMUTABLE,
    "other_agents": STABILITY_L1_IMMUTABLE,
    "core_tools": STABILITY_L2_SEMI,
    "common_tools": STABILITY_L2_SEMI,
    "hidden_tools": STABILITY_L2_SEMI,
    "quality": STABILITY_L2_SEMI,
    "memory_context": STABILITY_L3_VARIABLE,
    "skills": STABILITY_L3_VARIABLE,
    "directory": STABILITY_L3_VARIABLE,
}


def get_stability(section_id: str) -> str:
    """获取 section 的缓存稳定性级别。"""
    return STABILITY_DEFAULT.get(section_id, STABILITY_L3_VARIABLE)


@dataclass
class CacheBlock:
    """单个缓存块。"""
    stability: str
    content: str = ""
    cache_key: str = ""
    token_count: int = 0

    def needs_refresh(self, content_to_build: str) -> bool:
        if self.stability == STABILITY_L3_VARIABLE:
            return True
        if not self.cache_key:
            return True
        new_key = self._hash(content_to_build)
        return new_key != self.cache_key

    def refresh(self, content: str):
        self.content = content
        self.cache_key = self._hash(content)
        self.token_count = int(len(content) / 1.6)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def render(self) -> str:
        return self.content


class PromptCache:
    """Prompt 缓存管理器。

    三层缓存：L1 (immutable) / L2 (semi) / L3 (variable)。
    同一 session 的 LLM 调用中，L1+L2 命中缓存不重复组装。
    """

    def __init__(self):
        self._l1_cache: str = ""
        self._l1_key: str = ""
        self._l1_tokens: int = 0
        self._l2_cache: str = ""
        self._l2_key: str = ""
        self._l2_tokens: int = 0
        self._hit_count: int = 0
        self._miss_count: int = 0

    def get_block(self, sections: list, stability: str) -> CacheBlock:
        """获取指定稳定层的缓存块。"""
        block = CacheBlock(stability=stability)
        if stability == STABILITY_L3_VARIABLE:
            block.content = self._assemble_sections(sections)
            block.token_count = int(len(block.content) / 1.6)
            return block

        current_text = self._assemble_sections(sections)
        if stability == STABILITY_L1_IMMUTABLE:
            cache, key = self._l1_cache, self._l1_key
        else:
            cache, key = self._l2_cache, self._l2_key

        current_key = hashlib.md5(current_text.encode("utf-8")).hexdigest()
        if cache and key == current_key:
            self._hit_count += 1
            block.content = cache
            block.cache_key = key
            block.token_count = (self._l1_tokens if stability == STABILITY_L1_IMMUTABLE
                                 else self._l2_tokens)
            return block

        self._miss_count += 1
        block.content = current_text
        block.cache_key = current_key
        block.token_count = int(len(current_text) / 1.6)
        if stability == STABILITY_L1_IMMUTABLE:
            self._l1_cache, self._l1_key = current_text, current_key
            self._l1_tokens = block.token_count
        else:
            self._l2_cache, self._l2_key = current_text, current_key
            self._l2_tokens = block.token_count
        return block

    def clear(self):
        self._l1_cache = self._l1_key = ""
        self._l1_tokens = 0
        self._l2_cache = self._l2_key = ""
        self._l2_tokens = 0
        self._hit_count = self._miss_count = 0

    def clear_l2(self):
        self._l2_cache = self._l2_key = ""
        self._l2_tokens = 0

    def stats(self) -> dict:
        total = self._hit_count + self._miss_count
        return {
            "hit": self._hit_count,
            "miss": self._miss_count,
            "hit_rate": round(self._hit_count / total, 3) if total > 0 else 0,
            "l1_cached": bool(self._l1_cache),
            "l2_cached": bool(self._l2_cache),
        }

    @staticmethod
    def _assemble_sections(sections: list) -> str:
        parts = []
        for sec in sections:
            rendered = sec.render()
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# P1-8: System Reminders — 轻量级动态上下文
# ─────────────────────────────────────────────────────────────────────

def build_reminders(
    turn_context: str = "",
    task: str = "",
    turn_count: int = 0,
    last_tool_results: Optional[list[str]] = None,
    memory_hints: Optional[list[str]] = None,
) -> str:
    """构建短提醒列表，每次用户消息前注入。

    Claude Code 设计启示（Section 7.1 ）:
    - 每次用户消息前注入 1-3 条简短、聚焦的「系统提醒」
    - 比完整 system prompt 刷新更轻量，更精准
    - 可承载：当前进度提示、工具使用约定、记忆线索
    - 提醒必须足够短（<1-2 句话），否则会稀释核心 system prompt

    Args:
        turn_context: 当前轮到时的上下文描述
        task: 原始任务描述
        turn_count: 当前轮次（0-based）
        last_tool_results: 上轮工具结果关键词（可选）
        memory_hints: 记忆提示词（可选）

    Returns:
        格式化的提醒字符串，空字符串表示无提醒。
        单条提醒不超过 80 字符，不超过 3 条。
    """
    reminders: list[str] = []

    # ── 轮次提醒：高轮次时提示聚焦 ──
    if turn_count > 5:
        reminders.append("注意：已进行多轮对话，聚焦当前任务，不要回顾已完成步骤。")

    # ── 工具结果提醒：上轮工具有失败或大结果时 ──
    if last_tool_results:
        fail_keywords = ["失败", "错误", "error", "fail", "error", "timeout"]
        has_failure = any(
            any(kw in result.lower() for kw in fail_keywords)
            for result in last_tool_results
        )
        if has_failure:
            reminders.append("上轮工具有失败，检查错误信息并尝试修复。")

    # ── 记忆提示：由外部传入，不自动生成 ──
    if memory_hints:
        for hint in memory_hints[:2]:
            if len(hint) < 60:
                reminders.append(hint)

    # ── 任务类型提醒 ──
    if task:
        task_lower = task.lower()
        if "git" in task_lower or "commit" in task_lower or "push" in task_lower:
            reminders.append("Git 操作后记得检查状态确认成功。")
        elif "deploy" in task_lower or "发布" in task_lower:
            reminders.append("部署前检查配置，部署后验证服务可用。")

    # 限制 3 条
    if not reminders:
        return ""

    return "\n".join(f"> 提醒: {r}" for r in reminders[:3])
