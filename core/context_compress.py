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
#
# Copyright (c) 2026 zhugezihou
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import json
import logging
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

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


class LLMSummarizer:
    """LLM 自摘要器。

    用主干 LLM（DeepSeek 等）自身对旧对话做摘要。
    不是阻塞调用——LLM 调用本来就由 AgentLoop 调度，
    压缩是异步触发的，不会影响主推理。

    Args:
        llm_chat: LLMChat 函数，接收 messages 列表返回 response
        max_summary_tokens: 摘要最大 token 数
        timeout: 超时秒数
    """

    def __init__(
        self,
        llm_chat: Optional[callable] = None,
        max_summary_tokens: int = 512,
        timeout: int = 30,
    ):
        self._llm_chat = llm_chat
        self.max_summary_tokens = max_summary_tokens
        self.timeout = timeout

    def set_llm(self, llm_chat: callable):
        """设置 LLM 聊天函数（惰性注入）。"""
        self._llm_chat = llm_chat

    def summarize(self, text: str) -> str:
        """用 LLM 生成摘要。

        Args:
            text: 需要摘要的对话文本

        Returns:
            摘要文本（失败时截断兜底）
        """
        if not text.strip():
            return ""
        if not self._llm_chat:
            return text[:600] + "..." if len(text) > 600 else text

        try:
            prompt = (
                "你是一个对话摘要器。请将以下多轮对话浓缩为 2-3 句中文摘要，"
                "保留关键信息：用户的核心需求、做出的决策、已知结果。\n\n"
                f"对话内容：\n{text}\n\n摘要："
            )
            resp = self._llm_chat([{"role": "user", "content": prompt}])
            content = ""
            if isinstance(resp, dict):
                content = resp.get("content", "")
            elif isinstance(resp, str):
                content = resp
            return content.strip() or text[:600] + "..."
        except Exception:
            return text[:600] + "..." if len(text) > 600 else text

    def is_available(self) -> bool:
        return self._llm_chat is not None


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
        summarizer: Optional[LLMSummarizer] = None,
    ):
        self.max_context_tokens = max_context_tokens
        self.keep_recent_rounds = keep_recent_rounds
        self.system_token_estimation = system_token_estimation
        self.summarizer = summarizer or LLMSummarizer()
        self.pin_manager = PinnedContentManager()
        self._pinned_summary = ""  # 缓存上次压缩时的 Pin 摘要信息

    def needs_compression(self, messages: list[dict]) -> bool:
        """判断是否需要压缩。"""
        total = self._count_tokens(messages)
        return total > self.max_context_tokens

    # ──────────────────────────────────────────────────────────────────────────
    # P0-1: 工具调用结果清除 (Tool Result Cleanup)
    # ──────────────────────────────────────────────────────────────────────────
    # 在压缩之前，对超过 N 轮的旧工具调用结果做轻量级替换。
    # 节省约 40-60% token，但不会丢失信息（保留调用名称和参数概要）。
    #

    def clean_old_tool_results(
        self,
        messages: list[dict],
        max_rounds: int = 4,
        keep_summary_chars: int = 100,
    ) -> tuple[list[dict], int]:
        """对超过 max_rounds 轮的旧工具结果进行清除。

        策略（非破坏性）：
          1. 遍历消息列表，按 role='user' 分割轮次
          2. 对超过 max_rounds 轮前的 tool 消息，将 content 替换为轻量占位
          3. 同时对同一轮内的 assistant 消息的 tool_calls 参数保留函数名但精简参数值
          4. 保留最后 keep_summary_chars 个字符的关键结果信息
          5. 将所有被替换的结果数据转为一行简短的描述，不丢失工具调用名称

        Args:
            messages: 完整消息列表
            max_rounds: 保留最近几轮的完整工具结果（默认 4 轮）
            keep_summary_chars: 旧工具结果保留的最大字符数（默认 100）

        Returns:
            (新的消息列表, 节省的 token 数)
        """
        if not messages:
            return messages, 0

        saved_tokens = 0
        new_messages = []  # type: list[dict]
        round_num = 0

        # 反向遍历：从最新消息往回算轮次
        # 先算出每条消息的轮次编号
        round_of_msg = []  # type: list[int]
        for m in messages:
            if m.get("role") == "user":
                round_num += 1
            round_of_msg.append(round_num)

        total_rounds = round_num

        # 如果总轮次 <= max_rounds，不需要清除
        if total_rounds <= max_rounds:
            return messages, 0

        # 清除阈值轮次：比最新 max_rounds 轮更早的都清除
        cleanup_threshold = total_rounds - max_rounds

        # 临时收集被清除的工具调用名和原长度（用于统计）
        cleaned_tools = {}  # type: dict[str, int]

        for i, m in enumerate(messages):
            msg_round = round_of_msg[i]
            role = m.get("role", "")

            if msg_round <= cleanup_threshold and role == "tool":
                # 旧轮次的工具结果 → 替换为精简占位
                content = m.get("content", "")
                # 如果已经是占位符了，跳过（避免重复处理）
                if isinstance(content, str) and content.startswith("[工具"):
                    new_messages.append(m)
                    continue

                # 算出原长度
                old_len = len(str(content)) if content else 0
                saved_tokens += estimate_tokens(str(content)) if content else 0

                # 提取工具名（从同一轮中最近的 assistant 消息中获取）
                tool_name = "?"
                for j in range(i - 1, max(i - 10, -1), -1):
                    if round_of_msg[j] == msg_round and messages[j].get("role") == "assistant":
                        tc_list = messages[j].get("tool_calls", [])
                        for tc in tc_list:
                            if tc.get("id") == m.get("tool_call_id"):
                                tool_name = tc.get("function", {}).get("name", "?")
                                break
                        break

                # 精简结果：保留前 keep_summary_chars 字符作为预览
                content_str = str(content) if content else ""
                if len(content_str) > keep_summary_chars:
                    preview = content_str[:keep_summary_chars] + "..."
                else:
                    preview = content_str

                key = tool_name or "?"
                cleaned_tools[key] = cleaned_tools.get(key, 0) + old_len

                # 生成精简占位
                new_msg = dict(m)
                new_msg["content"] = (
                    f"[工具结果已归档] {tool_name} | "
                    f"原长 {old_len} chars | "
                    f"摘要: {preview[:keep_summary_chars]}"
                )
                new_messages.append(new_msg)

            elif msg_round <= cleanup_threshold and role == "assistant":
                # 旧轮次的 assistant 消息 → 精简 tool_calls 参数
                tc_list = m.get("tool_calls", [])
                if tc_list:
                    # 精简 tool_calls 的 arguments：只留函数名，参数体积大幅压缩
                    new_tc_list = []
                    for tc in tc_list:
                        fn = tc.get("function", {})
                        arg_str = fn.get("arguments", "")
                        # 对参数做极简压缩：保留长度和类型信息，去掉值
                        if isinstance(arg_str, str) and len(arg_str) > 50:
                            # 尝试解析 JSON 并压缩
                            try:
                                parsed = json.loads(arg_str)
                                compressed_args = (
                                    "{" + ", ".join(
                                        f"{k}: {type(v).__name__}{'(' + str(len(str(v))) + ' chars)' if isinstance(v, str) else ''}"
                                        for k, v in parsed.items()
                                    ) + "}"
                                )
                            except (json.JSONDecodeError, TypeError):
                                compressed_args = f"({len(arg_str)} chars)"
                        else:
                            compressed_args = arg_str

                        new_fn = dict(fn)
                        new_fn["arguments"] = compressed_args
                        new_tc = dict(tc)
                        new_tc["function"] = new_fn
                        new_tc_list.append(new_tc)

                    new_msg = dict(m)
                    new_msg["tool_calls"] = new_tc_list
                    new_messages.append(new_msg)
                else:
                    new_messages.append(m)
            else:
                new_messages.append(m)

        # 统计日志
        if cleaned_tools:
            tool_stats = ", ".join(
                f"{name}: {size} chars"
                for name, size in sorted(
                    cleaned_tools.items(), key=lambda x: -x[1]
                )[:5]
            )
            logger.info(
                f"🧹 工具结果清除: 清理 {len(cleaned_tools)} 个工具, "
                f"节省约 {saved_tokens} tokens [{tool_stats}]"
            )

        return new_messages, saved_tokens

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

        # ── P1-2: 分离 Pin 消息，保护关键内容 ──
        pinned_msgs, compressible_msgs = self.pin_manager.separate_pinned(messages)
        pin_count = len(pinned_msgs)
        if pin_count > 0:
            logger.info(f"📌 Pin 保护: {pin_count}/{len(messages)} 条消息被保护")

        # 如果没有可压缩的消息（全部被 Pin），返回
        if not compressible_msgs:
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_removed=0,
                summary="所有消息均被 Pin，无需压缩",
            )

        # 只对可压缩部分进行操作
        compressible_only = [m for _, m in compressible_msgs]

        # ── P0-1: 压缩前先清除旧工具结果（节省 40-60% token） ──
        cleaned_messages, saved_tokens = self.clean_old_tool_results(compressible_only)

        # 如果清除后 + Pin 消息不再需要压缩，直接返回
        test_messages = [m for _, m in pinned_msgs] + cleaned_messages
        if self._count_tokens(test_messages) <= self.max_context_tokens * 0.8:
            compressed_tokens = self._count_tokens(cleaned_messages)
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                messages_removed=0,
                summary=f"工具结果清除后无需进一步压缩 (节省约 {saved_tokens} tokens)",
                compression_ratio=round(1 - compressed_tokens / original_tokens, 3),
            )

        # 用清理后的消息继续压缩
        messages = cleaned_messages

        # 分离 system 消息和非 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 保留最近 N 轮（1 user + 1 assistant/tool ≈ 2-3 messages per round）
        keep_count = self.keep_recent_rounds * 4
        recent_msgs = non_system[-keep_count:] if len(non_system) > keep_count else non_system
        old_msgs = non_system[:-keep_count] if len(non_system) > keep_count else []

        if not old_msgs:
            # 没有旧消息需要压缩，但 Pin 消息 + 最近轮次已足够
            pinned_contents = [m for _, m in pinned_msgs]
            compressed = pinned_contents + recent_msgs
            compressed_tokens = self._count_tokens(compressed)
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                messages_removed=0,
                summary="轮次少，无需压缩（Pin 消息已保留）",
            )

        # 生成旧消息的摘要
        summary = self._create_summary(old_msgs, llm_summarize)

        # 缓存 Pin 摘要（供后续压缩参考）
        self._pinned_summary = summary

        # 注入压缩通知
        summary_msg = {
            "role": "system",
            "content": f"[上下文压缩] 以下部分已被压缩为摘要（Pin 保护了 {pin_count} 条关键消息）：\n{summary}",
        }

        # 组装：Pin 消息（按原始顺序） + 压缩摘要 + 最近轮次
        pinned_contents = [m for _, m in pinned_msgs]
        compressed = pinned_contents + [summary_msg] + recent_msgs
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

        # ── P1-2: 分离 Pin 消息 ──
        pinned_msgs, compressible_msgs = self.pin_manager.separate_pinned(messages)
        pin_count = len(pinned_msgs)
        if pin_count > 0:
            logger.info(f"📌 Pin 保护: {pin_count}/{len(messages)} 条消息被保护")

        if not compressible_msgs:
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_removed=0,
                summary="所有消息均被 Pin，无需压缩",
            )

        compressible_only = [m for _, m in compressible_msgs]

        # ── P0-1: 压缩前先清除旧工具结果 ──
        cleaned_messages, saved_tokens = self.clean_old_tool_results(compressible_only)

        # 如果清除后 + Pin 消息不再需要压缩，直接返回
        test_messages = [m for _, m in pinned_msgs] + cleaned_messages
        if self._count_tokens(test_messages) <= self.max_context_tokens * 0.8:
            compressed_tokens = self._count_tokens(cleaned_messages)
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                messages_removed=0,
                summary=f"工具结果清除后无需进一步压缩 (节省约 {saved_tokens} tokens)",
            )

        # 用清理后的消息继续压缩
        messages = cleaned_messages

        # 分离 system 消息和非 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 保留最近 N 轮
        keep_count = self.keep_recent_rounds * 4
        recent_msgs = non_system[-keep_count:] if len(non_system) > keep_count else non_system
        old_msgs = non_system[:-keep_count] if len(non_system) > keep_count else []

        if not old_msgs:
            pinned_contents = [m for _, m in pinned_msgs]
            compressed = pinned_contents + recent_msgs
            compressed_tokens = self._count_tokens(compressed)
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                messages_removed=0,
                summary="轮次少，无需压缩（Pin 消息已保留）",
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
                f"本地 LLM 摘要 (用时 {elapsed:.1f}s，"
                f"Pin 保护了 {pin_count} 条关键消息)：\n{summary}"
            ),
        }

        pinned_contents = [m for _, m in pinned_msgs]
        compressed = pinned_contents + [summary_msg] + recent_msgs
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


