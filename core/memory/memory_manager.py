"""
memory_manager.py — 夸父记忆系统 v3（Hindsight-Lite 四网络 + 置信度演化）

v3 相对 v2 的改进：
  1. 四网络分层：World / Experience / Observation / Opinion
  2. Opinion 置信度演化：store() 时自动 reinforce/weaken/contradict
  3. LLM 萃取事实类型（降级到 rule-based 检测）
  4. build_memory_block 带 [World]/[Opinion(c=0.85)] 标签注入
  5. reflect() 调用 LLM 推理，返回观点更新

架构：
  CacheRing (L0)     ← 当前 session 热点
  NetworkStore (L1)   ← 四网络存储（World/Experience/Observation）
  OpinionEngine (L1b) ← 信念管理 + 置信度演化
  EpisodicBuffer      ← 短期事件
"""

import json
import time
from pathlib import Path
from typing import Any, Optional

from core.memory.sqlite_backend import SQLiteFTSBackend
from core.memory.episodic_buffer import EpisodicBuffer
from core.memory.hindsight_lite import (
    NetworkStore, OpinionEngine,
    NETWORK_WORLD, NETWORK_EXPERIENCE, NETWORK_OBSERVATION, NETWORK_OPINION,
    detect_fact_type,
)

DEFAULT_CACHE_CAPACITY = 20
DEFAULT_EPISODIC_MAX = 30


class CacheRing:
    """L0 缓存环：当前 session 的热点记忆。"""

    def __init__(self, max_entries: int = DEFAULT_CACHE_CAPACITY):
        self.max_entries = max_entries
        self._items: list[dict] = []

    def add(self, content: str, source: str = "", tags: list[str] = None,
            network: str = "", confidence: float = 1.0):
        for item in self._items:
            if item.get("content", "") == content:
                item["timestamp"] = time.time()
                item["network"] = network
                item["confidence"] = confidence
                self._items.remove(item)
                self._items.append(item)
                return
        self._items.append({
            "content": content[:500], "source": source, "tags": tags or [],
            "network": network, "confidence": confidence,
            "timestamp": time.time(),
        })
        if len(self._items) > self.max_entries:
            self._items.pop(0)

    def clear(self):
        self._items.clear()

    def build_prompt_block(self, budget_ratio: float = 1.0) -> str:
        if not self._items:
            return ""
        items = list(reversed(self._items))
        if budget_ratio < 0.5:
            limit = max(3, int(len(items) * budget_ratio * 2))
            items = items[:limit]

        lines = [f"=== 热点记忆 ({len(items)} 条) ==="]
        for i, item in enumerate(items, 1):
            c = item.get("content", "")[:200]
            net = item.get("network", "")
            conf = item.get("confidence", 1.0)
            tag_str = ""
            if net == NETWORK_OPINION:
                tag_str = f" [Opinion(c={conf:.2f})]"
            elif net:
                tag_str = f" [{net.capitalize()}]"
            lines.append(f"  {i}. {c}{tag_str}")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._items)


