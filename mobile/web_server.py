#!/usr/bin/env python3
"""
夸父 (Kuafu) 手机版 Web UI 服务器
==================================
纯 Python http.server 实现，零外部依赖。
提供 REST API + SSE 实时推送。

用法:
    python mobile/web_server.py [--port 8080]

环境变量:
    KUAFFU_BACKEND   — 模型后端 (local/cloud)
    KUAFFU_HOST      — 监听地址 (默认 0.0.0.0)
    KUAFFU_PORT      — 监听端口 (默认 8080)

API:
    GET  /               → SPA HTML
    POST /api/chat       → 发送消息
    GET  /api/status     → Agent 状态
    POST /api/reset      → 重置对话
    POST /api/approve    → 批准审批 (req_id)
    POST /api/reject     → 拒绝审批 (req_id)
    GET  /api/events     → SSE 实时事件流
    POST /api/model      → 切换模型
"""

import json
import logging
import os
import sys
import time
import queue
import argparse
import threading
import http.server
import socketserver
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from io import BytesIO

# ── 项目根路径 ──
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="[KUAFU-WEB] %(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kuafu-web")

# ═══════════════════════════════════════════════════════════════
# 全局
# ═══════════════════════════════════════════════════════════════

_agent = None
_agent_lock = threading.Lock()

# SSE 事件流：每个连接一个队列
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()

# 静态文件缓存
_STATIC_DIR = ROOT_DIR / "mobile" / "static"
_CHAT_HTML = None


def _load_chat_html() -> str:
    global _CHAT_HTML
    if _CHAT_HTML is None:
        p = _STATIC_DIR / "chat.html"
        if p.exists():
            _CHAT_HTML = p.read_text(encoding="utf-8")
        else:
            _CHAT_HTML = "<h1>夸父 Web UI</h1><p>chat.html 未找到</p>"
    return _CHAT_HTML


def get_agent():
    """获取或创建全局 KuafuAgent 实例。"""
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                log.info("🚀 初始化夸父 Agent...")
                from core.main import KuafuAgent
                _agent = KuafuAgent()
                log.info(f"✅ 就绪: backend={_agent.llm.backend}, model={_agent.llm.model}")
    return _agent


def _sse_broadcast(data: dict):
    """向所有 SSE 客户端广播 JSON 消息。"""
    msg = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead: list[queue.Queue] = []
    with _sse_lock:
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            if q in _sse_clients:
                _sse_clients.remove(q)


def _inject_loop_callbacks(loop):
    """注入 AgentLoop 的实时事件回调（通过 SSE 广播）。"""
    if getattr(loop, '_ws_injected', False):
        return

    loop.on_llm_start = lambda turn: _sse_broadcast({"type": "llm_start", "turn": turn, "ts": time.time()})
    loop.on_llm_end = lambda turn, status: _sse_broadcast({"type": "llm_end", "turn": turn, "status": status, "ts": time.time()})
    loop.on_tool_start = lambda name, args, ts: _sse_broadcast({"type": "tool_start", "tool": name, "args": args, "ts": ts})
    loop.on_tool_end = lambda name, args, elapsed, status: _sse_broadcast({"type": "tool_end", "tool": name, "args": args, "elapsed": elapsed, "status": status, "ts": time.time()})

    original_approval = loop.on_approval_request
    def _approval_cb(tool_name, args, req_id):
        _sse_broadcast({"type": "approval_request", "tool": tool_name, "args": args, "req_id": req_id, "ts": time.time(), "risk": "medium"})
        if original_approval:
            original_approval(tool_name, args, req_id)
    loop.on_approval_request = _approval_cb
    loop._ws_injected = True


# ═══════════════════════════════════════════════════════════════
# HTTP 请求处理器
# ═══════════════════════════════════════════════════════════════

