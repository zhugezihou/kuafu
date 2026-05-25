"""
core/webhook_server.py — WebHook 事件驱动服务器

外部系统可以通过 HTTP POST 触发夸父执行任务。
基于 Python 标准库 http.server，零新增依赖。

端点：
- POST /webhook/<token> — 通用 WebHook 入口
- GET  /health          — 健康检查
"""

from __future__ import annotations

import json
import time
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger("kuafu.webhook")


def _make_handler(on_task: Optional[Callable], config: dict):
    """动态生成 BaseHTTPRequestHandler 子类，注入回调。"""

    class _Handler(BaseHTTPRequestHandler):
        _config = config

        # 在 super().__init__ 之前设置，避免 handle_one_request 提前调用 do_*
        def __init__(self, *args, **kwargs):
            self._on_task = on_task
            super().__init__(*args, **kwargs)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json(200, {"status": "ok", "time": time.time()})
            else:
                self._send_json(404, {"error": "not_found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            expected_token = self._config.get("token", "")
            if expected_token:
                if parsed.path != f"/webhook/{expected_token}":
                    self._send_json(403, {"error": "invalid_token"})
                    return
            else:
                if not parsed.path.startswith("/webhook/"):
                    self._send_json(404, {"error": "not_found"})
                    return

            content_length = int(self.headers.get("Content-Length", 0))
            max_size = self._config.get("max_body_size", 1024 * 1024)
            if content_length > max_size:
                self._send_json(413, {"error": "body_too_large"})
                return

            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid_json"})
                return

            if self._on_task:
                try:
                    task_id = f"wh_{int(time.time())}_{id(payload)}"
                    self._on_task(payload, task_id)
                    self._send_json(202, {"status": "accepted", "task_id": task_id})
                except Exception as e:
                    logger.error(f"❌ WebHook 任务处理失败: {e}")
                    self._send_json(500, {"error": str(e)})
            else:
                self._send_json(503, {"error": "no_handler"})

        def _send_json(self, status: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            logger.debug(f"WebHook: {fmt % args}")

    return _Handler


class WebhookServer:
    """轻量 WebHook 服务器。

    用法:
        server = WebhookServer(port=8765, token="my-token")
        server.set_handler(on_task=my_callback)
        server.start()  # 后台线程
        ...
        server.stop()
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        token: str = "",
        max_body_size: int = 1024 * 1024,
    ):
        self.host = host
        self.port = port
        self.token = token
        self._config = {
            "token": token,
            "max_body_size": max_body_size,
        }
        self._on_task: Optional[Callable] = None
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def set_handler(self, on_task: Optional[Callable[[dict, str], None]] = None):
        """设置任务处理回调。

        Args:
            on_task: 回调函数 (payload: dict, task_id: str) -> None
        """
        self._on_task = on_task

    def start(self) -> bool:
        """在后台线程启动 HTTP 服务器。"""
        if self._running:
            logger.warning("WebHook 服务器已在运行")
            return True

        handler_cls = _make_handler(self._on_task, self._config)
        try:
            self._server = HTTPServer((self.host, self.port), handler_cls)
            self._running = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="kuafu-webhook",
            )
            self._thread.start()
            logger.info(f"🌐 WebHook 服务器启动: http://{self.host}:{self.port}")
            logger.info(f"🔑 Token: {self.token or '(无认证)'}")
            return True
        except Exception as e:
            logger.error(f"❌ WebHook 服务器启动失败: {e}")
            self._running = False
            self._server = None
            return False

    def stop(self):
        """停止服务器。"""
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server = None
        logger.info("WebHook 服务器已停止")

    def is_running(self) -> bool:
        return self._running
