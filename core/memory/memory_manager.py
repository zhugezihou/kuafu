"""
memory_manager.py — 夸父记忆系统 v2（去重存储 + 主动读取）

v2 相对 v1 的改进：
  1. 移除 EncodingGate 三信号（过于保守，大部分写入被过滤）
  2. 统一所有记忆走 SQLite FTS5（废弃 168个 json + reflections.json）
  3. 新增主动事实注入：build_memory_block 自动检索高价值事实
  4. 新增结构化事实表（facts），用于主动读取
  5. 降低去重阈值，只做内容哈希去重 + 冷却期

架构：
  CacheRing (L0)    ← 当前 session 热点记忆，总是注入
  SQLiteFTS (L1)    ← 所有持久记忆，FTS5 全文检索
  Facts (L1b)       ← 提取的结构化事实（preference/decision/lesson）
"""

import json
import time
from pathlib import Path
from typing import Optional

from core.memory.sqlite_backend import SQLiteFTSBackend
from core.memory.episodic_buffer import EpisodicBuffer

DEFAULT_CACHE_CAPACITY = 20
DEFAULT_EPISODIC_MAX = 30
DEFAULT_TTL_DAYS = 90  # 延长到 90 天


class CacheRing:
    """L0 缓存环：当前 session 的热点记忆。"""

    def __init__(self, max_entries: int = DEFAULT_CACHE_CAPACITY):
        self.max_entries = max_entries
        self._items: list[dict] = []

    def add(self, content: str, source: str = "", tags: list[str] = None):
        for item in self._items:
            if item.get("content", "") == content:
                item["timestamp"] = time.time()
                self._items.remove(item)
                self._items.append(item)
                return

        self._items.append({
            "content": content[:500],
            "source": source,
            "tags": tags or [],
            "timestamp": time.time(),
        })
        if len(self._items) > self.max_entries:
            self._items.pop(0)

    def remove(self, content: str):
        self._items = [x for x in self._items if x.get("content", "") != content]

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
            src = item.get("source", "")
            tags = item.get("tags", [])
            tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
            src_str = f" ({src})" if src else ""
            lines.append(f"  {i}. {c}{src_str}{tag_str}")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._items)


