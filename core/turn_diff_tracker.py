"""
core/turn_diff_tracker.py — Turn Diff Tracker（纯内存 diff 追踪）

源自 Codex CLI TurnDiffTracker：
  - 追踪每次 tool 调用的文件变更
  - 仅在有 diff 时标记"需要更新记忆"
  - 避免每轮都写记忆浪费 token

与现有 Observer 的关系：
  - Observer 收集所有工具调用的统计信息（用于进化）
  - TurnDiffTracker 专注于文件变更的 diff 追踪（用于记忆更新）
  - 两者互补：Observer 告诉你"做了什么"，DiffTracker 告诉你"改变了什么"
"""

import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kuafu.turn_diff")


class FileChange:
    """一次文件变更记录。"""
    def __init__(self, path: str, change_type: str = "modified",
                 old_preview: str = "", new_preview: str = "",
                 tool_name: str = ""):
        self.path = path
        self.change_type = change_type     # created / modified / deleted
        self.old_preview = old_preview[:200]
        self.new_preview = new_preview[:200]
        self.tool_name = tool_name
        self.timestamp = time.time()

    def is_significant(self) -> bool:
        """判断变更是否显著（足够重要才更新记忆）。"""
        if self.change_type == "deleted":
            return True
        if len(self.new_preview) > 50:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "type": self.change_type,
            "tool": self.tool_name,
            "timestamp": self.timestamp,
        }


class TurnDiffTracker:
    """纯内存 diff 追踪器。

    跟踪当前 turn 中所有 tool 调用导致的文件变更。
    在 turn 结束时检查是否有显著变更，决定是否触发记忆更新。
    """

    def __init__(self):
        self._changes: list[FileChange] = []
        self._enabled = True
        self._turn_count = 0

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def start_turn(self):
        """开始新的一轮。"""
        self._turn_count += 1
        self._changes = []

    def record_change(self, path: str, change_type: str = "modified",
                      old_preview: str = "", new_preview: str = "",
                      tool_name: str = ""):
        """记录一次文件变更。"""
        if not self._enabled:
            return
        change = FileChange(path, change_type, old_preview,
                           new_preview, tool_name)
        self._changes.append(change)
        logger.debug(f"📝 Diff: {change_type} {path} ({tool_name})")

    def record_terminal_output(self, command: str, output: str):
        """从 terminal 输出中检测文件变更。

        简单检测常见的文件操作输出模式（git diff / create / write）。
        """
        if not self._enabled:
            return
        # 检测 git diff 输出
        if "diff --git" in output[:200]:
            self._changes.append(FileChange(
                path="git: workspace",
                change_type="modified",
                new_preview=output[:200],
                tool_name="terminal(git)",
            ))
        # 检测 touch / mkdir -p
        if "touch " in command or "mkdir " in command:
            self._changes.append(FileChange(
                path=command,
                change_type="created",
                new_preview=output[:100],
                tool_name="terminal",
            ))

    def has_changes(self) -> bool:
        """是否有任何变更。"""
        return len(self._changes) > 0

    def has_significant_changes(self) -> bool:
        """是否有显著变更（需要更新记忆）。"""
        return any(c.is_significant() for c in self._changes)

    def get_changes(self) -> list[FileChange]:
        """获取当前 turn 的所有变更。"""
        return self._changes.copy()

    def get_change_summary(self) -> str:
        """获取变更摘要文本。"""
        if not self._changes:
            return ""

        by_type = {"created": 0, "modified": 0, "deleted": 0}
        for c in self._changes:
            by_type[c.change_type] = by_type.get(c.change_type, 0) + 1

        parts = [f"📝 本 turn 文件变更 ({len(self._changes)} 次)"]
        for change_type, count in by_type.items():
            if count > 0:
                icon = {"created": "➕", "modified": "✏️", "deleted": "🗑️"}
                parts.append(f"  {icon.get(change_type, '•')} {change_type}: {count}")

        return "\n".join(parts)

    def should_update_memory(self) -> bool:
        """判断是否需要更新记忆。

        决策逻辑：
          - 有显著变更 → 更新
          - 无变更 → 不更新
          - 被禁用 → 不更新
        """
        if not self._enabled:
            return False
        if not self._changes:
            return False
        return self.has_significant_changes()

    def reset(self):
        """重置追踪器。"""
        self._changes = []
        self._turn_count = 0
