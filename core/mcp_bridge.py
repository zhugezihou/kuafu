"""
mcp_bridge.py — MCP (Model Context Protocol) 集成桥接

设计原则：
- 零新增 Python 依赖（仅标准库 subprocess + json）
- 基于 JSON-RPC 2.0 over stdio 传输
- 支持动态发现、连接、调用 MCP Server 工具
- 外部插件，不影响 core/ 安全规则

协议参考：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
"""

import json
import os
import subprocess
import threading
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kuafu.mcp")

# ── 常量 ──────────────────────────────────────────────────────────────

MCP_CAPABILITIES = {
    "experimental": {},
    "roots": {"listChanged": False},
    "sampling": {},
}

CLIENT_INFO = {
    "name": "kuafu-mcp-bridge",
    "version": "0.1.0",
}

DEFAULT_TIMEOUT = 30  # 单次工具调用超时（秒）
MAX_RESTARTS = 3      # 进程崩溃最大重启次数


# ── 内部 JSON-RPC 助手 ──────────────────────────────────────────────

class _JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{code}] {message}")


def _make_request(method: str, params: dict = None, msg_id: int = 1) -> str:
    """构造 JSON-RPC 2.0 请求。"""
    req = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        req["params"] = params
    return json.dumps(req) + "\n"


def _parse_response(raw: str) -> dict:
    """解析 JSON-RPC 2.0 响应。"""
    data = json.loads(raw)
    if "error" in data:
        err = data["error"]
        raise _JsonRpcError(err.get("code", 0), err.get("message", "Unknown error"))
    return data.get("result", {})


# ── MCP Server 管理 ─────────────────────────────────────────────────

class MCPServer:
    """管理单个 MCP Server 进程的生命周期和通信。"""

    def __init__(self, name: str, command: str, args: list[str],
                 env: dict = None, timeout: int = DEFAULT_TIMEOUT):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.timeout = timeout
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._restart_count = 0
        self._available_tools: list[dict] = []
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._process is not None and self._process.poll() is None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send_and_receive(self, request: str) -> str:
        """发送一行 JSON-RPC 请求，读取一行响应。"""
        if not self.connected:
            raise RuntimeError(f"MCP Server '{self.name}' 未连接")
        try:
            self._process.stdin.write(request)
            self._process.stdin.flush()
            line = self._process.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP Server '{self.name}' 无响应（进程可能已退出）")
            return line.strip()
        except BrokenPipeError:
            self._connected = False
            raise RuntimeError(f"MCP Server '{self.name}' 管道已断开")

    def connect(self) -> bool:
        """启动 MCP Server 进程并完成初始化握手。"""
        with self._lock:
            if self.connected:
                return True

            try:
                merged_env = os.environ.copy()
                merged_env.update(self.env)

                self._process = subprocess.Popen(
                    [self.command] + self.args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=merged_env,
                    text=True,
                    bufsize=1,  # 行缓冲
                )

                # 1. 初始化：发送 initialize 请求
                init_req = _make_request("initialize", {
                    "protocolVersion": "2025-11-25",
                    "capabilities": MCP_CAPABILITIES,
                    "clientInfo": CLIENT_INFO,
                }, msg_id=self._next_id())

                init_raw = self._send_and_receive(init_req)
                init_result = _parse_response(init_raw)
                logger.info(f"MCP Server '{self.name}' 初始化成功: "
                            f"server={init_result.get('serverInfo', {})}")

                # 2. 发送 initialized 通知
                notif = json.dumps({
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }) + "\n"
                self._process.stdin.write(notif)
                self._process.stdin.flush()

                # 3. 获取工具列表
                self._refresh_tools()

                self._connected = True
                self._restart_count = 0
                logger.info(f"MCP Server '{self.name}' 已连接，"
                            f"发现 {len(self._available_tools)} 个工具")
                return True

            except Exception as e:
                self._connected = False
                logger.error(f"MCP Server '{self.name}' 连接失败: {e}")
                self.disconnect()
                return False

    def _refresh_tools(self):
        """刷新工具列表（调用 tools/list）。"""
        list_req = _make_request("tools/list", msg_id=self._next_id())
        raw = self._send_and_receive(list_req)
        result = _parse_response(raw)
        self._available_tools = result.get("tools", [])

    def list_tools(self) -> list[dict]:
        """返回当前可用的工具列表（MCP 格式）。"""
        if not self.connected:
            return []
        return self._available_tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        """调用一个 MCP 工具。

        返回夸父 ToolRegistry 兼容格式：{"success": bool, "output": str, ...}
        """
        if not self.connected:
            return {"success": False, "output": f"MCP Server '{self.name}' 未连接"}

        call_req = _make_request("tools/call", {
            "name": name,
            "arguments": arguments,
        }, msg_id=self._next_id())

        try:
            raw = self._send_and_receive(call_req)
            result = _parse_response(raw)
            # 提取文本内容
            texts = []
            for content in result.get("content", []):
                if content.get("type") == "text":
                    texts.append(content["text"])
            output = "\n".join(texts)
            is_error = result.get("isError", False)
            return {
                "success": not is_error,
                "output": output or "(无文本输出)",
                "raw_result": result,
            }
        except _JsonRpcError as e:
            return {"success": False, "output": f"MCP 错误: {e.message}"}
        except Exception as e:
            return {"success": False, "output": f"MCP 调用异常: {e}"}

    def disconnect(self):
        """断开连接，关闭子进程。"""
        with self._lock:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()
                self._process = None
            self._connected = False
            self._available_tools = []

    def restart(self) -> bool:
        """重启 MCP Server 连接（最多 MAX_RESTARTS 次）。"""
        if self._restart_count >= MAX_RESTARTS:
            logger.error(f"MCP Server '{self.name}' 已达最大重启次数")
            return False
        self._restart_count += 1
        self.disconnect()
        time.sleep(1)
        return self.connect()


