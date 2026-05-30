"""
夸父 Gateway — HTTP API + 守护进程管理。

职责：
1. HTTP REST API: 任务提交、状态查询、cron 管理
2. 健康检查端点（供 systemd 监控）
3. daemonize 模式（后台运行）
4. 心跳日志

设计：
- 纯标准库（http.server），零外部依赖
- 线程级并发处理
- API Key 认证（可选）
"""

import json
import os
import sys
import time
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Optional, Callable

ROOT_DIR = Path(__file__).resolve().parent.parent


class GatewayHandler(BaseHTTPRequestHandler):
    """HTTP API 请求处理器。"""

    # 类变量（由 GatewayServer 在创建时设置）
    agent: Any = None
    api_key: str = ""
    shutdown_event: Optional[threading.Event] = None
    start_time: float = 0.0

    def _send_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _check_auth(self) -> bool:
        if not self.api_key:
            return True
        token = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
        if token == self.api_key:
            return True
        self._send_json(401, {"error": "Unauthorized"})
        return False

    # ── GET 路由 ────────────────────────────────────────────

    def do_GET(self):
        if not self._check_auth():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._handle_health()
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/cron":
            self._handle_cron_list()
        elif path == "/api/sessions":
            self._handle_sessions_list()
        else:
            self._send_json(404, {"error": "Not Found"})

    # ── POST 路由 ───────────────────────────────────────────

    def do_POST(self):
        if not self._check_auth():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/task":
            self._handle_task()
        elif path == "/api/cron/create":
            self._handle_cron_create()
        elif path == "/api/cron/remove":
            self._handle_cron_remove()
        elif path == "/api/cron/start":
            self._handle_cron_start()
        elif path == "/api/cron/stop":
            self._handle_cron_stop()
        elif path == "/api/shutdown":
            self._handle_shutdown()
        else:
            self._send_json(404, {"error": "Not Found"})

    # ── 处理函数 ────────────────────────────────────────────

    def _handle_health(self):
        uptime = time.time() - self.start_time
        self._send_json(200, {
            "status": "ok",
            "uptime": round(uptime, 1),
            "version": getattr(self.agent, "version", "?"),
        })

    def _handle_status(self):
        agent = self.agent
        status = {
            "status": "ok",
            "version": getattr(agent, "version", "?"),
            "model": agent.llm.model if hasattr(agent, "llm") else "?",
            "backend": getattr(agent.llm, "backend", "?") if hasattr(agent, "llm") else "?",
            "task_count": getattr(agent, "_task_count", 0),
        }
        if hasattr(agent, "evolution"):
            evo = agent.evolution.get_evolution_stats() if hasattr(agent.evolution, "get_evolution_stats") else {}
            status["evolution"] = {
                "total": evo.get("total_evolutions", 0),
            }
        self._send_json(200, status)

    def _handle_task(self):
        body = self._read_body()
        task_text = body.get("task", body.get("prompt", ""))
        if not task_text:
            self._send_json(400, {"error": "Missing 'task' field"})
            return

        mode = body.get("mode", "standard")
        sync = body.get("sync", True)

        if sync:
            result = self.agent.run(task_text, mode=mode)
            self._send_json(200, {
                "success": result.get("success", False),
                "result": result.get("result", "")[:2000],
                "duration": result.get("duration", 0),
                "turns": result.get("turns", 0),
                "errors": result.get("errors", []),
            })
        else:
            # 异步执行
            threading.Thread(
                target=lambda: self.agent.run(task_text, mode=mode),
                daemon=True,
            ).start()
            self._send_json(202, {"status": "accepted", "task": task_text[:100]})

    def _handle_cron_list(self):
        scheduler = getattr(self.agent, '_cron_scheduler', None)
        if not scheduler:
            self._send_json(200, {"tasks": []})
            return
        tasks = []
        for t in scheduler.get_tasks():
            tasks.append(t.to_dict())
        self._send_json(200, {"tasks": tasks})

    def _handle_cron_create(self):
        body = self._read_body()
        from core.cron_scheduler import CronScheduler, CronTask

        scheduler = getattr(self.agent, '_cron_scheduler', None)
        if not scheduler:
            scheduler = CronScheduler(
                on_task_run=lambda task: self.agent.run(task.task_text)["result"]
            )
            self.agent._cron_scheduler = scheduler

        task = CronTask(
            name=body.get("name", f"api_{int(time.time())}"),
            schedule=body.get("schedule", "30m"),
            task_text=body.get("task", ""),
            enabled=True,
            output_mode=body.get("output_mode", "file"),
        )
        scheduler.add_task(task)
        if not scheduler._running:
            scheduler.start()
        self._send_json(200, {"status": "created", "name": task.name})

    def _handle_cron_remove(self):
        body = self._read_body()
        name = body.get("name", "")
        scheduler = getattr(self.agent, '_cron_scheduler', None)
        if scheduler and scheduler.remove_task(name):
            self._send_json(200, {"status": "removed", "name": name})
        else:
            self._send_json(404, {"error": f"Task '{name}' not found"})

    def _handle_cron_start(self):
        scheduler = getattr(self.agent, '_cron_scheduler', None)
        if scheduler:
            scheduler.start()
            self._send_json(200, {"status": "started"})
        else:
            self._send_json(200, {"status": "no scheduler"})

    def _handle_cron_stop(self):
        scheduler = getattr(self.agent, '_cron_scheduler', None)
        if scheduler:
            scheduler.stop()
            self._send_json(200, {"status": "stopped"})
        else:
            self._send_json(200, {"status": "no scheduler"})

    def _handle_sessions_list(self):
        store = getattr(self.agent, 'sessions', None)
        if not store:
            self._send_json(200, {"sessions": []})
            return
        sessions = store.list_sessions(limit=50)
        self._send_json(200, {
            "sessions": [
                {"id": s.id, "title": s.title, "messages": s.message_count,
                 "tokens": s.total_tokens, "status": s.status}
                for s in sessions
            ]
        })

    def _handle_shutdown(self):
        self._send_json(200, {"status": "shutting down"})
        if self.shutdown_event:
            self.shutdown_event.set()

    # ── 日志静默 ────────────────────────────────────────────

    def log_message(self, format, *args):
        pass  # 静默，不打印每个请求


