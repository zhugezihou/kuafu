"""
core/memory/init.py — 夸父记忆系统 v2

废弃 JSON 文件存储，统一使用 SQLite FTS5。
新增：主动读取路径（system_prompt_block 自动注入高价值事实）。
新增：事实提取（LLM 辅助的简短事实抽取）。
移除：EncodingGate（降级为纯去重 + 简单过滤）。
合并：mem_*.json / reflections.json / user_prefs.json → memory.db。

架构:
  memory.db (SQLite FTS5)
    ├── memories       — 原始记忆（来源: preference/decision/lesson/fact/session）
    ├── memories_fts   — FTS5 全文索引
    └── facts          — 提取的结构化事实（用于主动注入）

读取链路:
  build_memory_block() → 热点缓存 + 最近事件 + 主动事实注入

写入链路:
  store() / remember() → 去重检查 → SQLite → 事实提取(异步) → 缓存
"""

from core.memory.memory_manager import (
    MemoryManager, CacheRing, DEFAULT_CACHE_CAPACITY,
    DEFAULT_EPISODIC_MAX, DEFAULT_TTL_DAYS,
)
from core.memory.sqlite_backend import SQLiteFTSBackend
from core.memory.episodic_buffer import EpisodicBuffer
from core.memory.encoding_gate import EncodingGate

__all__ = [
    "MemoryManager", "CacheRing",
    "SQLiteFTSBackend", "EpisodicBuffer", "EncodingGate",
    "DEFAULT_CACHE_CAPACITY", "DEFAULT_EPISODIC_MAX", "DEFAULT_TTL_DAYS",
]
