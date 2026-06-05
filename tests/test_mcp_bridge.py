"""测试 core/mcp_bridge.py — MCP (Model Context Protocol) 集成桥接。"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open, PropertyMock


class TestJsonRpcHelpers:
    """JSON-RPC 辅助函数测试。"""

    def test_make_request_no_params(self):
        """无参请求格式正确。"""
        from core.mcp_bridge import _make_request
        req = _make_request("ping", msg_id=1)
        data = json.loads(req.strip())
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["method"] == "ping"
        assert "params" not in data

    def test_make_request_with_params(self):
        """有参请求包含 params。"""
        from core.mcp_bridge import _make_request
        req = _make_request("tools/call", {"name": "test"}, msg_id=2)
        data = json.loads(req.strip())
        assert data["params"]["name"] == "test"

    def test_parse_response_ok(self):
        """成功结果解析。"""
        from core.mcp_bridge import _parse_response
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        result = _parse_response(raw)
        assert "tools" in result

    def test_parse_response_error(self):
        """错误响应抛异常。"""
        from core.mcp_bridge import _parse_response, _JsonRpcError
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}})
        with pytest.raises(_JsonRpcError) as exc:
            _parse_response(raw)
        assert exc.value.code == -32601

    def test_parse_response_error_no_code(self):
        """错误响应无 code 用默认 0。"""
        from core.mcp_bridge import _parse_response, _JsonRpcError
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "fail"}})
        with pytest.raises(_JsonRpcError) as exc:
            _parse_response(raw)
        assert exc.value.code == 0


class TestMCPServer:
    """MCPServer 测试（不依赖真实子进程）。"""

    def test_connected_property_false_when_no_process(self):
        """无进程时 connected 为 False。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        assert server.connected is False

    def test_connected_property_false_when_process_dead(self):
        """进程已退出时 connected 为 False。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = 0  # process exited
        assert server.connected is False

    def test_connected_true(self):
        """进程存活时 connected 为 True。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None  # still running
        assert server.connected is True

    def test_disconnect_no_process(self):
        """无进程时 disconnect 不报错。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server.disconnect()
        assert server._connected is False

    def test_disconnect_with_process(self):
        """有进程时 terminate 被调用。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        mock_proc = MagicMock()
        server._process = mock_proc
        server._connected = True
        server._available_tools = ["tool1"]
        server.disconnect()
        mock_proc.terminate.assert_called_once()
        assert server._process is None
        assert server._connected is False
        assert server._available_tools == []

    def test_disconnect_kill_on_timeout(self):
        """terminate 超时时 kill 被调用。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = Exception("timeout")
        server._process = mock_proc
        server._connected = True
        server.disconnect()
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    def test_list_tools_not_connected(self):
        """未连接时 list_tools 返回空列表。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        assert server.list_tools() == []

    def test_list_tools_connected(self):
        """已连接时返回可用工具。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        server._available_tools = [{"name": "test_tool", "description": "A test"}]
        assert len(server.list_tools()) == 1

    def test_call_tool_not_connected(self):
        """未连接时 call_tool 返回错误。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        result = server.call_tool("anything", {})
        assert result["success"] is False

    def test_call_tool_success(self):
        """成功调用工具。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        # Mock _send_and_receive
        server._send_and_receive = MagicMock(return_value=json.dumps({
            "jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": "hello world"}]
            }
        }))
        result = server.call_tool("my_tool", {"arg1": "val1"})
        assert result["success"] is True
        assert result["output"] == "hello world"

    def test_call_tool_error_response(self):
        """MCP 返回错误。"""
        from core.mcp_bridge import MCPServer, _JsonRpcError
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        server._send_and_receive = MagicMock(side_effect=_JsonRpcError(-32603, "Internal error"))
        result = server.call_tool("failing", {})
        assert result["success"] is False
        assert "Internal error" in result["output"]

    def test_call_tool_exception(self):
        """调用异常返回错误。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        server._send_and_receive = MagicMock(side_effect=RuntimeError("crashed"))
        result = server.call_tool("my_tool", {})
        assert result["success"] is False

    def test_restart_exceeds_max(self):
        """超出最大重启次数返回 False。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._restart_count = 3
        assert server.restart() is False

    def test_restart_success(self):
        """重启成功。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._restart_count = 0
        server.connect = MagicMock(return_value=True)
        assert server.restart() is True
        assert server._restart_count == 1

    def test_next_id_increments(self):
        """_next_id 递增。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        assert server._next_id() == 1
        assert server._next_id() == 2

    def test_send_and_receive_not_connected(self):
        """未连接时 _send_and_receive 报错。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        with pytest.raises(RuntimeError, match="未连接"):
            server._send_and_receive("ping")

    def test_send_and_receive_success_returns_stripped_line(self):
        """_send_and_receive 成功时返回 strip 后的行（覆盖 L105）。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        # Return a non-empty line with whitespace to verify .strip()
        server._process.stdout.readline.return_value = "  {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}  \n"
        result = server._send_and_receive("ping\n")
        assert result == "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}"

    def test_send_and_receive_broken_pipe(self):
        """管道断开时异常。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        server._process.stdin.write.side_effect = BrokenPipeError()
        with pytest.raises(RuntimeError, match="管道已断开"):
            server._send_and_receive("ping")
        assert server._connected is False

    def test_send_and_receive_empty_line(self):
        """空行响应报错。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        server._process.stdout.readline.return_value = ""
        with pytest.raises(RuntimeError, match="无响应"):
            server._send_and_receive("ping")

    def test_connect_already_connected(self):
        """已连接时 connect 直接返回 True。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None
        assert server.connect() is True

    def test_connect_failure(self):
        """连接失败返回 False。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        # disconnect calls self._process.terminate() then wait() — avoid that
        server.disconnect = MagicMock()
        with patch("subprocess.Popen", side_effect=FileNotFoundError("no such binary")):
            assert server.connect() is False

    def test_connect_success_initialization_flow(self):
        """connect() 成功时完整走过初始化流程（覆盖 L131-158）。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server.disconnect = MagicMock()  # prevent cleanup from doing real subprocess stuff

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process is alive

        # Simulate the initialization sequence across 2 calls to _send_and_receive:
        # 1st call: initialize request → returns initialize response
        # 2nd call: tools/list request (from _refresh_tools) → returns tool list
        init_response = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "test-server", "version": "1.0"}}
        })
        tools_response = json.dumps({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": [{"name": "tool_a"}, {"name": "tool_b"}]}
        })
        server._send_and_receive = MagicMock(side_effect=[init_response, tools_response])

        with patch("subprocess.Popen", return_value=mock_proc):
            result = server.connect()

        assert result is True
        assert server._connected is True
        assert server._restart_count == 0
        assert len(server._available_tools) == 2
        assert server._available_tools[0]["name"] == "tool_a"
        # Verify initialize and tools/list were both called
        assert server._send_and_receive.call_count == 2
        # Verify initialized notification was sent to stdin
        mock_proc.stdin.write.assert_any_call(
            '{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}\n'
        )


