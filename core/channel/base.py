"""
channel/base.py — MessageChannel 抽象基类

定义统一的消息通道接口。所有消息平台通过此接口注册到夸父。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Message:
    """统一消息模型。"""
    text: str
    msg_id: str = ""
    platform: str = ""     # "feishu", "telegram", "discord", "email"
    chat_id: str = ""      # 来源群聊/频道 ID
    sender: str = ""       # 发送者 ID
    sender_name: str = ""  # 发送者显示名
    raw: dict = field(default_factory=dict)  # 原始消息数据


@dataclass
class SendResult:
    """发送结果。"""
    success: bool
    msg_id: str = ""
    platform: str = ""
    error: str = ""


class MessageChannel(ABC):
    """消息通道抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """通道唯一标识名。"""
        ...

    @abstractmethod
    def send(self, text: str, **kwargs) -> SendResult:
        """发送消息。"""
        ...

    @abstractmethod
    def poll(self) -> list[Message]:
        """轮询新消息。返回自上次调用以来未处理的消息。"""
        ...

    def start(self) -> None:
        """启动通道（可选重写）。"""
        pass

    def stop(self) -> None:
        """停止通道（可选重写）。"""
        pass
