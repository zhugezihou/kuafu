"""
core/agent_tree.py — Agent 树系统

源自 Codex CLI AgentControl + AgentRegistry + AgentPath：
  - AgentPath：类似文件系统路径的 agent 寻址（"/", "/child/grandchild", ".."）
  - AgentRegistry：全局 agent 注册表（name → LiveAgent）
  - watch 状态订阅：AgentStatus 通过 watch channel 通知

与现有 subagent.py 的关系：
  - AgentTree 是子 agent 的管理层
  - subagent.py 是子 agent 的执行层
  - AgentTree 记录和管理 agent 树结构
  - subagent.py 执行实际的任务委托
"""

import time
import json
import threading
import logging
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("kuafu.agent_tree")


# =========================================================================
# Agent 状态
# =========================================================================

class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"     # 等待审批或子 agent
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# =========================================================================
# AgentPath — 类似文件系统路径的 agent 寻址
# =========================================================================

class AgentPath:
    """Agent 路径寻址系统。

    格式：
      "/"              → 根 agent
      "/child"         → 根的子 agent
      "/child/grand"   → 孙 agent
      ".."             → 父
      "../sibling"     → 兄弟

    用法：
        path = AgentPath.parse("/child/grand")
        parent = path.parent()       # "/child"
        root = AgentPath.root()
    """

    def __init__(self, segments: list[str]):
        self._segments = segments

    @classmethod
    def root(cls) -> "AgentPath":
        return cls([])

    @classmethod
    def parse(cls, path_str: str) -> "AgentPath":
        """解析路径字符串。"""
        if not path_str or path_str == "/":
            return cls([])

        # 处理相对路径
        if path_str.startswith("/"):
            path_str = path_str[1:]

        segments = []
        for part in path_str.split("/"):
            if part == "..":
                if segments:
                    segments.pop()
            elif part and part != ".":
                segments.append(part)

        return cls(segments)

    def parent(self) -> "AgentPath":
        if not self._segments:
            return AgentPath.root()
        return AgentPath(self._segments[:-1])

    def child(self, name: str) -> "AgentPath":
        return AgentPath(self._segments + [name])

    def resolve(self, relative: str) -> Optional["AgentPath"]:
        """解析相对路径。如 resolve("..") → parent。"""
        if relative == "..":
            return self.parent()
        if relative.startswith("../"):
            parent = self.parent()
            return AgentPath.parse(relative[2:]) if parent.is_root() else None
        if relative.startswith("/"):
            return AgentPath.parse(relative)
        return self.child(relative)

    def is_root(self) -> bool:
        return len(self._segments) == 0

    @property
    def name(self) -> str:
        return self._segments[-1] if self._segments else "/"

    def __str__(self) -> str:
        if not self._segments:
            return "/"
        return "/" + "/".join(self._segments)

    def __eq__(self, other):
        if isinstance(other, AgentPath):
            return self._segments == other._segments
        return False

    def __hash__(self):
        return hash(str(self))


# =========================================================================
# LiveAgent — 运行中的 agent 信息
# =========================================================================

class LiveAgent:
    """一个正在运行的 live agent。"""

    def __init__(self, name: str, path: AgentPath,
                 parent_path: Optional[AgentPath] = None,
                 metadata: Optional[dict] = None):
        self.name = name
        self.path = path
        self.parent_path = parent_path
        self.metadata = metadata or {}
        self.status = AgentStatus.IDLE
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self._listeners: list[Callable] = []

    def set_status(self, status: AgentStatus):
        """更新状态并通知监听器。"""
        old = self.status
        self.status = status
        if status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED):
            self.completed_at = time.time()
        for cb in self._listeners:
            try:
                cb(self, old, status)
            except Exception as e:
                logger.warning(f"Agent 状态监听器异常: {e}")

    def on_status_change(self, callback: Callable):
        """注册状态变更回调。"""
        self._listeners.append(callback)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "parent": str(self.parent_path) if self.parent_path else None,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }


# =========================================================================
# AgentRegistry — 全局 agent 注册表
# =========================================================================

