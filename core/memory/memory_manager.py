"""
memory_manager.py — 夸父记忆系统核心（三层金字塔架构）

架构：
  CacheRing (L0)         ← 当前 session 的热点记忆，总是注入
  EpisodicBuffer (L1)    ← 短期事件缓冲区，超预算自动压缩
  LongTermStore (L2)     ← SQLite FTS5 全文检索，按需查询

编码门控：
  - 写入 LongTermStore 前经过 EncodingGate 三信号过滤
  - 只有 Novelty/Salience/Prediction-Error 加权 > 阈值才写入

零外部依赖：SQLite + Python 标准库
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from core.memory.sqlite_backend import SQLiteFTSBackend
from core.memory.encoding_gate import EncodingGate
from core.memory.episodic_buffer import EpisodicBuffer


# 默认配置
DEFAULT_CACHE_CAPACITY = 20  # 缓存环最大条目
DEFAULT_EPISODIC_MAX = 30    # 事件缓冲区最大条目
DEFAULT_TTL_DAYS = 30        # 默认记忆有效期
DEFAULT_GATE_THRESHOLD = 0.55  # 编码门控默认阈值


class CacheRing:
    """L0 缓存环：当前 session 的热点记忆。

    - 固定容量，FIFO 淘汰
    - 注入到 system prompt
    - 持有关键事实（用户偏好、项目决策等）
    """

    def __init__(self, max_entries: int = DEFAULT_CACHE_CAPACITY):
        self.max_entries = max_entries
        self._items: list[dict] = []

    def add(self, content: str, source: str = "", tags: list[str] = None):
        """添加热点记忆到缓存环。去重后 FIFO 淘汰。"""
        # 去重
        for item in self._items:
            if item.get("content", "") == content:
                # 更新 timestamp，提到前面
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

        # FIFO 淘汰
        if len(self._items) > self.max_entries:
            self._items.pop(0)

    def remove(self, content: str):
        """从缓存环移除"""
        self._items = [x for x in self._items if x.get("content", "") != content]

    def clear(self):
        """清空缓存环（新 session 开始）"""
        self._items.clear()

    def build_prompt_block(self, budget_ratio: float = 1.0) -> str:
        """生成注入到 system prompt 的热点记忆块。

        按最新→最旧排序。预算紧张时只保留最重要的。
        """
        if not self._items:
            return ""

        items = list(reversed(self._items))  # 最新的在前

        # 预算裁剪
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

        return '\n'.join(lines)

    def count(self) -> int:
        return len(self._items)


class MemoryManager:
    """夸父记忆管理器 — 三层金字塔统一入口。

    使用方式：
      mm = MemoryManager()
      mm.store("用户喜欢简洁回复", source="preference")
      results = mm.search("用户偏好")
      prompt = mm.build_memory_block(budget_ratio=0.7)
    """

    def __init__(self, db_path: Optional[Path] = None,
                 gate_threshold: float = DEFAULT_GATE_THRESHOLD,
                 cache_capacity: int = DEFAULT_CACHE_CAPACITY,
                 episodic_max: int = DEFAULT_EPISODIC_MAX):
        # L2: SQLite FTS5 长期存储
        self._longterm = SQLiteFTSBackend(db_path)

        # 编码门控（挂载 L2 做相似度检测）
        self._gate = EncodingGate(sqlite_backend=self._longterm)
        self._gate.set_threshold(gate_threshold)

        # L1: 短期事件缓冲区
        self._episodic = EpisodicBuffer(max_entries=episodic_max)

        # L0: 热点缓存环
        self._cache = CacheRing(max_entries=cache_capacity)

        # 统计
        self._total_stored = 0
        self._total_gated = 0  # 被门控过滤掉的

    # ── 三层写入 ────────────────────────────────────────────────────

    def store(self, content: str, context: str = "", source: str = "",
              tags: list[str] = None, importance: float = 0.5,
              bypass_gate: bool = False, to_longterm: bool = True,
              to_cache: bool = True, to_episodic: bool = True) -> str:
        """存储一条信息。

        Args:
            content: 记忆内容
            context: 上下文（可选）
            source: 来源标签（如 'preference', 'decision', 'command'）
            tags: 标签列表
            importance: 重要性（0.0~1.0）
            bypass_gate: 是否绕过编码门控（强制写入）
            to_longterm: 是否写入长期存储
            to_cache: 是否写入热点缓存
            to_episodic: 是否写入事件缓冲区

        Returns:
            记忆 ID（成功）或 'gated'（被门控过滤）
        """
        mem_id = ""

        # ── 第 1 步：写入长期存储（经过编码门控） ──
        if to_longterm:
            if bypass_gate:
                mem_id = self._longterm.store(
                    content, context=context, source=source,
                    tags=tags, importance=importance
                )
                self._total_stored += 1
            else:
                gate_result = self._gate.evaluate(content, context, source, tags)
                if gate_result["should_store"]:
                    mem_id = self._longterm.store(
                        content, context=context, source=source,
                        tags=tags, importance=importance
                    )
                    self._total_stored += 1
                else:
                    self._total_gated += 1
                    return "gated"

        # ── 第 2 步：写入短期事件缓冲区 ──
        if to_episodic:
            self._episodic.add_event(
                event_type=source or "memory",
                content=content,
                source=source,
                importance=importance,
            )

        # ── 第 3 步：写入热点缓存环 ──
        if to_cache:
            self._cache.add(content, source=source, tags=tags)

        return mem_id or "cached"

    def store_preference(self, content: str, bypass_gate: bool = False) -> str:
        """快捷方法：存储用户偏好。默认高重要性。"""
        return self.store(
            content, source="preference", tags=["preference"],
            importance=0.8, bypass_gate=bypass_gate,
        )

    def store_decision(self, content: str, bypass_gate: bool = False) -> str:
        """快捷方法：存储决策。默认高重要性。"""
        return self.store(
            content, source="decision", tags=["decision"],
            importance=0.75, bypass_gate=bypass_gate,
        )

    def store_lesson(self, content: str, bypass_gate: bool = False) -> str:
        """快捷方法：存储经验教训。默认高重要性。"""
        return self.store(
            content, source="lesson", tags=["lesson"],
            importance=0.85, bypass_gate=bypass_gate,
        )

    # ── 检索 ────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 5, min_importance: float = 0.0,
               source: str = "", include_cache: bool = True) -> list[dict]:
        """搜索记忆。

        搜索范围：
        1. L2: FTS5 全文检索
        2. L0: 缓存环（可选）
        """
        results = []

        # L2 长期检索
        longterm_results = self._longterm.search(
            query, limit=limit, min_importance=min_importance, source=source
        )
        results.extend(longterm_results)

        # L0 缓存环检索（关键词匹配）
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

    def reflect(self, query: str) -> str:
        """综合推理：搜索 + 组织"""
        results = self.search(query, limit=5)
        if not results:
            # L1 事件缓冲区也看看
            for e in reversed(self._episodic._events):
                q = query.lower()
                if q in e.get("content", "").lower():
                    results.append({
                        "content": e.get("content", "")[:300],
                        "source": e.get("source", ""),
                    })

        if not results:
            return f"关于「{query}」没有找到相关记忆。"

        lines = [f"关于「{query}」找到 {len(results)} 条相关记忆："]
        for i, r in enumerate(results, 1):
            content = r.get("content", "")[:300]
            source = r.get("source", "")
            source_str = f" [{source}]" if source else ""
            lines.append(f"\n{i}. {content}{source_str}")
            if r.get("time_decay", 1.0) < 0.5:
                lines.append(f"   (较旧记忆)")

        return '\n'.join(lines)

    # ── Prompt 注入 ──────────────────────────────────────────────────

    def build_memory_block(self, budget_ratio: float = 1.0,
                           include_search: str = "") -> str:
        """构建注入到 system prompt 的记忆块（三层合并）。

        Args:
            budget_ratio: 预算比例（0.0~1.0），来自 BudgetAllocator
            include_search: 可选的关键词，触发 L2 按需检索

        Returns:
            str: 格式化的记忆上下文块
        """
        parts = []

        # L0: 热点缓存（总是注入，预算紧张时精简）
        cache_block = self._cache.build_prompt_block(budget_ratio)
        if cache_block:
            parts.append(cache_block)

        # L1: 短期事件（预算感知注入）
        ep_block = self._episodic.build_prompt_block(budget_ratio)
        if ep_block:
            parts.append(ep_block)

        # L2: 按需检索（仅当有搜索关键词时）
        if include_search:
            search_results = self.search(include_search, limit=3)
            if search_results:
                sr_lines = [f"=== 相关记忆（搜索: {include_search}） ==="]
                for r in search_results:
                    c = r.get("content", "")[:200]
                    src = r.get("source", "")
                    src_str = f" [{src}]" if src else ""
                    sr_lines.append(f"  • {c}{src_str}")
                parts.append('\n'.join(sr_lines))

        return '\n\n'.join(parts)

    # ── Session 管理 ────────────────────────────────────────────────

    def new_session(self):
        """新 session：清空 L0 + L1，保留 L2"""
        self._cache.clear()
        self._episodic.clear()

    def add_episodic_event(self, event_type: str, content: str,
                           source: str = "", importance: float = 0.5):
        """添加一个事件到短期缓冲区（不写入长期存储）"""
        self._episodic.add_event(event_type, content, source, importance)

    def cache_hot(self, content: str, source: str = "", tags: list[str] = None):
        """强制加入热点缓存（不写入长期存储）"""
        self._cache.add(content, source, tags)

    # ── 维护 ────────────────────────────────────────────────────────

    def maintenance(self) -> dict:
        """触发维护：过期清理 + 统计"""
        expired = self._longterm.delete_expired()
        stats = self._longterm.get_stats()
        ep_stats = self._episodic.get_stats()
        return {
            "expired": expired,
            "merged": 0,  # 兼容旧接口
            "total_valid": stats["valid"],
            "total_stored": self._total_stored,
            "total_gated": self._total_gated,
            "cache_count": self._cache.count(),
            "episodic": ep_stats,
            "longterm": stats,
        }

    def get_gate_config(self) -> dict:
        """返回编码门控配置"""
        return self._gate.get_config()

    def set_gate_threshold(self, threshold: float):
        """调整门控阈值"""
        self._gate.set_threshold(threshold)

    def get_stats(self) -> dict:
        """完整统计"""
        longterm_stats = self._longterm.get_stats()
        return {
            "cache_count": self._cache.count(),
            "episodic": self._episodic.get_stats(),
            "longterm": longterm_stats,
            "gate_config": self._gate.get_config(),
            "total_stored": self._total_stored,
            "total_gated": self._total_gated,
        }

    # ── 兼容旧接口 ──────────────────────────────────────────────────

    def remember(self, key: str, content: str, tags: list = None) -> str:
        """兼容旧接口 remember(key, content, tags)"""
        return self.store(content, source=key, tags=tags)

    def recall(self, query: str, limit: int = 10) -> list[dict]:
        """兼容旧接口 recall(query)"""
        return self.search(query, limit=limit)

    def store_batch(self, items: list[dict]) -> str:
        """批量存储"""
        results = []
        for item in items:
            mem_id = self.store(
                item.get("content", ""),
                context=item.get("context", ""),
                source=item.get("source", ""),
                tags=item.get("tags"),
                importance=item.get("importance", 0.5),
            )
            results.append(mem_id)
        return json.dumps({"stored": len(results), "ids": results})

    # ── 工具模式（供 AgentLoop 使用） ────────────────────────────────

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_store",
                "description": "存储一条重要信息到长期记忆。适合记录：用户偏好、项目决策、经验教训、配置信息等。自动经过新颖性/重要性门控过滤。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "要记住的内容"},
                        "source": {"type": "string", "description": "来源标签：preference/decision/lesson/fact/config"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "搜索历史记忆。支持全文检索。搜索当前 session 及所有历史 session。",
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
                "description": "基于所有记忆做综合推理，回答需要跨 session 整合的问题。",
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
        """处理 AgentLoop 发起的记忆工具调用"""
        if tool_name == "memory_store":
            content = args.get("content", "")
            source = args.get("source", "")
            tags = args.get("tags")
            if not content:
                return json.dumps({"error": "content 不能为空"})
            result = self.store(content, source=source, tags=tags, bypass_gate=False)
            if result == "gated":
                return json.dumps({"result": "信息已存在或价值不足，跳过存储"})
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
