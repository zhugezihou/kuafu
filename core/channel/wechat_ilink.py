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
import base64
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
        self._last_qrcode_token: str = ""  # 最后一次获取的 qrcode token

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

    def _request(self, endpoint: str, body: dict, timeout: int = 15,
                  method: str = "POST") -> dict:
        """发送请求到 iLink API。

        Args:
            endpoint: API 端点（如 'get_bot_qrcode'）
            body: POST 请求的 JSON body
            timeout: 超时秒数
            method: HTTP 方法

        Returns:
            dict: 解析后的 JSON 响应
        """
        is_get = method == "GET"
        url = f"{BASE_URL}/ilink/bot/{endpoint}"
        headers = {
            "iLink-App-ClientVersion": "1",
        }
        if self._bot_token:
            # Bearer 用完整 token（包含 bot_id 前缀），不能只取 hex 部分
            headers["Authorization"] = f"Bearer {self._bot_token}"
            headers["AuthorizationType"] = "ilink_bot_token"
            # X-WECHAT-UIN 每次请求重新生成（uint32 -> 十进制 -> base64）
            import random
            uin_raw = str(random.randint(0, 4294967295))
            headers["X-WECHAT-UIN"] = base64.b64encode(uin_raw.encode()).decode()
            headers["SKRouteTag"] = "1001"

        if is_get:
            req = urllib.request.Request(url, headers=headers, method="GET")
        else:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {"errcode": -1, "errmsg": "empty response"}
                rst = json.loads(raw)
                return rst
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                return err_body
            except Exception:
                return {"errcode": e.code, "errmsg": str(e)}
        except Exception as e:
            return {"errcode": -1, "errmsg": str(e)}

    def get_qrcode_token(self) -> str:
        """获取登录二维码 token（不是图片 URL）。
        返回 qrcode token，用于轮询登录状态。
        """
        result = self._request("get_bot_qrcode?bot_type=3", {}, method="GET")
        # iLink 返回 {"qrcode": "<token>", "qrcode_img_content": "<图片URL>"}
        self._last_qrcode_token = result.get("qrcode", "")
        return self._last_qrcode_token

    def get_qrcode_img(self) -> str:
        """获取二维码图片 URL（用于渲染/显示）。"""
        result = self._request("get_bot_qrcode?bot_type=3", {}, method="GET")
        img_url = result.get("qrcode_img_content", "")
        token = result.get("qrcode", "")
        self._last_qrcode_token = token
        return img_url or token

    def is_logged_in(self) -> bool:
        """是否已登录（有有效 bot_token）。"""
        return bool(self._bot_token)

    def wait_for_login(self, timeout: int = 120) -> bool:
        """等待扫码登录。

        流程：
        1. 获取二维码 → 打印
        2. 用 qrcode token 直接尝试 getupdates（iLink 可能已绑定）
        3. 如果 getupdates 返回 bot_token 或有效消息 → 登录成功
        4. 否则回退到轮询 status（每 3 秒）
        """
        # 获取二维码
        img_url = self.get_qrcode_img()
        qrcode_token = self._last_qrcode_token
        if not img_url or not qrcode_token:
            print("[WeChat] 获取二维码失败")
            return False

        print("[WeChat] 请用微信扫描二维码登录")
        self._render_qrcode(img_url)
        print(f"[WeChat] 二维码: {img_url}")

        # 尝试用 qrcode token 直接 poll（某些 iLink 实现中 token 可直接用）
        self._bot_token = qrcode_token
        result = self._request("getupdates", {
            "get_updates_buf": "",
            "base_info": {"channel_version": "1.0.2"},
        }, timeout=5)
        if result.get("errcode") == 0:
            # 成功！拿到了 bot_token（或 qrcode_token 本身就够用）
            self._poll_buf = result.get("get_updates_buf", "")
            now = time.time()
            while time.time() - now < timeout:
                result = self._request("getupdates", {
                    "get_updates_buf": self._poll_buf,
                    "base_info": {"channel_version": "1.0.2"},
                }, timeout=30)
                if result.get("errcode") == 0:
                    self._poll_buf = result.get("get_updates_buf", self._poll_buf)
                    messages = result.get("messages", [])
                    if messages:
                        for msg_data in messages:
                            self._handle_incoming(msg_data)
                        # 有消息说明登录成功，保存 token 并返回
                        print("\n[WeChat] ✅ 登录成功（收到消息）")
                        self._save_state()
                        return True
                # 检查是否返回了 bot_token（某些实现在 getupdates 中下发）
                if result.get("bot_token"):
                    self._bot_token = result["bot_token"]
                if result.get("uin"):
                    self._uin = result["uin"]
                time.sleep(1)
            print("\n[WeChat] 超时：未收到消息")
            return False

        # getupdates 失败，回退到轮询状态
        print("[WeChat] 等待扫码确认...")
        start = time.time()
        self._bot_token = ""  # 重置 token
        while time.time() - start < timeout:
            result = self._request(
                f"get_qrcode_status?qrcode={qrcode_token}&bot_type=3",
                {}, method="GET", timeout=5,
            )
            status = result.get("status", "")
            if status == "confirmed":
                # 打印完整响应看字段
                print(f"\n[WeChat] 登录响应: {json.dumps(result, ensure_ascii=False)[:500]}")
                self._bot_token = result.get("bot_token", "")
                self._uin = result.get("uin", "")
                self._bot_open_id = result.get("bot_open_id", "")
                self._ilink_bot_id = result.get("ilink_bot_id", "")
                self._save_state()
                print(f"\n[WeChat] ✅ 登录成功 (token={self._bot_token[:30]}... uin={self._uin[:20]}...)")
                return True
            elif status == "scaned":
                print("\r[WeChat] 已扫码，请在手机上确认", end="", flush=True)
            else:
                print(f"\r[WeChat] 等待扫码... ({int(timeout-(time.time()-start))}s)", end="", flush=True)

            time.sleep(3)
            if not self._running:
                return False

        print("\n[WeChat] 登录超时")
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

                # 检查错误：errcode 存在且不为 0 才认为是错误
                errcode = result.get("errcode", 0)
                if errcode != 0:
                    errmsg = result.get("errmsg", str(errcode))
                    if "token" in errmsg.lower() or "session" in errmsg.lower() or errcode == -14:
                        print(f"\n[WeChat] 会话过期 (errcode={errcode}), 需要重新登录")
                        self._bot_token = ""
                        self._poll_buf = ""
                        self._save_state()
                        if self.wait_for_login(timeout=120):
                            continue
                        break
                    logger.warning(f"[WeChat] 轮询异常: {errmsg}")
                else:
                    # 成功：保存游标，处理消息
                    self._poll_buf = result.get("get_updates_buf", self._poll_buf)
                    messages = result.get("msgs", result.get("messages", []))
                    for msg_data in messages:
                        self._handle_incoming(msg_data)

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

            print(f"[WeChat] 收到消息: type={msg_type} state={msg_state} from={from_user} text={str(msg_data.get('item_list', []))[:120]}")

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
