"""
channel/wechat.py — 企业微信消息通道适配器（发送 + 接收）

使用企业微信 Bot API 发送消息到群聊。
使用企业微信回调模式接收消息（HTTP POST）。

环境变量：
  WECHAT_WEBHOOK_URL — 企业微信群 Bot Webhook URL（发送用）
  WECHAT_CORP_ID — 企业 ID
  WECHAT_AGENT_SECRET — 应用 Secret
  WECHAT_AGENT_ID — 应用 AgentId
  WECHAT_CALLBACK_TOKEN — 回调 URL 验证 Token（可选）
  WECHAT_CALLBACK_AES_KEY — 回调消息 AES Key（可选）
  WECHAT_CHAT_ID — 要监听的群聊 ID（可选，chatid）
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import threading
from pathlib import Path
from typing import Optional, Callable
import urllib.request
import urllib.error

from core.channel.base import MessageChannel, Message, SendResult

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class WeChatChannel(MessageChannel):
    """企业微信消息通道适配器。"""

    @property
    def name(self) -> str:
        return "wechat"

    def __init__(
        self,
        webhook_url: str = "",
        corp_id: str = "",
        agent_secret: str = "",
        agent_id: str = "",
        chat_id: str = "",
    ):
        self._lock = threading.Lock()
        self._inbox: list[Message] = []

        # 读取配置
        self.webhook_url = webhook_url or os.environ.get("WECHAT_WEBHOOK_URL", "")
        self.corp_id = corp_id or os.environ.get("WECHAT_CORP_ID", "")
        self.agent_secret = agent_secret or os.environ.get("WECHAT_AGENT_SECRET", "")
        self.agent_id = agent_id or os.environ.get("WECHAT_AGENT_ID", "")
        self.chat_id = chat_id or os.environ.get("WECHAT_CHAT_ID", "")

        # Token 缓存
        self._access_token = ""
        self._token_expire_at = 0

        self._seen_file = ROOT_DIR / "memory" / "wechat_seen.json"
        self._seen_ids: set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._load_seen()

    # ── Token 管理 ────────────────────────────────────────────

    def _get_access_token(self) -> str:
        """获取企业微信 access_token（自动续期）。"""
        now = time.time()
        if self._access_token and now < self._token_expire_at - 60:
            return self._access_token

        if not self.corp_id or not self.agent_secret:
            return ""

        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.corp_id}&corpsecret={self.agent_secret}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    expire = data.get("expires_in", 7200)
                    self._token_expire_at = now + expire
                    return self._access_token
                else:
                    print(f"[WeChat] Token 获取失败: {data}")
                    return ""
        except Exception as e:
            print(f"[WeChat] Token 异常: {e}")
            return ""

    # ── 消息发送 ──────────────────────────────────────────────

    def send(self, text: str, **kwargs) -> SendResult:
        """发送消息。

        优先使用 Webhook URL（群 Bot），
        Webhook 不可用时用应用消息 API。
        """
        if self.webhook_url:
            return self._send_via_webhook(text)
        else:
            return self._send_via_app(text, **kwargs)

    def _send_via_webhook(self, text: str) -> SendResult:
        """通过群 Bot Webhook 发送消息。"""
        body = json.dumps({
            "msgtype": "text",
            "text": {"content": text},
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    return SendResult(success=True, platform="wechat")
                else:
                    return SendResult(success=False, platform="wechat",
                                      error=str(data))
        except Exception as e:
            return SendResult(success=False, platform="wechat", error=str(e))

    def _send_via_app(self, text: str, **kwargs) -> SendResult:
        """通过企业微信应用消息 API 发送。"""
        token = self._get_access_token()
        if not token:
            return SendResult(success=False, platform="wechat", error="token 获取失败")

        chat_id = kwargs.get("chat_id", self.chat_id) or "@all"
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        body = json.dumps({
            "touser": chat_id,
            "msgtype": "text",
            "agentid": self.agent_id,
            "text": {"content": text},
            "safe": 0,
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json; charset=utf-8"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    return SendResult(success=True, platform="wechat")
                else:
                    return SendResult(success=False, platform="wechat", error=str(data))
        except Exception as e:
            return SendResult(success=False, platform="wechat", error=str(e))

    # ── 消息接收（轮询） ──────────────────────────────────────

    def poll(self) -> list[Message]:
        """取出所有待处理消息。"""
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    # ── 回调入口（供 HTTP Server 调用） ────────────────────────

    def handle_callback(self, body: dict) -> str:
        """处理企业微信回调推送的消息。

        企业微信回调格式：
        {
            "ToUserName": "toUser",
            "FromUserName": "fromUser",
            "CreateTime": "123456789",
            "MsgType": "text",
            "Content": "你好",
            "MsgId": "1234567890",
            "AgentID": "agentId"
        }

        Returns:
            回复文本（空字符串表示不回复）
        """
        msg_type = body.get("MsgType", "")
        if msg_type != "text":
            return ""

        content = body.get("Content", "").strip()
        msg_id = str(body.get("MsgId", ""))
        sender = body.get("FromUserName", "")
        sender_name = body.get("sender_name", sender)

        if not content:
            return ""

        # 去重
        msg_id_full = f"wx_{msg_id}"
        if msg_id_full in self._seen_ids:
            return ""
        self._seen_ids.add(msg_id_full)
        self._save_seen()

        msg = Message(
            text=content,
            msg_id=msg_id_full,
            platform="wechat",
            chat_id=str(body.get("ToUserName", "")),
            sender=sender,
            sender_name=sender_name,
            raw=body,
        )
        with self._lock:
            self._inbox.append(msg)

        # 返回空字符串，由上层决定回复策略
        return ""

    # ── 已处理消息持久化 ──────────────────────────────────────

    def _load_seen(self):
        try:
            if self._seen_file.exists():
                data = json.loads(self._seen_file.read_text(encoding="utf-8"))
                self._seen_ids = set(data.get("ids", []))
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            self._seen_file.parent.mkdir(parents=True, exist_ok=True)
            ids = list(self._seen_ids)[-1000:]
            self._seen_file.write_text(
                json.dumps({"ids": ids}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── 启动/停止 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动通道 (验证配置)。"""
        if self.webhook_url:
            print("[WeChat] Webhook URL 已配置，可发送消息")
        if self.corp_id and self.agent_secret:
            token = self._get_access_token()
            if token:
                print("[WeChat] 应用消息 API 就绪")
            else:
                print("[WeChat] ⚠️ 应用消息 API 验证失败")

        if not self.webhook_url and not (self.corp_id and self.agent_secret):
            print("[WeChat] ⚠️ 未配置任何发送方式")
        print("[WeChat] ✅ 通道就绪")

    def stop(self) -> None:
        """停止通道。"""
        self._save_seen()
        print("[WeChat] 通道已停止")
