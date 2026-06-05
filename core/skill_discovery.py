"""
core/skill_discovery.py — 技能发现与隐式触发系统

扩展现有 skill_resolver.py：
  - 隐式触发（自然语言自动匹配 skill，不依赖显式 /skill 命令）
  - SkillMetadata 结构化定义（优先级、触发条件、注入点）
  - 与现有 match_skills / inject_skills_to_prompt 兼容

设计源自 Codex CLI Skills 系统：
  - maybe_emit_implicit_skill_invocation() — 自然语言检测
  - SkillMetadata — 结构化元数据
  - 内置技能 vs 用户技能的优先级管理
"""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kuafu.skill_discovery")

ROOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT_DIR / "skills"


# =========================================================================
# SkillMetadata — 结构化技能元数据
# =========================================================================

@dataclass
class SkillMetadata:
    """技能的结构化元数据。比 YAML 中原始的 key-value 更丰富。

    新增字段（对比现有 YAML 格式）：
      - trigger_priority: 隐式触发的优先级（数字越大越优先触发）
      - trigger_keywords: 额外触发关键词（独立于 YAML 中的 keywords）
      - injection_point: 在 prompt 中的注入点（skills / tools / reminders）
      - implicit_only: 仅隐式触发，不显示在技能列表中
      - requires_confirmation: 触发前是否需要用户确认
    """
    name: str
    description: str = ""
    trigger_priority: int = 0           # 隐式触发优先级
    trigger_keywords: list = field(default_factory=list)  # 触发关键词
    injection_point: str = "skills"     # skills / tools / reminders
    implicit_only: bool = False         # 仅隐式，不在技能列表显示
    requires_confirmation: bool = False  # 触发前需要用户确认
    steps: list = field(default_factory=list)
    pitfalls: list = field(default_factory=list)
    file_path: str = ""
    category: str = ""
    usage_count: int = 0


# =========================================================================
# 隐式触发检测
# =========================================================================

def maybe_emit_implicit_skill_invocation(
    content: str,
    available_skills: Optional[list[SkillMetadata]] = None,
) -> Optional[SkillMetadata]:
    """检测用户输入是否隐式触发某个 skill。

    源自 Codex CLI maybe_emit_implicit_skill_invocation()。
    如果检测到触发词且匹配唯一 skill，返回该 skill 的元数据。
    如果匹配多个，返回优先级最高的。

    Args:
        content: 用户输入的文本
        available_skills: 可用技能列表。None 时自动加载。

    Returns:
        匹配的 SkillMetadata，或 None（没有匹配）
    """
    if not content:
        return None

    if available_skills is None:
        available_skills = load_all_skills()

    content_lower = content.lower()

    matched: list[tuple[SkillMetadata, int]] = []  # (skill, match_count)

    for skill in available_skills:
        if not skill.trigger_keywords:
            continue

        match_count = 0
        for kw in skill.trigger_keywords:
            if kw.lower() in content_lower:
                match_count += 1

        if match_count > 0:
            matched.append((skill, match_count))

    if not matched:
        return None

    # 按匹配数降序，同匹配数按优先级降序
    matched.sort(key=lambda x: (x[1], x[0].trigger_priority), reverse=True)
    return matched[0][0]


# =========================================================================
# 加载所有技能（含隐式触发元数据）
# =========================================================================

def load_all_skills() -> list[SkillMetadata]:
    """从 skills/*.yaml 加载所有技能，转为 SkillMetadata。"""
    if not SKILLS_DIR.exists():
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        return []

    skills = []
    for yaml_file in sorted(SKILLS_DIR.glob("*.yaml")):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or not isinstance(data, dict):
                continue

            # 从 YAML 字段构建元数据
            # trigger_priority 和 trigger_keywords 可以来自 YAML 的元数据
            meta_data = data.get("metadata", {})
            trigger_config = meta_data.get("trigger", {}) if isinstance(meta_data, dict) else {}

            skill = SkillMetadata(
                name=data.get("name", yaml_file.stem),
                description=data.get("description", ""),
                trigger_priority=trigger_config.get("priority", 0) if isinstance(trigger_config, dict) else 0,
                trigger_keywords=trigger_config.get("keywords", data.get("keywords", []))
                    if isinstance(trigger_config, dict) else data.get("keywords", []),
                injection_point=trigger_config.get("injection", "skills")
                    if isinstance(trigger_config, dict) else "skills",
                implicit_only=trigger_config.get("implicit_only", False)
                    if isinstance(trigger_config, dict) else False,
                requires_confirmation=trigger_config.get("requires_confirmation", False)
                    if isinstance(trigger_config, dict) else False,
                steps=data.get("steps", []),
                pitfalls=data.get("pitfalls", []),
                file_path=yaml_file.name,
                category=data.get("category", ""),
                usage_count=data.get("usage_count", 0),
            )
            skills.append(skill)

        except Exception as e:
            logger.warning(f"加载技能 {yaml_file.name} 失败: {e}")

    return skills


# =========================================================================
# 与现有 skill_resolver.py 的桥接
# =========================================================================

def get_implicit_skill_injection(task: str) -> Optional[str]:
    """检测任务是否隐式触发某个技能，返回注入文本。

    与现有 inject_skills_to_prompt() 不同：
      - inject_skills_to_prompt 把所有匹配技能都注入
      - get_implicit_skill_injection 只触发「隐式触发型」技能
        且注入更紧凑的格式
    """
    skill = maybe_emit_implicit_skill_invocation(task)
    if not skill:
        return None

    lines = [f"## 自动触发技能: {skill.name}"]
    if skill.description:
        lines.append(skill.description)
    lines.append("")

    if skill.steps:
        lines.append("**建议步骤：**")
        for i, step in enumerate(skill.steps, 1):
            lines.append(f"  {i}. {step}")
        lines.append("")

    return "\n".join(lines)
