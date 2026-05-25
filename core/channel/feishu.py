"""
channel/feishu.py — 飞书消息通道适配器

将 FeishuBot 适配到 MessageChannel 接口。
通过 on_message 回调桥接，不破坏现有 feishu_bot.py。
"""

from __future__ import annotations

import threading
from typing import Optional, Callable

from core.feishu_bot import FeishuBot
from core.channel.base import MessageChannel, Message, SendResult


class FeishuChannel(MessageChannel):
    """飞书消息通道适配器，包装 FeishuBot。"""

    @property
    def name(self) -> str:
        return "feishu"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        chat_id: str = "",
    ):
        self._lock = threading.Lock()
        self._inbox: list[Message] = []
        self._bot = FeishuBot(
            app_id=app_id,
            app_secret=app_secret,
            chat_id=chat_id,
            poll_interval=5.0,
            on_message=self._on_message_callback,
        )
        self._running = False

    def _on_message_callback(self, text: str, msg_id: str) -> str:
        """FeishuBot on_message 回调：将消息收入 inbox，返回空字符串表示不自动回复。"""
        msg = Message(
            text=text,
            msg_id=msg_id,
            platform="feishu",
            chat_id=self._bot.chat_id,
        )
        with self._lock:
            self._inbox.append(msg)
        # 返回空字符串——由上层决定如何回复
        return ""

    def send(self, text: str, **kwargs) -> SendResult:
        """发送消息。"""
        target_chat = kwargs.get("chat_id", "")
        ok = self._bot.send_text(text, chat_id=target_chat)
        return SendResult(success=ok, platform="feishu")

    def poll(self) -> list[Message]:
        """取出所有待处理消息。"""
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    def start(self) -> None:
        """启动飞书 Bot 轮询。"""
        self._running = True
        self._bot.start()

    def stop(self) -> None:
        """停止飞书 Bot 轮询。"""
        self._running = False
        self._bot.stop()
