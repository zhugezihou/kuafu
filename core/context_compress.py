"""
夸父上下文压缩系统 (Context Compression)

职责：
1. 当对话 token 数超过阈值时，自动压缩历史
2. 压缩策略：保留系统提示 + 最近 N 轮 + 总结中间轮次
3. 提供压缩摘要注入机制

设计原则：
- 零依赖，仅标准库
- 可配置阈值，默认 8000 tokens
- 压缩后保留关键上下文不丢失
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# token 估算常数（与 session_store 保持一致）
CHARS_PER_TOKEN = 2.0


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数。"""
    return int(len(text) / CHARS_PER_TOKEN)


@dataclass
class CompressionResult:
    """压缩结果。"""
    original_tokens: int
    compressed_tokens: int
    messages_removed: int
    summary: str = ""
    compression_ratio: float = 0.0

    def __post_init__(self):
        if self.original_tokens > 0:
            self.compression_ratio = round(
                1 - self.compressed_tokens / self.original_tokens, 3
            )


class ContextCompressor:
    """上下文压缩器。

    负责在 token 数超过阈值时压缩消息列表。
    压缩策略：
    1. 始终保留 system prompt
    2. 保留最近 N 轮完整对话
    3. 将中间的旧轮次用 LLM 生成的摘要替代
    """

    def __init__(
        self,
        max_context_tokens: int = 12000,
        keep_recent_rounds: int = 5,
        system_token_estimation: int = 2000,
    ):
        self.max_context_tokens = max_context_tokens
        self.keep_recent_rounds = keep_recent_rounds
        self.system_token_estimation = system_token_estimation

    def needs_compression(self, messages: list[dict]) -> bool:
        """判断是否需要压缩。"""
        total = self._count_tokens(messages)
        return total > self.max_context_tokens

    def compress(self, messages: list[dict], llm_summarize=None) -> CompressionResult:
        """压缩消息列表。

        Args:
            messages: 完整的消息列表
            llm_summarize: 可选的回调函数，用于生成摘要
                          def summarize(text: str) -> str

        Returns:
            CompressionResult
        """
        original_tokens = self._count_tokens(messages)

        if not self.needs_compression(messages):
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_removed=0,
                summary="无需压缩",
            )

        # 分离 system 消息和非 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 保留最近 N 轮（1 user + 1 assistant/tool ≈ 2-3 messages per round）
        keep_count = self.keep_recent_rounds * 4
        recent_msgs = non_system[-keep_count:] if len(non_system) > keep_count else non_system
        old_msgs = non_system[:-keep_count] if len(non_system) > keep_count else []

        if not old_msgs:
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=self._count_tokens(system_msgs + recent_msgs),
                messages_removed=0,
                summary="轮次少，无需压缩",
            )

        # 生成旧消息的摘要
        summary = self._create_summary(old_msgs, llm_summarize)

        # 注入压缩通知
        summary_msg = {
            "role": "system",
            "content": f"[上下文压缩] 以下部分已被压缩为摘要：\n{summary}",
        }

        compressed = system_msgs + [summary_msg] + recent_msgs
        compressed_tokens = self._count_tokens(compressed)

        return CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            messages_removed=len(old_msgs),
            summary=summary,
        )

    def _create_summary(self, messages: list[dict], llm_fn=None) -> str:
        """生成旧消息的摘要。"""
        # 提取用户问题和关键结果
        rounds = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user":
                rounds.append(f"用户: {content[:200]}")
            elif role == "assistant":
                # 如果有 tool_calls，记录工具调用
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {}).get("name", "")
                        rounds.append(f"  调用工具: {fn}")
                else:
                    rounds.append(f"夸父: {content[:200]}")
            elif role == "tool":
                if content and len(content) > 20:
                    rounds.append(f"  工具结果: {content[:100]}...")

        text = " | ".join(rounds)

        # 如果有 LLM 摘要回调，用它生成更智能的摘要
        if llm_fn and len(text) > 500:
            try:
                return llm_fn(text)
            except Exception:
                pass

        # 默认：基于关键字的浓缩
        if len(text) > 800:
            return text[:800] + "..."
        return text

    def _count_tokens(self, messages: list[dict]) -> int:
        """计算消息列表的总 token 数。"""
        total = 0
        for m in messages:
            content = m.get("content", "")
            total += estimate_tokens(content)
            # tool_calls 也占 token
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                total += estimate_tokens(json.dumps(fn.get("arguments", {}), ensure_ascii=False))
        return total

    def get_token_count(self, messages: list[dict]) -> dict:
        """获取详细的 token 统计。"""
        system_tokens = self._count_tokens([m for m in messages if m.get("role") == "system"])
        non_system = self._count_tokens([m for m in messages if m.get("role") != "system"])
        return {
            "total": system_tokens + non_system,
            "system": system_tokens,
            "conversation": non_system,
            "threshold": self.max_context_tokens,
            "needs_compression": (system_tokens + non_system) > self.max_context_tokens,
        }

    def estimate_fit_rounds(self, average_round_tokens: int = 400) -> int:
        """估算在阈值内还能容纳多少轮对话。"""
        available = self.max_context_tokens - self.system_token_estimation
        return max(1, available // max(average_round_tokens, 100))
