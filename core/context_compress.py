"""
夸父上下文压缩系统 (Context Compression)

职责：
1. 当对话 token 数超过阈值时，自动压缩历史
2. 压缩策略：保留系统提示 + 最近 N 轮 + 本地 LLM 总结中间轮次
3. 提供压缩摘要注入机制

设计原则：
- 零依赖，仅标准库
- 可配置阈值，默认 8000 tokens
- 本地 LLM（llama-server）做智能摘要
- 压缩后保留关键上下文不丢失
"""

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

# token 估算常数（与 session_store 保持一致）
# Qwen3.5-9B 实测中文约 1.69 chars/token，取安全值 1.6
CHARS_PER_TOKEN = 1.6

# 本地 llama-server 摘要专用配置
SUMMARY_BASE_URL = "http://localhost:8080"
SUMMARY_MAX_TOKENS = 256
SUMMARY_TIMEOUT = 30


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


class LocalSummarizer:
    """本地 llama-server 摘要器。

    使用已在运行的 llama-server（port 8080）对旧对话做智能摘要。
    因为是独立 HTTP 请求，不会阻塞主 Agent 的推理。
    """

    def __init__(
        self,
        base_url: str = SUMMARY_BASE_URL,
        max_tokens: int = SUMMARY_MAX_TOKENS,
        timeout: int = SUMMARY_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout

    def summarize(self, text: str) -> str:
        """调用本地 LLM 生成摘要。

        Args:
            text: 需要摘要的对话文本

        Returns:
            摘要文本（失败时返回空字符串，用截断文本兜底）
        """
        if not text.strip():
            return ""

        try:
            return self._call_llm(text)
        except Exception as e:
            # fallback: 截断
            return text[:600] + "..." if len(text) > 600 else text

    def _call_llm(self, text: str) -> str:
        """调用本地 llama-server 的 chat/completions API。"""
        prompt = (
            "你是一个对话摘要器。请将以下多轮对话浓缩为 2-3 句中文摘要，"
            "保留关键信息：用户的核心需求、做出的决策、已知结果。\n\n"
            f"对话内容：\n{text}\n\n摘要："
        )

        payload = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个高效的对话摘要器。"
                        "你只输出摘要内容本身，不加前缀、不加评价、不反问。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
            "stream": False,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip() or text[:600] + "..."

    def is_available(self) -> bool:
        """检查本地 llama-server 是否可访问。"""
        try:
            req = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False


class ContextCompressor:
    """上下文压缩器。

    负责在 token 数超过阈值时压缩消息列表。
    压缩策略：
    1. 始终保留 system prompt
    2. 保留最近 N 轮完整对话
    3. 将中间的旧轮次用本地 LLM 生成的摘要替代
    """

    def __init__(
        self,
        max_context_tokens: int = 12000,
        keep_recent_rounds: int = 5,
        system_token_estimation: int = 2000,
        summarizer: Optional[LocalSummarizer] = None,
    ):
        self.max_context_tokens = max_context_tokens
        self.keep_recent_rounds = keep_recent_rounds
        self.system_token_estimation = system_token_estimation
        self.summarizer = summarizer or LocalSummarizer()

    def needs_compression(self, messages: list[dict]) -> bool:
        """判断是否需要压缩。"""
        total = self._count_tokens(messages)
        return total > self.max_context_tokens

    def compress(self, messages: list[dict], llm_summarize: Optional[Callable] = None) -> CompressionResult:
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

    def compress_with_local_llm(self, messages: list[dict]) -> CompressionResult:
        """压缩消息列表——使用本地 LLM 生成摘要。

        比 compress() 更彻底：直接用本地模型做智能摘要。
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

        # 保留最近 N 轮
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

        # 构建对话文本供摘要
        dialogue_text = self._format_dialogue(old_msgs)

        # 用本地 LLM 生成摘要
        t0 = time.time()
        summary = self.summarizer.summarize(dialogue_text)
        elapsed = time.time() - t0

        # 注入压缩通知
        summary_msg = {
            "role": "system",
            "content": (
                f"[上下文压缩] 移除了 {len(old_msgs)} 条旧消息，"
                f"本地 LLM 摘要 (用时 {elapsed:.1f}s)：\n{summary}"
            ),
        }

        compressed = system_msgs + [summary_msg] + recent_msgs
        compressed_tokens = self._count_tokens(compressed)

        return CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            messages_removed=len(old_msgs),
            summary=summary,
        )

    def _format_dialogue(self, messages: list[dict]) -> str:
        """将消息列表格式化为对话文本。"""
        parts = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            tool_calls = m.get("tool_calls")

            if role == "user":
                parts.append(f"用户: {content[:300]}")
            elif role == "assistant":
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {}).get("name", "?")
                        parts.append(f"夸父: [调用工具 {fn}]")
                elif content:
                    parts.append(f"夸父: {content[:300]}")
            elif role == "tool":
                if content and len(str(content)) > 20:
                    parts.append(f"  [工具结果: {str(content)[:150]}]")

        return "\n".join(parts)

    def _create_summary(self, messages: list[dict], llm_fn: Optional[Callable] = None) -> str:
        """生成旧消息的摘要。

        优先使用本地 LLM 摘要，失败时回退到截断。
        """
        # 尝试用本地 LLM
        if self.summarizer and self.summarizer.is_available():
            dialogue_text = self._format_dialogue(messages)
            if len(dialogue_text) > 300:  # 足够长才值得用 LLM
                try:
                    summary = self.summarizer.summarize(dialogue_text)
                    if summary and len(summary) > 10:
                        return summary
                except Exception:
                    pass

        # 如果有外部 LLM 回调，用它
        if llm_fn:
            dialogue_text = self._format_dialogue(messages)
            if len(dialogue_text) > 500:
                try:
                    return llm_fn(dialogue_text)
                except Exception:
                    pass

        # 默认回退：基于关键字的浓缩
        rounds = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user":
                rounds.append(f"用户: {content[:200]}")
            elif role == "assistant":
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {}).get("name", "")
                        rounds.append(f"  调用工具: {fn}")
                else:
                    rounds.append(f"夸父: {content[:200]}")
            elif role == "tool":
                if content and len(content) > 20:
                    rounds.append(f"  工具结果: {str(content)[:100]}...")

        text = " | ".join(rounds)
        if len(text) > 800:
            return text[:800] + "..."
        return text

    def _count_tokens(self, messages: list[dict]) -> int:
        """计算消息列表的总 token 数。"""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, dict):
                total += estimate_tokens(json.dumps(content, ensure_ascii=False))
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
