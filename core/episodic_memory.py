"""
夸傅 Episodic Memory（情节记忆）系统

职责：
1. 将每轮对话的关键信息提取为结构化事件（EpisodicEvent）
2. 事件持久化到 JSONL 文件（按 session 组织）
3. 用户输入命中回溯关键词时检索相关事件
4. 检索到的事件注入 System Reminder，不占用 Working Memory 位置

设计原则：
- 不替换现有上下文压缩管线的任何环节
- 只做"按需注入"——只有在检测到回溯意图时才检索
- 零 LLM 调用开销——回溯检测纯关键词匹配，事件提取在工具执行后同步完成
- 存储是离散的 JSONL，不是向量库——关键词检索足够精确

与 NMM 记忆系统的边界：
- NMM：跨 session 的长期记忆（用户偏好、事实知识）
- Episodic Memory：单 session 内的精确历史（参数值、决策、工具结果）
"""

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ── 存储目录 ─────────────────────────────────────────────────────────
EPISODIC_DIR = Path(__file__).resolve().parent.parent / "memory" / "episodic"


# ── 数据结构 ─────────────────────────────────────────────────────────

@dataclass
class EpisodicEvent:
    """单轮对话的情节事件。

    记录了本轮对话中发生的关键操作和决策，供后续回溯检索。

    Attributes:
        session_id: 所属会话 ID
        turn: 轮次编号（1-based）
        focus: 本轮聚焦的任务/问题（简短概括，~50 chars）
        user_intent: 用户的输入意图（提取核心动词+对象）
        tools_called: 调用的工具列表 [(name, args_preview), ...]
        key_result: 本轮关键产出/结论（~200 chars）
        decisions: 做出的决策/确定的参数值（~200 chars）
        errors: 发生的错误（如果有）
        has_error: 是否有错误
        timestamp: 事件创建时间戳
    """
    session_id: str
    turn: int
    focus: str = ""
    user_intent: str = ""
    tools_called: list = field(default_factory=list)
    key_result: str = ""
    decisions: str = ""
    errors: str = ""
    has_error: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EpisodicEvent":
        return EpisodicEvent(
            session_id=d.get("session_id", ""),
            turn=d.get("turn", 0),
            focus=d.get("focus", ""),
            user_intent=d.get("user_intent", ""),
            tools_called=d.get("tools_called", []),
            key_result=d.get("key_result", ""),
            decisions=d.get("decisions", ""),
            errors=d.get("errors", ""),
            has_error=d.get("has_error", False),
            timestamp=d.get("timestamp", 0.0),
        )

    def to_summary(self, max_chars: int = 300) -> str:
        """将事件浓缩为一段可读摘要，供 System Reminder 注入。"""
        parts = [f"📖 第 {self.turn} 轮 | {self.focus}"]
        if self.user_intent:
            parts.append(self.user_intent[:80])
        if self.decisions:
            parts.append(f"  → {self.decisions[:120]}")
        if self.key_result:
            parts.append(f"  → {self.key_result[:120]}")
        if self.tools_called:
            tool_str = ", ".join(f"{name}({preview[:40]})" for name, preview in self.tools_called[:3])
            parts.append(f"  🔧 {tool_str}")
        if self.has_error and self.errors:
            parts.append(f"  ⚠️ {self.errors[:120]}")
        text = " | ".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text


# ── 回溯信号检测 ─────────────────────────────────────────────────────

# 用户输入中触发回溯检索的关键词
_BACKTRACK_PATTERNS = re.compile(
    r"(刚才|之前|上一步|上一次|上一轮|前面|前面说的|前面提到|"
    r"之前说的|之前提到的|刚刚|刚刚说的|上次说|"
    r"刚才那个|刚才的|前面的|最早|最开始|第一轮|"
    r"再看一下|再看一遍|重新看|回顾|回看|"
    r"之前那个|上一次那个)"
)

# 与当前轮次强相关的关键词（意味着用户可能在问本轮相关的东西，不需要检索历史）
_CURRENT_TURN_PATTERNS = re.compile(
    r"(现在|当前|这个|这里|这个文件|这个函数|这个值|"
    r"刚才你|刚刚你|你这轮|你刚才)"
)


