"""
episodic_buffer.py — 短期事件环形缓冲区

核心功能：
  1. 环状 FIFO 缓冲区（固定容量，自动淘汰最旧的事件）
  2. 超预算自动压缩：摘要 + 关键事实提取
  3. 注入到 system prompt

压缩策略（适配 32K 上下文预算）：
  - 缓冲区内事件数 > soft_limit → 触发压缩
  - 压缩后保留：摘要 + 最近 N 条 + 关键事实
"""

import json
import time
from typing import Optional


class EpisodicBuffer:
    """短期事件缓冲区（环状 FIFO）。

    存储当前 session 的结构化事件序列。
    注入到 system prompt 时，自动触发预算感知压缩。
    """

    def __init__(self, max_entries: int = 30, soft_limit: int = 20,
                 max_context_chars: int = 3000):
        self.max_entries = max_entries
        self.soft_limit = soft_limit  # 超过此值触发压缩
        self.max_context_chars = max_context_chars  # 注入上限
        self._events: list[dict] = []
        self._compressed_summary: Optional[str] = None
        self._key_facts: list[str] = []

    # ── 事件操作 ────────────────────────────────────────────────────

    def add_event(self, event_type: str, content: str,
                  source: str = "", importance: float = 0.5):
        """添加一个事件到缓冲区。

        如果缓冲区已满，淘汰最旧的事件。
        """
        event = {
            "type": event_type,
            "content": content[:500],
            "source": source,
            "importance": importance,
            "timestamp": time.time(),
        }

        self._events.append(event)

        # 环形淘汰
        if len(self._events) > self.max_entries:
            self._events.pop(0)

        # 超过 soft_limit 自动触发一次压缩（第一次）
        if len(self._events) == self.soft_limit and self._compressed_summary is None:
            self._compress()

    def clear(self):
        """清空缓冲区（新 session 开始时调用）"""
        self._events.clear()
        self._compressed_summary = None
        self._key_facts.clear()

    # ── 压缩 ────────────────────────────────────────────────────────

    def _compress(self):
        """将旧事件压缩为摘要 + 关键事实。

        按时间分两段：
          - 前 1/3 事件 → 摘要
          - 中 1/3 事件 → 关键事实提取
          - 后 1/3 事件 → 保留最新的事件
        """
        if not self._events:
            return

        n = len(self._events)
        split1 = n // 3
        split2 = 2 * n // 3

        old_events = self._events[:split1]
        mid_events = self._events[split1:split2]

        # 摘要：合并事件类型和大意
        type_counts = {}
        for e in old_events:
            t = e.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        summary_parts = [f"早期事件概览: "]
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            summary_parts.append(f"{t}x{c}")
        self._compressed_summary = ', '.join(summary_parts)

        # 关键事实：提取重要性 > 0.6 的事件
        self._key_facts = []
        for e in old_events + mid_events:
            if e.get("importance", 0) > 0.6:
                fact = e.get("content", "")[:200]
                if fact and fact not in self._key_facts:
                    self._key_facts.append(fact)

    # ── 注入生成 ────────────────────────────────────────────────────

    def build_prompt_block(self, budget_ratio: float = 1.0) -> str:
        """生成注入到 system prompt 的结构化块。

        Args:
            budget_ratio: 预算比例（0.0~1.0），决定多少内容注入

        Returns:
            str: 格式化后的文本块
        """
        # 压缩
        if len(self._events) > self.soft_limit and self._compressed_summary is None:
            self._compress()

        lines = []

        if budget_ratio < 0.3:
            # 最低预算：只给数量统计 + 摘要
            lines.append(f"[Session Events: {len(self._events)} events, "
                         f"{len(self._key_facts)} key facts]")
            if self._compressed_summary:
                lines.append(f"Summary: {self._compressed_summary}")
            if self._key_facts[:3]:
                lines.append(f"Key Facts: {' | '.join(self._key_facts[:3])}")
        else:
            # 全量注入
            lines.append(f"=== 当前 Session 事件 ({len(self._events)} 条) ===")

            if self._compressed_summary:
                lines.append(f"\n📋 早期概览: {self._compressed_summary}")
                if self._key_facts:
                    lines.append(f"   🔑 关键事实: {' | '.join(self._key_facts[:5])}")

            # 最近的 N 条（按预算比例）
            recent_count = max(3, min(len(self._events) // 2,
                                       int(self.max_entries * budget_ratio)))
            recent = self._events[-recent_count:]
            if recent:
                lines.append(f"\n=== 最近 {len(recent)} 条事件 ===")
                for i, e in enumerate(reversed(recent), 1):
                    t = e.get("type", "?")
                    c = e.get("content", "")[:150]
                    lines.append(f"  [{t}] {c}")

        result = '\n'.join(lines)
        # 硬截断
        budget_chars = int(self.max_context_chars * budget_ratio)
        if len(result) > budget_chars:
            result = result[:budget_chars] + f"\n... (截断, 共{len(self._events)}条)"

        return result

    def count(self) -> int:
        return len(self._events)

    def get_key_facts(self) -> list[str]:
        return self._key_facts

    def get_stats(self) -> dict:
        return {
            "events": len(self._events),
            "compressed": self._compressed_summary is not None,
            "key_facts": len(self._key_facts),
            "max_entries": self.max_entries,
            "soft_limit": self.soft_limit,
        }