class AgentRegistry:
    """全局 agent 注册表。

    线程安全。支持按路径查询、按名称查询、列出所有 agent。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._agents: dict[str, LiveAgent] = {}  # path → LiveAgent

    def register(self, agent: LiveAgent) -> bool:
        """注册一个 agent。如果路径已存在则返回 False。"""
        with self._lock:
            key = str(agent.path)
            if key in self._agents:
                return False
            self._agents[key] = agent
            logger.info(f"🌳 注册 agent [{key}]: {agent.name}")
            return True

    def unregister(self, path: AgentPath) -> bool:
        """注销一个 agent。"""
        with self._lock:
            key = str(path)
            if key in self._agents:
                del self._agents[key]
                return True
            return False

    def get(self, path: AgentPath) -> Optional[LiveAgent]:
        """获取指定路径的 agent。"""
        return self._agents.get(str(path))

    def get_by_path_str(self, path_str: str) -> Optional[LiveAgent]:
        """按路径字符串获取。"""
        path = AgentPath.parse(path_str)
        return self.get(path)

    def get_root(self) -> Optional[LiveAgent]:
        """获取根 agent。"""
        return self._agents.get("/")

    def list_children(self, parent_path: AgentPath) -> list[LiveAgent]:
        """列出指定 agent 的所有直接子 agent。"""
        prefix = str(parent_path)
        if prefix != "/":
            prefix += "/"
        children = []
        for key, agent in self._agents.items():
            if key.startswith(prefix) and key != prefix:
                # 确保是直接子节点（不是孙子）
                rest = key[len(prefix):]
                if "/" not in rest:
                    children.append(agent)
        return sorted(children, key=lambda a: a.created_at)

    def list_all(self) -> list[LiveAgent]:
        """列出所有 agent。"""
        return list(self._agents.values())

    def get_tree_diagram(self) -> str:
        """获取 agent 树的文本图示。"""
        root = self.get_root()
        if not root:
            return "(空)"

        lines = [f"🌳 Agent 树 ({len(self._agents)} agents)"]
        lines.append(f"  {root.name} [{root.status.value}]")

        def _render_children(path: AgentPath, indent: int = 2):
            children = self.list_children(path)
            for child in children:
                prefix = "  " * indent
                icon = {"running": "▶", "completed": "✅",
                        "failed": "❌", "idle": "○", "waiting": "⏳",
                        "cancelled": "⛔"}.get(child.status.value, "○")
                lines.append(f"{prefix}{icon} {child.name} [{child.status.value}]")
                _render_children(child.path, indent + 1)

        _render_children(AgentPath.root(), indent=2)
        return "\n".join(lines)

    def get_stats(self) -> dict:
        """获取统计信息。"""
        return {
            "total": len(self._agents),
            "running": sum(1 for a in self._agents.values()
                          if a.status == AgentStatus.RUNNING),
            "completed": sum(1 for a in self._agents.values()
                            if a.status == AgentStatus.COMPLETED),
            "failed": sum(1 for a in self._agents.values()
                          if a.status == AgentStatus.FAILED),
        }


# =========================================================================
# AgentTree — 高层 API
# =========================================================================

class AgentTree:
    """Agent 树管理器——高层 API。

    用法：
        tree = AgentTree()
        tree.init_root("夸父", metadata={"version": "0.4"})

        # 注册子 agent
        child = tree.spawn("调研 agent", AgentPath.parse("/research"))
        child.set_status(AgentStatus.RUNNING)

        # 查询
        agent = tree.resolve("/research")
        children = tree.list_children("/")
    """

    def __init__(self):
        self.registry = AgentRegistry()

    def init_root(self, name: str = "root",
                  metadata: Optional[dict] = None) -> LiveAgent:
        """初始化根 agent。"""
        root = LiveAgent(name, AgentPath.root(), metadata=metadata)
        self.registry.register(root)
        return root

    def spawn(self, name: str, parent_path: AgentPath,
              metadata: Optional[dict] = None) -> Optional[LiveAgent]:
        """在指定父节点下 spawn 一个子 agent。"""
        parent = self.registry.get(parent_path)
        if not parent:
            logger.warning(f"父 agent 不存在: {parent_path}")
            return None

        # 生成唯一子节点名
        base_name = name.replace(" ", "_").lower()[:20]
        path = parent_path.child(base_name)

        # 去重：如果路径已存在，加序号
        counter = 1
        while self.registry.get(path):
            path = parent_path.child(f"{base_name}_{counter}")
            counter += 1

        agent = LiveAgent(
            name=name,
            path=path,
            parent_path=parent_path,
            metadata=metadata,
        )
        self.registry.register(agent)
        return agent

    def resolve(self, path_str: str) -> Optional[LiveAgent]:
        """通过路径字符串查找 agent。"""
        path = AgentPath.parse(path_str)
        return self.registry.get(path)

    def list_children(self, path_str: str) -> list[LiveAgent]:
        """列出子 agent。"""
        path = AgentPath.parse(path_str)
        return self.registry.list_children(path)

    def get_tree(self) -> str:
        """获取树形图。"""
        return self.registry.get_tree_diagram()

    def get_stats(self) -> dict:
        """获取统计。"""
        return self.registry.get_stats()