MIME_MAP = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class KuafuHandler(http.server.BaseHTTPRequestHandler):
    """夸父 HTTP 请求处理器。"""

    def log_message(self, format, *args):
        log.info(f"{self.client_address[0]} - {format % args}")

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # SSE 事件流
        if path == "/api/events":
            self._handle_sse()
            return

        # API 路由
        if path == "/api/status":
            self._handle_status()
            return
        if path == "/api/approvals/pending":
            self._handle_pending_approvals()
            return

        # 静态文件
        if path == "/":
            self._send_html(_load_chat_html())
            return

        file_path = _STATIC_DIR / path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            ext = file_path.suffix
            mime = MIME_MAP.get(ext, "application/octet-stream")
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # CORS preflight
        if path == "/api/chat":
            self._handle_chat()
        elif path == "/api/reset":
            self._handle_reset()
        elif path == "/api/approve":
            self._handle_approve()
        elif path == "/api/reject":
            self._handle_reject()
        elif path == "/api/model":
            self._handle_model()
        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── SSE 事件流 ──────────────────────────────────────────────

    def _handle_sse(self):
        q: queue.Queue = queue.Queue()
        with _sse_lock:
            _sse_clients.append(q)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # 发送初始心跳
        self.wfile.write(f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except queue.Empty:
                    # 心跳保活
                    self.wfile.write(": heartbeat\n\n".encode())
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    # ── API 处理 ─────────────────────────────────────────────────

    def _handle_status(self):
        try:
            agent = get_agent()
            status = agent.get_status()
            self._send_json({
                "success": True,
                "name": status.get("name", "夸父"),
                "version": status.get("version", "0.4.0"),
                "model": status.get("llm_model", ""),
                "backend": agent.llm.backend,
                "task_count": status.get("task_count", 0),
                "memory_count": status.get("memory", {}).get("count", 0),
                "evolution_level": status.get("evolution", {}).get("level", 0),
                "prioritizer_alive": status.get("prioritizer", {}).get("alive", False),
            })
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_chat(self):
        try:
            body = self._read_body()
            text = (body.get("text") or body.get("message") or "").strip()
            if not text:
                self._send_json({"success": False, "message": "请输入消息"}, 400)
                return

            agent = get_agent()
            log.info(f"📝 收到: '{text[:60]}...'")

            # 在后台线程运行
            q: queue.Queue = queue.Queue()

            def _run():
                try:
                    agent._ws_inject_cb = _inject_loop_callbacks
                    result = agent.converse(text)
                    q.put(result)
                except Exception as e:
                    q.put({"success": False, "errors": [str(e)], "result": str(e),
                           "turns": 0, "duration": 0})

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            result = q.get()

            log.info(f"✅ 完成: duration={result.get('duration', 0)}s, turns={result.get('turns', 0)}")
            self._send_json({
                "success": result.get("success", False),
                "message": result.get("result", "") or result.get("summary", ""),
                "summary": result.get("summary", ""),
                "turns": result.get("turns", 0),
                "duration": result.get("duration", 0),
                "errors": result.get("errors", []),
                "task_type": result.get("task_type", "generic"),
                "model": agent.llm.model,
            })
        except Exception as e:
            log.error(f"❌ 任务失败: {e}")
            self._send_json({
                "success": False,
                "message": f"执行出错: {type(e).__name__}: {e}",
                "errors": [str(e)],
                "duration": 0,
            }, 500)

    def _handle_reset(self):
        try:
            agent = get_agent()
            agent.reset_conversation()
            self._send_json({"success": True, "message": "对话已重置"})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_approve(self):
        try:
            body = self._read_body()
            req_id = body.get("req_id", "")
            import core.approval as kuafu_approval
            ok = kuafu_approval.ApprovalManager.approve(req_id)
            if ok:
                _sse_broadcast({"type": "approval_result", "req_id": req_id, "approved": True})
                self._send_json({"success": True, "message": "已批准"})
            else:
                self._send_json({"success": False, "message": "审批失败或已处理"}, 400)
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_reject(self):
        try:
            body = self._read_body()
            req_id = body.get("req_id", "")
            import core.approval as kuafu_approval
            ok = kuafu_approval.ApprovalManager.reject(req_id)
            if ok:
                _sse_broadcast({"type": "approval_result", "req_id": req_id, "approved": False})
                self._send_json({"success": True, "message": "已拒绝"})
            else:
                self._send_json({"success": False, "message": "拒绝失败或已处理"}, 400)
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_pending_approvals(self):
        try:
            import core.approval as kuafu_approval
            pending = kuafu_approval.ApprovalManager.list_pending()
            self._send_json({
                "success": True,
                "approvals": [
                    {"id": r.id, "title": r.title, "detail": r.detail[:200],
                     "risk": r.risk, "tool": r.tool, "created_at": r.created_at}
                    for r in pending
                ]
            })
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_model(self):
        try:
            body = self._read_body()
            target = body.get("target", "").strip()
            if not target:
                self._send_json({"success": False, "message": "缺少 target 参数"}, 400)
                return
            agent = get_agent()
            msg = agent.switch_model(target)
            self._send_json({"success": True, "message": msg})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """多线程 HTTP 服务器。"""
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="夸父手机版 Web UI 服务器")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (默认 8080)")
    parser.add_argument("--host", type=str, default=None, help="监听地址 (默认 0.0.0.0)")
    args, _ = parser.parse_known_args()

    host = args.host or os.environ.get("KUAFFU_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("KUAFFU_PORT", "8080"))

    # 注入全局审批回调（确保子 Agent 的审批也能推送）
    import core.approval as kuafu_approval_module
    def _global_approval_cb(tool_name, args, req_id):
        _sse_broadcast({"type": "approval_request", "tool": tool_name, "args": args, "req_id": req_id, "ts": time.time(), "risk": "medium"})
    kuafu_approval_module.ON_APPROVAL_REQUEST_CB = _global_approval_cb

    # 加载 Agent
    log.info("预热夸父 Agent...")
    try:
        agent = get_agent()
        log.info(f"✅ Agent 就绪: backend={agent.llm.backend}, model={agent.llm.model}")
    except Exception as e:
        log.warning(f"Agent 预热失败（可在首次请求时初始化）: {e}")

    log.info(f"🌐 夸父手机版 Web UI 启动: http://{host}:{port}/")
    log.info(f"   SSE: http://{host}:{port}/api/events")
    log.info(f"   Ctrl+C 停止服务器")

    server = ThreadedHTTPServer((host, port), KuafuHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("🛑 服务器关闭")
        server.shutdown()


if __name__ == "__main__":
    main()
