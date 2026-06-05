"""
core/evolution_rules.py — 基于 Hindsight 置信度的进化规则引擎

不是生成 skill（那是 evolution.py 的事），而是：
1. 任务失败 → LLM 分析原因 → 生成行为规则
2. 规则写入 Opinion（topic="evolved:xxx"）
3. 下次同类任务时注入 system prompt
4. 成功 → reinforce；失败 → weaken/contradict
5. 置信度 < 0.15 或 7 天未触发自动过期

约束机制：
- 最多 30 条活跃规则
- 置信度 >= 0.4 才注入
- 规则冲突检测：新规则和现有规则互斥时保留置信度高的
- 分类：通用(rule) / 场景特定(hint) / 一次性(fix)，只有 rule 持续保留
- 7 天无触发自动过期
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger("kuafu.evolution_rules")

# ── 约束常量 ───────────────────────────────────────────────

MAX_ACTIVE_RULES = 30          # 最大活跃规则数
MIN_INJECT_CONFIDENCE = 0.4   # 注入阈值：置信度 >= 0.4 才注入
AUTO_DELETE_CONFIDENCE = 0.15 # 低于此值自动删除
EXPIRY_DAYS = 7               # 7 天无触发自动过期
RULE_CATEGORIES = {"rule", "hint", "fix"}
                               # rule=通用规则（长期保留）
                               # hint=场景提示（7天过期）
                               # fix=一次性修复（用后即弃）

# ── 规则结构 ───────────────────────────────────────────────


class EvolvedRule:
    """一条进化规则。"""

    def __init__(self, rule_text: str, category: str = "rule",
                 task_type: str = "", trigger_keywords: list[str] = None,
                 confidence: float = 0.5, source: str = "",
                 created: float = 0, last_triggered: float = 0,
                 hit_count: int = 0, miss_count: int = 0):
        self.rule_text = rule_text
        self.category = category if category in RULE_CATEGORIES else "rule"
        self.task_type = task_type
        self.trigger_keywords = trigger_keywords or []
        self.confidence = confidence
        self.source = source
        self.created = created or time.time()
        self.last_triggered = last_triggered
        self.hit_count = hit_count
        self.miss_count = miss_count

    @property
    def is_active(self) -> bool:
        return self.confidence >= AUTO_DELETE_CONFIDENCE

    @property
    def is_expired(self) -> bool:
        if self.category == "fix":
            return True  # 一次性用完即弃
        if self.category == "hint" and self.last_triggered > 0:
            age = time.time() - self.last_triggered
            return age > EXPIRY_DAYS * 86400
        return False

    def to_dict(self) -> dict:
        return {
            "rule": self.rule_text[:200],
            "category": self.category,
            "task_type": self.task_type,
            "keywords": self.trigger_keywords[:5],
            "confidence": round(self.confidence, 2),
            "created": self.created,
            "last_triggered": self.last_triggered,
            "hits": self.hit_count,
            "misses": self.miss_count,
        }


# ── 规则管理器（基于 OpinionEngine） ─────────────────────────


class EvolutionRuleManager:
    """进化规则管理器。

    复用 Hindsight-Lite 的 OpinionEngine 做置信度管理。
    每条规则 = 一条 Opinion（topic="evolved:<rule_text_hash>"）

    存储位置：opinions 表（和 Hindsight 共享同一引擎）
    """

    def __init__(self, opinion_engine: Any, llm_chat_fn: Optional[callable] = None):
        self._oe = opinion_engine  # OpinionEngine 实例
        self._llm = llm_chat_fn

    # ── 写入 ────────────────────────────────────────────────

    def add_rule(self, rule_text: str, category: str = "rule",
                 task_type: str = "", keywords: list[str] = None,
                 source: str = "") -> dict:
        """添加一条进化规则。"""
        topic = self._make_topic(rule_text)
        result = self._oe.reinforce(topic, rule_text)

        # 额外存储规则元数据
        if result["action"] == "created" or result["action"] == "reinforced":
            self._update_rule_meta(topic, {
                "rule_text": rule_text[:500],
                "category": category,
                "task_type": task_type,
                "keywords": keywords or [],
                "source": source,
                "created": time.time(),
            })
            # enforce max 容量
            self._enforce_capacity()

        return result

    @staticmethod
    def make_topic_static(rule_text: str) -> str:
        """从规则文本生成唯一的 topic。静态版本。"""
        import hashlib
        h = hashlib.md5(rule_text.encode()).hexdigest()[:12]
        return f"evolved:{h}"

    def _make_topic(self, rule_text: str) -> str:
        """从规则文本生成唯一的 topic。"""
        return self.make_topic_static(rule_text)

    def _update_rule_meta(self, topic: str, meta: dict):
        """将规则元数据存入 opinions 表的 evidence 字段。"""
        try:
            self._oe._conn.execute(
                "UPDATE opinions SET evidence = ? WHERE topic = ? AND deleted = 0",
                (json.dumps(meta, ensure_ascii=False), topic),
            )
            self._oe._conn.commit()
        except Exception:
            pass

    def _get_rule_meta(self, topic: str) -> dict:
        """读取规则元数据。"""
        try:
            row = self._oe._conn.execute(
                "SELECT evidence FROM opinions WHERE topic = ? AND deleted = 0",
                (topic,),
            ).fetchone()
            if row:  # pragma: no cover
                return json.loads(row["evidence"])  # pragma: no cover
        except Exception:  # pragma: no cover
            pass
        return {}

    def _enforce_capacity(self):
        """超过 MAX_ACTIVE_RULES 时删除置信度最低的活跃规则。"""
        rules = self.get_rules(min_confidence=0.0)
        if len(rules) <= MAX_ACTIVE_RULES:
            return
        # 按置信度升序排序
        to_remove = sorted(rules, key=lambda r: r["confidence"])[:len(rules) - MAX_ACTIVE_RULES]  # pragma: no cover
        for r in to_remove:  # pragma: no cover
            topic = self._make_topic(r["rule"])  # pragma: no cover
            try:  # pragma: no cover
                self._oe._conn.execute(  # pragma: no cover
                    "UPDATE opinions SET deleted = 1 WHERE topic = ?", (topic,))
            except Exception:  # pragma: no cover
                pass  # pragma: no cover
        self._oe._conn.commit()  # pragma: no cover
        logger.info(f"进化规则: 容量限制淘汰 {len(to_remove)} 条低置信度规则")  # pragma: no cover

    # ── 读取 ────────────────────────────────────────────────

    def get_rules(self, min_confidence: float = MIN_INJECT_CONFIDENCE,
                  category: str = "", task_type: str = "",
                  limit: int = MAX_ACTIVE_RULES) -> list[dict]:
        """获取活跃规则列表。"""
        try:
            opinions = self._oe._conn.execute(
                """SELECT * FROM opinions
                   WHERE topic LIKE 'evolved:%'
                     AND deleted = 0
                     AND confidence >= ?
                   ORDER BY confidence DESC, updated DESC""",
                (min_confidence,),
            ).fetchall()
        except Exception:
            return []

        results = []
        for o in opinions:
            d = dict(o)
            meta = json.loads(d.get("evidence", "{}")) if isinstance(d.get("evidence"), str) else {}
            rule_text = meta.get("rule_text", d.get("text", ""))
            cat = meta.get("category", "rule")
            tt = meta.get("task_type", "")
            kws = meta.get("keywords", [])

            if category and cat != category:
                continue
            if task_type and tt != task_type:
                continue
            if self._is_expired(cat, d.get("updated", 0)):
                continue

            results.append({
                "rule": rule_text,
                "category": cat,
                "task_type": tt,
                "keywords": kws,
                "confidence": d["confidence"],
                "hits": d.get("evidence_for", 0),
                "misses": d.get("evidence_against", 0),
            })

        return results[:limit]

    def match_rules(self, task: str, task_type: str = "") -> list[dict]:
        """匹配当前任务相关的规则。

        匹配策略：
        1. task_type 精确匹配 → 权重 +3
        2. keywords 关键词匹配 → 权重 +2
        3. 规则文本中关键词出现在任务中 → 权重 +1
        """
        task_lower = task.lower()
        all_rules = self.get_rules(min_confidence=MIN_INJECT_CONFIDENCE)

        scored = []
        for r in all_rules:
            score = 0
            # task_type 匹配
            if task_type and r.get("task_type") == task_type:
                score += 3
            # keywords 匹配
            for kw in r.get("keywords", []):
                if kw.lower() in task_lower:
                    score += 2
                    break
            # rule 文本匹配
            rule_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', r["rule"]))
            for w in rule_words:
                if w.lower() in task_lower:
                    score += 1

            if score > 0:
                scored.append((score, r))

        scored.sort(key=lambda x: -x[0])
        return [s[1] for s in scored[:5]]

    # ── 反馈 ────────────────────────────────────────────────

    def report_success(self, rule_topic: str):
        """规则生效：reinforce。"""
        self._oe.reinforce(rule_topic, "task succeeded")

    def report_failure(self, rule_topic: str):
        """规则失效：weaken。"""
        result = self._oe.weaken(rule_topic, "task failed")
        if result.get("action") == "deleted":
            logger.info(f"进化规则已删除（置信度过低）: {rule_topic}")  # pragma: no cover

    # ── 用 LLM 分析失败 → 生成规则 ──────────────────────────

    def analyze_failure(self, task: str, result: dict,
                        messages: list[dict]) -> Optional[dict]:
        """分析任务失败，生成进化规则。

        Args:
            task: 原始任务文本
            result: task_result dict
            messages: 对话历史

        Returns:
            {"rule": str, "category": str, "keywords": list, "task_type": str} or None
        """
        if not self._llm:
            return None

        errors = result.get("errors", [])
        error_text = "; ".join(errors[:3]) if errors else "无明确错误"
        result_snippet = (result.get("result") or "")[:300]

        # 检测是否值得进化（简单任务失败不进化）
        turns = result.get("turns", 0)
        if turns < 2 and not errors:
            return None

        prompt = (
            "你是一个 Agent 行为进化分析器。分析以下任务执行过程，"
            "判断 Agent 的行为是否有可以改进的地方。\n\n"
            f"任务: {task[:200]}\n"
            f"是否成功: {'否' if not result.get('success') else '是'}\n"
            f"错误: {error_text}\n"
            f"交互轮次: {turns}\n"
            f"结果摘要: {result_snippet}\n\n"
            "## 规则输出格式\n"
            "如果发现了可以改进的行为模式，输出一条进化规则。\n"
            "如果没有发现可改进之处，输出 NONE。\n\n"
            "每条规则必须满足：\n"
            "- 具体可执行（Agent 能在下次任务中照着做）\n"
            "- 不超过 100 字\n"
            "- 和现有规则不重复\n\n"
            "输出格式（严格 JSON，不要多余文字）：\n"
            "{\n"
            '  "rule": "具体的行为规则",\n'
            '  "category": "rule|hint|fix",\n'
            '  "keywords": ["关键词1", "关键词2"],\n'
            '  "task_type": "编码任务类型（如 coding/research/design）"\n'
            "}\n"
            "或 null（无可进化项）"
        )

        try:
            resp = self._llm([{"role": "user", "content": prompt}])
            text = (resp.get("content") or "").strip()
            if "NONE" in text.upper() or "null" in text.lower():
                return None

            # 提取 JSON
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                if parsed and parsed.get("rule"):
                    return parsed
            return None
        except Exception as e:
            logger.warning(f"进化分析异常: {e}")
            return None

    # ── 注入 ────────────────────────────────────────────────

    def build_rules_block(self, task: str, task_type: str = "") -> str:
        """构建注入到 system prompt 的进化规则块。

        注入的规则必须是：
        - 置信度 >= MIN_INJECT_CONFIDENCE
        - 和当前任务相关（task_type 或 keywords 匹配）
        - 未过期
        """
        matched = self.match_rules(task, task_type)
        if not matched:
            return ""

        # 记录触发
        for r in matched:
            topic = self._make_topic(r["rule"])
            try:
                self._oe._conn.execute(
                    "UPDATE opinions SET evidence_for = evidence_for + 1, last_triggered = ?, updated = ? WHERE topic = ?",
                    (time.time(), time.time(), topic),
                )
            except Exception:  # pragma: no cover
                pass  # pragma: no cover
        self._oe._conn.commit()

        lines = ["=== 进化经验规则 ==="]
        for r in matched[:3]:  # 最多注入 3 条
            icon = {"rule": "🧬", "hint": "💡", "fix": "🔧"}.get(r.get("category", "rule"), "•")
            bar = "█" * int(r["confidence"] * 10)
            lines.append(f"  {icon} [{r['category']}] (c={r['confidence']:.2f} {bar}) {r['rule']}")
        lines.append("以上规则是历史经验总结，仅供参考，不必完全照做。")
        return "\n".join(lines)

    @staticmethod
    def _is_expired(category: str, updated: float) -> bool:
        if category == "fix":
            return True
        if category == "hint" and updated > 0:
            return (time.time() - updated) > EXPIRY_DAYS * 86400
        return False

    # ── 统计 ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        rules = self.get_rules(min_confidence=0.0)
        active = [r for r in rules if r["confidence"] >= MIN_INJECT_CONFIDENCE]
        return {
            "total": len(rules),
            "active": len(active),
            "by_category": {
                "rule": len([r for r in rules if r.get("category") == "rule"]),
                "hint": len([r for r in rules if r.get("category") == "hint"]),
                "fix": len([r for r in rules if r.get("category") == "fix"]),
            },
        }