class PinnedContentManager:
    """关键信息 Pin 机制 (P1-2)。

    负责识别和保护「不能被压缩」的关键对话内容。
    被 Pin 的消息在 compress() 中会被排除在压缩范围之外。

    策略：
    - 自动 Pin：
      1. 系统提示（role=system）—— 始终保留
      2. 用户最近一轮提问 —— 用户的核心意图
      3. 包含显式 Pin 标记的消息（content 中含 '[PIN]' 或 metadata 中 pin=True）
      4. 白板消息（含 'whiteboard'/'决策'/'决议' 等关键词的 system 消息）
    - 显式 Pin：
      用户可以说 "记住这个"、"保留这条"、"pin 这条"

    Pin 优先级（决定在压缩中的保留顺序）：
      0. system prompt（最高优先级，永不压缩）
      1. 显式 Pin 消息
      2. 白板/决策消息
      3. 用户最近一轮提问
    """

    # 自动识别关键词
    WHITEBOARD_KEYWORDS = ["白板", "决策", "决议", "decision", "whiteboard", "已确定", "已决定", "方案选择"]
    PIN_MARKERS = ["[PIN]", "[pin]", "[保留]", "[KEEP]", "[keep]"]

    def __init__(self):
        self._explicit_pins: set[int] = set()  # 消息索引的显式 Pin 集合

    def identify(self, messages: list[dict]) -> list[int]:
        """识别所有需要 Pin 的消息索引。

        Returns:
            按优先级排序的消息索引列表（高优先级在前）
        """
        pinned_indices = set()

        for i, m in enumerate(messages):
            role = m.get("role", "")
            content = str(m.get("content", ""))
            metadata = m.get("metadata", {})

            # P0: system prompt 始终 Pin
            if role == "system":
                pinned_indices.add(i)
                continue

            # P1: 显式 Pin（metadata 标记或消息中的 Pin 标记）
            if metadata.get("pin", False):
                pinned_indices.add(i)
                continue
            if any(marker in content for marker in self.PIN_MARKERS):
                pinned_indices.add(i)
                # 如果 Pin 的是一条 user 消息，也 Pin 紧随其后的 assistant 回复
                if role == "user" and i + 1 < len(messages):
                    pinned_indices.add(i + 1)
                continue

            # P2: 白板/决策消息（摘要注入的系统消息）
            # 注：system 消息已在 P0 被 pin，此分支实际不会被执行（防御性保留）
            if role == "system" and any(kw in content for kw in self.WHITEBOARD_KEYWORDS):  # pragma: no cover
                pinned_indices.add(i)  # pragma: no cover
                continue  # pragma: no cover

        # 用户最近一轮提问（最后一条 user 消息）
        last_user = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user = i
                break
        if last_user is not None:
            pinned_indices.add(last_user)

        # 按优先级排序
        def sort_key(idx):
            m = messages[idx]
            role = m.get("role", "")
            content = str(m.get("content", ""))
            # system 最高
            if role == "system":
                return 0
            # 显式 Pin 次高
            if m.get("metadata", {}).get("pin", False):
                return 1
            if any(marker in content for marker in self.PIN_MARKERS):
                return 1
            # 白板/决策（注：此分支仅在非 system 但含白板关键词时触发，
            # system 角色已在 L710 优先返回。保留此分支为防御性设计。）
            if role == "system" and any(kw in content for kw in self.WHITEBOARD_KEYWORDS):
                return 2  # pragma: no cover
            # 用户最近提问
            return 3

        return sorted(pinned_indices, key=sort_key)

    def pin_message(self, index: int):
        """显式 Pin 某条消息。"""
        self._explicit_pins.add(index)

    def unpin_message(self, index: int):
        """取消 Pin。"""
        self._explicit_pins.discard(index)

    def is_pinned_index(self, index: int, messages: list[dict]) -> bool:
        """检查某条消息是否被 Pin（结合显式和自动识别）。"""
        return index in self.identify(messages)

    def separate_pinned(
        self, messages: list[dict]
    ) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
        """将消息分为 Pin 组和可压缩组。

        Returns:
            (pinned_msgs, compressible_msgs)
            每个元素为 (index, message) 元组，保留原始索引用于重组。
        """
        pinned_indices = self.identify(messages)
        pinned: list[tuple[int, dict]] = []
        compressible: list[tuple[int, dict]] = []

        for i, m in enumerate(messages):
            if i in pinned_indices:
                pinned.append((i, m))
            else:
                compressible.append((i, m))

        return pinned, compressible


