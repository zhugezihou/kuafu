"""
tests/test_tool_orchestrator.py — ToolOrchestrator 测试套件

测试四阶段编排：Approval → Safety → Execute → Retry
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch

from core.tool_orchestrator import (
    ToolOrchestrator, ToolOrchestratorConfig, ToolExecutionRequest,
    ApprovalDecision, SafetyDecision, PhaseHook,
)


# =========================================================================
# 辅助：Mock 工具注册表
# =========================================================================

class MockToolRegistry:
    """模拟 ToolRegistry，不依赖实际工具"""
    def __init__(self):
        self._handlers = {}

    def register(self, name, schema, handler):
        self._handlers[name] = handler

    def execute(self, tool_call: dict) -> dict:
        fn_name = tool_call.get("function", {}).get("name", "")
        raw_args = tool_call.get("function", {}).get("arguments", {})
        if isinstance(raw_args, str):
            raw_args = json.loads(raw_args)
        if fn_name not in self._handlers:
            return {"success": False, "output": f"unknown tool: {fn_name}"}
        try:
            return self._handlers[fn_name](raw_args)
        except Exception as e:
            return {"success": False, "output": str(e)}


# =========================================================================
# Phase 1: Approval 测试
# =========================================================================

class TestApprovalPhase:

    def test_no_approval_tools_skip(self):
        """只读工具不经过审批"""
        orch = ToolOrchestrator(MockToolRegistry())
        req = ToolExecutionRequest(tool_name="read_file", args={"path": "test.txt"})
        decision = orch._phase_approval(req)
        assert decision == ApprovalDecision.ALLOW

    def test_disable_approval(self):
        """禁用审批后全部放行"""
        config = ToolOrchestratorConfig(enable_approval=False)
        orch = ToolOrchestrator(MockToolRegistry(), config=config)
        req = ToolExecutionRequest(tool_name="terminal", args={"command": "rm -rf /"})
        decision = orch._phase_approval(req)
        assert decision == ApprovalDecision.ALLOW


# =========================================================================
# Phase 2: Safety 测试
# =========================================================================

class TestSafetyPhase:

    def test_safe_command_passes(self):
        """安全命令通过"""
        orch = ToolOrchestrator(MockToolRegistry())
        req = ToolExecutionRequest(tool_name="terminal", args={"command": "ls -la"})
        decision = orch._phase_safety(req)
        assert decision == SafetyDecision.ALLOW

    def test_disable_safety(self):
        """禁用安全后全部放行"""
        config = ToolOrchestratorConfig(enable_safety=False)
        orch = ToolOrchestrator(MockToolRegistry(), config=config)
        req = ToolExecutionRequest(tool_name="terminal", args={"command": "rm -rf /"})
        decision = orch._phase_safety(req)
        assert decision == SafetyDecision.ALLOW


# =========================================================================
# Phase 3: Execute 测试
# =========================================================================

class TestExecutePhase:

    def test_successful_execution(self):
        """成功执行"""
        registry = MockToolRegistry()
        registry.register("echo", {"description": "echo"}, lambda args: {"success": True, "output": args.get("text", "")})

        orch = ToolOrchestrator(registry)
        req = ToolExecutionRequest(tool_name="echo", args={"text": "hello"})
        result = orch._phase_execute_with_retry(req)

        assert result.success is True
        assert result.output == "hello"
        assert result.retry_count == 0

    def test_failed_execution(self):
        """失败执行"""
        registry = MockToolRegistry()
        registry.register("fail", {"description": "fail"}, lambda args: {"success": False, "output": "error occurred"})

        orch = ToolOrchestrator(registry)
        req = ToolExecutionRequest(tool_name="fail", args={})
        result = orch._phase_execute_with_retry(req)

        assert result.success is False
        assert "error" in result.output

    def test_exception_in_handler(self):
        """handler 抛异常"""
        registry = MockToolRegistry()

        def crash(args):
            raise RuntimeError("crash!")

        registry.register("crash", {"description": "crash"}, crash)

        orch = ToolOrchestrator(registry)
        req = ToolExecutionRequest(tool_name="crash", args={})
        result = orch._phase_execute_with_retry(req)

        assert result.success is False
        assert result.error == "crash!"


# =========================================================================
# 完整 Execute 测试（所有阶段串联）
# =========================================================================

class TestFullExecute:

    def test_successful_tool_execution(self):
        """完整工具执行链路"""
        registry = MockToolRegistry()
        registry.register("greet", {"description": "greet"}, lambda args: {"success": True, "output": f"Hello {args.get('name', 'world')}"})

        orch = ToolOrchestrator(registry)
        req = ToolExecutionRequest(tool_name="greet", args={"name": "Kuafu"})
        result = orch.execute(req)

        assert result.success is True
        assert result.output == "Hello Kuafu"

    def test_hard_deny_stops_execution(self):
        """硬黑名单阻止执行（通过 PolicyManager）"""
        registry = MockToolRegistry()
        registry.register("shutdown", {"description": "shutdown"}, lambda args: {"success": True, "output": "shutdown"})

        orch = ToolOrchestrator(registry)
        req = ToolExecutionRequest(tool_name="shutdown", args={})
        result = orch.execute(req)

        assert result.success is False
        assert result.decision == ApprovalDecision.DENY


# =========================================================================
# Hook 系统测试
# =========================================================================

class TestPhaseHooks:

    def test_hooks_fire_on_execute(self):
        """Hook 在 execute 前后触发"""
        registry = MockToolRegistry()
        registry.register("test", {"description": "test"}, lambda args: {"success": True, "output": "ok"})

        start_called = False
        end_called = False

        class TestHook(PhaseHook):
            def on_execute_start(self, req):
                nonlocal start_called
                start_called = True

            def on_execute_end(self, req, result):
                nonlocal end_called
                end_called = True

        orch = ToolOrchestrator(registry)
        orch.register_hook(TestHook())

        req = ToolExecutionRequest(tool_name="test", args={})
        orch._phase_execute_with_retry(req)

        assert start_called is True
        assert end_called is True

    def test_hook_can_override_approval(self):
        """Hook 可以覆盖审批决策"""
        registry = MockToolRegistry()
        registry.register("test", {"description": "test"}, lambda args: {"success": True, "output": "ok"})

        from core.policy_manager import PolicyDecision, PolicyAction

        class BlockHook(PhaseHook):
            def on_approval(self, req, decision):
                return PolicyDecision(action=PolicyAction.DENY, reason="hook denied")

        orch = ToolOrchestrator(registry)
        orch.register_hook(BlockHook())

        req = ToolExecutionRequest(tool_name="test", args={})
        result = orch.execute(req)

        assert result.success is False
        assert result.decision == ApprovalDecision.DENY


# =========================================================================
# 配置测试
# =========================================================================

class TestConfig:

    def test_default_config(self):
        """默认配置"""
        config = ToolOrchestratorConfig()
        assert config.enable_approval is True
        assert config.enable_safety is True
        assert config.enable_retry is True
        assert config.max_retries == 2

    def test_custom_config(self):
        """自定义配置"""
        config = ToolOrchestratorConfig(
            enable_approval=False,
            max_retries=5,
        )
        assert config.enable_approval is False
        assert config.max_retries == 5
