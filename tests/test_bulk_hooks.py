"""
Core test for HookRegistry — init, register, unregister, get, trigger, _execute_shell, _execute_webhook, template.
"""
import json
import os
import time
from unittest.mock import patch, MagicMock, call, ANY

import pytest


class TestHooks:
    """Complete coverage for HookRegistry and hook trigger system."""

    # ---- HookRegistry.init ----
    def test_init_initializes(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = False
            HookRegistry.init()
            assert HookRegistry._initialized is True
            assert HookRegistry._handlers == {}

    def test_init_already_initialized(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {"on_agent_start": []}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            HookRegistry.init()
            # Should not reload
            assert HookRegistry._handlers == {"on_agent_start": []}

    def test_init_loads_from_file(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        config_data = {
            "on_agent_start": [
                {"id": "h1", "event": "on_agent_start", "type": "shell",
                 "config": {"command": "echo hi"}, "enabled": True, "async_": True,
                 "priority": 0, "created_at": 100, "description": "", "max_retries": 0, "timeout": 10}
            ]
        }
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = True
            MockPath.read_text.return_value = json.dumps(config_data)
            HookRegistry.init()
            assert HookRegistry._initialized is True
            assert "on_agent_start" in HookRegistry._handlers
            assert len(HookRegistry._handlers["on_agent_start"]) == 1
            assert HookRegistry._handlers["on_agent_start"][0].id == "h1"

    def test_init_corrupted_file(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = True
            MockPath.read_text.return_value = "not json!!!"
            HookRegistry.init()
            assert HookRegistry._initialized is True

    def test_init_unknown_event_skipped(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        config_data = {
            "unknown_event": [
                {"id": "h_bad", "event": "unknown_event", "type": "shell",
                 "config": {}, "enabled": True, "async_": True,
                 "priority": 0, "created_at": 100, "description": "", "max_retries": 0, "timeout": 10}
            ]
        }
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = True
            MockPath.read_text.return_value = json.dumps(config_data)
            HookRegistry.init()
            assert "unknown_event" not in HookRegistry._handlers

    # ---- HookRegistry.register ----
    def test_register_valid_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = HookRegistry.register("on_agent_start", "shell", {"command": "echo test"},
                                         description="test hook", priority=5)
            assert hid.startswith("hook_")
            assert "on_agent_start" in HookRegistry._handlers
            assert len(HookRegistry._handlers["on_agent_start"]) == 1
            handler = HookRegistry._handlers["on_agent_start"][0]
            assert handler.id == hid
            assert handler.type == "shell"
            assert handler.config["command"] == "echo test"
            assert handler.description == "test hook"
            assert handler.priority == 5
            assert handler.enabled is True

    def test_register_unknown_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with pytest.raises(ValueError, match="未知事件"):
            HookRegistry.register("nonexistent_event", "shell", {"command": ""})

    def test_register_maintains_priority_order(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            HookRegistry.register("on_agent_start", "shell", {}, priority=1)
            HookRegistry.register("on_agent_start", "shell", {}, priority=10)
            handlers = HookRegistry._handlers["on_agent_start"]
            assert handlers[0].priority == 10
            assert handlers[1].priority == 1

    # ---- HookRegistry.unregister ----
    def test_unregister_existing(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = HookRegistry.register("on_agent_start", "shell", {})
            result = HookRegistry.unregister(hid)
            assert result is True
            assert HookRegistry._handlers["on_agent_start"] == []

    def test_unregister_nonexistent(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        result = HookRegistry.unregister("nonexistent")
        assert result is False

    def test_unregister_wrong_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {"on_agent_start": []}
        result = HookRegistry.unregister("hook_nonexistent")
        assert result is False

    # ---- HookRegistry.get_handlers ----
    def test_get_handlers_returns_enabled(self):
        from core.hooks import HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="h1", event="on_agent_start", type="shell", config={}, enabled=True),
                HookHandler(id="h2", event="on_agent_start", type="shell", config={}, enabled=False),
            ]
        }
        handlers = HookRegistry.get_handlers("on_agent_start")
        assert len(handlers) == 1
        assert handlers[0].id == "h1"

    def test_get_handlers_no_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        handlers = HookRegistry.get_handlers("on_agent_start")
        assert handlers == []

    def test_get_handlers_calls_init(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'init') as mock_init:
            HookRegistry.get_handlers("on_agent_start")
            mock_init.assert_called_once()

    # ---- save ----
    def test_save_writes_config(self):
        from core.hooks import HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="h1", event="on_agent_start", type="shell",
                            config={"command": "echo hi"})
            ]
        }
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.parent.mkdir = MagicMock()
            MockPath.write_text = MagicMock()
            HookRegistry.save()
            MockPath.write_text.assert_called_once()
            written = json.loads(MockPath.write_text.call_args[0][0])
            assert "on_agent_start" in written
            assert written["on_agent_start"][0]["id"] == "h1"

    # ---- _render_template ----
    def test_render_template_basic(self):
        from core.hooks import _render_template
        result = _render_template("Hello {{name}}", {"name": "World"})
        assert result == "Hello World"

    def test_render_template_dict_value(self):
        from core.hooks import _render_template
        result = _render_template("Data: {{args}}", {"args": {"key": "val"}})
        assert "Data:" in result
        assert '"key"' in result

    def test_render_template_list_value(self):
        from core.hooks import _render_template
        result = _render_template("Items: {{items}}", {"items": [1, 2, 3]})
        assert "Items:" in result

    def test_render_template_missing_key(self):
        from core.hooks import _render_template
        result = _render_template("{{unknown}}", {})
        assert result == "{{unknown}}"

    def test_render_template_no_vars(self):
        from core.hooks import _render_template
        result = _render_template("plain text", {})
        assert result == "plain text"

    # ---- _render_config ----
    def test_render_config_string_values(self):
        from core.hooks import _render_config
        config = {"command": "echo {{name}}", "url": "https://{{host}}/api"}
        context = {"name": "test", "host": "example.com"}
        result = _render_config(config, context)
        assert result["command"] == "echo test"
        assert result["url"] == "https://example.com/api"

    def test_render_config_nested_dict(self):
        from core.hooks import _render_config
        config = {"headers": {"Authorization": "Bearer {{token}}"}}
        context = {"token": "abc123"}
        result = _render_config(config, context)
        assert result["headers"]["Authorization"] == "Bearer abc123"

    def test_render_config_list_items(self):
        from core.hooks import _render_config
        config = {"urls": ["https://{{host}}/1", "https://{{host}}/2"]}
        context = {"host": "api.test"}
        result = _render_config(config, context)
        assert result["urls"] == ["https://api.test/1", "https://api.test/2"]

    def test_render_config_non_string_values(self):
        from core.hooks import _render_config
        config = {"count": 42, "enabled": True}
        result = _render_config(config, {})
        assert result["count"] == 42
        assert result["enabled"] is True

    # ---- _execute_shell ----
    def test_execute_shell_success(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h1", event="on_agent_start", type="shell",
                              config={"command": "echo hello"}, timeout=5)
        result = _execute_shell(handler, {})
        assert result.success is True
        assert "hello" in result.output
        assert result.type == "shell"
        assert result.handler_id == "h1"

    def test_execute_shell_with_template(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h2", event="on_tool_before", type="shell",
                              config={"command": "echo Tool: {{tool}}"}, timeout=5)
        result = _execute_shell(handler, {"tool": "terminal"})
        assert result.success is True
        assert "Tool: terminal" in result.output

    def test_execute_shell_failure(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h3", event="on_agent_start", type="shell",
                              config={"command": "exit 1"}, timeout=5)
        result = _execute_shell(handler, {})
        assert result.success is False
        assert result.error is not None

    def test_execute_shell_timeout(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h4", event="on_agent_start", type="shell",
                              config={"command": "sleep 100"}, timeout=1)
        result = _execute_shell(handler, {})
        assert result.success is False
        assert "超时" in (result.error or "")

    def test_execute_shell_stderr(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h5", event="on_agent_start", type="shell",
                              config={"command": "echo ok && echo err >&2"}, timeout=5)
        result = _execute_shell(handler, {})
        assert result.success is True
        assert "STDERR:" in result.output

    def test_execute_shell_exception(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h6", event="on_agent_start", type="shell",
                              config={"command": "invalid_command_xyz_123"}, timeout=5)
        # Should catch exception and return error
        # On Linux, shell will return 127 for invalid command, but shell=True should work
        result = _execute_shell(handler, {})
        assert result.success is False

    # ---- _execute_webhook ----
    def test_execute_webhook_no_url(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw1", event="on_agent_start", type="webhook",
                              config={}, timeout=5)
        result = _execute_webhook(handler, {})
        assert result.success is False
        assert "缺少 url" in (result.error or "")

    def test_execute_webhook_success(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw2", event="on_agent_start", type="webhook",
                              config={"url": "https://example.com/hook", "method": "POST",
                                      "headers": {"X-Test": "1"}, "body": "data"}, timeout=5)
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"ok": true}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = _execute_webhook(handler, {})
            assert result.success is True
            assert '{"ok": true}' in result.output

    def test_execute_webhook_http_error(self):
        from core.hooks import _execute_webhook, HookHandler
        from urllib.error import HTTPError
        handler = HookHandler(id="hw3", event="on_agent_start", type="webhook",
                              config={"url": "https://example.com/hook"}, timeout=5)
        with patch('urllib.request.urlopen', side_effect=HTTPError(
                "url", 500, "Internal Error", {}, None)):
            result = _execute_webhook(handler, {})
            assert result.success is False
            assert "HTTP" in (result.error or "")

    def test_execute_webhook_network_error(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw4", event="on_agent_start", type="webhook",
                              config={"url": "https://example.com/hook"}, timeout=5)
        with patch('urllib.request.urlopen', side_effect=Exception("connection refused")):
            result = _execute_webhook(handler, {})
            assert result.success is False
            assert "connection refused" in (result.error or "")

    def test_execute_webhook_with_template_in_config(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw5", event="on_agent_start", type="webhook",
                              config={"url": "https://{{host}}/hook", "method": "POST",
                                      "body": '{"tool": "{{tool}}"}'}, timeout=5)
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'ok'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = _execute_webhook(handler, {"host": "api.test", "tool": "terminal"})
            assert result.success is True

    # ---- trigger ----
    def test_trigger_unknown_event(self):
        from core.hooks import trigger
        with patch('core.hooks.HookRegistry.get_handlers', return_value=[]):
            results = trigger("nonexistent")
            assert results == []

    def test_trigger_no_handlers(self):
        from core.hooks import trigger
        with patch('core.hooks.logger') as mock_log:
            with patch('core.hooks.HookRegistry.get_handlers', return_value=[]):
                results = trigger("on_agent_start")
                assert results == []

    def test_trigger_shell_handler(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr1", event="on_agent_start", type="shell",
                            config={"command": "echo triggered"}, enabled=True,
                            async_=False, priority=0, timeout=5)
            ]
        }
        results = trigger("on_agent_start", {"test": "val"})
        assert len(results) == 1
        assert results[0].success is True
        assert "triggered" in results[0].output

    def test_trigger_unknown_executor_type(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr2", event="on_agent_start", type="unknown_type",
                            config={}, enabled=True, async_=False)
            ]
        }
        results = trigger("on_agent_start", {})
        assert len(results) == 1
        assert results[0].success is False
        assert "未知执行类型" in (results[0].error or "")

    def test_trigger_synchronous_blocked(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_permission_check": [
                HookHandler(id="tr3", event="on_permission_check", type="shell",
                            config={"command": "exit 1", "block_on_failure": True},
                            enabled=True, async_=False, timeout=5),
                HookHandler(id="tr4", event="on_permission_check", type="shell",
                            config={"command": "echo should not run"},
                            enabled=True, async_=False, timeout=5),
            ]
        }
        results = trigger("on_permission_check", {}, synchronous=True)
        assert len(results) == 2
        assert results[0].success is False
        assert results[0].blocked is True
        assert results[1].success is False
        assert "上游处理器阻止" in (results[1].error or "")

    def test_trigger_with_retry(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr5", event="on_agent_start", type="shell",
                            config={"command": "echo ok"}, enabled=True,
                            async_=False, max_retries=2, timeout=5)
            ]
        }
        results = trigger("on_agent_start", {})
        assert len(results) == 1
        assert results[0].success is True

    def test_trigger_async_handler(self):
        """Async handlers (async_=True) in async mode should be skipped from sync result logging."""
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr6", event="on_agent_start", type="shell",
                            config={"command": "echo async"}, enabled=True,
                            async_=True, timeout=5)
            ]
        }
        # async_ handlers should still execute in sync trigger
        # The async_ flag just determines logging behavior
        results = trigger("on_agent_start", {})
        assert len(results) == 1
        assert results[0].success is True

    def test_trigger_with_context(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_tool_before": [
                HookHandler(id="tr7", event="on_tool_before", type="shell",
                            config={"command": "echo {{tool}}"}, enabled=True,
                            async_=False, timeout=5)
            ]
        }
        results = trigger("on_tool_before", {"tool": "read_file"})
        assert len(results) == 1
        assert "read_file" in results[0].output

    # ---- trigger_async / trigger_sync ----
    def test_trigger_async_starts_thread(self):
        from core.hooks import trigger_async
        with patch('threading.Thread') as mock_thread:
            trigger_async("on_agent_start", {"test": True})
            mock_thread.assert_called_once()
            mock_thread.return_value.start.assert_called_once()

    def test_trigger_sync_delegates(self):
        from core.hooks import trigger_sync
        with patch('core.hooks.trigger') as mock_trigger:
            mock_trigger.return_value = []
            results = trigger_sync("on_agent_start", {"test": True})
            mock_trigger.assert_called_with("on_agent_start", {"test": True}, synchronous=True)
            assert results == []

    # ---- Convenience registration functions ----
    def test_on_tool_before_shell(self):
        from core.hooks import on_tool_before_shell, HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = on_tool_before_shell("echo pre", description="shell pre-check", priority=10)
            assert hid is not None
            handlers = HookRegistry._handlers["on_tool_before"]
            assert handlers[0].type == "shell"
            assert handlers[0].config["command"] == "echo pre"

    def test_on_tool_before_llm(self):
        from core.hooks import on_tool_before_llm, HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = on_tool_before_llm("analyze {{tool}}", model="gpt-4",
                                      description="llm analysis", block_on_failure=True)
            assert hid is not None
            handlers = HookRegistry._handlers["on_tool_before"]
            assert handlers[0].type == "llm"
            assert handlers[0].config["model"] == "gpt-4"
            assert handlers[0].config["block_on_failure"] is True

    def test_on_approval_notify_webhook(self):
        from core.hooks import on_approval_notify_webhook, HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = on_approval_notify_webhook("https://example.com/notify")
            assert hid is not None
            handlers = HookRegistry._handlers["on_approval_result"]
            assert handlers[0].type == "webhook"
            assert handlers[0].config["url"] == "https://example.com/notify"

    # ---- init_hooks ----
    def test_init_hooks(self):
        from core.hooks import init_hooks, HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'init') as mock_init:
            init_hooks()
            mock_init.assert_called_once()
