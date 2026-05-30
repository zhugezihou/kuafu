"""
channel/manager.py — ChannelManager 多通道管理器

统一管理所有消息通道的注册、启动、停止和消息轮询。
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.channel")


class ChannelManager:
    """多平台消息通道管理器。"""

    def __init__(self):
        self._channels: dict[str, MessageChannel] = {}

    def register(self, channel: MessageChannel) -> None:
        """注册一个消息通道。"""
        self._channels[channel.name] = channel
        logger.info(f"通道已注册: {channel.name}")

    def get(self, name: str) -> Optional[MessageChannel]:
        """按名称获取通道。"""
        return self._channels.get(name)

    def list(self) -> list[str]:
        """列出所有已注册通道。"""
        return list(self._channels.keys())

    def start_all(self) -> None:
        """启动所有已注册通道。"""
        for name, channel in self._channels.items():
            logger.info(f"启动通道: {name}")
            try:
                channel.start()
            except Exception as e:
                logger.error(f"通道 {name} 启动失败: {e}")

    def stop_all(self) -> None:
        """停止所有通道。"""
        for name, channel in self._channels.items():
            try:
                channel.stop()
            except Exception as e:
                logger.error(f"通道 {name} 停止失败: {e}")

    def poll_all(self) -> list[Message]:
        """轮询所有通道的新消息。"""
        messages: list[Message] = []
        for name, channel in self._channels.items():
            try:
                msgs = channel.poll()
                if msgs:
                    logger.debug(f"通道 {name}: {len(msgs)} 条新消息")
                    messages.extend(msgs)
            except Exception as e:
                logger.error(f"通道 {name} 轮询失败: {e}")
        return messages

    def broadcast(self, text: str, **kwargs) -> list[SendResult]:
        """向所有通道广播消息。"""
        results: list[SendResult] = []
        for name, channel in self._channels.items():
            try:
                result = channel.send(text, **kwargs)
                results.append(result)
            except Exception as e:
                results.append(SendResult(success=False, platform=name, error=str(e)))
        return results
