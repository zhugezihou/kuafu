#!/usr/bin/env python3
"""
夸父 (Kuafu) PC 版 Web UI 服务器
==================================
FastAPI + WebSocket 实时推送审批、工具执行计时器。

用法:
    python mobile/web_server.py [--port 8080]

环境变量:
    KUAFFU_BACKEND   — 模型后端 (local/cloud)
    KUAFFU_HOST      — 监听地址 (默认 0.0.0.0)
    KUAFFU_PORT      — 监听端口 (默认 8080)

API:
    GET  /              → SPA HTML
    POST /api/chat      → 发送消息
    GET  /api/status    → Agent 状态
    POST /api/reset     → 重置对话
    POST /api/approve   → 批准审批 (req_id)
    POST /api/reject    → 拒绝审批 (req_id)
    GET  /api/approvals/pending → 待审批列表
    WS   /ws            → 实时事件推送
"""

import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── 项目根路径 ──
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# ── 导入夸父核心 ──
from core.main import KuafuAgent
from core.agent_loop import AgentLoop
from core.llm import LLMClient
import core.approval as kuafu_approval

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

_agent: Optional[KuafuAgent] = None
_agent_lock = threading.Lock()
_last_loop: Optional[AgentLoop] = None  # 最后一次 converse 创建的 AgentLoop 引用

# WebSocket 连接管理
_ws_connections: list[WebSocket] = []
_ws_lock = asyncio.Lock()

app = FastAPI(title="夸父 Web UI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _ws_broadcast(data: dict):
    """向所有已连接的 WebSocket 广播消息。"""
    msg = json.dumps(data, ensure_ascii=False)
    dead: list[WebSocket] = []
    async with _ws_lock:
        for ws in _ws_connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _ws_connections.remove(ws)


def _ws_broadcast_sync(event_type: str, data: dict):
    """线程安全同步版广播（由 agent 后台线程调用）。"""
    try:
        coro = _ws_broadcast({"type": event_type, **data})
        if hasattr(app, '_ws_loop') and app._ws_loop:
            asyncio.run_coroutine_threadsafe(coro, app._ws_loop)
    except Exception as e:
        log.warning(f"WS 广播失败: {e}")


def _inject_loop_callbacks(loop: AgentLoop):
    """注入 AgentLoop 的实时事件回调。"""
    if getattr(loop, '_ws_injected', False):
        return  # 避免重复注入

    loop.on_llm_start = lambda turn: _ws_broadcast_sync("llm_start", {"turn": turn, "ts": __import__('time').time()})
    loop.on_llm_end = lambda turn, status: _ws_broadcast_sync("llm_end", {"turn": turn, "status": status, "ts": __import__('time').time()})
    loop.on_tool_start = lambda name, args, ts: _ws_broadcast_sync("tool_start", {"tool": name, "args": args, "ts": ts})
    loop.on_tool_end = lambda name, args, elapsed, status: _ws_broadcast_sync("tool_end", {"tool": name, "args": args, "elapsed": elapsed, "status": status, "ts": __import__('time').time()})

    # 审批回调包装
    original_approval = loop.on_approval_request
    def _approval_cb(tool_name, args, req_id):
        _ws_broadcast_sync("approval_request", {
            "tool": tool_name, "args": args, "req_id": req_id,
            "ts": __import__('time').time(), "risk": "medium",
        })
        if original_approval:
            original_approval(tool_name, args, req_id)
    loop.on_approval_request = _approval_cb
    loop._ws_injected = True


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
# API 路由
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    """提供 SPA 聊天界面。"""
    html_path = ROOT_DIR / "mobile" / "static" / "chat.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>夸父 Web UI</h1><p>chat.html 未找到</p>")


def _with_ws_callbacks(agent: KuafuAgent) -> KuafuAgent:
    """Patch agent.converse 以捕获 AgentLoop 并注入 WS 回调。

    因为 AgentLoop 是在 agent.converse() 内部创建的局部变量，
    我们通过包装 converse 方法来拦截它。
    """
    original_converse = agent.converse

    def _patched_converse(text: str) -> dict:
        # 标记：让 main.py 在创建 loop 后调用我们的回调
        # 但我们不能改 main.py，所以换个方式：在 converse 之前 mock
        result = original_converse(text)

        # converse 完成后，尝试从 agent 内部获取最后一次 loop 引用
        # 实际上无法直接获取，所以我们用另一种方式：
        # 每次 converse 结束后，搜索 AgentLoop 实例（通过已注册的回调痕迹）
        global _last_loop

        # 真正的方案：让 main.py 把 loop 暴露出来
        # 但目前先这样——回调会在首次工具调用前注入
        return result

    agent.converse = _patched_converse  # type: ignore
    return agent


