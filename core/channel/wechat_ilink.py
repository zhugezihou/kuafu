"""
channel/wechat_ilink.py — 个人微信 iLink API 通道（腾讯官方协议）

使用腾讯官方 iLink Bot API 连接个人微信。
无需第三方 Token，扫码登录，官方稳定。

iLink 是腾讯于 2026 年 3 月开放的微信 Bot 协议：
- 扫码登录（安全可控）
- 长轮询接收消息
- 发送文本/图片/文件
- 支持群聊 @mention

环境变量：
  WECHAT_ILINK_DATA_DIR — iLink 持久化数据存储目录（可选，默认 memory/）
  无需 API Key 或 Token。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import urllib.request
import urllib.error

from core.channel.base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.wechat_ilink")

BASE_URL = "https://ilinkai.weixin.qq.com"
POLL_INTERVAL = 3.0  # 长轮询间隔（秒）


class WeChatILinkChannel(MessageChannel):
    """微信 iLink API 通道（腾讯官方）。"""

    @property
    def name(self) -> str:
        return "wechat"

    def __init__(self):
        self._lock = threading.Lock()
        self._inbox: list[Message] = []

        # iLink 认证数据
        self._bot_token: str = ""
        self._bot_open_id: str = ""
        self._uin: str = ""
        self._config: dict = {}
        self._poll_buf: str = ""  # get_updates_buf（游标）

        # 持久化路径
        data_dir = Path(os.environ.get("WECHAT_ILINK_DATA_DIR", ""))
        if not data_dir:
            from pathlib import Path as _Path
            data_dir = _Path(__file__).resolve().parent.parent.parent / "memory"
        self._state_file = data_dir / "wechat_ilink_state.json"

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._restart_event = threading.Event()

        # 加载持久化状态
        self._load_state()

    # ── 认证流程 ──────────────────────────────────────────────

    def _request(self, endpoint: str, body: dict, timeout: int = 15) -> dict:
        """发送 POST 请求到 iLink API。"""
        url = f"{BASE_URL}/ilink/bot/{endpoint}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-WECHAT-UIN": self._uin,
        }
        if self._bot_token:
            headers["X-WECHAT-BOT-TOKEN"] = self._bot_token

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {"errcode": -1, "errmsg": "empty response"}
                rst = json.loads(raw)
                # iLink API 返回 {errcode: 0, ...}
                return rst
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                return err_body
            except Exception:
                return {"errcode": e.code, "errmsg": str(e)}
        except Exception as e:
            return {"errcode": -1, "errmsg": str(e)}

    def is_logged_in(self) -> bool:
        """是否已登录（有有效 bot_token）。"""
        return bool(self._bot_token)

    def get_qrcode_url(self) -> str:
        """获取登录二维码 URL。返回图片 URL，扫码后确认登录。"""
        result = self._request("get_bot_qrcode", {"base_info": {"channel_version": "1.0.2"}})
        if result.get("errcode") == 0:
            return result.get("qrcode", "")
        return ""

    def wait_for_login(self, timeout: int = 120) -> bool:
        """等待扫码登录（轮询二维码状态）。

        Args:
            timeout: 超时秒数（默认 120s）

        Returns:
            是否登录成功
        """
        qrcode_url = self.get_qrcode_url()
        if not qrcode_url:
            print("[WeChat] 获取二维码失败")
            return False

        print("[WeChat] 请用微信扫描二维码登录")
        print(f"[WeChat] 二维码: {qrcode_url}")
        # 尝试打印服务器二维码
        self._render_qrcode(qrcode_url)

        start = time.time()
        while time.time() - start < timeout:
            result = self._request("get_qrcode_status", {
                "qrcode": qrcode_url,
                "base_info": {"channel_version": "1.0.2"},
            })
            status = result.get("status", "")
            if status == "confirmed":
                self._bot_token = result.get("bot_token", "")
                self._uin = result.get("uin", "")
                self._bot_open_id = result.get("bot_open_id", "")
                self._save_state()
                print("[WeChat] ✅ 登录成功")
                return True
            elif status == "expired":
                print("[WeChat] 二维码已过期，重新生成")
                return self.wait_for_login(timeout)

            # 每 2 秒检查
            time.sleep(2)
            if not self._running:
                return False

        print("[WeChat] 登录超时")
        return False

    @staticmethod
    def _render_qrcode(url: str):
        """尝试打印二维码到终端。"""
        try:
            import urllib.parse
            # 先尝试本地生成二维码（需要 qrcode 库）
            try:
                import qrcode
                import sys
                qr = qrcode.QRCode(box_size=1, border=1)
                qr.add_data(url)
                qr.print_ascii(out=sys.stdout)
                return
            except ImportError:
                pass
            # 否则用在线 API
            print(f"  QR: https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(url)}")
        except Exception:
            print(f"  请扫码: {url}")

    # ── 消息发送 ──────────────────────────────────────────────

    def send(self, text: str, **kwargs) -> SendResult:
        """发送消息。

        kwargs:
            chat_id: 接收方 open_id（from_user_id）
            context_token: 消息上下文 token（回复时需带上）
        """
        if not self._bot_token:
            return SendResult(success=False, platform="wechat", error="未登录")

        to_user = kwargs.get("chat_id", "")
        ctx_token = kwargs.get("context_token", "")
        if not to_user:
            return SendResult(success=False, platform="wechat", error="chat_id 未指定")

        msg = {
            "to_user_id": to_user,
            "from_user_id": "",
            "client_id": str(uuid.uuid4()),
            "message_type": 2,
            "message_state": 2,
            "context_token": ctx_token,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        result = self._request("sendmessage", {
            "msg": msg,
            "base_info": {"channel_version": "1.0.2"},
        })
        ok = result.get("errcode") == 0
        return SendResult(
            success=ok,
            platform="wechat",
            error="" if ok else result.get("errmsg", ""),
        )

    # ── 消息接收 ──────────────────────────────────────────────

    def poll(self) -> list[Message]:
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    # ── 启动 / 消息循环 ───────────────────────────────────────

    def start(self) -> None:
        """启动微信通道。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="wechat-ilink",
        )
        self._thread.start()
        print("[WeChat] 启动 iLink 微信通道...")

    def _run_loop(self):
        """主循环：登录 → 长轮询收消息。"""
        # 1. 登录
        if not self._bot_token:
            print("[WeChat] 需要扫码登录")
            if not self.wait_for_login(timeout=120):
                print("[WeChat] ❌ 登录失败")
                self._running = False
                return

        # 2. 获取配置
        self._fetch_config()

        # 3. 长轮询消息
        self._poll_loop()

    def _fetch_config(self):
        """获取 Bot 配置。"""
        result = self._request("getconfig", {"base_info": {"channel_version": "1.0.2"}})
        if result.get("errcode") == 0:
            self._config = result
            print(f"[WeChat] 配置就绪")

    def _poll_loop(self):
        """长轮询接收消息。"""
        print("[WeChat] 开始接收消息...")
        while self._running:
            try:
                body = {
                    "get_updates_buf": self._poll_buf,
                    "base_info": {"channel_version": "1.0.2"},
                }
                result = self._request("getupdates", body, timeout=30)

                if result.get("errcode") == 0:
                    # 保存游标
                    self._poll_buf = result.get("get_updates_buf", self._poll_buf)

                    # 处理消息
                    messages = result.get("messages", [])
                    for msg_data in messages:
                        self._handle_incoming(msg_data)
                else:
                    err = result.get("errmsg", "")
                    if err:
                        # token 过期需要重新登录
                        if "token" in err.lower():
                            print("[WeChat] token 过期，需要重新登录")
                            self._bot_token = ""
                            self._save_state()
                            if self.wait_for_login(timeout=120):
                                continue
                            break
                        logger.warning(f"[WeChat] 轮询异常: {err}")

            except Exception as e:
                logger.error(f"[WeChat] 轮询异常: {e}")

            if not self._running:
                break

        print("[WeChat] 消息接收已停止")

    def _handle_incoming(self, msg_data: dict):
        """处理收到的消息。"""
        try:
            msg_type = msg_data.get("message_type", 0)
            msg_state = msg_data.get("message_state", 0)
            from_user = msg_data.get("from_user_id", "")
            ctx_token = msg_data.get("context_token", "")
            msg_id = msg_data.get("client_id", "")

            # 只处理用户发来的文本消息
            if msg_type != 1:
                return
            if msg_state != 2:
                return
            if not from_user:
                return

            # 提取文本内容
            items = msg_data.get("item_list", [])
            text = ""
            for item in items:
                if item.get("type") == 1:
                    text_item = item.get("text_item", {})
                    text = text_item.get("text", "")
                    break
            if not text:
                return

            # 构建消息对象，保存 context_token 用于回复
            msg = Message(
                text=text,
                msg_id=msg_id,
                platform="wechat",
                chat_id=from_user,
                sender=from_user,
                sender_name=from_user,
                raw={"context_token": ctx_token, "from_user": from_user},
            )
            with self._lock:
                self._inbox.append(msg)

        except Exception as e:
            logger.error(f"[WeChat] 处理消息失败: {e}")

    # ── 持久化 ────────────────────────────────────────────────

    def _save_state(self):
        """保存登录状态。"""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "bot_token": self._bot_token,
                "uin": self._uin,
                "bot_open_id": self._bot_open_id,
                "poll_buf": self._poll_buf,
                "updated_at": datetime.now().isoformat(),
            }
            self._state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[WeChat] 状态保存失败: {e}")

    def _load_state(self):
        """加载持久化的登录状态。"""
        try:
            if self._state_file.exists():
                state = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._bot_token = state.get("bot_token", "")
                self._uin = state.get("uin", "")
                self._bot_open_id = state.get("bot_open_id", "")
                self._poll_buf = state.get("poll_buf", "")
                if self._bot_token:
                    print(f"[WeChat] 已加载登录状态（open_id: {self._bot_open_id[:20]}...）")
        except Exception:
            pass

    # ── 停止 ──────────────────────────────────────────────────

    def stop(self) -> None:
        """停止通道。"""
        self._running = False
        self._save_state()
        print("[WeChat] 通道已停止")