def has_backtrack_signal(user_input: str) -> bool:
    """检测用户输入是否包含回溯历史的关键词。

    返回 True 表示用户可能在引用之前轮次的内容，需要检索 Episodic Events。

    规则：
    - 命中 _BACKTRACK_PATTERNS 且未明确指向"当前"
    - 如果同时命中回溯和当前，视为当前（不检索）
    - 如果输入很短（<8 chars）且不含回溯关键词，不检索
    """
    if not user_input or len(user_input.strip()) < 4:
        return False

    # 先检测"当前"关键词——如果用户说的是"这个参数"（指当前的），不检索
    has_current = bool(_CURRENT_TURN_PATTERNS.search(user_input))
    has_backtrack = bool(_BACKTRACK_PATTERNS.search(user_input))

    if has_current and has_backtrack:
        # "刚才你写的这个文件" → 是当前话题，不检索
        return False

    return has_backtrack


def extract_focus_from_input(user_input: str) -> str:
    """从用户输入中提取核心关注点（用于匹配存储的事件）。"""
    # 移除回溯词后提取关键词
    cleaned = _BACKTRACK_PATTERNS.sub("", user_input).strip()
    # 取前 30 个有效字符
    if len(cleaned) > 40:
        cleaned = cleaned[:40] + "..."
    return cleaned


# ── 事件提取 ─────────────────────────────────────────────────────────

def extract_user_intent(user_input: str) -> str:
    """从用户原始输入中提取意图概要。"""
    text = user_input.strip()
    if not text:
        return ""
    # 取第一句话作为意图
    first = text.split("。")[0].split(".")[0].split("\n")[0]
    if len(first) > 60:
        first = first[:60] + "..."
    return first


def extract_decisions(tool_calls: list, tool_results: list) -> str:
    """从工具调用和结果中提取关键词决策。

    提取场景：
    - write_file/patch → 写入了什么
    - terminal → 设置/配置了什么
    - 数值参数 → 关键值
    """
    decisions = []
    for name, args_preview in tool_calls:
        args_lower = args_preview.lower()
        if name in ("write_file",):
            # 提取文件名
            for kw in ("path=", "path =", "'", '"'):
                if kw in args_preview:
                    decisions.append(f"写文件 {args_preview[:60]}")
                    break
        elif name in ("patch",):
            decisions.append(f"修改文件 {args_preview[:60]}")
        elif name == "terminal" and any(kw in args_lower for kw in ("config", "set", "export", "install")):
            decisions.append(f"执行配置 {args_preview[:60]}")
        elif name == "calculate":
            decisions.append(f"计算 {args_preview[:40]}")
    return "; ".join(decisions[:3])  # 最多 3 条决策


# ── 存储与检索 ───────────────────────────────────────────────────────

