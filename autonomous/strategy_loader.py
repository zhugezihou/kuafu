"""
夸父策略加载器 — 统一的 strategy/ 读取接口。

职责：
1. 从 strategy/prompts.yaml 加载提示模板
2. 从 strategy/task_strategies.yaml 加载任务策略
3. 从 strategy/quality.yaml 加载质量标准
4. 提供缓存 + 热加载（检测文件变更后重新读取）

原则：
- 纯读取，不改写（进化由 evolution.py 负责写回）
- 降级友好：文件不存在 / 格式错误时返回合理默认值
- 线程安全（读多写少的场景，简单缓存 + 懒刷新即可）
"""

import os
import time
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kuafu.strategy")

ROOT_DIR = Path(__file__).resolve().parent.parent
STRATEGY_DIR = ROOT_DIR / "strategy"

# ── 缓存 ─────────────────────────────────────────────

_cache: dict[str, dict] = {}
_cache_mtime: dict[str, float] = {}  # path → mtime
_CACHE_TTL = 5.0  # 秒，检测文件变更间隔


def _load_yaml(path: Path) -> Optional[dict]:
    """安全加载 YAML 文件。失败时返回 None。"""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"策略文件不存在: {path}")
        return None
    except Exception as e:
        logger.error(f"加载 {path.name} 失败: {e}")
        return None


def _get_cache(name: str) -> Optional[dict]:
    """从缓存读取，如果文件已变更则重新加载。"""
    now = time.time()
    mtime = _cache_mtime.get(name, 0)
    # 首次加载或超过 TTL 时检查文件变更
    if name not in _cache or now - mtime > _CACHE_TTL:
        path = STRATEGY_DIR / f"{name}.yaml"
        try:
            current_mtime = path.stat().st_mtime
            cached_mtime = _cache_mtime.get(name + ":mtime", 0)
            if current_mtime != cached_mtime:
                data = _load_yaml(path)
                if data is not None:
                    _cache[name] = data
                    _cache_mtime[name] = now
                    _cache_mtime[name + ":mtime"] = current_mtime
                return _cache.get(name)
        except OSError:
            return _cache.get(name)  # 文件不存在时返回旧缓存

    return _cache.get(name)


def clear_cache():
    """清除缓存（测试用）。"""
    _cache.clear()
    _cache_mtime.clear()


# ── 公开接口 ─────────────────────────────────────────


def get_prompt(task_type: str = "default") -> dict:
    """获取指定任务类型的提示模板。

    Args:
        task_type: 任务类型（default / coding / research / ...）

    Returns:
        { "system": str, "reflection": str }
        文件不存在或类型未定义时返回默认模板。
    """
    data = _get_cache("prompts")
    if data is None:
        return _DEFAULT_PROMPTS.get("default", {"system": "", "reflection": ""})

    prompts = data.get("prompts", {})
    task_prompt = prompts.get(task_type)
    if task_prompt:
        return task_prompt

    # 尝试回退到 default
    default = prompts.get("default")
    if default:
        return default

    return {"system": "", "reflection": ""}


def get_strategy(task_type: str = "generic") -> dict:
    """获取指定任务类型的执行策略。

    Args:
        task_type: 任务类型（generic / coding / research / file_operation / ...）

    Returns:
        策略字典：
        { "max_retries": int, "reflection_depth": str, "evolution_level_cap": int,
          "requirements": list[str] }
        文件不存在或类型未定义时返回通用默认策略。
    """
    data = _get_cache("task_strategies")
    if data is None:
        return _DEFAULT_STRATEGIES.get("generic", {})

    strategy = data.get(task_type)
    if strategy:
        return strategy

    # 回退到 generic
    generic = data.get("generic")
    if generic:
        return generic

    return _DEFAULT_STRATEGIES.get("generic", {})