class ToolResultStore:
    """工具结果磁盘存储 + 自动摘要 (Microcompact)。

    大工具结果写入磁盘，上下文中只留简短摘要 + 文件路径。
    阈值：超过 MICRO_THRESHOLD_CHARS 的结果自动写磁盘。
    """

    MICRO_THRESHOLD_CHARS = 2000  # 超过此长度才做 microcompact

    def __init__(self, base_dir: Optional[str] = None):
        base = base_dir or os.environ.get("KUAFU_DATA_DIR") or str(Path("~/.config/kuafu").expanduser())
        self.results_dir = Path(base) / "tool_results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        # 保留最近 200 个文件
        self._max_files = 200
        self._next_id = 0

    def store(self, fn_name: str, raw_output: str) -> dict:
        """将工具结果写入磁盘，返回占位信息。

        Returns:
            dict: {
                "file_id": str,          # 磁盘文件名
                "file_path": str,        # 绝对路径
                "preview": str,          # 前 200 字预览
                "original_len": int,     # 原始长度
                "compact": str,          # 进上下文的占位字符串
            }
        """
        self._next_id += 1
        file_id = f"{int(time.time())}_{self._next_id}_{fn_name[:20]}"
        file_path = self.results_dir / f"{file_id}.txt"

        # 写入磁盘
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(raw_output)

        # 清理旧文件
        self._cleanup_old_files()

        # 生成预览（前200字）
        preview = raw_output[:200].strip()
        if len(raw_output) > 200:
            preview += "..."

        original_len = len(raw_output)
        compact = (
            f"[工具结果已存储] {fn_name} | "
            f"大小: {original_len} chars | "
            f"预览: {preview} | "
            f"完整路径: {file_path}"
        )

        return {
            "file_id": file_id,
            "file_path": str(file_path),
            "preview": preview,
            "original_len": original_len,
            "compact": compact,
        }

    def read_result(self, file_id_or_path: str) -> str:
        """根据文件 ID 或路径读取完整的工具结果。"""
        p = Path(file_id_or_path)
        if not p.is_absolute():
            p = self.results_dir / f"{file_id_or_path}.txt"
        if not p.exists():
            return f"[工具结果文件不存在: {p}]"
        try:
            return p.read_text(encoding="utf-8")
        except Exception as e:
            return f"[读取工具结果失败: {e}]"

    def _cleanup_old_files(self):
        """保留最多 _max_files 个文件，删除最旧的。"""
        files = sorted(self.results_dir.iterdir(), key=lambda f: f.stat().st_mtime)
        while len(files) > self._max_files:
            files[0].unlink(missing_ok=True)
            files = files[1:]

    @classmethod
    def load(cls, path: str) -> Optional[str]:
        """类方法：根据文件路径读取存储的工具结果。"""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    @classmethod
    def should_compact(cls, text: str) -> bool:
        """判断是否需要对工具结果做 microcompact。"""
        return len(text) > cls.MICRO_THRESHOLD_CHARS

    @classmethod
    def try_read_from_path(cls, text: str) -> str:
        """判断 text 是否是 microcompact 占位，若是则尝试读取磁盘文件。

        用于内置读取工具：当 LLM 需要更多细节时自动读取。
        """
        if "完整路径:" not in text:
            return ""
        # 提取路径
        for line in text.split("\n"):
            if "完整路径:" in line:
                path = line.split("完整路径:")[-1].strip()
                p = Path(path)
                if p.exists():
                    try:
                        return p.read_text(encoding="utf-8")
                    except Exception:
                        pass
        return ""


