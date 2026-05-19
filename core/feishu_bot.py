"""
夸父飞书渠道 — 轮询模式飞书 Bot 通道。

职责：
1. 定时轮询飞书群聊新消息
2. 检测 @bot 提及并提取消息文本
3. 将消息转发给夸父 Agent 处理
4. 将回复发送回飞书群聊

设计原则：
- 零外部依赖（仅 urllib + json）
- 长轮询而非 WebSocket（保持简单）
- 所有飞书 API 调用通过统一方法发送
- 消息去重持久化，避免重启后重复处理

飞书 API 文档：https://open.feishu.cn/document/server-docs
"""

import json
import os
import re
import time
import threading
from pathlib import Path
from typing import Callable, Optional
import urllib.request
import urllib.error

ROOT_DIR = Path(__file__).resolve().parent.parent

# 从 .env 加载
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")
FEISHU_ENABLED = bool(FEISHU_APP_ID and FEISHU_APP_SECRET and FEISHU_CHAT_ID)

# 飞书 API 基础 URL
BASE_URL = "https://open.feishu.cn/open-apis"


class FeishuBot:
    """飞书 Bot，通过轮询模式接收和发送消息。"""

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        chat_id: str = "",
        poll_interval: float = 5.0,
        on_message: Optional[Callable[[str, str], str]] = None,
        seen_file: str = "",
    ):
        """
        Args:
            app_id: 飞书应用 App ID
            app_secret: 飞书应用 App Secret
            chat_id: 要监听的群聊 ID（如 oc_d860f9f653e3421db6ea419a81414cf6）
            poll_interval: 轮询间隔（秒）
            on_message: 收到消息后的回调 (message_text, msg_id) → 回复文本
            seen_file: 已处理消息 ID 的持久化路径
        """
        self.app_id = app_id or FEISHU_APP_ID
        self.app_secret = app_secret or FEISHU_APP_SECRET
        self.chat_id = chat_id or FEISHU_CHAT_ID
        self.poll_interval = poll_interval
        self.on_message = on_message
        self._seen_file = Path(seen_file or (ROOT_DIR / "memory" / "feishu_seen.json"))
        self._seen_ids: set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tenant_token = ""
        self._token_expire_at = 0
        self._bot_open_id = ""

        # 加载已处理的 message IDs
        self._load_seen()

    # ── Token 管理 ──────────────────────────────────────────────

    def _get_tenant_token(self) -> str:
        """获取 tenant_access_token（自动续期）。"""
        now = time.time()
        if self._tenant_token and now < self._token_expire_at - 60:
            return self._tenant_token

        url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
        body = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json; charset=utf-8",
        }, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    expire = data.get("expire", 7200)
                    self._tenant_token = data["tenant_access_token"]
                    self._token_expire_at = now + expire
                    return self._tenant_token
                else:
                    print(f"[FeishuBot] Token 获取失败: {data}")
                    return ""
        except Exception as e:
            print(f"[FeishuBot] Token 请求异常: {e}")
            return self._tenant_token  # 返回过期的 token，上层会重试

    # ── Bot 信息 ────────────────────────────────────────────────

    def get_bot_info(self) -> dict:
        """获取 Bot 自身信息，缓存 open_id。"""
        token = self._get_tenant_token()
        if not token:
            return {}

        url = f"{BASE_URL}/bot/v3/info"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
        }, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    bot = data.get("bot", {})
                    self._bot_open_id = bot.get("open_id", "")
                    return bot
                return {}
        except Exception as e:
            print(f"[FeishuBot] Bot info 异常: {e}")
            return {}

    # ── 消息发送 ────────────────────────────────────────────────

    def send_text(self, text: str, chat_id: str = "") -> bool:
        """发送文本消息到群聊。

        Args:
            text: 消息文本
            chat_id: 目标群聊 ID，默认使用 self.chat_id

        Returns:
            bool 是否发送成功
        """
        if not text.strip():
            return False

        target_chat = chat_id or self.chat_id
        token = self._get_tenant_token()
        if not token:
            return False

        url = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
        body = json.dumps({
            "receive_id": target_chat,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    return True
                else:
                    print(f"[FeishuBot] 发送失败: {data}")
                    return False
        except Exception as e:
            print(f"[FeishuBot] 发送异常: {e}")
            return False

    def send_image(self, image_path: str, chat_id: str = "") -> bool:
        """发送图片消息到群聊。

        需要先上传图片获取 image_key，再发消息。
        飞书支持: JPEG, PNG, WEBP, GIF（最大 10MB）。

        Args:
            image_path: 本地图片路径
            chat_id: 目标群聊 ID

        Returns:
            bool 是否发送成功
        """
        target_chat = chat_id or self.chat_id
        token = self._get_tenant_token()
        if not token:
            return False

        img_path = Path(image_path)
        if not img_path.exists():
            print(f"[FeishuBot] 图片不存在: {image_path}")
            return False

        # 步骤 1: 上传图片获取 image_key
        import mimetypes
        boundary = "----KuafuFeishuBot" + str(int(time.time()))
        img_data = img_path.read_bytes()
        mime_type = mimetypes.guess_type(str(img_path))[0] or "image/png"
        filename = img_path.name

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'.encode())
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
        body.extend(img_data)
        body.extend(f"\r\n--{boundary}--\r\n".encode())

        upload_url = f"{BASE_URL}/im/v1/images"
        upload_req = urllib.request.Request(upload_url, data=bytes(body), headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }, method="POST")

        try:
            with urllib.request.urlopen(upload_req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") != 0:
                    print(f"[FeishuBot] 图片上传失败: {data}")
                    return False
                image_key = data.get("data", {}).get("image_key", "")
        except Exception as e:
            print(f"[FeishuBot] 图片上传异常: {e}")
            return False

        # 步骤 2: 发送图片消息
        msg_url = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
        msg_body = json.dumps({
            "receive_id": target_chat,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
        }, ensure_ascii=False).encode("utf-8")

        msg_req = urllib.request.Request(msg_url, data=msg_body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }, method="POST")

        try:
            with urllib.request.urlopen(msg_req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("code") == 0
        except Exception as e:
            print(f"[FeishuBot] 图片消息发送异常: {e}")
            return False

    def send_file(self, file_path: str, chat_id: str = "") -> bool:
        """发送文件到群聊。

        需要先上传文件获取 file_key，再发消息。
        支持任意文件类型（最大 20MB）。

        Args:
            file_path: 本地文件路径
            chat_id: 目标群聊 ID

        Returns:
            bool 是否发送成功
        """
        target_chat = chat_id or self.chat_id
        token = self._get_tenant_token()
        if not token:
            return False

        fpath = Path(file_path)
        if not fpath.exists():
            print(f"[FeishuBot] 文件不存在: {file_path}")
            return False

        # 步骤 1: 上传文件
        import mimetypes
        boundary = "----KuafuFeishuBot" + str(int(time.time()))
        file_data = fpath.read_bytes()
        mime_type = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
        filename = fpath.name

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="file_type"\r\n\r\nstream\r\n'.encode())
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="file_name"\r\n\r\n{filename}\r\n'.encode())
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
        body.extend(file_data)
        body.extend(f"\r\n--{boundary}--\r\n".encode())

        upload_url = f"{BASE_URL}/im/v1/files"
        upload_req = urllib.request.Request(upload_url, data=bytes(body), headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }, method="POST")

        try:
            with urllib.request.urlopen(upload_req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") != 0:
                    print(f"[FeishuBot] 文件上传失败: {data}")
                    return False
                file_key = data.get("data", {}).get("file_key", "")
        except Exception as e:
            print(f"[FeishuBot] 文件上传异常: {e}")
            return False

        # 步骤 2: 发送文件消息
        msg_url = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
        msg_body = json.dumps({
            "receive_id": target_chat,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }, ensure_ascii=False).encode("utf-8")

        msg_req = urllib.request.Request(msg_url, data=msg_body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }, method="POST")

        try:
            with urllib.request.urlopen(msg_req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("code") == 0
        except Exception as e:
            print(f"[FeishuBot] 文件消息发送异常: {e}")
            return False

    def reply_text(self, msg_id: str, text: str) -> bool:
        """回复特定消息（带引用）。"""
        if not text.strip():
            return False

        token = self._get_tenant_token()
        if not token:
            return False

        url = f"{BASE_URL}/im/v1/messages/{msg_id}/reply"
        body = json.dumps({
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    return True
                else:
                    print(f"[FeishuBot] 回复失败: {data}")
                    return False
        except Exception as e:
            print(f"[FeishuBot] 回复异常: {e}")
            return False

    # ── 消息接收（轮询） ────────────────────────────────────────

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """获取最近的群聊消息。

        ⚠️ sort_type=ByCreateTimeDesc 是必须的，否则按从旧到新排序。
        """
        token = self._get_tenant_token()
        if not token:
            return []

        url = (
            f"{BASE_URL}/im/v1/messages"
            f"?container_id_type=chat"
            f"&container_id={self.chat_id}"
            f"&page_size={limit}"
            f"&sort_type=ByCreateTimeDesc"
        )

        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
        }, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    items = data.get("data", {}).get("items", [])
                    # 按创建时间正序排列（最早的在前），方便按顺序处理
                    items.sort(key=lambda m: m.get("create_time", ""))
                    return items
                else:
                    return []
        except Exception as e:
            print(f"[FeishuBot] 获取消息异常: {e}")
            return []

    def _is_bot_mentioned(self, msg: dict) -> bool:
        """检查消息中是否 @了 Bot。"""
        mentions = msg.get("mentions", [])
        if not mentions:
            return False
        # 用 open_id 比对
        for m in mentions:
            if m.get("id", "") == self._bot_open_id:
                return True
        return False

    @staticmethod
    def _extract_text(msg: dict) -> str:
        """从消息中提取纯文本，去除 @mention 标记。"""
        body = msg.get("body", {})
        content = body.get("content", "{}")
        msg_type = msg.get("msg_type", "")

        if msg_type == "text":
            try:
                text = json.loads(content).get("text", "")
            except Exception:
                text = content
        else:
            text = content

        # 去除所有 @mention 标记（如 @_user_1）
        clean_text = re.sub(r"@\S+", "", text).strip()
        return clean_text

    # ── 已处理消息持久化 ────────────────────────────────────────

    def _load_seen(self):
        """从文件加载已处理的 message IDs。"""
        try:
            if self._seen_file.exists():
                data = json.loads(self._seen_file.read_text(encoding="utf-8"))
                self._seen_ids = set(data.get("ids", []))
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        """持久化已处理的 message IDs（保留最近 1000 条）。"""
        try:
            self._seen_file.parent.mkdir(parents=True, exist_ok=True)
            ids = list(self._seen_ids)[-1000:]
            self._seen_file.write_text(
                json.dumps({"ids": ids}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[FeishuBot] 保存 seen IDs 失败: {e}")

    # ── 轮询主循环 ──────────────────────────────────────────────

    def _poll_loop(self):
        """轮询主循环（在后台线程中运行）。"""
        print(f"[FeishuBot] 🟢 开始轮询 {self.chat_id}（间隔 {self.poll_interval}s）")
        while self._running:
            try:
                msgs = self.get_recent_messages(limit=20)
                for msg in reversed(msgs):  # 最新的先处理
                    msg_id = msg.get("message_id", "")
                    if not msg_id or msg_id in self._seen_ids:
                        continue

                    self._seen_ids.add(msg_id)

                    # 检查是不是文本消息
                    msg_type = msg.get("msg_type", "")
                    if msg_type != "text":
                        continue

                    # 检查是否 @Bot
                    if not self._is_bot_mentioned(msg):
                        continue

                    # 提取文本
                    clean_text = self._extract_text(msg)
                    if not clean_text:
                        continue

                    print(f"[FeishuBot] 📩 收到: {clean_text[:80]}")

                    # 调用回调处理
                    if self.on_message:
                        try:
                            reply = self.on_message(clean_text, msg_id)
                            if reply:
                                # 回复不能太长，飞书有 20000 字符限制
                                if len(reply) > 19000:
                                    reply = reply[:19000] + "\n\n...（回复已截断）"
                                self.reply_text(msg_id, reply)
                                print(f"[FeishuBot] ✅ 已回复: {reply[:60]}...")
                            else:
                                print(f"[FeishuBot] ⚠️ 回调无回复")
                        except Exception as e:
                            error_text = f"❌ 处理出错: {str(e)[:100]}"
                            print(f"[FeishuBot] ❌ 回调异常: {e}")
                            self.reply_text(msg_id, error_text)

                # 定期持久化 seen IDs
                self._save_seen()

            except Exception as e:
                print(f"[FeishuBot] 轮询异常: {e}")

            # 等待
            for _ in range(int(self.poll_interval / 0.5)):
                if not self._running:
                    break
                time.sleep(0.5)

        print("[FeishuBot] 🔴 轮询已停止")

    # ── 启动 / 停止 ─────────────────────────────────────────────

    def start(self):
        """启动飞书 Bot 轮询（异步线程）。"""
        if self._running:
            print("[FeishuBot] 已在运行中")
            return

        if not self.app_id or not self.app_secret:
            print("[FeishuBot] ❌ App ID 或 Secret 未配置")
            return

        if not self.chat_id:
            print("[FeishuBot] ❌ 群聊 ID 未配置")
            return

        # 获取 Bot 信息，缓存 open_id
        print("[FeishuBot] 获取 Bot 信息...")
        bot_info = self.get_bot_info()
        if bot_info:
            print(f"[FeishuBot] Bot: {bot_info.get('app_name', 'unknown')} (open_id: {self._bot_open_id[:20]}...)")
        else:
            print(f"[FeishuBot] ⚠️ 无法获取 Bot 信息")

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print("[FeishuBot] ✅ 已启动")

    def stop(self):
        """停止轮询。"""
        self._running = False
        self._save_seen()
        print("[FeishuBot] 正在停止...")

    def is_running(self) -> bool:
        return self._running


# ── 简易测试入口 ───────────────────────────────────────────────

if __name__ == "__main__":
    if not FEISHU_ENABLED:
        print("飞书 Bot 未配置。请在 .env 中设置 FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID。")
        exit(1)

    def on_msg(text: str, msg_id: str) -> str:
        return f"收到: {text}（自动回复）"

    bot = FeishuBot(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        chat_id=FEISHU_CHAT_ID,
        on_message=on_msg,
    )
    bot.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bot.stop()
        print("已退出")