@app.post("/api/chat")
async def api_chat(request: Request):
    """发送消息给夸父。"""
    body = await request.json()
    text = (body.get("text") or body.get("message") or "").strip()
    if not text:
        return JSONResponse({"success": False, "message": "请输入消息"}, status_code=400)

    agent = get_agent()

    log.info(f"📝 收到: '{text[:60]}...'")
    try:
        # 在后台线程运行（converse 是同步阻塞的）
        import queue
        q: queue.Queue = queue.Queue()

        def _run():
            try:
                # 注册 WS 注入回调（在 main.py 创建 AgentLoop 后自动调用）
                agent._ws_inject_cb = _inject_loop_callbacks
                result = agent.converse(text)
                q.put(result)
            except Exception as e:
                q.put({"success": False, "errors": [str(e)], "result": str(e),
                       "turns": 0, "duration": 0})

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        result = q.get()

        # 尝试从反向渠道获取 AgentLoop 引用
        # 方法：在 main.py 中 loop 赋值后，通过某种方式暴露
        # 但目前先这样——后续完善

        log.info(f"✅ 完成: duration={result.get('duration', 0)}s, turns={result.get('turns', 0)}")
        return JSONResponse({
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
        return JSONResponse({
            "success": False,
            "message": f"执行出错: {type(e).__name__}: {e}",
            "errors": [str(e)],
            "duration": 0,
        }, status_code=500)


@app.get("/api/status")
async def api_status():
    """Agent 状态。"""
    try:
        agent = get_agent()
        status = agent.get_status()
        return JSONResponse({
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
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/reset")
async def api_reset():
    """重置对话。"""
    try:
        agent = get_agent()
        agent.reset_conversation()
        return JSONResponse({"success": True, "message": "对话已重置"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/approve")
async def api_approve(request: Request):
    """批准审批请求。"""
    body = await request.json()
    req_id = body.get("req_id", "")
    ok = kuafu_approval.ApprovalManager.approve(req_id)
    if ok:
        await _ws_broadcast({"type": "approval_result", "req_id": req_id, "approved": True})
        return JSONResponse({"success": True, "message": "已批准"})
    return JSONResponse({"success": False, "message": "审批失败或已处理"}, status_code=400)


@app.post("/api/reject")
async def api_reject(request: Request):
    """拒绝审批请求。"""
    body = await request.json()
    req_id = body.get("req_id", "")
    ok = kuafu_approval.ApprovalManager.reject(req_id)
    if ok:
        await _ws_broadcast({"type": "approval_result", "req_id": req_id, "approved": False})
        return JSONResponse({"success": True, "message": "已拒绝"})
    return JSONResponse({"success": False, "message": "拒绝失败或已处理"}, status_code=400)


@app.get("/api/approvals/pending")
async def api_pending_approvals():
    """获取待审批列表。"""
    pending = kuafu_approval.ApprovalManager.list_pending()
    return JSONResponse({
        "success": True,
        "approvals": [
            {
                "id": r.id,
                "title": r.title,
                "detail": r.detail[:200],
                "risk": r.risk,
                "tool": r.tool,
                "created_at": r.created_at,
            }
            for r in pending
        ]
    })


@app.post("/api/model")
async def api_model(request: Request):
    """切换模型。"""
    body = await request.json()
    target = body.get("target", "").strip()
    if not target:
        return JSONResponse({"success": False, "message": "缺少 target 参数"}, status_code=400)
    try:
        agent = get_agent()
        msg = agent.switch_model(target)
        return JSONResponse({"success": True, "message": msg})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════
# WebSocket 实时推送
# ═══════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    async with _ws_lock:
        _ws_connections.append(ws)
    log.info(f"🔌 WebSocket 客户端已连接 ({len(_ws_connections)} 个)")

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action", "")
                if action == "approve":
                    req_id = msg.get("req_id", "")
                    kuafu_approval.ApprovalManager.approve(req_id)
                    await _ws_broadcast({"type": "approval_result", "req_id": req_id, "approved": True})
                elif action == "reject":
                    req_id = msg.get("req_id", "")
                    kuafu_approval.ApprovalManager.reject(req_id)
                    await _ws_broadcast({"type": "approval_result", "req_id": req_id, "approved": False})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            if ws in _ws_connections:
                _ws_connections.remove(ws)
        log.info(f"🔌 WebSocket 客户端断开 ({len(_ws_connections)} 个)")


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    """存储事件循环引用，供 agent 线程推送消息。"""
    app._ws_loop = asyncio.get_running_loop()
    log.info("🌐 WebSocket 事件循环就绪，Web UI 启动中...")

    # 预热 Agent
    log.info("预热夸父 Agent...")
    try:
        agent = get_agent()
        log.info(f"✅ Agent 就绪: backend={agent.llm.backend}, model={agent.llm.model}")
    except Exception as e:
        log.warning(f"Agent 预热失败（可在首次请求时初始化）: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="夸父 PC 版 Web UI 服务器")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (默认 8080)")
    parser.add_argument("--host", type=str, default=None, help="监听地址 (默认 0.0.0.0)")
    args, _ = parser.parse_known_args()

    host = args.host or os.environ.get("KUAFFU_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("KUAFFU_PORT", "8080"))

    log.info(f"🌐 夸父 PC Web UI 启动: http://{host}:{port}/")
    log.info(f"   WebSocket: ws://{host}:{port}/ws")
    log.info(f"   浏览器打开 http://localhost:{port}/ 即可使用")
    log.info(f"   Ctrl+C 停止服务器")

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
