"""
channel/feishu_ws.py — 飞书 WebSocket 通道

通过 lark-oapi SDK 连接飞书 WebSocket，收发消息和卡片交互。
支持：
- 接收群聊/私聊文本消息（@bot 过滤）
- 发送文本消息
- 发送审批卡片（interactive card）
- 接收卡片按钮回调（批准/拒绝审批）

环境变量：
  FEISHU_APP_ID — 飞书应用 App ID
  FEISHU_APP_SECRET — 飞书应用 App Secret
  FEISHU_CHAT_ID — 默认飞书群聊天 ID
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.channel.base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.feishu_ws")

# 审批按钮回调 — 外部注入
ON_CARD_APPROVAL_CB: Optional[Callable[[str, str], None]] = None
"""回调签名: (approval_id: str, action: str) -> None, action 为 'approve' 或 'reject'"""


class FeishuWebSocketChannel(MessageChannel):
    """飞书 WebSocket 通道（lark-oapi SDK）。"""

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
        self._bot_open_id: str = ""  # 保存 bot 自己的 open_id，用于 @bot 过滤
        self._card_approval_state: dict[str, threading.Event] = {}
        """approval_id → Event，用于解阻塞等待审批的线程"""

    # ── 消息发送 ──────────────────────────────────────────────

    def send(self, text: str, **kwargs) -> SendResult:
        """通过飞书 API 发送文本消息。"""
        chat_id = kwargs.get("chat_id", "")
        return self._send_api(chat_id, "text", {"text": text})

    def send_card(self, card: dict, chat_id: str = "") -> SendResult:
        """发送飞书消息卡片（interactive）。"""
        return self._send_api(chat_id, "interactive", card)

    def _send_api(self, chat_id: str, msg_type: str, content: dict | str) -> SendResult:
        """飞书 API 消息发送底层方法。"""
        token = self._get_tenant_token()
        if not token:
            return SendResult(success=False, platform="feishu", error="token 获取失败")

        from urllib.request import Request, urlopen

        target = chat_id or os.environ.get("FEISHU_CHAT_ID", "")
        if not target:
            return SendResult(success=False, platform="feishu", error="chat_id 未指定")

        if isinstance(content, str):
            content_str = content
        else:
            content_str = json.dumps(content, ensure_ascii=False)

        body = json.dumps({
            "receive_id": target,
            "msg_type": msg_type,
            "content": content_str,
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

    # ── 消息接收 ──────────────────────────────────────────────

    def poll(self) -> list[Message]:
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    def _on_message(self, text: str, msg_id: str = "", chat_id: str = "", sender: str = "", chat_type: str = "", mentions: list | None = None):
        # 群聊消息必须 @bot 才处理（使用 SDK mentions 字段，不依赖文本显示名）
        # 私聊（p2p）消息无需 @，直接处理
        if chat_type == "group" or (chat_id and chat_type != "p2p"):
            bot_id = getattr(self, '_bot_open_id', None)
            if not mentions or not any(
                # SDK object: m.key 是 UserId 对象, 属性是 user_id / open_id / union_id
                (hasattr(m, 'key') and (
                    (isinstance(m.key, dict) and m.key.get("user_id", "") == bot_id)
                    or (hasattr(m.key, 'user_id') and m.key.user_id == bot_id)
                    or (hasattr(m.key, 'open_id') and m.key.open_id == bot_id)
                    or (hasattr(m.key, 'id') and m.key.id == bot_id)
                ))
                # Mention 对象自身的 id (MentionEvent.id) 不是 user_id, 不匹配
                # dict 形态的 mentions：兼容旧 SDK
                or (isinstance(m, dict) and (
                    m.get("user_id", "") == bot_id
                    or m.get("open_id", "") == bot_id
                    or m.get("id", "") == bot_id
                    or m.get("key", {}).get("user_id", "") == bot_id
                    or m.get("key", {}).get("open_id", "") == bot_id
                ))
                # name 匹配：任何时候都检查 name 作为兜底
                or (isinstance(m, dict) and m.get("name", "") == "夸父")
                or (hasattr(m, 'name') and m.name == "夸父")
                for m in mentions
            ):
                print(f"[FeishuWS] 忽略非@bot消息: {text[:60]}")
                return

        # 清洗 text：去掉所有飞书 @mention 前缀（如 @夸父 / @中书令 / @user_1）
        # 飞书 WS 消息 content.text 中 @ 标记在不同场景下可能渲染不同的显示名，
        # 因此统一用正则去掉 text 首部的 @mention，只保留实际用户输入内容。
        import re as _re
        cleaned = _re.sub(r"^@[^\s]+\s*", "", text, count=1).strip()

        msg = Message(
            text=cleaned,
            msg_id=msg_id,
            platform="feishu",
            chat_id=chat_id,
            sender=sender,
        )
        with self._lock:
            self._inbox.append(msg)

    # ── 审批卡片 ──────────────────────────────────────────────

    def _build_approval_card(self, approval_id: str, tool: str, args_summary: str) -> dict:
        """构建审批按钮卡片 JSON。"""
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🔐 审批请求"},
                "template": "orange",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**工具**: `{tool}`\n**参数**: `{args_summary}`\n\n**ID**: `{approval_id}`",
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 批准"},
                            "type": "primary",
                            "value": {"approval_id": approval_id, "action": "approve"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                            "type": "danger",
                            "value": {"approval_id": approval_id, "action": "reject"},
                        },
                    ],
                },
            ],
        }

    def send_approval_card(self, approval_id: str, tool: str, args_summary: str, chat_id: str = "") -> SendResult:
        """发送审批卡片并注册等待事件。"""
        ev = threading.Event()
        self._card_approval_state[approval_id] = ev
        card = self._build_approval_card(approval_id, tool, args_summary)
        result = self.send_card(card, chat_id=chat_id)
        if result.success and result.msg_id:
            # 保存卡片对应的消息 ID，后续更新用
            if not hasattr(self, '_card_msg_ids'):
                self._card_msg_ids: dict[str, str] = {}
            self._card_msg_ids[approval_id] = result.msg_id
        if not result.success:
            self._card_approval_state.pop(approval_id, None)
        return result

    def wait_approval(self, approval_id: str, timeout: float = 300) -> Optional[str]:
        """阻塞等待审批结果。返回 'approve' 或 'reject'，超时返回 None。"""
        ev = self._card_approval_state.get(approval_id)
        if not ev:
            return None
        # 将卡片按钮回调写入事件
        result_holder: list[Optional[str]] = [None]
        import core.channel.feishu_ws as _feishu_mod
        original_cb = _feishu_mod.ON_CARD_APPROVAL_CB

        def _cb(aid: str, action: str):
            if aid == approval_id:
                result_holder[0] = action
                ev.set()

        _feishu_mod.ON_CARD_APPROVAL_CB = _cb

        ev.wait(timeout=timeout)
        _feishu_mod.ON_CARD_APPROVAL_CB = original_cb
        self._card_approval_state.pop(approval_id, None)
        return result_holder[0]

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
            from urllib.request import Request, urlopen
            import json
            # 获取 bot 自己的 open_id，用于 @bot 过滤
            token = self._get_tenant_token()
            if token:
                req = Request(
                    "https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                    method="GET",
                )
                try:
                    with urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("code") == 0:
                            self._bot_open_id = (data.get("bot", {}) or {}).get("open_id", "")
                            print(f"[FeishuWS] Bot open_id: {self._bot_open_id[:8]}...")
                except Exception:
                    pass

            import lark_oapi as lark
        except ImportError:
            import sys
            print("[FeishuWS] ❌ lark-oapi 未安装，无法启动飞书 WebSocket 通道。请安装: pip install lark-oapi")
            sys.exit(1)

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
                            chat_type=chat_type,
                            mentions=mentions if isinstance(mentions, list) else [],
                        )
                    except Exception as e:
                        import traceback
                        print(f"[FeishuWS] 处理消息异常: {e}")
                        traceback.print_exc()

                def on_card_action(data) -> None:
                    """收到飞书卡片按钮回调事件。"""
                    try:
                        event = data.event if hasattr(data, 'event') else data
                        action = getattr(event, 'action', None) if not isinstance(event, dict) else event.get('action')
                        if not action:
                            action = getattr(data, 'action', None) if not isinstance(data, dict) else data.get('action')
                        if not action:
                            return
                        value = getattr(action, 'value', {}) if not isinstance(action, dict) else action.get('value', {})
                        if not value:
                            return
                        approval_id = value.get('approval_id') if isinstance(value, dict) else None
                        action_type = value.get('action') if isinstance(value, dict) else None
                        print(f"[FeishuWS] 卡片按钮: {action_type} (ID: {approval_id})")

                        # 更新卡片：把按钮替换成已处理状态，防止重复点击
                        token = self._get_tenant_token()
                        if token and approval_id:
                            result_text = "✅ 已批准" if action_type == "approve" else "❌ 已拒绝"
                            # 发一条新消息说明结果（比 PATCH 更新卡片更可靠）
                            try:
                                from urllib.request import Request as _Req, urlopen as _urlopen
                                # 获取卡片所在群聊
                                chat_id = ""
                                _evt = getattr(data, 'event', None)
                                if _evt:
                                    _ctx = getattr(_evt, 'context', None)
                                    if _ctx and hasattr(_ctx, 'open_chat_id') and _ctx.open_chat_id:
                                        chat_id = _ctx.open_chat_id
                                if chat_id:
                                    result_msg = f"**审批结果**: {result_text}\n**审批ID**: `{approval_id}`"
                                    result_body = json.dumps({
                                        "receive_id": chat_id,
                                        "msg_type": "text",
                                        "content": json.dumps({"text": result_msg}),
                                    }, ensure_ascii=False).encode("utf-8")
                                    _r = _Req(
                                        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                                        data=result_body,
                                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                        method="POST",
                                    )
                                    with _urlopen(_r, timeout=10) as _resp:
                                        pass
                                    print(f"[FeishuWS] 审批结果消息已发送: {result_text}")
                            except Exception as e2:
                                print(f"[FeishuWS] 审批结果消息发送失败: {e2}")

                        cb = ON_CARD_APPROVAL_CB
                        if cb:
                            cb(str(approval_id), str(action_type))
                    except Exception as e:
                        import traceback
                        print(f"[FeishuWS] 处理卡片回调异常: {e}")
                        traceback.print_exc()

                handler = (
                    EventDispatcherHandler.builder("", "")
                    .register_p2_im_message_receive_v1(on_message)
                    .register_p2_card_action_trigger(on_card_action)
                    .build()
                )

                cli = lark.ws.Client(
                    self.app_id,
                    self.app_secret,
                    event_handler=handler,
                    log_level=lark.LogLevel.ERROR,
                )

                print(f"[FeishuWS] WebSocket 已连接 (重连 #{reconnect_count})", flush=True)
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

    def stop(self) -> None:
        """停止通道。"""
        self._running = False
        print("[FeishuWS] 已停止")
