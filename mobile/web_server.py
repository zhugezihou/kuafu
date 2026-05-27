#!/usr/bin/env python3
"""
夸父 (Kuafu) 移动端 Web UI 服务器
=====================================
纯 Python 标准库（http.server），零外部依赖。
在手机浏览器中提供类 ChatGPT 聊天界面。

用法:
    python mobile/web_server.py [--port 8080]

环境变量:
    KUAFFU_BACKEND   — 模型后端 (local/cloud)
    KUAFFU_HOST      — 监听地址 (默认 0.0.0.0)
    KUAFFU_PORT      — 监听端口 (默认 8080)

架构:
    - /           → 聊天界面 (SPA HTML)
    - /api/chat   → POST: 发送消息 (流式 SSE)
    - /api/status → GET:  查看状态
    - /api/reset  → POST: 重置对话
    - /api/model  → GET/POST: 查询/切换模型
"""

import json
import os
import sys
import time
import threading
import queue
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
import urllib.parse

# ── 添加项目根目录到 sys.path ──
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# ── 导入夸父核心 ──
from core.main import KuafuAgent
from core.llm import LLMClient

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="[KUAFU-WEB] %(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kuafu-web")

# ── 全局 Agent（单例）──
_agent: Optional[KuafuAgent] = None
_agent_lock = threading.Lock()


def get_agent() -> KuafuAgent:
    """获取或创建全局 KuafuAgent 实例。"""
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                log.info("🚀 初始化夸父 Agent...")
                _agent = KuafuAgent()
                log.info(f"✅ 就绪: backend={_agent.llm.backend}, model={_agent.llm.model}")
    return _agent


# ═══════════════════════════════════════════════════════════════
# SSE 流式聊天
# ═══════════════════════════════════════════════════════════════

def stream_chat(text: str) -> dict:
    """执行任务并获取结果（非流式，因为 AgentLoop 不支持流式）。"""
    agent = get_agent()
    start = time.time()

    try:
        result = agent.converse(text)
        elapsed = round(time.time() - start, 1)

        return {
            "success": result.get("success", False),
            "result": result.get("result", ""),
            "summary": result.get("summary", ""),
            "turns": result.get("turns", 0),
            "errors": result.get("errors", []),
            "duration": elapsed,
            "task_type": result.get("task_type", "generic"),
            "model": agent.llm.model,
        }
    except Exception as e:
        log.error(f"❌ 任务失败: {e}")
        return {
            "success": False,
            "result": f"执行出错: {type(e).__name__}: {e}",
            "errors": [str(e)],
            "duration": round(time.time() - start, 1),
        }


# ═══════════════════════════════════════════════════════════════
# HTML 页面
# ═══════════════════════════════════════════════════════════════

def render_chat_ui() -> str:
    """返回聊天界面 HTML。"""
    with open(ROOT_DIR / "mobile" / "static" / "chat.html", "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════════════════════════════

class KuafuHTTPHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理。"""

    def log_message(self, format, *args):
        """用我们的日志格式替代默认的 stderr 输出。"""
        log.info(f"{self.client_address[0]} - {format % args}")

    def _send_json(self, data: dict, status: int = 200):
        """发送 JSON 响应。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        """读取并解析 JSON 请求体。"""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw.decode("utf-8", errors="replace")}

    def _send_chat_response(self, result: dict):
        """返回聊天结果（JSON 格式，前端渲染）。"""
        self._send_json({
            "success": result["success"],
            "message": result.get("result", ""),
            "summary": result.get("summary", ""),
            "turns": result.get("turns", 0),
            "duration": result.get("duration", 0),
            "errors": result.get("errors", []),
            "task_type": result.get("task_type", "generic"),
            "model": result.get("model", ""),
        })

    # ── 路由 ──

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/index.html":
            # 聊天界面
            html = render_chat_ui()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html.encode("utf-8"))))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path == "/api/status":
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

        elif path == "/api/model":
            try:
                agent = get_agent()
                self._send_json({
                    "success": True,
                    "model": agent.llm.model,
                    "backend": agent.llm.backend,
                    "base_url": agent.llm.base_url,
                    "max_tokens": agent.llm.max_tokens,
                    "temperature": agent.llm.temperature,
                })
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/chat":
            body = self._read_body()
            text = body.get("text", body.get("message", "")).strip()
            if not text:
                self._send_json({"success": False, "message": "请输入消息"}, 400)
                return

            # 流式输出此任务正在进行中
            log.info(f"📝 收到: '{text[:60]}...'")
            result = stream_chat(text)
            log.info(f"✅ 完成: duration={result.get('duration', 0)}s, turns={result.get('turns', 0)}, "
                     f"success={result.get('success', False)}")
            self._send_chat_response(result)

        elif path == "/api/reset":
            try:
                agent = get_agent()
                agent.reset_conversation()
                self._send_json({"success": True, "message": "对话已重置"})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        elif path == "/api/model":
            body = self._read_body()
            target = body.get("target", "").strip()
            if not target:
                self._send_json({"success": False, "message": "缺少 target 参数"}, 400)
                return
            try:
                agent = get_agent()
                msg = agent.switch_model(target)
                self._send_json({"success": True, "message": msg})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_OPTIONS(self):
        """CORS preflight。"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    # 命令行参数解析
    import argparse
    parser = argparse.ArgumentParser(description="夸父移动端 Web UI 服务器")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (默认 8080)")
    parser.add_argument("--host", type=str, default=None, help="监听地址 (默认 0.0.0.0)")
    args, _ = parser.parse_known_args()

    host = args.host or os.environ.get("KUAFFU_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("KUAFFU_PORT", "8080"))

    # 预热 Agent
    log.info("预热夸父 Agent...")
    try:
        get_agent()
    except Exception as e:
        log.error(f"❌ Agent 初始化失败: {e}")
        sys.exit(1)

    server = HTTPServer((host, port), KuafuHTTPHandler)
    log.info(f"🌐 夸父 Web UI 已启动: http://{host}:{port}/")
    log.info(f"📱 手机浏览器打开 http://<手机IP>:{port}/ 即可使用")
    log.info(f"   确保手机和电脑在同一网络下")
    log.info(f"   Ctrl+C 停止服务器")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("服务器已停止")
        server.server_close()


if __name__ == "__main__":
    main()
