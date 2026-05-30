"""
channel/wechat_personal.py — 个人微信 WebSocket 通道

使用 Wechaty + wechaty-puppet-service（iPad 协议）连接个人微信。
需要 Wechaty 的 puppet token（可申请免费社区 token）。

架构：
  夸父 (Python)
    └── python-wechaty (通过 gRPC 连接 puppet service)
         └── wechaty-puppet-service (Docker 或云端)
              └── 微信 iPad 协议

环境变量：
  WECHAT_PUPPET_TOKEN — Wechaty Puppet Service Token
                        免费申请：https://wechaty.js.org/docs/puppet-services/
  或
  WECHAT_PUPPET_ENDPOINT — 自定义 puppet 服务地址（可选）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from core.channel.base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.wechat_personal")


class WeChatPersonalChannel(MessageChannel):
    """个人微信 Wechaty 通道。"""

    @property
    def name(self) -> str:
        return "wechat"

    def __init__(self, puppet_token: str = ""):
        self.puppet_token = puppet_token or os.environ.get("WECHAT_PUPPET_TOKEN", "")
        self._lock = threading.Lock()
        self._inbox: list[Message] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._bot: Any = None
        self._contact_self: Any = None
        self._room_cache: dict[str, str] = {}
        self._pending_messages: list[dict] = []

    # ── 消息发送 ──────────────────────────────────────────────

    def send(self, text: str, **kwargs) -> SendResult:
        """发送消息。

        kwargs:
            chat_id: 联系人ID 或 群ID（Room ID）
        """
        target = kwargs.get("chat_id", "")
        if not target:
            return SendResult(success=False, platform="wechat", error="chat_id 未指定")

        # 消息入队，由 wechaty 事件循环发送
        self._pending_messages.append({"text": text, "target": target})
        return SendResult(success=True, platform="wechat")

    # ── 消息接收 ──────────────────────────────────────────────

    def poll(self) -> list[Message]:
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    def _push_message(self, text: str, sender_id: str, sender_name: str,
                      room_id: str = "", msg_id: str = ""):
        msg = Message(
            text=text,
            msg_id=msg_id,
            platform="wechat",
            chat_id=room_id or sender_id,
            sender=sender_id,
            sender_name=sender_name,
        )
        with self._lock:
            self._inbox.append(msg)

    def _send_pending(self):
        """发送待发送消息队列。"""
        if not self._pending_messages:
            return

        batch = list(self._pending_messages)
        self._pending_messages.clear()

        loop = self._get_event_loop()
        if not loop:
            return

        for item in batch:
            try:
                # 异步发送，不能阻塞
                asyncio.run_coroutine_threadsafe(
                    self._async_send(item["text"], item["target"]),
                    loop,
                )
            except Exception as e:
                logger.error(f"[Wechat] 发送消息失败: {e}")

    def _get_event_loop(self):
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

    async def _async_send(self, text: str, target: str):
        """在 wechaty 事件循环中发送消息。"""
        if not self._bot:
            return

        try:
            # 尝试按 Room ID 发送
            room = self._bot.Room.load(target)
            await room.say(text)
        except Exception:
            try:
                contact = self._bot.Contact.load(target)
                await contact.say(text)
            except Exception as e:
                logger.error(f"[Wechat] 发送到 {target} 失败: {e}")

    # ── Wechaty 启动 ──────────────────────────────────────────

    def start(self) -> None:
        """启动 Wechaty 连接。"""
        if self._running:
            return

        if not self.puppet_token:
            print("[Wechat] ⚠️ WECHAT_PUPPET_TOKEN 未配置，通道不可用")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_wechaty,
            daemon=True,
            name="wechaty",
        )
        self._thread.start()
        print("[Wechat] 启动 Wechaty（请扫码登录微信）...")

    def _run_wechaty(self):
        """在后台线程中启动 wechaty 事件循环。"""
        try:
            from wechaty import Wechaty, WechatyOptions, Contact, Room, Message as WMessage
            from wechaty_puppet import get_logger
        except ImportError:
            print("[Wechat] python-wechaty 未安装，请执行:")
            print("  pip install wechaty wechaty-puppet-service")
            self._running = False
            return

        async def main():
            options = WechatyOptions(
                puppet=self.puppet_token,
                puppet_service_endpoint=os.environ.get("WECHAT_PUPPET_ENDPOINT", ""),
            )

            bot = Wechaty(options)

            @bot.on("scan")
            async def on_scan(qrcode: str, status: int, data: Any):
                print(f"[Wechat] 扫码登录: {qrcode}")
                # qrcode 是 URL，可以生成二维码

            @bot.on("login")
            async def on_login(user: Contact):
                self._contact_self = user
                print(f"[Wechat] 登录成功: {user.name}")

            @bot.on("message")
            async def on_message(msg: WMessage):
                try:
                    # 只处理文本消息
                    if msg.type() != WMessage.Type.MESSAGE_TYPE_TEXT:
                        return

                    talker = msg.talker()
                    text = msg.text()
                    room = msg.room()
                    msg_id = msg.id

                    # 忽略自己的消息
                    if talker.contact_id == self._contact_self.contact_id:
                        return

                    room_id = ""
                    if room:
                        room_id = room.room_id
                        # 群聊只处理 @bot 的消息
                        if not await msg.mention_self():
                            return
                        # 去掉 @xxxx
                        text = text.replace(f"@{self._contact_self.name}", "").strip()

                    self._push_message(
                        text=text,
                        sender_id=talker.contact_id,
                        sender_name=talker.name,
                        room_id=room_id,
                        msg_id=msg_id,
                    )
                except Exception as e:
                    logger.error(f"[Wechat] 消息处理异常: {e}")

            self._bot = bot
            await bot.start()

        try:
            asyncio.run(main())
        except Exception as e:
            print(f"[Wechat] Wechaty 异常: {e}")
            self._running = False

    def stop(self) -> None:
        """停止通道。"""
        self._running = False
        # wechaty bot 在 asyncio 中运行，stop 后 asyncio.run 会退出
        print("[Wechat] 已停止")