# ══════════════════════════════════════════════════════════════════════
# ContextCollapse — 非破坏性上下文投影
# ══════════════════════════════════════════════════════════════════════
#
# 核心思想：
# 1. 压缩时，将 messages 原始完整副本写入 session JSONL（磁盘）
# 2. 上下文中的 messages 替换为摘要投影（compact projection）
# 3. 当 LLM 需要查看被压缩的原始内容时，可调用原始细节读取工具
#
# 与 ContextCompressor 的区别：
# - ContextCompressor: 破坏性压缩——摘要覆盖旧消息，一旦压缩细节不可恢复
# - ContextCollapse: 非破坏性压缩——原始数据存入磁盘，上下文只存投影
#                    支持按需还原（delegate_task 可读取 JSONL）
# =====================================================================


@dataclass
class CollapseResult:
    """非破坏性压缩的结果。"""
    original_count: int           # 压缩前的消息条数
    collapsed_count: int          # 压缩后的消息条数
    messages_written: int         # 写入磁盘的消息条数
    summary: str = ""             # LLM 生成的摘要文本
    tokens_saved: int = 0         # 节省的 token 数


class ContextCollapse:
    """上下文非破坏性压缩器。

    保留原始消息到 JSONL 磁盘文件，仅将上下文中的旧消息替换为摘要投影。
    支持代理人通过工具读取原始细节（类似 Claude Code 的虚拟投影）。
    """

    def __init__(
        self,
        summarizer: Optional["LLMSummarizer"] = None,
        keep_recent_rounds: int = 5,
        summary_prompt: str = "",
    ):
        self.summarizer = summarizer or LLMSummarizer()
        self.keep_recent_rounds = keep_recent_rounds
        self.summary_prompt = summary_prompt or (
            "你是一个对话摘要器。请将以下多轮对话内容浓缩为2-3句中文摘要，"
            "保留关键信息：用户的核心需求、做出的决策、已确认的结果、关键工具调用。"
            "不要遗漏重要的数值、文件名、路径和代码功能。"
        )

    def collapse(
        self,
        messages: list[dict],
        session_id: str = "",
        session_store: Optional[object] = None,
        force: bool = False,
        threshold_tokens: int = 10000,
    ) -> CollapseResult:
        """对 messages 执行非破坏性压缩。

        1. 检查是否需要压缩（超阈）
        2. 将原始消息完整写入 session_store JSONL
        3. 用摘要投影替换上下文中的旧消息
        4. 新增一个专用工具 'read_collapsed_context' 让 LLM 可查询原始细节

        Returns:
            CollapseResult
        """
        original_count = len(messages)

        # token 估算
        total_tokens = sum(
            estimate_tokens(str(m.get("content", "")))
            for m in messages
        )

        if not force and total_tokens <= threshold_tokens:
            return CollapseResult(
                original_count=original_count,
                collapsed_count=original_count,
                messages_written=0,
                tokens_saved=0,
            )

        # 分离 system / non-system
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 保留最近 N 轮的完整消息
        keep_count = self.keep_recent_rounds * 4  # user+assistant+tool 约 4 msg/round
        recent_msgs = non_system[-keep_count:] if len(non_system) > keep_count else non_system
        old_msgs = non_system[:-keep_count] if len(non_system) > keep_count else []

        if not old_msgs:
            return CollapseResult(
                original_count=original_count,
                collapsed_count=original_count,
                messages_written=0,
                tokens_saved=0,
                summary="轮次少，无需压缩",
            )

        # 写入原始消息到 JSONL ══════════════════════════════════════
        if session_id and session_store and hasattr(session_store, "save_raw_messages"):
            session_store.save_raw_messages(session_id, messages)
            messages_written = len(messages)
        else:
            messages_written = 0

        # 生成旧消息的摘要投影 ══════════════════════════════════════
        summary = self._generate_summary(old_msgs)

        old_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in old_msgs)
        new_tokens = estimate_tokens(summary)

        # 构建压缩后的消息列表（系统 + 投影 + 最近完整消息）
        num_old = len(old_msgs)
        collapse_note = {
            "role": "system",
            "content": (
                f"【上下文投影】以下 {num_old} 条旧消息已被压缩为摘要以节省上下文。\n"
                f"原始数据完整保留在磁盘，通过 tool:\n"
                f"  read_collapsed_context(start=0, max_tokens=2000)\n"
                f"可按需读取原始细节。\n\n"
                f"摘要：\n{summary}"
            ),
        }

        compressed = system_msgs + [collapse_note] + recent_msgs

        return CollapseResult(
            original_count=original_count,
            collapsed_count=len(compressed),
            messages_written=messages_written,
            summary=summary,
            tokens_saved=old_tokens - new_tokens,
        )

    def _generate_summary(self, messages: list[dict]) -> str:
        """用本地 LLM 生成旧消息的摘要投影。"""
        dialogue = self._format_dialogue(messages)

        if len(dialogue) < 300:
            return dialogue[:600] if len(dialogue) > 600 else dialogue

        try:
            if self.summarizer and self.summarizer.is_available():
                return self.summarizer.summarize(dialogue)
        except Exception:
            pass

        return self._keyword_summary(messages)

    def _format_dialogue(self, messages: list[dict]) -> str:
        """将消息格式化为对话文本。"""
        parts = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            tool_calls = m.get("tool_calls")

            if role == "user":
                parts.append(f"用户: {content[:500]}")
            elif role == "assistant":
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {}).get("name", "?")
                        parts.append(f"夸父: [调用 {fn}]")
                elif content:
                    parts.append(f"夸父: {content[:500]}")
            elif role == "tool":
                if content and len(str(content)) > 20:
                    parts.append(f"  [工具: {str(content)[:200]}]")
        return "\n".join(parts)

    def _keyword_summary(self, messages: list[dict]) -> str:
        "基于关键字的摘要回退方案。"
        rounds = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user":
                rounds.append(f"用户: {content[:200]}")
            elif role == "assistant":
                tc = m.get("tool_calls")
                if tc:
                    rounds.append(f"  调用: {tc[0]['function']['name']}")
                else:
                    rounds.append(f"夸父: {content[:200]}")
            elif role == "tool":
                if content and len(content) > 20:
                    rounds.append(f"  结果: {str(content)[:100]}...")
        text = " | ".join(rounds)
        if len(text) > 800:
            return text[:800] + "..."
        return text


