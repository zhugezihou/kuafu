"""
夸父技能解析器 — 技能发现、匹配与注入。

职责：
1. 从 skills/*.yaml 扫描所有已注册技能
2. 根据用户任务匹配最相关的技能
3. 将匹配的技能信息注入 system prompt
4. 记录技能使用情况，反馈给进化引擎

依赖：pyyaml（夸父核心依赖之一）
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT_DIR / "skills"
COMPLETION_LOG = ROOT_DIR / "logs" / "skill_usage.jsonl"

# 技能模板：匹配关键词 → 技能名
SKILL_TRIGGERS = {}


def _load_triggers() -> dict:
    """懒加载技能触发词表。"""
    global SKILL_TRIGGERS
    if SKILL_TRIGGERS:
        return SKILL_TRIGGERS

    if not SKILLS_DIR.exists():
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    triggers = {}
    for yaml_file in sorted(SKILLS_DIR.glob("*.yaml")):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or not isinstance(data, dict):
                continue
            skill_name = data.get("name", yaml_file.stem)
            keywords = data.get("keywords", [])
            if keywords:
                for kw in keywords:
                    kw_lower = kw.lower().strip()
                    if kw_lower:
                        triggers[kw_lower] = skill_name
        except Exception as e:
            print(f"[SkillResolver] 加载 {yaml_file.name} 失败: {e}")

    SKILL_TRIGGERS = triggers
    return triggers


def discover_skills() -> list[dict]:
    """扫描 skills/ 目录，返回所有技能元信息。"""
    _load_triggers()
    skills = []
    if not SKILLS_DIR.exists():
        return skills

    for yaml_file in sorted(SKILLS_DIR.glob("*.yaml")):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict):
                skills.append({
                    "name": data.get("name", yaml_file.stem),
                    "description": data.get("description", ""),
                    "file": yaml_file.name,
                    "keywords": data.get("keywords", []),
                    "usage_count": data.get("usage_count", 0),
                })
        except Exception:
            pass

    return skills


def match_skills(task: str) -> list[dict]:
    """根据用户任务，匹配最相关的技能列表。

    两阶段匹配：
    1. task_type 精确匹配（P3 生成的 skill 格式）
    2. keywords 模糊匹配（传统格式）

    Returns:
        [{"name": str, "steps": list[str], "description": str, "task_type": str, ...}, ...]
        按匹配度从高到低排序。task_type 精确匹配优先于 keyword 模糊匹配。
    """
    task_lower = task.lower()
    matched_names = set()
    scores = {}

    # 阶段1：task_type 精确匹配
    # 先探测任务类型
    task_type = _detect_task_type(task)
    if task_type != "generic":
        tt_matches = _match_by_task_type(task_type)
        for name in tt_matches:
            matched_names.add(name)
            scores[name] = scores.get(name, 0) + 10  # 精确匹配权重10

    # 阶段2：keywords 模糊匹配（传统方式）
    triggers = _load_triggers()
    if triggers:
        for kw, skill_name in triggers.items():
            if kw in task_lower:
                matched_names.add(skill_name)
                scores[skill_name] = scores.get(skill_name, 0) + 1

    if not matched_names:
        return []

    # 加载匹配技能的完整信息
    result = []
    for yaml_file in SKILLS_DIR.glob("*.yaml"):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            name = data.get("name", yaml_file.stem)
            if name in matched_names:
                result.append({
                    "name": name,
                    "description": data.get("description", ""),
                    "steps": data.get("steps", []),
                    "examples": data.get("examples", []),
                    "pitfalls": data.get("pitfalls", []),
                    "task_type": data.get("task_type", ""),
                    "score": scores.get(name, 1),
                    "file": yaml_file.name,
                })
        except Exception:
            pass

    # 按匹配分降序（task_type 精确匹配 +10 自然排在前面）
    result.sort(key=lambda x: x["score"], reverse=True)
    return result


def _detect_task_type(task: str) -> str:
    """从用户任务文本探测任务类型。

    返回: "coding" / "research" / "file_operation" / "weather" / "generic"
    """
    task_lower = task.lower()
    # coding 关键词
    coding_kw = ["写代码", "写一个", "实现", "编程", "debug", "修复bug",
                 "重构", "写脚本", "代码", "函数", "类", "api", "接口"]
    for kw in coding_kw:
        if kw in task_lower:
            return "coding"

    # research 关键词
    research_kw = ["搜索", "调研", "研究", "查一下", "找资料", "收集",
                   "分析", "总结", "对比", "比较", "趋势", "最新"]
    for kw in research_kw:
        if kw in task_lower:
            return "research"

    # file_operation 关键词
    file_kw = ["读文件", "写文件", "处理文件", "压缩", "解压", "备份",
               "移动文件", "复制", "删除", "重命名"]
    for kw in file_kw:
        if kw in task_lower:
            return "file_operation"

    # weather 关键词
    weather_kw = ["天气", "气温", "下雨", "下雪", "刮风", "台风",
                  "湿度", "温度", "是多少度"]
    for kw in weather_kw:
        if kw in task_lower:
            return "weather"

    return "generic"


def _match_by_task_type(task_type: str) -> list[str]:
    """按 task_type 精确匹配 skill。

    返回匹配的 skill 名称列表。
    """
    matched = []
    for yaml_file in SKILLS_DIR.glob("*.yaml"):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            if data.get("task_type") == task_type:
                matched.append(data.get("name", yaml_file.stem))
        except Exception:
            pass
    return matched


def inject_skills_to_prompt(task: str, existing_prompt: str) -> str:
    """将匹配的技能注入到 system prompt 末尾。

    Args:
        task: 用户任务文本
        existing_prompt: 已有的 system prompt

    Returns:
        追加了技能信息的 system prompt
    """
    matched = match_skills(task)
    if not matched:
        return existing_prompt

    parts = [existing_prompt, ""]
    parts.append("## 相关技能参考")
    parts.append("以下技能与你当前任务相关，仅供参考：")
    parts.append("")

    for skill in matched[:3]:  # 最多注入 3 个
        parts.append(f"---")
        parts.append(f"### {skill['name']}")
        if skill.get("description"):
            parts.append(f"{skill['description']}")
        parts.append("")
        if skill.get("steps"):
            parts.append("**步骤：**")
            for i, step in enumerate(skill["steps"], 1):
                parts.append(f"  {i}. {step}")
            parts.append("")
        if skill.get("pitfalls"):
            parts.append("**注意事项：**")
            for pitfall in skill["pitfalls"]:
                parts.append(f"  ⚠️ {pitfall}")
            parts.append("")

    parts.append("---")
    parts.append("技能仅供参考，你不必完全照做。根据实际情况决定如何执行。")

    return "\n".join(parts)


def record_usage(skill_name: str, task: str, success: bool, duration: float):
    """记录技能使用情况。

    记录到 logs/skill_usage.jsonl，供进化引擎分析。
    """
    log_dir = COMPLETION_LOG.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "skill": skill_name,
        "task": task[:100],
        "success": success,
        "duration": round(duration, 2),
    }
    try:
        with open(COMPLETION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def increment_usage(skill_name: str):
    """增加技能的使用计数（更新 yaml 中的 usage_count）。"""
    for yaml_file in SKILLS_DIR.glob("*.yaml"):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            if data.get("name") == skill_name:
                count = data.get("usage_count", 0)
                data["usage_count"] = count + 1
                # 写回
                with open(yaml_file, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                return
        except Exception:
            pass