class TestMCPBridge:
    """MCPBridge 测试。"""

    def test_init_empty(self):
        """初始化无 server。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        assert bridge._servers == {}

    def test_load_config_no_file(self):
        """配置文件不存在时 load_config 报错。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        with pytest.raises(FileNotFoundError):
            bridge.load_config("/nonexistent/path.yaml")

    def test_load_config_no_mcp_servers(self):
        """配置中无 mcp_servers 字段。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        with patch("pathlib.Path.read_text", return_value="other: value"):
            bridge.load_config("/fake.yaml")
            assert bridge._servers == {}

    def test_load_config_disabled_server(self):
        """disabled server 被跳过。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        yaml_content = """
mcp_servers:
  disabled_one:
    enabled: false
    command: python
  active_one:
    command: echo
    args: ["hello"]
"""
        with patch("pathlib.Path.read_text", return_value=yaml_content):
            bridge.load_config("/fake.yaml")
            assert "disabled_one" not in bridge._servers
            assert "active_one" in bridge._servers

    def test_load_config_with_servers(self):
        """正常加载配置。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        yaml_content = """
mcp_servers:
  my_server:
    command: python
    args: ["-m", "my_mcp_server"]
    env:
      KEY: value
    timeout: 60
"""
        with patch("pathlib.Path.read_text", return_value=yaml_content):
            bridge.load_config("/fake.yaml")
            assert "my_server" in bridge._servers
            s = bridge._servers["my_server"]
            assert s.command == "python"

    def test_get_all_tools_empty(self):
        """无 server 时 get_all_tools 返回空。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        assert bridge.get_all_tools() == []

    def test_get_all_tools_not_connected(self):
        """未连接的 server 被跳过。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        mock_server = MagicMock()
        mock_server.connected = False
        bridge._servers["test"] = mock_server
        assert bridge.get_all_tools() == []

    def test_get_all_tools_with_connected(self):
        """连接的 server 返回工具列表。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        mock_server = MagicMock()
        mock_server.connected = True
        mock_server.list_tools.return_value = [
            {"name": "my_tool", "description": "Does something",
             "inputSchema": {"type": "object", "properties": {}}}
        ]
        bridge._servers["test"] = mock_server
        tools = bridge.get_all_tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "my_tool"

    def test_get_handler_unknown_tool(self):
        """未知工具名返回 None。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        assert bridge.get_handler("unknown") is None

    def test_get_handler_known_tool(self):
        """已知工具返回闭包处理函数。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        mock_server = MagicMock()
        mock_server.call_tool.return_value = {"success": True, "output": "ok"}
        bridge._tool_to_server["my_tool"] = "test"
        bridge._servers["test"] = mock_server
        handler = bridge.get_handler("my_tool")
        assert handler is not None
        result = handler({"arg": "val"})
        assert result["success"] is True

    def test_connect_all_empty(self):
        """空 server 列表返回空失败列表。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        assert bridge.connect_all() == []

    def test_connect_all_with_success_failure(self):
        """连接成功和失败的 server。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        ok_server = MagicMock()
        ok_server.connect.return_value = True
        ok_server.list_tools.return_value = [{"name": "ok_tool"}]
        fail_server = MagicMock()
        fail_server.connect.return_value = False
        bridge._servers["ok"] = ok_server
        bridge._servers["fail"] = fail_server
        failed = bridge.connect_all()
        assert "fail" in failed
        assert "ok" not in failed
        assert bridge._tool_to_server.get("ok_tool") == "ok"

    def test_get_server_status_empty(self):
        """空 server 返回空列表。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        assert bridge.get_server_status() == []

    def test_get_server_status(self):
        """返回 server 状态。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        mock_server = MagicMock()
        mock_server.connected = True
        mock_server.list_tools.return_value = [{"name": "t1"}]
        bridge._servers["test"] = mock_server
        status = bridge.get_server_status()
        assert len(status) == 1
        assert status[0]["name"] == "test"
        assert status[0]["connected"] is True

    def test_disconnect_all(self):
        """断开所有连接。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        mock_server = MagicMock()
        bridge._servers["test"] = mock_server
        bridge._tool_to_server["tool1"] = "test"
        bridge.disconnect_all()
        mock_server.disconnect.assert_called_once()
        assert bridge._tool_to_server == {}

    def test_get_registry_items_skips_disconnected(self):
        """_get_registry_items 跳过未连接的 server（覆盖 L334）。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()

        connected_server = MagicMock()
        connected_server.connected = True
        connected_server.list_tools.return_value = [{"name": "conn_tool", "description": "ok"}]

        disconnected_server = MagicMock()
        disconnected_server.connected = False  # should be skipped at L334

        bridge._servers["conn"] = connected_server
        bridge._servers["disc"] = disconnected_server

        items = bridge._get_registry_items()

        # disconnected server's tools must not appear
        names = [name for name, _ in items]
        assert "conn_tool" in names
        assert len(items) == 1
        # disconnected_server.list_tools should never have been called
        disconnected_server.list_tools.assert_not_called()

    def test_register_to_registry(self):
        """注册工具到 registry。"""
        from core.mcp_bridge import MCPBridge
        bridge = MCPBridge()
        mock_server = MagicMock()
        mock_server.connected = True
        mock_server.list_tools.return_value = [
            {"name": "my_tool", "description": "test", "inputSchema": {"type": "object"}}
        ]
        bridge._servers["test"] = mock_server
        bridge._tool_to_server["my_tool"] = "test"
        mock_registry = MagicMock()
        count = bridge.register_to_registry(mock_registry)
        assert count == 1
        mock_registry.register.assert_called_once()

    def test_refresh_tools(self):
        """_refresh_tools 更新工具列表。"""
        from core.mcp_bridge import MCPServer
        server = MCPServer("test", "echo", [])
        server._connected = True
        server._process = MagicMock()
        server._process.poll.return_value = None

        expected_response = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {"tools": [{"name": "t1"}, {"name": "t2"}]}
        })
        server._send_and_receive = MagicMock(return_value=expected_response)
        server._refresh_tools()
        assert len(server._available_tools) == 2
        assert server._available_tools[0]["name"] == "t1"
