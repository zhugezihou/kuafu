"""
memory/hindsight_lite.py — Hindsight-Lite 四网络记忆系统

Hindsight 论文核心思想的轻量实现：
1. 四网络：World / Experience / Observation / Opinion
2. Opinion 置信度演化：reinforce/weaken/contradict
3. 事实类型 LLM 萃取（可选，降级到规则分类）
4. 无 embedding、无图遍历、无多路检索

设计原则：
- 零外部依赖（纯 SQLite）
- LLM 萃取可降级（不用 LLM 也能跑，只是不分网络）
- 置信度演化是核心价值
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("kuafu.hindsight_lite")

# ── 网络类型 ───────────────────────────────────────────────

NETWORK_WORLD = "world"           # 客观事实
NETWORK_EXPERIENCE = "experience"  # Agent 自身经历
NETWORK_OBSERVATION = "observation" # 实体摘要（合并的事实）
NETWORK_OPINION = "opinion"        # 主观信念

ALL_NETWORKS = [NETWORK_WORLD, NETWORK_EXPERIENCE, NETWORK_OBSERVATION, NETWORK_OPINION]

# ── 置信度更新常量 ─────────────────────────────────────────

REINFORCE_STEP = 0.10   # 新证据支持 → +0.1
WEAKEN_STEP = 0.08      # 新证据弱反对 → -0.08
CONTRADICT_STEP = 0.15  # 新证据强烈反对 → -0.15
MAX_CONFIDENCE = 0.99
MIN_CONFIDENCE = 0.05
OPINION_FORM_THRESHOLD = 0.60  # 置信度超过此值才形成 Opinion
OPINION_RETAIN_THRESHOLD = 0.15  # 低于此值删除 Opinion

# ── 事实类型检测关键词 ─────────────────────────────────────

_FACT_TYPE_KEYWORDS = {
    NETWORK_WORLD: [
        "是", "有", "存在", "位于", "属于", "包含",
        "is", "are", "was", "were", "has", "have",
        "located", "contains", "consists",
    ],
    NETWORK_EXPERIENCE: [
        "我", "帮我", "我帮", "我创建", "我写", "我部署",
        "我修复", "我改", "我实现", "我配置",
        "I", "we", "i created", "i wrote", "i deployed",
        "i fixed", "i implemented",
    ],
    NETWORK_OPINION: [
        "觉得", "认为", "建议", "推荐", "最好", "更喜欢",
        "think", "believe", "suggest", "recommend", "prefer",
        "better", "best", "should",
    ],
}


def detect_fact_type(content: str) -> str:
    """规则检测事实类型（不用 LLM 时的降级方案）。"""
    if not content:
        return NETWORK_WORLD
    lower = content.lower()

    # Opinion 优先（含主观判断）
    for kw in _FACT_TYPE_KEYWORDS.get(NETWORK_OPINION, []):
        if kw in lower:
            return NETWORK_OPINION

    # Experience
    for kw in _FACT_TYPE_KEYWORDS.get(NETWORK_EXPERIENCE, []):
        if kw in lower:
            return NETWORK_EXPERIENCE

    return NETWORK_WORLD


# ── Opinion 置信度演化引擎 ─────────────────────────────────


class OpinionEngine:
    """信念管理：置信度动态调整。

    每条 Opinion:
      - text: 信念文本
      - confidence: [0,1] 置信度
      - evidence_for: 支持证据数
      - evidence_against: 反对证据数
      - created: 创建时间
      - updated: 最后更新时间
    """

    def __init__(self, conn: Any):
        self._conn = conn

    def get_opinions(self, limit: int = 10, min_confidence: float = 0.0) -> list[dict]:
        """获取当前有效 Opinion，按置信度降序。"""
        try:
            rows = self._conn.execute(
                """SELECT * FROM opinions
                   WHERE confidence >= ? AND deleted = 0
                   ORDER BY confidence DESC, updated DESC
                   LIMIT ?""",
                (min_confidence, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def search_opinions(self, query: str, limit: int = 5) -> list[dict]:
        """搜索相关 Opinion（LIKE 搜索，不依赖 FTS）。"""
        try:
            rows = self._conn.execute(
                """SELECT * FROM opinions
                   WHERE (text LIKE ? OR topic LIKE ?)
                     AND deleted = 0
                   ORDER BY confidence DESC, updated DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def reinforce(self, topic: str, evidence_text: str) -> dict:
        """新证据支持信念：+confidence。"""
        existing = self._find_opinion(topic)
        if existing:
            new_c = min(existing["confidence"] + REINFORCE_STEP, MAX_CONFIDENCE)
            self._conn.execute(
                "UPDATE opinions SET confidence = ?, evidence_for = evidence_for + 1, updated = ?, evidence = ? WHERE id = ?",
                (new_c, time.time(), json.dumps({"last": evidence_text[:200]}, ensure_ascii=False), existing["id"]),
            )
            self._conn.commit()
            return {"action": "reinforced", "id": existing["id"], "confidence": new_c, "text": existing["text"]}
        else:
            # 置信度不够形成 Opinion，先记录为弱信念
            return self._create_opinion(topic, evidence_text, confidence=0.40)

    def weaken(self, topic: str, evidence_text: str) -> dict:
        """新证据弱反对：-confidence。"""
        existing = self._find_opinion(topic)
        if not existing:
            return {"action": "noop"}
        new_c = max(existing["confidence"] - WEAKEN_STEP, MIN_CONFIDENCE)
        self._conn.execute(
            "UPDATE opinions SET confidence = ?, evidence_against = evidence_against + 1, updated = ?, evidence = ? WHERE id = ?",
            (new_c, time.time(), json.dumps({"last": evidence_text[:200]}, ensure_ascii=False), existing["id"]),
        )
        self._conn.commit()
        if new_c < OPINION_RETAIN_THRESHOLD:
            self._delete_opinion(existing["id"])
            return {"action": "deleted", "id": existing["id"], "reason": "confidence too low"}
        return {"action": "weakened", "id": existing["id"], "confidence": new_c, "text": existing["text"]}

    def contradict(self, topic: str, evidence_text: str) -> dict:
        """新证据强烈反对：大幅降 confidence。"""
        existing = self._find_opinion(topic)
        if not existing:
            return {"action": "noop"}
        new_c = max(existing["confidence"] - CONTRADICT_STEP, MIN_CONFIDENCE)
        self._conn.execute(
            "UPDATE opinions SET confidence = ?, evidence_against = evidence_against + 1, updated = ?, evidence = ? WHERE id = ?",
            (new_c, time.time(), json.dumps({"last": evidence_text[:200]}, ensure_ascii=False), existing["id"]),
        )
        self._conn.commit()
        if new_c < OPINION_RETAIN_THRESHOLD:
            self._delete_opinion(existing["id"])
            return {"action": "deleted", "id": existing["id"], "reason": "confidence too low"}
        return {"action": "contradicted", "id": existing["id"], "confidence": new_c, "text": existing["text"]}

    def get_or_create(self, topic: str, initial_text: str,
                      initial_confidence: float = 0.50) -> dict:
        """获取或创建 Opinion。"""
        existing = self._find_opinion(topic)
        if existing:
            return existing
        return self._create_opinion(topic, initial_text, confidence=initial_confidence)

    def _find_opinion(self, topic: str) -> Optional[dict]:
        """按 topic 查找已有 Opinion（精确 + 模糊）。"""
        if not topic:
            return None
        try:
            row = self._conn.execute(
                "SELECT * FROM opinions WHERE topic = ? AND deleted = 0",
                (topic.strip().lower(),),
            ).fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
        return None

    def _create_opinion(self, topic: str, text: str, confidence: float = 0.50) -> dict:
        """创建一条 Opinion。"""
        import os as _os
        oid = f"op_{int(time.time() * 1000)}_{_os.urandom(2).hex()}"
        now = time.time()
        self._conn.execute(
            """INSERT INTO opinions
               (id, topic, text, confidence, evidence_for, evidence_against, created, updated, evidence)
               VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?)""",
            (oid, topic.strip().lower(), text[:500], confidence, now, now,
             json.dumps({"initial": text[:200]}, ensure_ascii=False)),
        )
        self._conn.commit()
        return {"action": "created", "id": oid, "confidence": confidence, "text": text}

    def _delete_opinion(self, oid: str):
        """软删除 Opinion。"""
        try:
            self._conn.execute("UPDATE opinions SET deleted = 1, updated = ? WHERE id = ?",
                               (time.time(), oid))
            self._conn.commit()
        except Exception:
            pass


