"""
channel/gateway_loop.py — Gateway 消息循环

消费所有已注册通道的新消息，交给夸父 Agent 处理。
自动回复或通过指定通道发送结果。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from core.channel.base import MessageChannel, Message, SendResult
from core.channel.manager import ChannelManager

logger = logging.getLogger("kuafu.gateway")


class GatewayLoop:
    """Gateway 消息循环：消费通道消息 → Agent 处理 → 回复。"""

    def __init__(
        self,
        agent: Any,
        channel_manager: ChannelManager,
        poll_interval: float = 2.0,
    ):
        self.agent = agent
        self.channels = channel_manager
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """启动消息循环（后台线程）。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="gateway-loop",
        )
        self._thread.start()
        channels = self.channels.list()
        print(f"[GatewayLoop] 🟢 已启动，通道: {', '.join(channels) if channels else '(无)'}")

    def stop(self):
        """停止消息循环。"""
        self._running = False
        print("[GatewayLoop] 🔴 已停止")

    def _loop(self):
        """主循环：轮询所有通道的消息并处理。"""
        while self._running:
            try:
                messages = self.channels.poll_all()
                for msg in messages:
                    self._handle_message(msg)
            except Exception as e:
                logger.error(f"GatewayLoop 轮询异常: {e}")

            for _ in range(int(self.poll_interval / 0.2)):
                if not self._running:
                    return
                time.sleep(0.2)

    def _handle_message(self, msg: Message):
        """处理单条消息。"""
        if not msg.text.strip():
            return

        print(f"[GatewayLoop] 📩 {msg.platform}/{msg.chat_id}: {msg.text[:60]}")

        try:
            result = self.agent.run(msg.text)
            reply = result.get("result", "")
            if not reply:
                return

            # 回消息到来源通道
            channel = self.channels.get(msg.platform)
            if channel:
                channel.send(reply, chat_id=msg.chat_id)
                print(f"[GatewayLoop] ✅ 已回复 {msg.platform}")
        except Exception as e:
            print(f"[GatewayLoop] ❌ 处理失败: {e}")
            channel = self.channels.get(msg.platform)
            if channel:
                channel.send(f"❌ 处理出错: {str(e)[:200]}", chat_id=msg.chat_id)
