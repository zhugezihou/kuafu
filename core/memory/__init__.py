"""
core/memory/ — 夸父记忆系统 v3（Hindsight-Lite）

架构:
  MemoryManager     ← 统一入口（写入/读取/Reflect）
  CacheRing         ← L0 热点缓存
  NetworkStore      ← 四网络存储（World/Experience/Observation）
  OpinionEngine     ← 信念管理 + 置信度演化
  SQLiteFTSBackend  ← FTS5 全文检索引擎
  EpisodicBuffer    ← 短期事件缓冲区
"""

from core.memory.memory_manager import (
    MemoryManager, CacheRing,
    DEFAULT_CACHE_CAPACITY, DEFAULT_EPISODIC_MAX,
)
from core.memory.sqlite_backend import SQLiteFTSBackend
from core.memory.episodic_buffer import EpisodicBuffer
from core.memory.hindsight_lite import (
    NetworkStore, OpinionEngine,
    NETWORK_WORLD, NETWORK_EXPERIENCE, NETWORK_OBSERVATION, NETWORK_OPINION,
)

__all__ = [
    "MemoryManager", "CacheRing",
    "SQLiteFTSBackend", "EpisodicBuffer",
    "NetworkStore", "OpinionEngine",
    "NETWORK_WORLD", "NETWORK_EXPERIENCE", "NETWORK_OBSERVATION", "NETWORK_OPINION",
    "DEFAULT_CACHE_CAPACITY", "DEFAULT_EPISODIC_MAX",
]