# ── 四网络写入器 ───────────────────────────────────────────


class NetworkStore:
    """四网络存储管理。

    每个网络对应 facts 表里的一个 category 值：
      - "world" / "experience" / "observation" / "opinion"
    """

    def __init__(self, conn: Any):
        self._conn = conn

    def store(self, content: str, network: str, entity: str = "",
              importance: float = 0.7, source: str = "") -> str:
        """存入一条事实到指定网络。"""
        import os as _os
        fid = f"fact_{int(time.time() * 1000)}_{_os.urandom(2).hex()}"
        self._conn.execute(
            """INSERT INTO facts (id, fact, category, source, importance, timestamp, entity)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (fid, content[:500], network, source, importance, time.time(), entity),
        )
        self._conn.commit()
        return fid

    def search(self, network: str, query: str = "", limit: int = 3) -> list[dict]:
        """搜索指定网络的事实。"""
        try:
            if query:
                rows = self._conn.execute(
                    """SELECT f.* FROM facts_fts fts
                       JOIN facts f ON f.rowid = fts.rowid
                       WHERE facts_fts MATCH ? AND f.category = ?
                       ORDER BY f.importance DESC, f.timestamp DESC
                       LIMIT ?""",
                    (query, network, limit),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            # fallback
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY importance DESC, timestamp DESC LIMIT ?",
                (network, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_observations(self, entity: str = "", limit: int = 5) -> list[dict]:
        """获取 Observation（实体摘要）。"""
        if entity:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE category = 'observation' AND entity LIKE ? ORDER BY importance DESC LIMIT ?",
                (f"%{entity}%", limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE category = 'observation' ORDER BY importance DESC, timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def merge_observation(self, entity: str, new_fact: str) -> str:
        """合并 Observation：同一 entity 追加事实，不创建新的。"""
        existing = self._conn.execute(
            "SELECT * FROM facts WHERE category = 'observation' AND entity = ? ORDER BY timestamp DESC LIMIT 1",
            (entity,),
        ).fetchone()
        if existing:
            e = dict(existing)
            merged = f"{e['fact']}; {new_fact}"[:500]
            self._conn.execute(
                "UPDATE facts SET fact = ?, importance = MIN(importance + 0.05, 1.0), timestamp = ? WHERE id = ?",
                (merged, time.time(), e["id"]),
            )
            self._conn.commit()
            return e["id"]
        else:
            return self.store(new_fact, NETWORK_OBSERVATION, entity=entity, importance=0.6)