# ═══════════════════════════════════════════════════════════════════
# P0-1: BudgetReduction (廉价层管线)
# ═══════════════════════════════════════════════════════════════════

# 各类工具结果的"安全截断长度"（字符数）
# 超过此长度的结果会被就地裁剪，保留前缀+摘要句式
BUDGET_REDUCTION_LIMITS = {
    "default": 3000,         # 通用工具
    "search": 5000,          # web_search 结果列表可能很长但结构稀疏
    "read_file": 5000,       # 读文件内容
    "terminal": 4000,        # 终端输出
    "web_fetch": 8000,       # 网页抓取全文
    "web_extract": 8000,     # 网页提取
    "list_dir": 2000,        # 目录列表
    "git_log": 3000,         # git log 输出
}


def budget_reduce_output(
    content: str,
    tool_name: str = "",
    hard_limit: int = 8000,
) -> str:
    """P0-1: 在工具结果进入上下文前就地裁剪超大输出。

    策略（非破坏性，不丢关键信息）：
    1. 超长内容 → 保留头部 + 尾部，中间替换为统计摘要
    2. 结构化数据（JSON 数组）→ 保留前 N 条 + 元素计数
    3. 代码/日志 → 保留开头关键部分

    Claude Code 参考：Budget Reduction 是 5 层管线中的第 1 层，
    唯一"零 token 成本"的压缩操作。
    """
    if not content or len(content) < 2000:
        return content  # 小结果不处理

    limit = BUDGET_REDUCTION_LIMITS.get(tool_name, BUDGET_REDUCTION_LIMITS["default"])
    limit = min(limit, hard_limit)  # 不超过硬上限

    if len(content) <= limit:
        return content

    # ── 检测 JSON 数组 ─────────────────────────────────────────
    stripped = content.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            data = json.loads(stripped)
            if isinstance(data, list) and len(data) > 20:
                # 保留前 N 条 + 统计信息
                keep = max(10, limit // 500)
                head = json.dumps(data[:keep], ensure_ascii=False, indent=2)
                return (
                    f"{head}\n\n"
                    f"[BudgetReduction: JSON 数组共 {len(data)} 条，此处仅展示前 {keep} 条。"
                    f"原长 {len(content)} 字，已压缩 {len(data) - keep} 条]"
                )
        except (json.JSONDecodeError, TypeError):
            pass

    # ── 检测 JSON 对象（如 web_search 返回） ──────────────────
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            # 遍历查找大字符串字段
            return _reduce_json_object(data, limit)
        except (json.JSONDecodeError, TypeError):
            pass

    # ── 普通文本：保留头 + 尾 + 中间摘要 ──────────────────────
    head_chars = limit // 2
    tail_chars = min(limit // 3, 1000)
    original_len = len(content)

    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars > 0 else ""

    # 估算行数
    lines = content.count("\\n") + 1

    return (
        f"{head}\n\n"
        f"[BudgetReduction: 原输出 {original_len} 字 / ~{lines} 行，"
        f"此处展示头部 {head_chars} 字 + 尾部 {tail_chars} 字。"
        f"原始完整结果在执行日志中可查]\n\n"
        f"{tail}"
    )


def _reduce_json_object(data: dict, limit: int) -> str:
    """递归缩减 JSON 对象中的超大字段。"""
    def _walk(v, depth=0):
        if depth > 3:
            return str(v)[:200] if isinstance(v, str) else v
        if isinstance(v, dict):
            result = {}
            for k, val in v.items():
                val_str = str(val)
                if len(val_str) > 1000 and isinstance(val, str):
                    result[k] = val_str[:500] + f" [...truncated, orig {len(val_str)} chars]"
                elif isinstance(val, (dict, list)):
                    result[k] = _walk(val, depth + 1)
                else:
                    result[k] = val
            return result
        if isinstance(v, list):
            if len(v) > 15:
                return _walk(v[:15], depth) + [f"...({len(v) - 15} more items)"]
            return [_walk(item, depth + 1) for item in v]
        return v

    reduced = _walk(data)
    result = json.dumps(reduced, ensure_ascii=False, indent=2)
    if len(result) > limit:
        return result[:limit] + f"\n[...truncated, original output was {len(json.dumps(data, ensure_ascii=False))} chars]"
    return result