class MemoryManager:
    """夸父记忆管理器 — v3（Hindsight-Lite）。"""

    def __init__(self, db_path: Optional[Path] = None,
                 cache_capacity: int = DEFAULT_CACHE_CAPACITY,
                 episodic_max: int = DEFAULT_EPISODIC_MAX,
                 llm_chat_fn: Optional[callable] = None):
        # L1: SQLite 后端
        self._longterm = SQLiteFTSBackend(db_path)
        self._init_hindsight_tables()

        # Hindsight 组件
        self._networks = NetworkStore(self._longterm._conn)
        self._opinions = OpinionEngine(self._longterm._conn)

        # L0 + L1b
        self._cache = CacheRing(max_entries=cache_capacity)
        self._episodic = EpisodicBuffer(max_entries=episodic_max)

        # LLM 萃取（可选）
        self._llm_chat = llm_chat_fn

        # 冷却期
        self._cooldown: dict[str, float] = {}
        self._total_stored = 0
        self._total_dedup = 0

    # ── Hindsight 表初始化 ───────────────────────────────────────

    def _init_hindsight_tables(self):
        conn = self._longterm._conn

        # Opinions 表（信念管理）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS opinions (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                text TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                evidence_for INTEGER DEFAULT 0,
                evidence_against INTEGER DEFAULT 0,
                created REAL NOT NULL,
                updated REAL NOT NULL,
                evidence TEXT DEFAULT '{}',
                deleted INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opinions_confidence
            ON opinions(confidence DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opinions_topic
            ON opinions(topic)
        """)
        # Opinion FTS 已移除（用 LIKE 搜索代替，避免 FTS trigger 复杂性）
        # facts 表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                fact TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'experience',
                source TEXT DEFAULT '',
                importance REAL DEFAULT 0.5,
                timestamp REAL NOT NULL,
                entity TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_category
            ON facts(category)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_importance
            ON facts(importance DESC)
        """)
        # FTS5 全文索引（如果不存在）
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                    fact, content='facts', content_rowid='rowid'
                )
            """)
        except Exception:
            pass  # FTS5 可能不可用
        # entity 列迁移（兼容旧库）
        try:
            conn.execute("ALTER TABLE facts ADD COLUMN entity TEXT DEFAULT ''")
        except Exception:
            pass

        conn.commit()

    # ── 写入（Hindsight 增强） ────────────────────────────────────

    def store(self, content: str, context: str = "", source: str = "",
              tags: list[str] = None, importance: float = 0.5,
              bypass_gate: bool = False) -> str:
        """存储一条记忆。

        v3 增强：
        1. 检测事实类型（World/Experience/Opinion）
        2. 若为 Opinion，触发置信度演化
        3. 高重要性写入 Observation 合并
        """
        if not content or len(content.strip()) < 5:
            return "gated"

        # 冷却期
        if not bypass_gate and source:
            last = self._cooldown.get(source, 0)
            if time.time() - last < 30:
                return "gated_cooldown"

        # 写入 SQLite（基础存储）
        mem_id = self._longterm.store(
            content, context=context, source=source,
            tags=tags, importance=importance,
        )
        if mem_id.endswith("_dedup"):
            self._total_dedup += 1
            return "gated_dedup"

        self._total_stored += 1
        if source:
            self._cooldown[source] = time.time()

        # ── Hindsight：事实类型检测 + 分网络存储 ──
        fact_type = self._detect_or_classify(content, context, source)

        if fact_type == NETWORK_OPINION or importance >= 0.8:
            # 高重要性 → 形成或强化 Opinion
            topic = source or content[:50]
            result = self._opinions.reinforce(topic, content)
            self._cache.add(content, source=source, tags=tags,
                            network=NETWORK_OPINION, confidence=result.get("confidence", 0.7))
        else:
            # 客观事实 → World / Experience
            entity = tags[0] if tags and len(tags) > 0 else ""
            self._networks.store(content, network=fact_type, entity=entity, importance=importance)
            self._cache.add(content, source=source, tags=tags, network=fact_type)

            # Observation 合并（同一 entity）
            if entity:
                self._networks.merge_observation(entity, content)

        # EpisodicBuffer
        self._episodic.add_event(source or "memory", content, source=source, importance=importance)

        return mem_id

    def _detect_or_classify(self, content: str, context: str = "",
                            source: str = "") -> str:
        """检测事实类型。先用 LLM（如有），降级到 rule-based。"""
        # 明确 source 的偏好/决策/教训直接走 Opinion
        if source in ("preference", "decision", "lesson", "opinion"):
            return NETWORK_OPINION

        # 有 LLM 时尝试萃取
        if self._llm_chat:
            try:
                return self._llm_classify(content)
            except Exception:
                pass

        # 降级到规则
        return detect_fact_type(content)

    def _llm_classify(self, content: str) -> str:
        """用 LLM 判断事实类型。返回网络名。"""
        prompt = (
            "判断以下内容的类型，只输出一个词：\n"
            "  world - 客观事实陈述\n"
            "  experience - Agent 的个人经历或操作\n"
            "  opinion - 主观判断、偏好、建议、信念\n\n"
            f"内容: {content[:200]}\n\n"
            "输出:"
        )
        resp = self._llm_chat([{"role": "user", "content": prompt}])
        text = (resp.get("content") or "").strip().lower()
        if "opinion" in text:
            return NETWORK_OPINION
        if "experience" in text:
            return NETWORK_EXPERIENCE
        return NETWORK_WORLD

    # ── 快捷写入 ──────────────────────────────────────────────────

    def store_preference(self, content: str) -> str:
        return self.store(content, source="preference", tags=["preference"],
                          importance=0.85, bypass_gate=True)

    def store_decision(self, content: str) -> str:
        return self.store(content, source="decision", tags=["decision"],
                          importance=0.8, bypass_gate=True)

    def store_lesson(self, content: str) -> str:
        return self.store(content, source="lesson", tags=["lesson"],
                          importance=0.9, bypass_gate=True)

    # ── 检索 ──────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 5, min_importance: float = 0.0,
               source: str = "", include_cache: bool = True) -> list[dict]:
        results = self._longterm.search(query, limit=limit, min_importance=min_importance, source=source)
        if include_cache and len(results) < limit:
            q = query.lower()
            for item in reversed(self._cache._items):
                c = item.get("content", "").lower()
                if q in c:
                    results.append({
                        "id": "cache", "content": item.get("content", ""),
                        "source": item.get("source", ""), "tags": item.get("tags", []),
                        "final_score": 0.9, "time_decay": 1.0,
                        "network": item.get("network", ""),
                        "confidence": item.get("confidence", 1.0),
                    })
                    if len(results) >= limit:
                        break
        return results[:limit]

    def search_opinions(self, query: str, limit: int = 5) -> list[dict]:
        return self._opinions.search_opinions(query, limit=limit)

    # ── Build Memory Block（Hindsight 增强读取） ──────────────────

    def build_memory_block(self, budget_ratio: float = 1.0,
                           include_search: str = "") -> str:
        """构建注入到 system prompt 的记忆块（Hindsight 四网络标签）。"""
        parts = []

        # L0: 热点缓存
        cache_block = self._cache.build_prompt_block(budget_ratio)
        if cache_block:
            parts.append(cache_block)

        # World + Experience 事实
        if self._cache.count() < 5:
            world = self._networks.search(NETWORK_WORLD, query=include_search, limit=2)
            exp = self._networks.search(NETWORK_EXPERIENCE, query=include_search, limit=2)
            obs = self._networks.get_observations(limit=2)

            fact_lines = []
            for f in world:
                fact_lines.append(f"  [World] {f['fact'][:200]}")
            for f in exp:
                fact_lines.append(f"  [Experience] {f['fact'][:200]}")
            for f in obs:
                fact_lines.append(f"  [Observation] {f['fact'][:200]}")
            if fact_lines:
                parts.append("=== 世界事实 ===\n" + "\n".join(fact_lines))

        # Opinion（带置信度）
        opinions = self._opinions.get_opinions(limit=3, min_confidence=0.3)
        if opinions:
            op_lines = ["=== 当前信念 ==="]
            for o in opinions:
                bar = "█" * int(o["confidence"] * 10) + "░" * (10 - int(o["confidence"] * 10))
                op_lines.append(f"  [Opinion(c={o['confidence']:.2f}) {bar}] {o['text'][:200]}")
            parts.append("\n".join(op_lines))

        # FTS5 按需检索
        if include_search:
            search_results = self._longterm.search(include_search, limit=3)
            if search_results:
                sr_lines = [f"=== 相关记忆（搜索: {include_search}） ==="]
                for r in search_results:
                    c = r.get("content", "")[:200]
                    parts.append("  • " + c)
                parts.append("\n".join(sr_lines))

        return "\n\n".join(parts)

    # ── Reflect（推理链路） ──────────────────────────────────────

    def reflect(self, query: str) -> str:
        """v3 增强：在搜索结果基础上用 LLM 推理 + 更新 Opinion。

        如果 LLM 不可用，回退到 v2 的拼接模式。
        """
        if not self._llm_chat:
            return self._reflect_fallback(query)

        # 1. 检索
        facts = self._longterm.search(query, limit=5)
        opinions = self._opinions.search_opinions(query, limit=3)
        world = self._networks.search(NETWORK_WORLD, query=query, limit=2)
        exp = self._networks.search(NETWORK_EXPERIENCE, query=query, limit=2)

        # 2. 构建推理 prompt
        context_parts = []
        if facts:
            context_parts.append("## 相关记忆")
            for f in facts:
                context_parts.append(f"- {f['content'][:200]}")
        if world:
            context_parts.append("## 客观事实")
            for f in world:
                context_parts.append(f"- {f['fact'][:200]}")
        if exp:
            context_parts.append("## 经历")
            for f in exp:
                context_parts.append(f"- {f['fact'][:200]}")
        if opinions:
            context_parts.append("## 已有信念（带置信度）")
            for o in opinions:
                context_parts.append(f"- [{o['confidence']:.2f}] {o['text'][:200]}")

        prompt = (
            "你是一个 AI 记忆推理模块。基于以下记忆回答用户问题。\n\n"
            f"{chr(10).join(context_parts)}\n\n"
            "## 任务\n"
            f"问题: {query}\n\n"
            "请按以下格式输出（不要多余文字）：\n"
            "REASONING: <你的推理过程>\n"
            "ANSWER: <最终答案>\n"
            "OPINION: <如有新的信念形成或已有信念需要更新，写 topic=信念文本 confidence=0.x>，无则写 NONE\n"
        )

        try:
            resp = self._llm_chat([{"role": "user", "content": prompt}])
            text = (resp.get("content") or "").strip()

            # 3. 解析 Opinion 更新
            for line in text.split("\n"):
                if line.startswith("OPINION:") and "NONE" not in line:
                    try:
                        opinion_text = line[8:].strip()
                        # 尝试解析 topic=xxx confidence=0.x
                        import re as _re
                        m = _re.search(r'topic=([^ ]+(?: [^ ]+)*?)\s+confidence=([0-9.]+)', opinion_text)
                        if m:
                            topic = m.group(1).strip()
                            conf = float(m.group(2))
                            self._opinions.reinforce(topic, f"reflect: {query}")
                    except Exception:
                        pass
                    break

            # 4. 提取 ANSWER 部分
            answer = text
            if "ANSWER:" in text:
                answer = text.split("ANSWER:", 1)[1].strip()
            return answer

        except Exception as e:
            return self._reflect_fallback(query)

    def _reflect_fallback(self, query: str) -> str:
        """无 LLM 时的回退：拼接搜索结果为文本。"""
        results = self._longterm.search(query, limit=5)
        opinions = self._opinions.search_opinions(query, limit=3)

        if not results and not opinions:
            return f"关于「{query}」没有找到相关记忆。"

        lines = [f"关于「{query}」找到 {len(results)} 条相关记忆："]
        for i, r in enumerate(results, 1):
            c = r.get("content", "")[:300]
            src = r.get("source", "")
            lines.append(f"\n{i}. {c} [{src}]" if src else f"\n{i}. {c}")

        if opinions:
            lines.append("\n相关信念：")
            for o in opinions:
                lines.append(f"  [{o['confidence']:.2f}] {o['text'][:200]}")

        return "\n".join(lines)

    # ── Session 管理 ──────────────────────────────────────────────

    def new_session(self):
        self._cache.clear()
        self._episodic.clear()

    def add_episodic_event(self, event_type: str, content: str,
                           source: str = "", importance: float = 0.5):
        self._episodic.add_event(event_type, content, source, importance)

    def cache_hot(self, content: str, source: str = "", tags: list[str] = None,
                  network: str = "", confidence: float = 1.0):
        self._cache.add(content, source=source, tags=tags,
                        network=network, confidence=confidence)

    # ── 维护 ──────────────────────────────────────────────────────

    def maintenance(self) -> dict:
        expired = self._longterm.delete_expired()
        stats = self._longterm.get_stats()
        return {
            "expired": expired, "merged": 0,
            "total_valid": stats["valid"],
            "total_stored": self._total_stored,
            "total_dedup": self._total_dedup,
            "cache_count": self._cache.count(),
            "longterm": stats,
        }

    def get_stats(self) -> dict:
        longterm_stats = self._longterm.get_stats()
        conn = self._longterm._conn
        fact_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        opinion_count = conn.execute(
            "SELECT COUNT(*) FROM opinions WHERE deleted = 0 AND confidence >= ?",
            (0.3,),
        ).fetchone()[0]
        return {
            "cache_count": self._cache.count(),
            "facts_count": fact_count,
            "opinions_count": opinion_count,
            "episodic": self._episodic.get_stats(),
            "longterm": longterm_stats,
            "total_stored": self._total_stored,
            "total_dedup": self._total_dedup,
        }

    # ── 兼容旧接口 ──────────────────────────────────────────────

    def remember(self, key: str, content: str, tags: list = None) -> str:
        return self.store(content, source=key, tags=tags)

    def recall(self, query: str, limit: int = 10) -> list[dict]:
        return self.search(query, limit=limit)

    # ── 工具模式 ──────────────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_store",
                "description": "存储一条重要信息到长期记忆。自动分类为 World/Experience/Observation/Opinion。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "要记住的内容"},
                        "source": {"type": "string", "description": "类别"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "搜索历史记忆。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_reflect",
                "description": "基于所有记忆做推理回答。会检索相关事实、经历和已有信念，形成带置信度的回答。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "要推理的问题"},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if tool_name == "memory_store":
            content = args.get("content", "")
            if not content:
                return json.dumps({"error": "content 不能为空"})
            result = self.store(content, source=args.get("source", ""), tags=args.get("tags"))
            if result.startswith("gated"):
                return json.dumps({"result": "信息已存在或重复，跳过存储"})
            return json.dumps({"result": "记忆已存储"})

        elif tool_name == "memory_search":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            if not query:
                return json.dumps({"error": "query 不能为空"})
            results = self.search(query, limit=limit)
            if not results:
                return json.dumps({"result": "没有找到相关记忆。"})
            lines = [f"{i+1}. {r['content'][:200]}" for i, r in enumerate(results)]
            return json.dumps({"result": "\n".join(lines)})

        elif tool_name == "memory_reflect":
            query = args.get("query", "")
            if not query:
                return json.dumps({"error": "query 不能为空"})
            answer = self.reflect(query)
            return json.dumps({"result": answer})

        return json.dumps({"error": f"未知记忆工具: {tool_name}"})