class EpisodicMemory:
    """Episodic Memory 存储与检索。

    按 session 组织为独立的 JSONL 文件，每行一个 EpisodicEvent。
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self._base_dir = Path(base_dir).resolve() if base_dir else EPISODIC_DIR.resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        # 内存缓存：session_id → [EpisodicEvent, ...]
        self._cache: dict[str, list[EpisodicEvent]] = {}
        self._cache_dirty: set[str] = set()

    def _session_path(self, session_id: str) -> Path:
        """获取 session 对应的 JSONL 文件路径。"""
        # 对 session_id 做 sanitize（避免路径穿越）
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
        return self._base_dir / f"{safe}.jsonl"

    def _load_session(self, session_id: str) -> list[EpisodicEvent]:
        """从磁盘加载一个 session 的所有事件到内存缓存。"""
        if session_id in self._cache:
            return self._cache[session_id]

        events: list[EpisodicEvent] = []
        path = self._session_path(session_id)
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").strip().split("\n"):
                    if line.strip():
                        d = json.loads(line)
                        events.append(EpisodicEvent.from_dict(d))
            except (json.JSONDecodeError, IOError):
                pass  # 文件损坏时返回空列表

        self._cache[session_id] = events
        return events

    def _flush_session(self, session_id: str):
        """将内存缓存中指定 session 的脏数据写回磁盘。"""
        if session_id not in self._cache_dirty:
            return
        events = self._cache.get(session_id, [])
        path = self._session_path(session_id)
        try:
            lines = "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in events)
            path.write_text(lines + "\n", encoding="utf-8")
            self._cache_dirty.discard(session_id)
        except IOError:
            pass  # 写入失败时静默

    def append(self, event: EpisodicEvent):
        """追加一个事件。立即写入内存缓存，惰性写磁盘。"""
        session_id = event.session_id
        if session_id not in self._cache:
            self._load_session(session_id)
        self._cache[session_id].append(event)
        self._cache_dirty.add(session_id)

        # 每 10 个事件 flush 一次磁盘
        if len(self._cache[session_id]) % 10 == 0:
            self._flush_session(session_id)

    def flush_all(self):
        """将全部脏数据写回磁盘。"""
        for sid in list(self._cache_dirty):
            self._flush_session(sid)

    def get_session_events(self, session_id: str) -> list[EpisodicEvent]:
        """获取一个 session 的全部事件（按轮次升序）。"""
        events = self._load_session(session_id)
        return sorted(events, key=lambda e: (e.turn, e.timestamp))

    def get_recent(self, session_id: str, n: int = 3) -> list[EpisodicEvent]:
        """获取最近 N 轮事件。"""
        events = self.get_session_events(session_id)
        return events[-n:] if len(events) >= n else events[:]

    def search(
        self,
        session_id: str,
        query: str = "",
        max_results: int = 3,
    ) -> list[EpisodicEvent]:
        """通过关键词检索事件。

        匹配字段：focus、user_intent、decisions、key_result、tools_called
        使用简单的全文关键词匹配，不依赖向量库。

        Args:
            session_id: 要检索的会话 ID
            query: 搜索关键词
            max_results: 最大返回条数

        Returns:
            匹配的事件列表，按轮次降序（最新的优先）
        """
        events = self.get_session_events(session_id)
        if not events or not query:
            return []

        keywords = [kw.strip().lower() for kw in query.split() if len(kw.strip()) > 1]

        scored: list[tuple[float, EpisodicEvent]] = []
        for event in events:
            score = self._match_score(event, keywords)
            if score > 0:
                scored.append((score, event))

        # 按分数降序，同分按轮次降序
        scored.sort(key=lambda x: (-x[0], -x[1].turn))
        return [e for _, e in scored[:max_results]]

    def _match_score(self, event: EpisodicEvent, keywords: list[str]) -> float:
        """计算事件与关键词的匹配分数。

        分数 = 匹配的关键词数 / 总关键词数
        字段权重：decisions > focus > key_result > user_intent > tools_called
        """
        if not keywords:
            return 0.0

        search_text = (
            f"{event.focus} {event.user_intent} {event.decisions} "
            f"{event.key_result} {event.errors} "
        )
        # tools_called 也加入搜索
        for name, preview in event.tools_called:
            search_text += f" {name} {preview} "

        search_text = search_text.lower()

        matched = 0
        for kw in keywords:
            if kw in search_text:
                matched += 1

        if matched == 0:
            return 0.0

        # 额外加权：decisions 中的匹配加分
        decision_text = event.decisions.lower()
        decision_matches = sum(1 for kw in keywords if kw in decision_text)
        bonus = decision_matches * 0.3

        return (matched / len(keywords)) + bonus

    def clear_session(self, session_id: str):
        """清除指定 session 的所有事件。"""
        self._cache.pop(session_id, None)
        self._cache_dirty.discard(session_id)
        path = self._session_path(session_id)
        if path.exists():
            try:
                path.unlink()
            except IOError:
                pass

    @property
    def stats(self) -> dict:
        """统计信息。"""
        total_events = sum(len(events) for events in self._cache.values())
        total_sessions = len(self._cache)
        dirty_sessions = len(self._cache_dirty)
        return {
            "total_sessions": total_sessions,
            "total_events": total_events,
            "dirty_sessions": dirty_sessions,
            "disk_location": str(self._base_dir),
        }
