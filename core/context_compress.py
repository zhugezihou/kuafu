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
import os
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


class ToolResultStore:
    """工具结果磁盘存储 + 自动摘要 (Microcompact)。

    大工具结果写入磁盘，上下文中只留简短摘要 + 文件路径。
    阈值：超过 MICRO_THRESHOLD_CHARS 的结果自动写磁盘。
    """

    MICRO_THRESHOLD_CHARS = 2000  # 超过此长度才做 microcompact

    def __init__(self, base_dir: Optional[str] = None):
        base = base_dir or os.environ.get("KUAFU_DATA_DIR") or str(Path.home() / ".kuafu")
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
        summarizer: Optional["LocalSummarizer"] = None,
        keep_recent_rounds: int = 5,
        summary_prompt: str = "",
    ):
        self.summarizer = summarizer or LocalSummarizer()
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
        """基于关键字的摘要回退方案。"""
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
