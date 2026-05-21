"""白板架构 — 外部状态存储模块。

白板 (Whiteboard) 是夸父的外部推理状态存储，
不消耗 LLM 上下文窗口。LLM 每步只读取当前所需的分区。
"""

from core.whiteboard.whiteboard import Whiteboard
from core.whiteboard.decomposer import Decomposer, Step
from core.whiteboard.executor import WhiteboardExecutor

__all__ = ["Whiteboard", "Decomposer", "Step", "WhiteboardExecutor"]
