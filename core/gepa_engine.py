"""
core/gepa_engine.py — GEPA 进化算法核心

GEPA = General Evolutionary Programming Algorithm
通用进化编程算法，为技能（Skill）的进化提供结构化的
变异（Mutation）、交叉（Crossover）、选择（Selection）算子。

设计原则：
- 技能即基因组：每个 Skill 是一个可进化的个体
- 适应度评估：基于执行结果、错误率、用户反馈的综合评分
- 变异：单步改进（步骤增删改、触发条件优化、错误模式更新）
- 交叉：两个 Skill 融合（提取各自优势步骤合并为衍生技能）
- 选择：低适应度技能淘汰，高适应度技能强化

与现有进化管道的关系：
  Observer → Judge(LLM) → SkillWriter → GEPA Engine
                                          │
                                    ┌─────┴──────┐
                                    │ 适应度评估   │
                                    │ 变异算子     │
                                    │ 交叉算子     │
                                    │ 选择算子     │
                                    └─────┬──────┘
                                          │
                                    ┌─────┴──────┐
                                    │ Skills/ 目录 │
                                    │ evolution   │
                                    │_state.json  │
                                    └────────────┘
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("kuafu.gepa")

ROOT_DIR = Path(__file__).resolve().parent.parent


# ── 数据结构 ─────────────────────────────────────────────────


@dataclass
class SkillGenome:
    """技能基因组 — GEPA 进化的基本个体单元。

    对应一个 skills/*.yaml 文件的内容。
    """
    name: str                           # 技能名（英文小写，hyphens连接）
    trigger: str = ""                   # 触发条件描述
    task_type: str = "generic"          # 所属任务类型
    steps: list[str] = field(default_factory=list)       # 执行步骤
    keywords: list[str] = field(default_factory=list)    # 关键词
    pitfalls: list[str] = field(default_factory=list)    # 陷阱/注意事项
    error_pattern: str = ""             # 相关的错误模式
    version: int = 1                    # 版本号
    parent: Optional[str] = None        # 父技能名称（衍生来源）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "trigger": self.trigger,
            "task_type": self.task_type,
            "steps": self.steps,
            "keywords": self.keywords,
            "pitfalls": self.pitfalls,
            "error_pattern": self.error_pattern,
            "version": self.version,
            "parent": self.parent,
        }

    @classmethod
    def from_skill_dict(cls, skill: dict, task_type: str = "generic") -> SkillGenome:
        """从 Judge 输出的 skill dict 构建 SkillGenome。"""
        return cls(
            name=skill.get("name", f"auto-{task_type}-{int(time.time())}"),
            trigger=skill.get("trigger", ""),
            task_type=task_type,
            steps=skill.get("steps", []),
            keywords=skill.get("keywords", []),
            pitfalls=skill.get("pitfalls", []),
            error_pattern=skill.get("error_pattern", ""),
        )


@dataclass
class FitnessRecord:
    """单次适应度评估记录。"""
    score: float                        # 适应度评分 (0-1)
    metrics: dict = field(default_factory=dict)  # 详细指标
    timestamp: float = field(default_factory=time.time)


# ── 适应度评估器 ─────────────────────────────────────────────


class FitnessEvaluator:
    """适应度评估器（5 维）。

    根据技能的执行历史数据计算综合适应度评分。
    5 个维度覆盖技能的全生命周期质量评估：

    1. success_rate (0.30) — 执行成功率
       - 目标：技能在真实任务中的可靠性
       - 计算：成功次数 / 总执行次数
       - 权重最高，因为可靠性是技能最有价值的指标

    2. usage_count (0.10) — 使用频率
       - 目标：技能的实际使用热度
       - 计算：log 缩放，50 次使用算满分
       - 权重低，避免高频技能垄断进化资源

    3. error_reduction (0.20) — 错误减少率
       - 目标：技能对错误处理的改善效果
       - 计算：(使用前错误 - 使用后错误) / 使用前错误
       - 有 before/after 对比时才有意义

    4. step_efficiency (0.15) — 步骤简洁度
       - 目标：步骤越少越容易被理解和复用
       - 计算：1 - (step_count - 1) * 0.1，10 步以上降到底
       - 不受 LLM 偏见影响的纯量化指标

    5. recency (0.10) — 时效性
       - 目标：最近使用的技能更有价值
       - 计算：1 - days_since_last_use / 30，30天后衰减到 0.1
       - 防止旧技能占位不产生价值

    6. quality_score (0.15) — LLM 质量评分（新增）
       - 目标：评估步骤的清晰度、完整性、通用性
       - 计算：由 LLM 根据技能内容评估
       - 弥补纯量化指标无法评估的「步骤质量」

    纯数学计算，零 LLM 成本（quality_score 维度可选 LLM）。
    """

    # 权重配置（5 个量化维度 + 1 个 LLM 维度）
    WEIGHTS = {
        "success_rate": 0.30,           # 执行成功率
        "usage_count": 0.10,            # 使用频率（越高越好）
        "error_reduction": 0.20,        # 错误减少程度
        "step_efficiency": 0.15,        # 步骤简洁度（越少越好）
        "recency": 0.10,                # 最近使用时间（越新越好）
        "quality_score": 0.15,          # LLM 质量评分（步骤清晰度/完整性/通用性）
    }

    @classmethod
    def evaluate(
        cls,
        success_rate: Optional[float] = None,
        usage_count: Optional[int] = None,
        error_before: Optional[int] = None,
        error_after: Optional[int] = None,
        step_count: Optional[int] = None,
        last_used_days: Optional[float] = None,
        quality_score: Optional[float] = None,
    ) -> FitnessRecord:
        """计算综合适应度评分。

        Args:
            success_rate: 执行成功率 (0-1)，None 表示未知
            usage_count: 使用次数，None 表示未知
            error_before: 技能使用前的错误数，None 表示未知
            error_after: 技能使用后的错误数，None 表示未知
            step_count: 步骤数，None 表示未知
            last_used_days: 距离上次使用的天数，None 表示未知
            quality_score: LLM 质量评分 (0-1)，None 表示未评估

        Returns:
            FitnessRecord，包含 score 和详细 metrics
        """
        metrics = {}
        scores = []

        # 1. 成功率
        if success_rate is not None:
            s = min(1.0, max(0.0, success_rate))
            metrics["success_rate"] = round(s, 4)
            scores.append(s * cls.WEIGHTS["success_rate"])
        else:
            # 无数据默认中性 0.5
            metrics["success_rate"] = 0.5
            scores.append(0.5 * cls.WEIGHTS["success_rate"])

        # 2. 使用频率（log 缩放，避免高频技能垄断进化资源）
        if usage_count is not None and usage_count > 0:
            # log(1+x) / log(51) — 50次满分，1次也有不错的基础分
            import math
            s = min(1.0, math.log(usage_count + 1) / math.log(51))
            metrics["usage_frequency"] = round(s, 4)
            scores.append(s * cls.WEIGHTS["usage_count"])
        else:
            scores.append(0.0 * cls.WEIGHTS["usage_count"])
            metrics["usage_frequency"] = 0.0

        # 3. 错误减少率
        if error_before is not None and error_after is not None and error_before > 0:
            reduction = (error_before - error_after) / error_before
            s = max(0.0, min(1.0, reduction))
            metrics["error_reduction"] = round(s, 4)
            scores.append(s * cls.WEIGHTS["error_reduction"])
        elif error_before is not None and error_before > 0:
            # 没有 after 数据，保守假设仍有部分错误
            s = 0.3
            metrics["error_reduction"] = 0.3
            scores.append(s * cls.WEIGHTS["error_reduction"])
        else:
            # 无错误数据 → 中性分
            s = 0.5
            metrics["error_reduction"] = 0.5
            scores.append(s * cls.WEIGHTS["error_reduction"])

        # 4. 步骤效率（步骤越少分越高）
        if step_count is not None and step_count > 0:
            # 2-4 步最佳（满分附近），1 步略减（太简单），10 步以上降到底
            if step_count <= 1:
                s = 0.7  # 单步技能可能太简单
            elif step_count <= 4:
                s = 1.0  # 2-4 步理想
            else:
                s = max(0.1, 1.0 - (step_count - 4) * 0.05)  # 5步起每步-0.05
            metrics["step_efficiency"] = round(s, 4)
            scores.append(s * cls.WEIGHTS["step_efficiency"])
        else:
            scores.append(0.5 * cls.WEIGHTS["step_efficiency"])

        # 5. 时效性
        if last_used_days is not None:
            if last_used_days <= 1:
                s = 1.0  # 当天使用满分
            else:
                s = max(0.1, 1.0 - (last_used_days - 1) / 29.0)  # 30天后衰减到 0.1
            metrics["recency"] = round(s, 4)
            scores.append(s * cls.WEIGHTS["recency"])
        else:
            scores.append(0.5 * cls.WEIGHTS["recency"])

        # 6. LLM 质量评分（可选维度）
        if quality_score is not None:
            s = min(1.0, max(0.0, quality_score))
            metrics["quality_score"] = round(s, 4)
            scores.append(s * cls.WEIGHTS["quality_score"])
        else:
            # 未评估时用其他维度的均值代替，不影响总体
            other_weight = cls.WEIGHTS["quality_score"]
            other_total_weight = sum(cls.WEIGHTS.values()) - other_weight
            if other_total_weight > 0 and len([w for w in cls.WEIGHTS.values() if w > 0]) > 1:
                # 从已有 score 中推算一个中性值
                actual_scores = [sc for sc in scores if sc > 0]
                avg_score = sum(actual_scores) / len(actual_scores) if actual_scores else 0.5
            else:
                avg_score = 0.5
            metrics["quality_score"] = None
            scores.append(avg_score * other_weight)

        total = sum(scores)
        return FitnessRecord(
            score=round(min(1.0, total), 4),
            metrics=metrics,
        )

    @classmethod
    def describe(cls, record: FitnessRecord) -> str:
        """生成人类可读的评估摘要。"""
        parts = [f"适应度: {record.score:.2f}/1.00"]
        for key, value in record.metrics.items():
            if value is not None:
                label = {
                    "success_rate": "成功率",
                    "usage_frequency": "使用频率",
                    "error_reduction": "错误减少",
                    "step_efficiency": "步骤效率",
                    "recency": "时效性",
                    "quality_score": "质量评分",
                }.get(key, key)
                parts.append(f"  {label}: {value:.2f}")
            else:
                parts.append(f"  质量评分: (未评估)")
        return "\n".join(parts)


# ── LLM 驱动质量评估器 ───────────────────────────────────────

QUALITY_EVAL_PROMPT = """你是一个技能质量评估器。评估以下技能在三个维度上的质量，返回 JSON。

## 技能
名称: {name}
触发条件: {trigger}
步骤:
{steps}

## 评估维度
1. 步骤清晰度 (clarity) — 步骤是否明确、无歧义、可独立执行？(0-1)
2. 完整性 (completeness) — 步骤是否覆盖了从初始化到收尾的完整流程？(0-1)
3. 通用性 (generalizability) — 步骤是否适用于多种场景，不绑定特定环境？(0-1)

## 评分标准
- 0.0-0.3: 差 — 步骤模糊、不完整、或高度特化
- 0.3-0.6: 中 — 步骤基本可用但有明显改进空间
- 0.6-0.9: 良 — 步骤清晰完整，有一定通用性
- 0.9-1.0: 优 — 步骤极简且通用，可直接复用

## 输出格式（严格 JSON）
{{
    "clarity": 0.75,
    "completeness": 0.80,
    "generalizability": 0.70,
    "overall": 0.75,
    "reasoning": "简短理由"
}}
"""


class QualityAwareFitnessEvaluator:
    """LLM 增强的 5 维适应度评估器。

    在 FitnessEvaluator 的纯计算 5 维基础上，增加 LLM 对
    技能步骤质量的评审，提供更全面的适应度评估。

    使用方式：
    1. LLM 质量评估 — 仅对新技能或版本变更的技能执行
    2. 量化 5 维评估 — 每次执行都计算（零 LLM 成本）
    3. 综合评分 — 加权组合
    """

    def __init__(self, llm_chat_fn: Optional[Callable] = None):
        self._llm_chat = llm_chat_fn
        self._quant = FitnessEvaluator

        # 缓存 LLM 评估结果，避免重复调用
        self._quality_cache: dict[str, tuple[float, float]] = {}
        """{skill_name: (quality_score, timestamp)} — 缓存 1 小时内有效"""

        self._stats = {"llm_calls": 0, "cache_hits": 0}

    def evaluate(
        self,
        genome: SkillGenome,
        success_rate: Optional[float] = None,
        usage_count: Optional[int] = None,
        error_before: Optional[int] = None,
        error_after: Optional[int] = None,
        last_used_days: Optional[float] = None,
        force_llm: bool = False,
    ) -> FitnessRecord:
        """带 LLM 质量评估的 5 维适应度评估。

        流程：
        1. 尝试从缓存获取质量评分
        2. 缓存未命中 → 调 LLM 评估质量
        3. 将质量评分传入 FitnessEvaluator
        4. 返回综合 FitnessRecord

        Args:
            genome: 技能基因组
            success_rate: 执行成功率
            usage_count: 使用次数
            error_before: 使用前错误数
            error_after: 使用后错误数
            last_used_days: 距离上次使用天数
            force_llm: 是否强制调 LLM（忽略缓存）

        Returns:
            FitnessRecord
        """
        quality_score = self._get_quality_score(genome, force=force_llm)
        return self._quant.evaluate(
            success_rate=success_rate,
            usage_count=usage_count,
            error_before=error_before,
            error_after=error_after,
            step_count=len(genome.steps),
            last_used_days=last_used_days,
            quality_score=quality_score,
        )

    def _get_quality_score(self, genome: SkillGenome, force: bool = False) -> Optional[float]:
        """获取技能的质量评分（优先缓存）。

        Returns:
            float (0-1) 或 None（LLM 不可用）
        """
        if not self._llm_chat:
            return None

        cache_key = f"{genome.name}:v{genome.version}"
        now = time.time()
        cache_ttl = 3600  # 1 小时

        if not force and cache_key in self._quality_cache:
            cached_score, cached_time = self._quality_cache[cache_key]
            if now - cached_time < cache_ttl:
                self._stats["cache_hits"] += 1
                return cached_score

        # 调 LLM 评估
        quality = self._call_llm_quality(genome)
        if quality is not None:
            self._quality_cache[cache_key] = (quality, now)
            self._stats["llm_calls"] += 1
        return quality

    def _call_llm_quality(self, genome: SkillGenome) -> Optional[float]:
        """调 LLM 评估技能质量。"""
        if not self._llm_chat:
            return None

        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(genome.steps))
        if not steps_text:
            steps_text = "  (无步骤)"

        prompt = QUALITY_EVAL_PROMPT.format(
            name=genome.name,
            trigger=genome.trigger or "(无)",
            steps=steps_text,
        )

        try:
            result = self._llm_chat([
                {"role": "system", "content": "你是夸父的技能质量评估器。严格按输出格式返回 JSON。"},
                {"role": "user", "content": prompt},
            ])
            content = ""
            if isinstance(result, dict):
                content = result.get("content", "")
            elif isinstance(result, str):
                content = result

            if not content:
                return None

            parsed = json.loads(content)
            overall = parsed.get("overall") or parsed.get("clarity", 0.5)
            return max(0.0, min(1.0, float(overall)))

        except (json.JSONDecodeError, ValueError, TypeError, Exception) as e:
            logger.debug(f"LLM 质量评估失败: {e}")
            return None

    def get_stats(self) -> dict:
        """获取评估统计。"""
        return dict(self._stats)

    def invalidate_cache(self, skill_name: str):
        """使指定技能的缓存失效（版本变更后调用）。"""
        keys_to_remove = [k for k in self._quality_cache if k.startswith(f"{skill_name}:")]
        for k in keys_to_remove:
            self._quality_cache.pop(k, None)


# ── 变异算子 ─────────────────────────────────────────────────


class MutationOperator:
    """变异算子 — 对单个 SkillGenome 进行微调。

    支持四种变异类型，通过 LLM 或纯规则执行。
    """

    # 变异的类型和触发概率
    TYPES = [
        ("add_step", 0.25),          # 添加步骤
        ("remove_step", 0.20),       # 移除冗余步骤
        ("optimize_trigger", 0.25),  # 优化触发条件
        ("update_error", 0.30),      # 更新错误模式
    ]

    def __init__(self, llm_chat_fn: Optional[Callable] = None):
        self._llm_chat = llm_chat_fn
        self._stats = {"total": 0, "by_type": {}}

    def mutate(self, genome: SkillGenome, context: Optional[dict] = None) -> SkillGenome:
        """对技能基因组执行一次变异。

        随机选择一种变异类型执行。变异结果可以是：
        - 生成新版本（修改后版本号 +1）
        - 或返回 None（表示无法变异）

        Args:
            genome: 原始技能基因组
            context: 可选的额外上下文（错误日志、用户反馈等）

        Returns:
            变异后的新 SkillGenome（版本号 +1）
        """
        # 边缘保护：步骤太少的技能不适合删除步骤
        valid_types = self.TYPES.copy()
        if len(genome.steps) <= 1:
            valid_types = [t for t in valid_types if t[0] != "remove_step"]

        # 按概率选择变异类型
        if not valid_types:
            return genome

        types, weights = zip(*valid_types)
        mutation_type = random.choices(types, weights=weights, k=1)[0]

        self._stats["total"] += 1
        self._stats["by_type"][mutation_type] = self._stats["by_type"].get(mutation_type, 0) + 1

        # 执行变异
        if mutation_type == "add_step":
            return self._add_step(genome, context)
        elif mutation_type == "remove_step":
            return self._remove_step(genome)
        elif mutation_type == "optimize_trigger":
            return self._optimize_trigger(genome, context)
        elif mutation_type == "update_error":
            return self._update_error(genome, context)

        return genome

    def _add_step(self, genome: SkillGenome, context: Optional[dict] = None) -> SkillGenome:
        """添加一个步骤（规则式或 LLM 式）。"""
        new = SkillGenome(**genome.to_dict())
        new.version += 1
        new.parent = genome.name

        if context and context.get("new_error"):
            # 从新错误中提取修复步骤
            new.steps = list(genome.steps)
            new.steps.append(f"遇到错误「{context['new_error']}」时，执行诊断并修复")
        elif context and context.get("user_feedback"):
            # 从用户反馈中提取新步骤
            new.steps = list(genome.steps)
            new.steps.append(f"注意: {context['user_feedback']}")
        else:
            # 通用添加：在最后追加一个可变通步骤
            new.steps = list(genome.steps)
            new.steps.append("如遇异常，根据错误信息调整参数重试")

        return new

    def _remove_step(self, genome: SkillGenome) -> SkillGenome:
        """移除一个冗余步骤（随机选择）。"""
        if len(genome.steps) <= 1:
            return genome

        new = SkillGenome(**genome.to_dict())
        new.version += 1
        new.parent = genome.name
        new.steps = list(genome.steps)

        # 不删第一步和最后一步（通常是初始化/收尾）
        if len(new.steps) <= 2:
            return genome

        idx = random.randint(1, len(new.steps) - 2)
        removed = new.steps.pop(idx)
        logger.debug(f"GEPA 变异: 移除第 {idx+1} 步「{removed}」")

        return new

    def _optimize_trigger(self, genome: SkillGenome, context: Optional[dict] = None) -> SkillGenome:
        """优化触发条件。"""
        new = SkillGenome(**genome.to_dict())
        new.version += 1
        new.parent = genome.name

        # 添加关键词优化
        if context and context.get("new_keywords"):
            existing = set(new.keywords)
            for kw in context["new_keywords"]:
                if kw not in existing and len(kw) > 1:
                    new.keywords.append(kw)

        # 如果触发条件为空，用步骤摘要填充
        if not new.trigger and new.steps:
            new.trigger = new.steps[0][:50]

        return new

    def _update_error(self, genome: SkillGenome, context: Optional[dict] = None) -> SkillGenome:
        """更新错误模式。"""
        new = SkillGenome(**genome.to_dict())
        new.version += 1
        new.parent = genome.name

        if context and context.get("new_error"):
            existing = set(new.error_pattern.split("; ") if new.error_pattern else [])
            new_error = context["new_error"]
            if new_error not in existing:
                if new.error_pattern:
                    new.error_pattern = new.error_pattern + "; " + new_error
                else:
                    new.error_pattern = new_error

        return new

    def get_stats(self) -> dict:
        """获取变异统计。"""
        return dict(self._stats)


# ── 交叉算子 ─────────────────────────────────────────────────


class CrossoverOperator:
    """交叉算子 — 融合两个技能基因组产生衍生技能。

    从两个父技能中各取一部分步骤/特征，组合为新的子技能。
    """

    def __init__(self, llm_chat_fn: Optional[Callable] = None):
        self._llm_chat = llm_chat_fn
        self._stats = {"total": 0}

    def crossover(
        self,
        genome_a: SkillGenome,
        genome_b: SkillGenome,
    ) -> Optional[SkillGenome]:
        """对两个技能基因组执行交叉操作。

        交叉方式：
        - 取 A 的前半部分步骤 + B 的后半部分步骤（单点交叉）
        - 合并关键词（去重）
        - 合并陷阱（去重）
        - 触发条件使用更详细的那个
        - 错误模式合并
        - task_type 继承经验更丰富的那方

        Args:
            genome_a: 父技能 A
            genome_b: 父技能 B

        Returns:
            交叉后的新 SkillGenome，或 None（无法交叉）
        """
        if not genome_a.steps or not genome_b.steps:
            return None

        self._stats["total"] += 1

        # 单点交叉：A 的前半 + B 的后半
        mid_a = len(genome_a.steps) // 2
        mid_b = len(genome_b.steps) // 2
        new_steps = genome_a.steps[:mid_a] + genome_b.steps[mid_b:]

        # 如果交叉结果太短，做整段保留
        if len(new_steps) < 2:
            new_steps = genome_a.steps[:1] + genome_b.steps[-1:]

        # 关键词合并去重
        new_keywords = list(dict.fromkeys(genome_a.keywords + genome_b.keywords))

        # 陷阱合并去重
        new_pitfalls = list(dict.fromkeys(genome_a.pitfalls + genome_b.pitfalls))

        # 触发条件：取更详细的那个
        new_trigger = genome_a.trigger if len(genome_a.trigger) >= len(genome_b.trigger) else genome_b.trigger

        # 错误模式合并
        errors = set()
        if genome_a.error_pattern:
            errors.update(genome_a.error_pattern.split("; "))
        if genome_b.error_pattern:
            errors.update(genome_b.error_pattern.split("; "))
        new_error = "; ".join(sorted(errors)) if errors else ""

        # 生成新名称
        base_a = genome_a.name.replace("-v2", "").replace("-v3", "")
        base_b = genome_b.name.replace("-v2", "").replace("-v3", "")
        new_name = f"{base_a}-{base_b}-hybrid"

        child = SkillGenome(
            name=new_name,
            trigger=new_trigger,
            task_type=genome_a.task_type if genome_a.task_type != "generic" else genome_b.task_type,
            steps=new_steps,
            keywords=new_keywords,
            pitfalls=new_pitfalls,
            error_pattern=new_error,
            version=1,
            parent=f"{genome_a.name}+{genome_b.name}",
        )

        return child


# ── 选择算子 ─────────────────────────────────────────────────


class SelectionOperator:
    """选择算子 — 根据适应度决定技能的保留/淘汰/变异。

    管理技能池的演化：
    - 高适应度的技能保留并有机会变异
    - 低适应度的技能标记为淘汰
    - 中等适应度的技能有机会交叉产生新模式
    """

    # 阈值配置
    ELITE_THRESHOLD = 0.7       # ≥ 0.7 视为精英技能
    WEAK_THRESHOLD = 0.3        # < 0.3 视为弱技能
    CULL_THRESHOLD = 0.15       # < 0.15 淘汰

    def __init__(self):
        self._stats = {
            "elite": 0,       # 精英技能数
            "weak": 0,        # 弱技能数
            "culled": 0,      # 已淘汰数
            "retained": 0,    # 保留数
        }

    def classify(self, fitness: FitnessRecord) -> str:
        """根据适应度评分分类技能。

        Returns:
            "elite" | "normal" | "weak" | "cull"
        """
        if fitness.score >= self.ELITE_THRESHOLD:
            return "elite"
        elif fitness.score < self.CULL_THRESHOLD:
            return "cull"
        elif fitness.score < self.WEAK_THRESHOLD:
            return "weak"
        return "normal"

    def select(
        self,
        genomes: list[tuple[str, SkillGenome, FitnessRecord]],
    ) -> dict[str, list[str]]:
        """对技能池执行一轮选择。

        Args:
            genomes: [(skill_name, SkillGenome, FitnessRecord), ...]

        Returns:
            {
                "elite": [skill_name, ...],  # 保留并有机会变异
                "normal": [skill_name, ...],  # 保留
                "weak": [skill_name, ...],    # 标记需变异或交叉
                "cull": [skill_name, ...],    # 淘汰候选
            }
        """
        result = {"elite": [], "normal": [], "weak": [], "cull": []}

        for name, genome, fitness in genomes:
            cls = self.classify(fitness)
            result[cls].append(name)

        self._stats["elite"] = len(result["elite"])
        self._stats["weak"] = len(result["weak"])
        self._stats["culled"] = len(result["cull"])
        self._stats["retained"] = len(result["elite"]) + len(result["normal"])

        return result

    def get_stats(self) -> dict:
        return dict(self._stats)


# ── GEPA 引擎 ────────────────────────────────────────────────


class GEPAEngine:
    """GEPA 进化算法引擎。

    整合适应度评估、变异、交叉、选择四个算子，
    提供技能进化的全生命周期管理。

    用法：
        engine = GEPAEngine()
        # 评估
        fitness = engine.evaluate_fitness(genome, ...)
        # 变异
        mutated = engine.mutate(genome, context)
        # 交叉
        child = engine.crossover(genome_a, genome_b)
        # 选择
        decisions = engine.select(all_genomes)
        # 一轮完整进化
        result = engine.evolve_once(all_genomes, context)
    """

    def __init__(self, llm_chat_fn: Optional[Callable] = None):
        self.fitness = QualityAwareFitnessEvaluator(llm_chat_fn=llm_chat_fn)
        self.mutation = MutationOperator(llm_chat_fn=llm_chat_fn)
        self.crossover = CrossoverOperator(llm_chat_fn=llm_chat_fn)
        self.selection = SelectionOperator()

        self._generation = 0
        self._history: list[dict] = []  # 每代的进化记录

    # ── 单次调用接口 ──

    def evaluate_fitness(
        self,
        genome: SkillGenome,
        success_rate: Optional[float] = None,
        usage_count: Optional[int] = None,
        error_before: Optional[int] = None,
        error_after: Optional[int] = None,
        last_used_days: Optional[float] = None,
        force_llm: bool = False,
    ) -> FitnessRecord:
        """评估单个技能的适应度（支持 LLM 质量评分）。"""
        return self.fitness.evaluate(
            genome=genome,
            success_rate=success_rate,
            usage_count=usage_count,
            error_before=error_before,
            error_after=error_after,
            last_used_days=last_used_days,
            force_llm=force_llm,
        )

    def evaluate_with_report(self, genome: SkillGenome, **kwargs) -> dict:
        """评估并返回结构化报告。"""
        record = self.evaluate_fitness(genome, **kwargs)
        return {
            "skill_name": genome.name,
            "version": genome.version,
            "fitness": record.score,
            "metrics": record.metrics,
            "summary": FitnessEvaluator.describe(record),
        }

    def invalidate_fitness_cache(self, skill_name: str):
        """使技能的 LLM 质量评估缓存失效。"""
        self.fitness.invalidate_cache(skill_name)

    def mutate(self, genome: SkillGenome, context: Optional[dict] = None) -> SkillGenome:
        """对单个技能执行变异。"""
        return self.mutation.mutate(genome, context)

    def crossover(
        self,
        genome_a: SkillGenome,
        genome_b: SkillGenome,
    ) -> Optional[SkillGenome]:
        """对两个技能执行交叉。"""
        return self.crossover.crossover(genome_a, genome_b)

    def select(
        self,
        genomes: list[tuple[str, SkillGenome, FitnessRecord]],
    ) -> dict[str, list[str]]:
        """执行一轮选择，返回分类结果。"""
        return self.selection.select(genomes)

    # ── 进化周期 ──

    def evolve_once(
        self,
        genomes: list[tuple[str, SkillGenome, FitnessRecord]],
        context: Optional[dict] = None,
    ) -> dict:
        """执行一轮完整的进化周期。

        流程：
        1. 分类：根据适应度将技能分为 elite/normal/weak/cull
        2. 选择：cull 标记为淘汰
        3. 变异：weak 技能尝试变异
        4. 交叉：从 elite + normal 中随机配对进行交叉
        5. 生成新版本

        Args:
            genomes: [(skill_name, SkillGenome, FitnessRecord), ...]
            context: 可选的上下文（新错误、用户反馈等）

        Returns:
            {
                "generation": int,
                "mutations": [SkillGenome, ...],    # 变异结果
                "crossovers": [SkillGenome, ...],   # 交叉结果
                "culled": [str, ...],               # 被淘汰的技能名
                "decisions": dict,                  # 选择分类结果
            }
        """
        self._generation += 1
        result = {
            "generation": self._generation,
            "mutations": [],
            "crossovers": [],
            "culled": [],
            "decisions": {},
        }

        # 1. 分类
        decisions = self.selection.select(genomes)
        result["decisions"] = decisions

        # 2. 淘汰
        result["culled"] = decisions.get("cull", [])

        # 3. 变异 weak 技能
        weak_names = decisions.get("weak", [])
        name_map = {n: g for n, g, _ in genomes}
        for name in weak_names:
            genome = name_map.get(name)
            if genome:
                mutated = self.mutation.mutate(genome, context)
                if mutated and mutated.version != genome.version:
                    result["mutations"].append(mutated)

        # 4. 交叉：从 elite + normal 中配对
        breeding_pool = decisions.get("elite", []) + decisions.get("normal", [])
        if len(breeding_pool) >= 2:
            # 随机配对
            random.shuffle(breeding_pool)
            pairs = [(breeding_pool[i], breeding_pool[i + 1])
                     for i in range(0, len(breeding_pool) - 1, 2)]
            for name_a, name_b in pairs[:2]:  # 最多产生 2 个交叉后代
                g_a = name_map.get(name_a)
                g_b = name_map.get(name_b)
                if g_a and g_b:
                    child = self.crossover.crossover(g_a, g_b)
                    if child:
                        result["crossovers"].append(child)

        # 记录代次历史
        record = {
            "gen": self._generation,
            "time": time.time(),
            "mutations": len(result["mutations"]),
            "crossovers": len(result["crossovers"]),
            "culled": len(result["culled"]),
            "elite_count": len(decisions.get("elite", [])),
            "weak_count": len(decisions.get("weak", [])),
            "normal_count": len(decisions.get("normal", [])),
        }
        self._history.append(record)

        return result

    def get_stats(self) -> dict:
        """获取引擎统计信息。"""
        llm_stats = self.fitness.get_stats()
        return {
            "generation": self._generation,
            "total_mutations": self.mutation.get_stats()["total"],
            "total_crossovers": self.crossover._stats["total"],
            "selection": self.selection.get_stats(),
            "history": self._history[-10:] if self._history else [],
            "llm_quality_calls": llm_stats.get("llm_calls", 0),
            "llm_quality_cache_hits": llm_stats.get("cache_hits", 0),
        }
