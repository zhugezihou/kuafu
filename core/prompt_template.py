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

import json
from dataclasses import dataclass, field
from typing import Optional


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