class MemoryManager:
    """夸父记忆管理器 — v2（去重存储 + 主动读取）。"""

    def __init__(self, db_path: Optional[Path] = None,
                 cache_capacity: int = DEFAULT_CACHE_CAPACITY,
                 episodic_max: int = DEFAULT_EPISODIC_MAX):
        # L1: SQLite FTS5 长期存储（包含 facts 表）
        self._longterm = SQLiteFTSBackend(db_path)
        self._init_facts_table()

        # L0: 热点缓存环
        self._cache = CacheRing(max_entries=cache_capacity)

        # L1b: 短期事件缓冲
        self._episodic = EpisodicBuffer(max_entries=episodic_max)

        # 冷却期：同一 source 30s 内不重复写入
        self._cooldown: dict[str, float] = {}

        # 统计
        self._total_stored = 0
        self._total_dedup = 0

    # ── Facts 表初始化 ────────────────────────────────────────────────

    def _init_facts_table(self):
        """facts 表：存储提取的结构化事实，用于主动注入。"""
        conn = self._longterm._conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                fact TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                source TEXT DEFAULT '',
                importance REAL DEFAULT 0.7,
                timestamp REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_accessed REAL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_category
            ON facts(category, importance DESC)
        """)
        # 事实 FTS（支持关键词搜索）
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                fact, category, source,
                tokenize='unicode61 tokenchars ''-/_#@.'''
            )
        """)
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, fact, category, source)
                VALUES (new.rowid, new.fact, new.category, new.source);
            END;
            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, fact, category, source)
                VALUES ('delete', old.rowid, old.fact, old.category, old.source);
            END;
        """)
        conn.commit()

    # ── 写入 ──────────────────────────────────────────────────────────

    def store(self, content: str, context: str = "", source: str = "",
              tags: list[str] = None, importance: float = 0.5,
              bypass_gate: bool = False) -> str:
        """存储一条记忆。

        v2 简化：将 EncodingGate 降级为简单去重 + 冷却期。
        bypass_gate=True 时强制写入（绕过冷却/去重）。
        """
        if not content or len(content.strip()) < 5:
            return "gated"

        # 冷却期检查（同一 source 30s 内不重复）
        if not bypass_gate and source:
            last = self._cooldown.get(source, 0)
            if time.time() - last < 30:
                return "gated_cooldown"

        # 写入 SQLite（自带内容哈希去重）
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

        # 高重要性（>=0.7）自动加入缓存和事实表
        if importance >= 0.7:
            self._cache.add(content, source=source, tags=tags)
            self._store_fact(content, category=source or "general",
                             source=source, importance=importance)

        # 事件缓冲
        self._episodic.add_event(source or "memory", content,
                                 source=source, importance=importance)

        return mem_id

    def _store_fact(self, fact: str, category: str = "general",
                    source: str = "", importance: float = 0.7):
        """存储一条结构化事实。"""
        conn = self._longterm._conn
        import os
        fact_id = f"fact_{int(time.time() * 1000)}_{os.urandom(2).hex()}"

        # 去重：内容相同的 facts 不重复写入
        existing = conn.execute(
            "SELECT id FROM facts WHERE fact = ?", (fact,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE facts SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                (time.time(), existing[0])
            )
            conn.commit()
            return

        conn.execute(
            "INSERT INTO facts (id, fact, category, source, importance, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (fact_id, fact, category, source, importance, time.time())
        )
        conn.commit()

    # ── 快捷写入 ──────────────────────────────────────────────────────

    def store_preference(self, content: str) -> str:
        return self.store(content, source="preference", tags=["preference"], importance=0.85, bypass_gate=True)

    def store_decision(self, content: str) -> str:
        return self.store(content, source="decision", tags=["decision"], importance=0.8, bypass_gate=True)

    def store_lesson(self, content: str) -> str:
        return self.store(content, source="lesson", tags=["lesson"], importance=0.9, bypass_gate=True)

    def store_fact(self, content: str, category: str = "general") -> str:
        return self.store(content, source=category, tags=["fact"], importance=0.7, bypass_gate=True)

    # ── 检索 ──────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 5, min_importance: float = 0.0,
               source: str = "", include_cache: bool = True) -> list[dict]:
        """搜索记忆（L1 + L0）。"""
        results = []
        longterm_results = self._longterm.search(
            query, limit=limit, min_importance=min_importance, source=source
        )
        results.extend(longterm_results)

        if include_cache and len(results) < limit:
            q = query.lower()
            for item in reversed(self._cache._items):
                c = item.get("content", "").lower()
                if q in c and item not in results:
                    results.append({
                        "id": "cache",
                        "content": item.get("content", ""),
                        "source": item.get("source", ""),
                        "tags": item.get("tags", []),
                        "final_score": 0.9,
                        "time_decay": 1.0,
                    })
                    if len(results) >= limit:
                        break
        return results[:limit]

    def search_facts(self, query: str = "", limit: int = 5, category: str = "") -> list[dict]:
        """搜索结构化事实。"""
        conn = self._longterm._conn

        if category:
            rows = conn.execute(
                """SELECT * FROM facts WHERE category = ?
                   ORDER BY importance DESC, timestamp DESC LIMIT ?""",
                (category, limit)
            ).fetchall()
        elif query:
            # FTS5 搜索
            fts_q = " AND ".join(f'"{w}"*' for w in query.split() if len(w) > 1)
            if fts_q:
                try:
                    rows = conn.execute(
                        """SELECT f.* FROM facts_fts fts
                           JOIN facts f ON f.rowid = fts.rowid
                           WHERE facts_fts MATCH ?
                           ORDER BY f.importance DESC LIMIT ?""",
                        (fts_q, limit)
                    ).fetchall()
                except Exception:
                    rows = []
            else:
                rows = conn.execute(
                    "SELECT * FROM facts ORDER BY importance DESC, timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM facts ORDER BY importance DESC, timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()

        return [dict(r) for r in rows]

    def reflect(self, query: str) -> str:
        """综合推理：搜索 + 组织。"""
        results = self.search(query, limit=5)
        facts = self.search_facts(query, limit=3)

        if not results and not facts:
            return f"关于「{query}」没有找到相关记忆。"

        lines = [f"关于「{query}」找到 {len(results)} 条相关记忆："]
        for i, r in enumerate(results, 1):
            c = r.get("content", "")[:300]
            src = r.get("source", "")
            lines.append(f"\n{i}. {c} [{src}]" if src else f"\n{i}. {c}")

        if facts:
            lines.append(f"\n相关事实：")
            for f in facts[:3]:
                lines.append(f"  • {f['fact'][:200]} [{f['category']}]")

        return "\n".join(lines)

    # ── Prompt 注入（核心读取路径） ────────────────────────────────────

    def build_memory_block(self, budget_ratio: float = 1.0,
                           include_search: str = "") -> str:
        """构建注入到 system prompt 的记忆块。

        三层合并：
        1. 热点缓存（L0）— 总是注入
        2. 关键事实（facts）— 按 importance 排序主动注入
        3. 按需检索（L1）— 有关键词时触发
        """
        parts = []

        # L0: 热点缓存
        cache_block = self._cache.build_prompt_block(budget_ratio)
        if cache_block:
            parts.append(cache_block)

        # L1b: 主动事实注入（L0 缓存 < 5 条时补充）
        if self._cache.count() < 5:
            facts = self.search_facts(limit=3)
            if facts:
                f_lines = ["=== 关键事实 ==="]
                for f in facts:
                    importance_mark = "⭐" if f["importance"] >= 0.85 else "•"
                    f_lines.append(f"  {importance_mark} [{f['category']}] {f['fact'][:200]}")
                parts.append("\n".join(f_lines))

        # L1: 按需检索
        if include_search:
            search_results = self.search(include_search, limit=3)
            if search_results:
                sr_lines = [f"=== 相关记忆（搜索: {include_search}） ==="]
                for r in search_results:
                    c = r.get("content", "")[:200]
                    src = r.get("source", "")
                    src_str = f" [{src}]" if src else ""
                    sr_lines.append(f"  • {c}{src_str}")
                parts.append("\n".join(sr_lines))

        return "\n\n".join(parts)

    # ── Session 管理 ──────────────────────────────────────────────────

    def new_session(self):
        self._cache.clear()
        self._episodic.clear()

    def add_episodic_event(self, event_type: str, content: str,
                           source: str = "", importance: float = 0.5):
        self._episodic.add_event(event_type, content, source, importance)

    def cache_hot(self, content: str, source: str = "", tags: list[str] = None):
        self._cache.add(content, source, tags)

    # ── 维护 ──────────────────────────────────────────────────────────

    def maintenance(self) -> dict:
        expired = self._longterm.delete_expired()
        stats = self._longterm.get_stats()
        return {
            "expired": expired,
            "merged": 0,
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
        return {
            "cache_count": self._cache.count(),
            "facts_count": fact_count,
            "episodic": self._episodic.get_stats(),
            "longterm": longterm_stats,
            "total_stored": self._total_stored,
            "total_dedup": self._total_dedup,
        }

    # ── 兼容旧接口 ──────────────────────────────────────────────────

    def remember(self, key: str, content: str, tags: list = None) -> str:
        return self.store(content, source=key, tags=tags)

    def recall(self, query: str, limit: int = 10) -> list[dict]:
        return self.search(query, limit=limit)

    # ── 工具模式（供 AgentLoop 使用） ────────────────────────────────

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_store",
                "description": "存储一条重要信息到长期记忆。适合记录：用户偏好、项目决策、经验教训、配置信息等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "要记住的内容"},
                        "source": {"type": "string", "description": "类别：preference/decision/lesson/fact/config"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "搜索历史记忆。支持全文检索。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "limit": {"type": "integer", "description": "返回结果数上限", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_reflect",
                "description": "基于所有记忆做综合推理。",
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
            source = args.get("source", "")
            tags = args.get("tags")
            if not content:
                return json.dumps({"error": "content 不能为空"})
            result = self.store(content, source=source, tags=tags)
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
