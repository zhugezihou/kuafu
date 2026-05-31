"""
channel/feishu_ws.py — 飞书 WebSocket 直连通道（替代轮询）

使用 lark-oapi SDK 的 WebSocket 事件订阅。
建立持久 WS 连接，飞书主动推送消息。
无需轮询，零延迟。

环境变量：
  FEISHU_APP_ID — 飞书应用 App ID
  FEISHU_APP_SECRET — 飞书应用 App Secret
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

from core.channel.base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.feishu_ws")


class FeishuWebSocketChannel(MessageChannel):
    """飞书 WebSocket 直连通道。"""

    @property
    def name(self) -> str:
        return "feishu"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
    ):
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._lock = threading.Lock()
        self._inbox: list[Message] = []
        self._ws_client: Any = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── 消息发送 ──────────────────────────────────────────────

    def send(self, text: str, **kwargs) -> SendResult:
        """通过飞书 API 发送消息。"""
        chat_id = kwargs.get("chat_id", "")
        token = self._get_tenant_token()
        if not token:
            return SendResult(success=False, platform="feishu", error="token 获取失败")

        from urllib.request import Request, urlopen
        target = chat_id or os.environ.get("FEISHU_CHAT_ID", "")
        if not target:
            return SendResult(success=False, platform="feishu", error="chat_id 未指定")

        body = json.dumps({
            "receive_id": target,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }, ensure_ascii=False).encode("utf-8")

        req = Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ok = data.get("code") == 0
                return SendResult(
                    success=ok,
                    platform="feishu",
                    error="" if ok else str(data),
                )
        except Exception as e:
            return SendResult(success=False, platform="feishu", error=str(e))

    # ── Token 管理 ────────────────────────────────────────────

    def _get_tenant_token(self) -> str:
        from urllib.request import Request, urlopen
        body = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }).encode("utf-8")
        req = Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("tenant_access_token", "")
        except Exception:
            return ""

    # ── 消息接收（WS/轮询 混合模式） ──────────────────────────

    def poll(self) -> list[Message]:
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    def _on_message(self, text: str, msg_id: str = "", chat_id: str = "", sender: str = ""):
        # 群聊消息必须 @bot 才处理
        # 飞书 SDK 在 @bot 时消息文本中会包含 @_user_X 格式的 @，不再用 mentions 判断
        # 所以直接信任上游的过滤，不过滤私聊
        msg = Message(
            text=text,
            msg_id=msg_id,
            platform="feishu",
            chat_id=chat_id,
            sender=sender,
        )
        with self._lock:
            self._inbox.append(msg)

    # ── WS 启动（lark-oapi SDK） ──────────────────────────────

    def start(self) -> None:
        """启动飞书 WebSocket 订阅。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._ws_loop,
            daemon=True,
            name="feishu-ws",
        )
        self._thread.start()
        print("[FeishuWS] 启动 WebSocket 直连...")

    def _ws_loop(self):
        """WebSocket 事件循环（含自动重连）。"""
        try:
            import lark_oapi as lark
        except ImportError:
            print("[FeishuWS] lark-oapi 未安装，回退到轮询模式")
            self._fallback_poll_loop()
            return

        reconnect_count = 0
        while self._running:
            try:
                from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

                def on_message(data) -> None:
                    """收到飞书消息事件。"""
                    try:
                        event_data = getattr(data, 'event', data) if not isinstance(data, dict) else data
                        msg = getattr(event_data, 'message', None) or (isinstance(event_data, dict) and event_data.get('message'))
                        if not msg:
                            return

                        # 兼容对象和 dict 两种格式
                        if isinstance(msg, dict):
                            msg_type = msg.get('msg_type', msg.get('message_type', ''))
                            if msg_type != 'text':
                                return
                            content_raw = msg.get('content', '')
                            chat_type = msg.get('chat_type', '')
                            msg_id = msg.get('message_id', '')
                            chat_id = msg.get('chat_id', '')
                            sender = (msg.get('sender') or {}).get('id', '')
                            mentions = msg.get('mentions', [])
                        else:
                            msg_type = getattr(msg, 'message_type', getattr(msg, 'msg_type', ''))
                            if msg_type != 'text':
                                return
                            content_raw = getattr(msg, 'content', '')
                            chat_type = getattr(msg, 'chat_type', '')
                            msg_id = getattr(msg, 'message_id', '')
                            chat_id = getattr(msg, 'chat_id', '')
                            sender = getattr(getattr(msg, 'sender', None), 'id', '') if hasattr(msg, 'sender') else ''
                            mentions = getattr(msg, 'mentions', [])

                        if not content_raw:
                            return
                        content = json.loads(content_raw).get("text", content_raw)

                        self._on_message(
                            text=content,
                            msg_id=msg_id,
                            chat_id=chat_id,
                            sender=sender,
                        )
                    except Exception as e:
                        import traceback
                        print(f"[FeishuWS] 处理消息异常: {e}")
                        traceback.print_exc()

                handler = (
                    EventDispatcherHandler.builder("", "")
                    .register_p2_im_message_receive_v1(on_message)
                    .build()
                )

                cli = lark.ws.Client(
                    self.app_id,
                    self.app_secret,
                    event_handler=handler,
                    log_level=lark.LogLevel.ERROR,
                )

                print(f"[FeishuWS] WebSocket 已连接 (重连 #{reconnect_count})")
                reconnect_count = 0
                cli.start()  # 阻塞直到断开

            except Exception as e:
                reconnect_count += 1
                delay = min(reconnect_count * 5, 60)
                print(f"[FeishuWS] 连接断开 ({e}), {delay}s 后重连...")
                for _ in range(delay * 2):
                    if not self._running:
                        return
                    time.sleep(0.5)

    def _fallback_poll_loop(self):
        """无 lark-oapi 时的轮询回退。"""
        import urllib.request
        from urllib.request import Request, urlopen

        seen_ids: set[str] = set()
        while self._running:
            try:
                token = self._get_tenant_token()
                if not token:
                    time.sleep(10)
                    continue

                url = "https://open.feishu.cn/open-apis/im/v1/messages" \
                      f"?container_id_type=chat" \
                      f"&container_id={os.environ.get('FEISHU_CHAT_ID', '')}" \
                      f"&page_size=10&sort_type=ByCreateTimeDesc"
                req = Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    items = data.get("data", {}).get("items", [])

                for msg in reversed(items):
                    msg_id = msg.get("message_id", "")
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    if msg.get("msg_type") != "text":
                        continue

                    body = msg.get("body", {})
                    content = json.loads(body.get("content", "{}")).get("text", "")
                    self._on_message(
                        text=content,
                        msg_id=msg_id,
                        chat_id=msg.get("chat_id", ""),
                        sender=msg.get("sender", {}).get("id", ""),
                    )
            except Exception as e:
                logger.error(f"[FeishuWS] 轮询异常: {e}")

            for _ in range(50):
                if not self._running:
                    return
                time.sleep(0.1)

    def stop(self) -> None:
        """停止通道。"""
        self._running = False
        print("[FeishuWS] 已停止")