def get_quality(task_type: str = "default") -> list[dict]:
    """获取质量标准。

    支持两种格式：
    1. 顶层数组: [{"rule": ..., "severity": ...}, ...] — 全局标准
    2. dict: {"quality": {"code": [...], "research": [...]}} — 按类型区分

    Args:
        task_type: 任务类型（code / research / file_op / 默认返回全局规则）

    Returns:
        [{"rule": str, "severity": "required"|"warning"|"optional"}, ...]
    """
    data = _get_cache("quality")
    if data is None:
        return []

    # 格式1: 顶层数组 → 全局规则
    if isinstance(data, list):
        return data if len(data) > 0 else []

    # 格式2: dict → 按类型
    quality_map = data.get("quality", {})
    rules = quality_map.get(task_type)
    if rules:
        return rules

    return []


def get_all_prompts() -> dict:
    """获取所有提示模板（用于 evolution 读取当前状态）。"""
    data = _get_cache("prompts")
    if data is None:
        return {"prompts": dict(_DEFAULT_PROMPTS)}
    return data


def get_all_strategies() -> dict:
    """获取所有任务策略（用于 evolution 读取当前状态）。"""
    data = _get_cache("task_strategies")
    if data is None:
        return dict(_DEFAULT_STRATEGIES)
    return data


def get_all_quality() -> dict:
    """获取所有质量标准。"""
    data = _get_cache("quality")
    if data is None:
        return {"quality": {}}
    return data


def render_prompt(template: str, **kwargs) -> str:
    """渲染提示模板，替换占位符。

    支持 {name} 格式的占位符替换。
    缺失的占位符保留原样。
    """
    result = template
    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        result = result.replace(placeholder, str(value))
    return result


def get_rules() -> list[str]:
    """获取当前所有策略中的 requirements 汇总（用于注入 system prompt）。"""
    data = _get_cache("task_strategies")
    if data is None:
        return _DEFAULT_RULES

    rules = []
    seen = set()
    for task_type, info in data.items():
        if isinstance(info, dict):
            reqs = info.get("requirements", [])
            for r in reqs:
                if r not in seen:
                    rules.append(r)
                    seen.add(r)
    return rules or _DEFAULT_RULES


# ── 默认值（降级保底） ───────────────────────────────
# 当 strategy/ 文件不存在或格式错误时使用这些默认值

_DEFAULT_PROMPTS = {
    "default": {
        "system": "你是夸父(Kuafu)，一个自我进化的AI agent。\n当前任务是: {task_description}\n请直接完成这个任务。",
        "reflection": "分析刚刚完成的任务：{task_result}\n反思：1. 哪里可以改进？2. 用户有没有纠正？3. 下次怎么做？",
    },
    "coding": {
        "system": "你是夸父(Kuafu)，一个擅长编程的AI agent。\n需要完成: {task_description}\n要求：先测试再交付，PEP 8，类型注解。",
        "reflection": "分析刚刚写的代码：{task_result}\n反思：1. 有bug吗？2. 更好的方式？3. 可以提取技能吗？",
    },
    "research": {
        "system": "你是夸父(Kuafu)，一个擅长研究调研的AI agent。\n需要调研: {task_description}\n要求：先搜索再整理，列出可信来源。",
        "reflection": "分析刚刚完成的调研：{task_result}\n反思：1. 信息来源全面？2. 遗漏关键信息？3. 下次优化？",
    },
}

_DEFAULT_STRATEGIES = {
    "generic": {
        "max_retries": 2,
        "reflection_depth": "detailed",
        "evolution_level_cap": 2,
        "requirements": [],
    },
    "coding": {
        "max_retries": 3,
        "reflection_depth": "detailed",
        "evolution_level_cap": 3,
        "requirements": ["先跑测试", "检查类型标注"],
    },
    "research": {
        "max_retries": 2,
        "reflection_depth": "moderate",
        "evolution_level_cap": 2,
        "requirements": ["至少 3 个独立来源", "标注不确定的信息"],
    },
}

_DEFAULT_RULES = [
    "你是夸父，一个自我进化的 AI agent",
    "用户是你的主人",
    "每次任务完成后必须反思",
    "如果用户纠正了你，记住这个教训",
    "绝对不可以修改 core/ 目录下的任何文件",
    "用中文思考和回复",
]