class MCPBridge:
    """管理多个 MCP Server，聚合工具列表，注册到夸父 ToolRegistry。"""

    def __init__(self, config_path: str = None):
        self._servers: dict[str, MCPServer] = {}
        self._config_path = config_path or ""
        self._tool_to_server: dict[str, str] = {}  # tool_name -> server_name

    def load_config(self, path: str):
        """从 YAML 配置文件加载 MCP Server 配置。"""
        import yaml
        self._config_path = path
        raw = Path(path).read_text(encoding="utf-8")
        config = yaml.safe_load(raw)
        servers_cfg = config.get("mcp_servers") if config else None
        if not servers_cfg:
            servers_cfg = {}

        for name, cfg in servers_cfg.items():
            if not cfg.get("enabled", True):
                continue
            server = MCPServer(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
                timeout=cfg.get("timeout", DEFAULT_TIMEOUT),
            )
            self._servers[name] = server

        logger.info(f"MCP 配置已加载: {len(self._servers)} 个 Server")

    def connect_all(self) -> list[str]:
        """连接所有配置的 MCP Server，返回失败列表。"""
        failed = []
        for name, server in self._servers.items():
            ok = server.connect()
            if ok:
                for tool in server.list_tools():
                    self._tool_to_server[tool["name"]] = name
            else:
                failed.append(name)
        return failed

    def get_all_tools(self) -> list[dict]:
        """聚合所有 MCP Server 的工具列表，转换为夸父兼容的 schema。

        返回格式：[{"name": str, "function": {"name": str, "description": str,
                      "parameters": {...}}}, ...]
        """
        schemas = []
        for name, server in self._servers.items():
            if not server.connected:
                continue
            for tool in server.list_tools():
                mcp_schema = {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", tool.get("title", "")),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
                schemas.append(mcp_schema)
        return schemas

    def get_handler(self, tool_name: str):
        """为指定工具名生成处理函数（闭包）。"""
        server_name = self._tool_to_server.get(tool_name)
        if not server_name or server_name not in self._servers:
            return None
        server = self._servers[server_name]

        def handler(args: dict) -> dict:
            return server.call_tool(tool_name, args)

        return handler

    def register_to_registry(self, registry):
        """将 MCP 工具注册到夸父 ToolRegistry。

        Args:
            registry: ToolRegistry 实例
        """
        count = 0
        for name, schema in self._get_registry_items():
            handler = self.get_handler(name)
            if handler:
                registry.register(name, schema, handler)
                count += 1
        logger.info(f"MCP 已注册 {count} 个工具到 ToolRegistry")
        return count

    def _get_registry_items(self) -> list[tuple[str, dict]]:
        """返回 (工具名, schema) 列表。"""
        items = []
        for name, server in self._servers.items():
            if not server.connected:
                continue
            for tool in server.list_tools():
                schema = {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object"}),
                    },
                }
                items.append((tool["name"], schema))
        return items

    def disconnect_all(self):
        """断开所有 MCP Server 连接。"""
        for server in self._servers.values():
            server.disconnect()
        self._tool_to_server.clear()

    def get_server_status(self) -> list[dict]:
        """返回所有 Server 的状态（用于诊断）。"""
        return [
            {"name": name, "connected": s.connected, "tools": len(s.list_tools())}
            for name, s in self._servers.items()
        ]