class GatewayServer:
    """夸父 Gateway HTTP 服务器。"""

    def __init__(
        self,
        agent: Any,
        host: str = "127.0.0.1",
        port: int = 8765,
        api_key: str = "",
    ):
        self.agent = agent
        self.host = host
        self.port = port
        self.api_key = api_key or os.environ.get("KUAFFU_GATEWAY_KEY", "")
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._shutdown_event = threading.Event()

    def start(self) -> bool:
        """启动 Gateway。"""
        if self._running:
            print("[Gateway] 已在运行")
            return True

        try:
            # 设置 Handler 的类变量
            GatewayHandler.agent = self.agent
            GatewayHandler.api_key = self.api_key
            GatewayHandler.shutdown_event = self._shutdown_event
            GatewayHandler.start_time = time.time()

            self._server = HTTPServer((self.host, self.port), GatewayHandler)
            self._server.timeout = 1.0  # 1秒超时，便于 shutdown 检查
            self._running = True

            self._thread = threading.Thread(
                target=self._serve,
                daemon=True,
                name="gateway-http",
            )
            self._thread.start()

            print(f"[Gateway] 启动: http://{self.host}:{self.port}")
            if self.api_key:
                print(f"[Gateway] API Key 认证已启用")
            return True

        except OSError as e:
            print(f"[Gateway] 启动失败: {e}")
            return False

    def _serve(self):
        """HTTP 服务循环。"""
        while self._running and not self._shutdown_event.is_set():
            self._server.handle_request()

    def stop(self):
        """停止 Gateway。"""
        self._running = False
        self._shutdown_event.set()
        if self._server:
            self._server.server_close()
        print("[Gateway] 已停止")

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()


