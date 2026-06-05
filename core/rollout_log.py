"""
core/rollout_log.py — Rollout 事件日志（事件源 + 游标查询）

不破坏 SessionStore 现有接口。在 SQLite 基础上增加 JSONL 事件流层。

事件驱动持久化模式（源自 Codex CLI Rollout）：
  - 每个 session 对应一个 JSONL 文件
  - 每条记录是一个 RolloutEvent（包含类型、时间戳、数据）
  - 支持游标分页查询、增量读取、归档

设计原则：
  - SessionStore 继续做快速查询（SQLite 不改）
  - RolloutLog 做事件溯源（JSONL 追加写）
  - 两者共存：SessionStore 写 SQLite + RolloutLog 写 JSONL
  - 可通过 RolloutLog 完全重建 SessionStore
"""

import json
import time
import os
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Iterator

logger = logging.getLogger("kuafu.rollout")

ROOT_DIR = Path(__file__).resolve().parent.parent
ROLLOUT_DIR = ROOT_DIR / "memory" / "rollout"


# =========================================================================
# 事件类型
# =========================================================================

@dataclass
class RolloutEvent:
    """单条 Rollout 事件。

    type 取值: session_create, user_message, assistant_message, tool_call,
               tool_result, turn_start, turn_end, compact, meta, fork, archive
    """
    type: str
    session_id: str
    timestamp: float = 0.0
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# =========================================================================
# Rollout 日志
# =========================================================================

class RolloutLog:
    """Rollout 事件日志——JSONL 追加写 + 游标查询。

    用法：
        log = RolloutLog()
        log.append(RolloutEvent("user_message", "sess_123",
                    data={"role": "user", "content": "hello"}))
        events = log.query("sess_123", limit=10, offset=5)
        cursor = log.cursor("sess_123", start_from="last_read")
    """

    def __init__(self, rollout_dir: Optional[Path] = None):
        self.rollout_dir = rollout_dir or ROLLOUT_DIR
        self.rollout_dir.mkdir(parents=True, exist_ok=True)

    # ── 写入 ──────────────────────────────────────────────────────

    def append(self, event: RolloutEvent) -> str:
        """追加一条事件。返回写入的 JSON 字符串。"""
        path = self._get_path(event.session_id)
        line = event.to_json()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return line

    def append_raw(self, session_id: str, event_type: str,
                   data: Optional[dict] = None) -> str:
        """快捷追加（自动创建 RolloutEvent）。"""
        event = RolloutEvent(
            type=event_type,
            session_id=session_id,
            data=data or {},
        )
        return self.append(event)

    # ── 查询 ──────────────────────────────────────────────────────

    def query(self, session_id: str, limit: int = 50,
              offset: int = 0) -> list[RolloutEvent]:
        """查询某 session 的事件列表（游标分页）。"""
        path = self._get_path(session_id)
        if not path.exists():
            return []

        events = []
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                if i < offset:
                    continue
                if len(events) >= limit:
                    break
                try:
                    data = json.loads(line)
                    events.append(RolloutEvent(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return events

    def query_by_type(self, session_id: str, event_type: str,
                      limit: int = 50) -> list[RolloutEvent]:
        """按事件类型过滤查询。"""
        path = self._get_path(session_id)
        if not path.exists():
            return []

        events = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == event_type:
                        events.append(RolloutEvent(**data))
                        if len(events) >= limit:
                            break
                except (json.JSONDecodeError, TypeError):
                    continue
        return events

    def cursor(self, session_id: str,
               start_from: int = 0) -> Iterator[RolloutEvent]:
        """返回游标迭代器，从指定行号开始增量读取。"""
        path = self._get_path(session_id)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                if i < start_from:
                    continue
                try:
                    yield RolloutEvent(**json.loads(line))
                except (json.JSONDecodeError, TypeError):
                    continue

    def count(self, session_id: str) -> int:
        """返回 session 的事件总数。"""
        path = self._get_path(session_id)
        if not path.exists():
            return 0
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def list_sessions(self) -> list[str]:
        """列出所有有 rollout 日志的 session ID。"""
        if not self.rollout_dir.exists():
            return []
        sessions = []
        for f in sorted(self.rollout_dir.glob("*.jsonl")):
            sid = f.stem
            if sid:
                sessions.append(sid)
        return sessions

    def get_meta(self, session_id: str) -> Optional[dict]:
        """获取 session 元数据（第一条 meta 事件）。"""
        events = self.query_by_type(session_id, "meta", limit=1)
        if events:
            return events[0].data
        return None

    # ── 归档 ──────────────────────────────────────────────────────

    def archive(self, session_id: str) -> bool:
        """归档 session：将日志移到 archive 目录。"""
        archive_dir = self.rollout_dir / "archived"
        archive_dir.mkdir(parents=True, exist_ok=True)

        src = self._get_path(session_id)
        if not src.exists():
            return False

        dst = archive_dir / f"{session_id}.jsonl"
        # 避免覆盖
        if dst.exists():
            ts = int(time.time())
            dst = archive_dir / f"{session_id}_{ts}.jsonl"

        os.rename(str(src), str(dst))
        logger.info(f"📦 归档 rollout: {session_id} → {dst.name}")
        return True

    def restore(self, session_id: str) -> bool:
        """从 archive 恢复 session。"""
        archive_dir = self.rollout_dir / "archived"
        src = archive_dir / f"{session_id}.jsonl"
        if not src.exists():
            # 尝试查找带时间戳的
            matches = list(archive_dir.glob(f"{session_id}_*.jsonl"))
            if not matches:
                return False
            src = matches[-1]  # 最新的

        dst = self._get_path(session_id)
        os.rename(str(src), str(dst))
        logger.info(f"📂 恢复 rollout: {session_id} ← {src.name}")
        return True

    # ── 辅助 ──────────────────────────────────────────────────────

    def _get_path(self, session_id: str) -> Path:
        """获取 session 的 JSONL 文件路径。"""
        # 安全处理 session_id（防止路径穿越）
        safe_name = session_id.replace("/", "_").replace("\\", "_")
        return self.rollout_dir / f"{safe_name}.jsonl"
