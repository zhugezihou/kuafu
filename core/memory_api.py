"""
夸父记忆系统 — 不可变的核心层。

职责：
1. remember(key, content) — 写入记忆
2. recall(query) — 检索记忆
3. reflect(on_topic) — 记忆反思与合成
4. 默认使用文件存储，可配置对接 Hindsight

接口约定：V1 到 V∞ 签名不变。
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT_DIR / "memory"


class MemoryAPI:
    """夸父记忆系统 API。

    默认使用本地 JSON 文件存储（V1 简易版）。
    可通过 set_backend() 切换为 Hindsight / SQLite 等后端。
    """

    def __init__(self, backend: str = "file"):
        self._backend = backend
        self._episodic_file = MEMORY_DIR / "episodic.json"
        self._semantic_file = MEMORY_DIR / "semantic.json"
        self._reflection_file = MEMORY_DIR / "reflections.json"
        self._user_profile_file = MEMORY_DIR / "user_profile.json"
        self._ensure_memory_dir()

    def _ensure_memory_dir(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        for f in [self._episodic_file, self._semantic_file, self._reflection_file]:
            if not f.exists():
                f.write_text("[]", encoding="utf-8")
        if not self._user_profile_file.exists():
            self._user_profile_file.write_text(
                json.dumps({"user_name": "用户", "preferences": {}}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # ---- 公开接口（向后兼容） ----

    def remember(self, key: str, content: str, tags: Optional[list] = None) -> bool:
        """存储一段记忆。

        Args:
            key: 记忆的标识符
            content: 记忆内容
            tags: 可选的标签列表（用于检索）

        Returns:
            True 表示成功
        """
        if self._backend == "hindsight":
            return self._hindsight_remember(key, content, tags or [])
        return self._file_remember(key, content, tags or [])

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        """按查询检索记忆。

        Args:
            query: 搜索关键词
            limit: 最多返回条数

        Returns:
            匹配的记忆列表，每项包含 {key, content, tags, timestamp}
        """
        if self._backend == "hindsight":
            return self._hindsight_recall(query, limit)
        return self._file_recall(query, limit)

    def reflect(self, on_topic: str) -> Optional[str]:
        """基于已有记忆进行反思和合成推理。

        Args:
            on_topic: 反思的主题

        Returns:
            反思结果文本，或 None（当信息不足时）
        """
        memories = self.recall(on_topic, limit=10)
        if not memories:
            return None
        return self._synthesize_reflection(on_topic, memories)

    # ---- 内部实现 ----

    def _file_remember(self, key: str, content: str, tags: list) -> bool:
        memories = json.loads(self._episodic_file.read_text(encoding="utf-8"))
        entry = {
            "key": key,
            "content": content,
            "tags": tags,
            "timestamp": time.time(),
        }
        # 去重：相同 key 则覆盖
        for i, m in enumerate(memories):
            if m.get("key") == key:
                memories[i] = entry
                break
        else:
            memories.append(entry)
        self._episodic_file.write_text(
            json.dumps(memories, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True

    def _file_recall(self, query: str, limit: int) -> list[dict]:
        query_lower = query.lower()
        memories = json.loads(self._episodic_file.read_text(encoding="utf-8"))
        results = []
        for m in memories:
            score = 0
            # 内容匹配
            if query_lower in m.get("content", "").lower():
                score += 2
            # key 匹配
            if query_lower in m.get("key", "").lower():
                score += 3
            # tag 匹配
            for tag in m.get("tags", []):
                if query_lower in tag.lower():
                    score += 1
            if score > 0:
                results.append((score, m))
        results.sort(key=lambda x: -x[0])
        return [m for _, m in results[:limit]]

    def _hindsight_remember(self, key: str, content: str, tags: list) -> bool:
        # TODO: 对接 Hindsight HTTP API
        # 临时 fallback 到 file 后端
        return self._file_remember(key, content, tags)

    def _hindsight_recall(self, query: str, limit: int) -> list[dict]:
        # TODO: 对接 Hindsight HTTP API
        return self._file_recall(query, limit)

    def _synthesize_reflection(self, topic: str, memories: list[dict]) -> str:
        lines = [f"## 关于「{topic}」的反思"]
        for m in memories:
            lines.append(f"- {m.get('key', '?')}: {m.get('content', '')}")
        return "\n".join(lines)

    def set_backend(self, backend: str):
        """切换记忆后端。支持: 'file', 'hindsight'"""
        self._backend = backend

    def get_status(self) -> dict:
        return {
            "backend": self._backend,
            "episodic_count": len(json.loads(self._episodic_file.read_text(encoding="utf-8"))),
            "semantic_count": len(json.loads(self._semantic_file.read_text(encoding="utf-8"))),
            "user_profile": json.loads(self._user_profile_file.read_text(encoding="utf-8")),
        }
