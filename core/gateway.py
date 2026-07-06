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
from socketserver import ThreadingMixIn
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
    gateway_server: Any = None  # 引用 GatewayServer 实例，供通道管理 API 使用

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

    # ── CORS 辅助 ──────────────────────────────────────────

    def _add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    # ── OPTIONS (CORS 预检) ──────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(200)
        self._add_cors_headers()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    # ── _send_json 统一加 CORS ─────────────────────────────

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

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
        elif path == "/api/channel/discover":
            self._handle_channel_discover()
        elif path == "/api/channel/list":
            self._handle_channel_list()
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
        elif path == "/api/health":
            self._send_json(200, {"status": "ok", "mode": "gateway"})
        elif path == "/api/restart":
            self._handle_restart()
        # ── 通道管理 API ──
        elif path == "/api/channel/discover":
            self._handle_channel_discover()
        elif path == "/api/channel/load":
            self._handle_channel_load()
        elif path == "/api/channel/remove":
            self._handle_channel_remove()
        elif path == "/api/channel/reload":
            self._handle_channel_reload()
        elif path == "/api/channel/list":
            self._handle_channel_list()
        # ── 批量任务 API ──
        elif path == "/api/batch/submit":
            self._handle_batch_submit()
        elif path == "/api/batch/status":
            self._handle_batch_status()
        elif path == "/api/batch/list":
            self._handle_batch_list()
        elif path == "/api/batch/cancel":
            self._handle_batch_cancel()
        elif path == "/api/batch/retry":
            self._handle_batch_retry()
        elif path == "/api/batch/clear":
            self._handle_batch_clear()
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
        # P3 自我审查状态
        try:
            from core.self_review import SelfReviewer
            if hasattr(agent, '_self_reviewer') and agent._self_reviewer is not None:
                status['self_review'] = {
                    'alive': agent._self_reviewer.is_running,
                    'findings': len(getattr(agent._self_reviewer, '_previous_findings', [])),
                }
        except Exception:
            pass
        # P4 空闲自主代理状态
        try:
            if hasattr(agent, '_idle_agent') and agent._idle_agent is not None:
                status['idle_agent'] = agent._idle_agent.get_status()
        except Exception:
            pass
        self._send_json(200, status)

    def _handle_task(self):
        body = self._read_body()
        task_text = body.get("task", body.get("prompt", ""))
        if not task_text:
            self._send_json(400, {"error": "Missing 'task' field"})
            return

        mode = body.get("mode", "standard")
        sync = body.get("sync", True)

        import datetime as _dt
        ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[Gateway] {ts} 收到任务: mode={mode} sync={sync} len={len(task_text)}", flush=True)

        if sync:
            try:
                print(f"[Gateway] {_dt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} agent.run() 开始...", flush=True)
                result = self.agent.run(task_text, mode=mode)
                duration = result.get("duration", 0)
                print(f"[Gateway] {_dt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} agent.run() 完成: "
                      f"success={result.get('success')} turns={result.get('turns')} "
                      f"duration={duration}s "
                      f"result_len={len(result.get('result', ''))}",
                      flush=True)

                # 安全兜底：空结果+空错误时注入诊断信息
                final_result = result.get("result", "")
                final_errors = result.get("errors", [])
                if not final_result and not final_errors:
                    final_errors = ["夸父引擎未能生成回答。这可能因为 LLM 调用失败（API Key 无效/网络不通/账户余额不足），请联系管理员检查 Gateway 日志。"]
                    print(f"[Gateway] ⚠️ 空结果空错误，注入诊断提示", flush=True)
                elif not final_result and final_errors:
                    # result 为空但 errors 有内容，直接显示错误
                    final_result = f"⚠️ {final_errors[0]}"

                # 如果 result 非空但非常短（可能是 LLM 调用失败但 Gateway 没捕获），补充日志到响应
                if final_result and len(final_result) < 5 and not final_errors:
                    import traceback as _tb
                    # 打印实际 agent.run 返回的完整内容到日志
                    print(f"[Gateway] ⚠️ 返回结果过短: {repr(final_result)}", flush=True)
                    print(f"[Gateway] ⚠️ 完整 result 字典: {json.dumps(result, ensure_ascii=False, default=str)[:500]}", flush=True)

                self._send_json(200, {
                    "success": result.get("success", False),
                    "result": final_result,
                    "duration": result.get("duration", 0),
                    "turns": result.get("turns", 0),
                    "errors": final_errors,
                })
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                error_msg = str(e)
                print(f"[Gateway] _handle_task 异常: {error_msg}\n{tb}", flush=True)
                # 尝试用 errors=replace 打印完整错误信息
                safe_error = error_msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                safe_tb = tb.encode("utf-8", errors="replace").decode("utf-8", errors="replace")[-500:]
                self._send_json(500, {
                    "success": False,
                    "result": f"引擎内部错误: {safe_error}",
                    "error": safe_error,
                    "traceback": safe_tb,
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
                on_task_run=lambda task: self.agent.run(task.task_text, skip_approval=True)["result"]
            )
            self.agent._cron_scheduler = scheduler

        task = CronTask(
            name=body.get("name", f"api_{int(time.time())}"),
            schedule=body.get("schedule", "30m"),
            task_text=body.get("task", ""),
            enabled=True,
            output_mode=body.get("output_mode", "file"),
            source_channel=body.get("source_channel", ""),
            chat_id=body.get("chat_id", ""),
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

    def _handle_restart(self):
        import subprocess, os, sys, time as _time

        # ── 检查待审批 ──
        try:
            from core.approval import ApprovalManager
            pending = ApprovalManager.list_pending()
            if pending:
                cnt = len(pending)
                self._send_json(200, {
                    "status": "waiting_approvals",
                    "pending": cnt,
                    "message": f"等待 {cnt} 个待审批完成 (最多30s)",
                })
                # 等待待审批完成或超时
                for _ in range(30):
                    if not ApprovalManager.list_pending():
                        break
                    _time.sleep(1)
        except Exception:
            pass

        self._send_json(200, {"status": "restarting"})

        kuafu_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        python = sys.executable
        venv_python = os.path.join(kuafu_dir, "venv", "bin", "python")

        # 优先用 venv 的 python
        target_python = python
        if os.path.exists(venv_python):
            target_python = venv_python

        # 先停止当前 Gateway，释放端口
        if self.shutdown_event:
            self.shutdown_event.set()

        # 等待端口释放
        _time.sleep(1)

        # 再拉起新 Gateway 进程
        subprocess.Popen(
            [target_python, "-c", 
             "import sys, core.cli; sys.argv = ['kuafu', 'gateway', 'start']; sys.exit(core.cli.main())"],
            cwd=kuafu_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ── 通道管理 API ──────────────────────────────────────────

    def _get_channel_mgr(self):
        """获取 GatewayServer 的 ChannelManager。"""
        gw = getattr(type(self), 'gateway_server', None)
        if gw is None:
            return None
        return getattr(gw, 'channels', None)

    def _handle_channel_discover(self):
        """扫描所有可用的通道类。"""
        from core.channel.manager import ChannelManager
        registry = ChannelManager.discover_channels()
        self._send_json(200, {
            "discovered": {name: cls.__name__ for name, cls in registry.items()},
        })

    def _handle_channel_load(self):
        """热加载一个通道。"""
        body = self._read_body()
        name = body.get("name", "")
        if not name:
            self._send_json(400, {"error": "Missing 'name' field"})
            return

        mgr = self._get_channel_mgr()
        if not mgr:
            self._send_json(400, {"error": "ChannelManager not available"})
            return

        ch = mgr.load_channel(name)
        if ch:
            self._send_json(200, {"status": "loaded", "name": name})
        else:
            self._send_json(500, {"error": f"Failed to load channel '{name}'"})

    def _handle_channel_remove(self):
        """移除并停止一个通道。"""
        body = self._read_body()
        name = body.get("name", "")
        if not name:
            self._send_json(400, {"error": "Missing 'name' field"})
            return

        mgr = self._get_channel_mgr()
        if not mgr:
            self._send_json(400, {"error": "ChannelManager not available"})
            return

        ok = mgr.remove(name)
        if ok:
            self._send_json(200, {"status": "removed", "name": name})
        else:
            self._send_json(404, {"error": f"Channel '{name}' not found"})

    def _handle_channel_reload(self):
        """热重载一个通道（stop → load → start）。"""
        body = self._read_body()
        name = body.get("name", "")
        if not name:
            self._send_json(400, {"error": "Missing 'name' field"})
            return

        mgr = self._get_channel_mgr()
        if not mgr:
            self._send_json(400, {"error": "ChannelManager not available"})
            return

        ok = mgr.reload_channel(name)
        if ok:
            self._send_json(200, {"status": "reloaded", "name": name})
        else:
            self._send_json(500, {"error": f"Failed to reload channel '{name}'"})

    def _handle_channel_list(self):
        """列出所有已注册通道及状态。"""
        mgr = self._get_channel_mgr()
        if not mgr:
            self._send_json(200, {"channels": []})
            return

        channels_info = []
        for name in mgr.list():
            ch = mgr.get(name)
            running = getattr(ch, '_running', False) if ch else False
            channels_info.append({"name": name, "running": running})
        self._send_json(200, {"channels": channels_info})

    # ── 批量任务 API ───────────────────────────────────────────

    def _handle_batch_submit(self):
        """提交批量任务。"""
        from core.batch_engine import BatchEngine
        engine = BatchEngine(agent=self.agent)

        body = self._read_body()
        tasks = body.get("tasks", [])
        if not tasks:
            self._send_json(400, {"error": "Missing 'tasks' field (list of strings)"})
            return

        batch_id = body.get("batch_id", "")
        mode = body.get("mode", "standard")

        batch_id = engine.submit(tasks, mode=mode, batch_id=batch_id or None)
        self._send_json(202, {
            "status": "accepted",
            "batch_id": batch_id,
            "total": len(tasks),
        })

    def _handle_batch_status(self):
        """查询批次状态。"""
        from core.batch_engine import BatchEngine
        engine = BatchEngine(agent=self.agent)

        body = self._read_body()
        batch_id = body.get("batch_id", body.get("batch", ""))
        if not batch_id:
            self._send_json(400, {"error": "Missing 'batch_id' field"})
            return

        status = engine.get_status(batch_id)
        self._send_json(200, {
            "batch_id": status.batch_id,
            "total": status.total,
            "completed": status.completed,
            "running": status.running,
            "failed": status.failed,
            "pending": status.pending,
            "results": status.results,
        })

    def _handle_batch_list(self):
        """列出所有批次。"""
        from core.batch_engine import BatchEngine
        engine = BatchEngine(agent=self.agent)

        limit = self._get_query_param("limit", 20)
        batches = engine.get_all_batches(limit=int(limit))
        self._send_json(200, {"batches": batches})

    def _handle_batch_cancel(self):
        """取消批次。"""
        from core.batch_engine import BatchEngine
        engine = BatchEngine(agent=self.agent)

        body = self._read_body()
        batch_id = body.get("batch_id", "")
        if not batch_id:
            self._send_json(400, {"error": "Missing 'batch_id' field"})
            return

        count = engine.cancel_batch(batch_id)
        self._send_json(200, {"status": "cancelled", "count": count})

    def _handle_batch_retry(self):
        """重试失败任务。"""
        from core.batch_engine import BatchEngine
        engine = BatchEngine(agent=self.agent)

        body = self._read_body()
        batch_id = body.get("batch_id", "")
        if not batch_id:
            self._send_json(400, {"error": "Missing 'batch_id' field"})
            return

        count = engine.retry_failed(batch_id)
        self._send_json(200, {"status": "retrying", "count": count})

    def _handle_batch_clear(self):
        """清理批次记录。"""
        from core.batch_engine import BatchEngine
        engine = BatchEngine(agent=self.agent)

        body = self._read_body()
        batch_id = body.get("batch_id", "")
        if not batch_id:
            self._send_json(400, {"error": "Missing 'batch_id' field"})
            return

        count = engine.clear_batch(batch_id)
        self._send_json(200, {"status": "cleared", "count": count})

    def _get_query_param(self, name: str, default: Any = None) -> Any:
        """从 URL 查询参数中取值。"""
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        vals = qs.get(name, [])
        return vals[0] if vals else default

    # ── Gateway 详细日志 ──────────────────────────────────────

    def log_message(self, format, *args):
        """HTTP 访问日志 — Desktop 模式静默"""
        if os.environ.get("KUAFU_DESKTOP") == "1":
            pass  # Desktop 模式静默
        else:
            import time
            print(f"[Gateway] {self.client_address[0]} - {self.command} {self.path} - {time.strftime('%H:%M:%S')}")


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
        self.api_key = api_key or os.environ.get("KUAFU_GATEWAY_KEY", "")
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._shutdown_event = threading.Event()
        self._feishu_bot: Optional[Any] = None
        self._wechat_bot: Optional[Any] = None

        # 通道管理器
        self.channels: Any = None
        self._gateway_loop: Any = None
        self._inject_desktop_env()
        self._init_channels()

    def _inject_desktop_env(self):
        """注入 Desktop 环境变量（确保首次启动配置正确）"""
        import datetime
        if os.environ.get("KUAFU_DESKTOP") != "1":
            return
        # 通知 Platform 层进入 Desktop 模式
        from core.platform import Platform
        Platform.set_desktop_mode(True)
        # Windows 上强制 UTF-8 编码，避免 emoji 等字符导致 GBK 编码错误
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        os.environ["PYTHONUTF8"] = "1"
        # 在 Python 运行时层面强制 stdout/stderr 编码
        if hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
        if hasattr(sys.stderr, 'reconfigure'):
            try:
                sys.stderr.reconfigure(encoding='utf-8')
            except Exception:
                pass
        # 暴力替换 stdout/stderr 为 UTF-8 编码包装器
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        os.environ.setdefault("KUAFU_PROVIDERS", "deepseek")
        os.environ.setdefault("KUAFU_LLM_BACKEND", "cloud")
        # 打印当前编码以便调试
        print(f"[Gateway] sys.stdout.encoding={sys.stdout.encoding}", flush=True)
        print(f"[Gateway] PYTHONIOENCODING={os.environ.get('PYTHONIOENCODING', '(未设置)')}", flush=True)
        msg = (
            f"\n{'='*50}\n"
            f"  夸父 Gateway (Desktop)\n"
            f"  启动时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  后端: {os.environ.get('KUAFU_LLM_BACKEND', 'cloud')}\n"
            f"  提供商: {os.environ.get('KUAFU_PROVIDERS', 'deepseek')}\n"
            f"  端口: {self.port}\n"
            f"{'='*50}"
        )
        print(msg, flush=True)

    def _init_channels(self):
        """初始化消息通道（直连模式：飞书WS + 微信iLink）。"""
        # Desktop 模式下不加载消息通道（微信扫码/飞书 WS 无意义）
        if os.environ.get("KUAFU_DESKTOP") == "1":
            print("[Gateway] Desktop 模式：跳过消息通道注册")
            return

        try:
            from core.channel import ChannelManager
            from core.channel.feishu_ws import FeishuWebSocketChannel
            from core.channel.wechat_ilink import WeChatILinkChannel
            from core.channel.gateway_loop import GatewayLoop

            mgr = ChannelManager()

            # 飞书 WebSocket 直连通道
            fs_app_id = os.environ.get("FEISHU_APP_ID", "")
            fs_app_secret = os.environ.get("FEISHU_APP_SECRET", "")
            if fs_app_id and fs_app_secret:
                mgr.register(FeishuWebSocketChannel())
                print("[Gateway] 飞书 WS 直连通道已注册")

            # 微信 iLink API 通道（腾讯官方，零配置，扫码登录）
            mgr.register(WeChatILinkChannel())
            print("[Gateway] 微信 iLink 通道已注册（扫码登录）")

            if mgr.list():
                self.channels = mgr
                self._gateway_loop = GatewayLoop(self.agent, mgr)
                # 从 ChannelManager 获取 Bot 实例注入 cron
                for ch_name in mgr.list():
                    ch = mgr.get(ch_name)
                    if not ch:
                        continue
                    if 'feishu' in ch_name.lower() or '飞书' in ch_name:
                        if hasattr(ch, 'send') or hasattr(ch, 'send_text'):
                            self._feishu_bot = ch
                    if 'wechat' in ch_name.lower() or '微信' in ch_name or 'ilink' in ch_name.lower():
                        if hasattr(ch, 'send') or hasattr(ch, 'send_text'):
                            self._wechat_bot = ch
                if self._feishu_bot:
                    print("[Gateway] 📱 飞书 Bot 已注入")
                if self._wechat_bot:
                    print("[Gateway] 💬 微信 Bot 已注入")
            else:
                print("[Gateway] 未配置任何消息通道（仅 HTTP API）")

        except ImportError as e:
            print(f"[Gateway] 通道初始化跳过: {e}")
        except Exception as e:
            print(f"[Gateway] 通道初始化异常: {e}")

    def _auto_start_cron(self):
        """自动加载 cron/schedule.yaml 并启动调度器。"""
        try:
            from core.cron_scheduler import CronScheduler
            scheduler = getattr(self.agent, '_cron_scheduler', None)
            if scheduler:
                # 已有调度器，确保它在运行
                if not scheduler._running:
                    scheduler.start()
                    print("[Gateway] Cron 调度器已恢复运行")
                return

            schedule_path = ROOT_DIR / "cron" / "schedule.yaml"
            if not schedule_path.exists():
                print("[Gateway] cron/schedule.yaml 不存在，跳过调度器启动")
                return

            # 创建调度器，加载 schedule.yaml 中的任务
            scheduler = CronScheduler(
                on_task_run=lambda task: self.agent.run(task.task_text, skip_approval=True)["result"],
            )
            self.agent._cron_scheduler = scheduler
            # 同步到模块级全局变量，供 AgentLoop 的 cron_list/cron_remove 读取
            from core.cron_scheduler import _global_scheduler
            _global_scheduler = scheduler
            # 注入通道 Bot
            if hasattr(self, '_feishu_bot') and self._feishu_bot:
                scheduler.set_feishu_bot(self._feishu_bot)
            if hasattr(self, '_wechat_bot') and self._wechat_bot:
                scheduler.set_wechat_bot(self._wechat_bot)
            if scheduler.get_tasks():
                scheduler.start()
                print(f"[Gateway] Cron 调度器已启动，{len(scheduler.get_tasks())} 个任务")
            else:
                print("[Gateway] schedule.yaml 中无有效任务")
                # 仍然启动调度器（空状态），等待后续通过 API 添加的任务
                scheduler.start()
                print("[Gateway] Cron 调度器已启动（空状态，等待 API 添加任务）")
        except Exception as e:
            print(f"[Gateway] Cron 调度器启动失败: {e}")

    def _start_workflow_editor(self):
        """后台启动工作流编辑器 Web 服务。"""
        try:
            from workflow_v2_web.server import main as wf_server_main
            import threading
            t = threading.Thread(target=wf_server_main, daemon=True,
                                 name="workflow-editor")
            t.start()
            print("[Gateway] 🏗️ 工作流编辑器启动 (http://localhost:8899)")
        except Exception as e:
            print(f"[Gateway] 工作流编辑器启动失败: {e}")

    # ── 通道管理 API ──

    def start(self) -> bool:
        """启动 Gateway。"""
        if self._running:
            print("[Gateway] 已在运行")
            return True

        try:
            # 启动消息通道
            if self.channels:
                self.channels.start_all()
                print(f"[Gateway] 通道: {', '.join(self.channels.list())}")
            if self._gateway_loop:
                self._gateway_loop.start()

            # 自动加载 cron/schedule.yaml 并启动调度器
            self._auto_start_cron()

            # 启动工作流编辑器 API（后台线程）
            self._start_workflow_editor()

            # 设置 Handler 的类变量
            GatewayHandler.agent = self.agent
            GatewayHandler.api_key = self.api_key
            GatewayHandler.shutdown_event = self._shutdown_event
            GatewayHandler.start_time = time.time()
            GatewayHandler.gateway_server = self

            # 用 ThreadingMixIn 实现并发处理，防止长任务阻塞其他 API
            class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
                allow_reuse_address = True
                daemon_threads = True

            self._server = ThreadedHTTPServer((self.host, self.port), GatewayHandler)
            self._server.timeout = 1.0  # 1秒超时，便于 shutdown 检查
            self._running = True

            self._thread = threading.Thread(
                target=self._serve,
                daemon=True,
                name="gateway-http",
            )
            self._thread.start()

            print(f"[Gateway] 启动: http://{self.host}:{self.port}", flush=True)
            if self.api_key:
                print(f"[Gateway] API Key 认证已启用")
            # 打印环境关键参数
            import datetime as _dt
            print(f"[Gateway] 时间: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            print(f"[Gateway] Python: {sys.version.split()[0]} {sys.executable}", flush=True)
            print(f"[Gateway] PYTHONPATH: {os.environ.get('PYTHONPATH', '(未设置)')}", flush=True)
            print(f"[Gateway] 工作目录: {os.getcwd()}", flush=True)
            for key in ["KUAFU_DESKTOP", "KUAFU_LLM_BACKEND", "KUAFU_PROVIDERS",
                         "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
                         "KUAFU_LLM_ENDPOINT", "KUAFU_LLM_MODEL_PATH"]:
                val = os.environ.get(key, "")
                if key.endswith("API_KEY") and val:
                    val = val[:8] + "..."
                if val:
                    print(f"[Gateway]   {key}={val}", flush=True)
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
        if self._gateway_loop:
            self._gateway_loop.stop()
        if self.channels:
            self.channels.stop_all()
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
Environment=KUAFU_INTERACTIVE=0
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