# ── systemd user service 管理 ───────────────────────────────

SYSTEMD_SERVICE_NAME = "kuafu-gateway"
SYSTEMD_SERVICE_FILE = f"""
[Unit]
Description=Kuafu Gateway — HTTP API for Kuafu AI Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={sys.executable} -m core.gateway --serve
WorkingDirectory={ROOT_DIR}
Environment=PYTHONPATH={ROOT_DIR}
Environment=KUAFFU_INTERACTIVE=0
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def install_service() -> bool:
    """安装 systemd user service。"""
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / f"{SYSTEMD_SERVICE_NAME}.service"

    try:
        service_path.write_text(SYSTEMD_SERVICE_FILE.strip(), encoding="utf-8")
        os.system(f"systemctl --user daemon-reload")
        print(f"[Gateway] systemd service 已安装: {service_path}")
        print(f"[Gateway] 启动: systemctl --user start {SYSTEMD_SERVICE_NAME}")
        print(f"[Gateway] 自启: systemctl --user enable {SYSTEMD_SERVICE_NAME}")
        print(f"[Gateway] 状态: systemctl --user status {SYSTEMD_SERVICE_NAME}")
        return True
    except OSError as e:
        print(f"[Gateway] 安装 systemd service 失败: {e}")
        return False


def uninstall_service() -> bool:
    """卸载 systemd user service。"""
    service_path = Path.home() / ".config" / "systemd" / "user" / f"{SYSTEMD_SERVICE_NAME}.service"
    if not service_path.exists():
        print("[Gateway] service 文件不存在")
        return True

    os.system(f"systemctl --user stop {SYSTEMD_SERVICE_NAME} 2>/dev/null")
    os.system(f"systemctl --user disable {SYSTEMD_SERVICE_NAME} 2>/dev/null")
    try:
        service_path.unlink()
        os.system(f"systemctl --user daemon-reload")
        print(f"[Gateway] systemd service 已卸载")
        return True
    except OSError as e:
        print(f"[Gateway] 卸载失败: {e}")
        return False


# ── CLI ─────────────────────────────────────────────────────


def entry_main():
    """作为 python -m core.gateway 运行的入口。

    用法:
        python -m core.gateway              # 前台运行
        python -m core.gateway --serve      # 前台运行
        python -m core.gateway --install    # 安装 systemd service
        python -m core.gateway --uninstall  # 卸载 systemd service
        python -m core.gateway --port 8765  # 指定端口
        python -m core.gateway --key xxx    # 指定 API Key
    """
    import argparse

    parser = argparse.ArgumentParser(description="夸父 Gateway")
    parser.add_argument("--serve", action="store_true", help="前台运行 Gateway")
    parser.add_argument("--install", action="store_true", help="安装 systemd service")
    parser.add_argument("--uninstall", action="store_true", help="卸载 systemd service")
    parser.add_argument("--port", type=int, default=8765, help="端口 (默认: 8765)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--key", default="", help="API Key")
    args = parser.parse_args()

    if args.install:
        install_service()
        return

    if args.uninstall:
        uninstall_service()
        return

    if args.serve:
        from core.main import KuafuAgent
        agent = KuafuAgent()
        gw = GatewayServer(agent, host=args.host, port=args.port, api_key=args.key)
        if not gw.start():
            sys.exit(1)
        print(f"[Gateway] 夸父 Gateway 运行中 (http://{args.host}:{args.port})")
        print(f"[Gateway] 按 Ctrl+C 停止")
        try:
            gw._shutdown_event.wait()
        except KeyboardInterrupt:
            print()
        gw.stop()
        return


if __name__ == "__main__":
    entry_main()
