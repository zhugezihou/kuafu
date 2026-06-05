"""
tests/test_policy_manager.py — PolicyManager 统一策略管理器测试
"""

import pytest
from core.policy_manager import (
    PolicyManager, PolicyDecision, PolicyAction,
    get_policy, pretooluse_check,
)


class TestPolicyDecision:

    def test_allow(self):
        d = PolicyDecision.allow("放行")
        assert d.action == PolicyAction.ALLOW
        assert d.reason == "放行"
        assert d.auto is True

    def test_deny(self):
        d = PolicyDecision.deny("拒绝", rule_id="rule_1")
        assert d.action == PolicyAction.DENY
        assert d.reason == "拒绝"
        assert d.rule_id == "rule_1"
        assert d.auto is True

    def test_escalate(self):
        d = PolicyDecision.escalate("需要审批", req_id="req_1", risk="high")
        assert d.action == PolicyAction.ESCALATE
        assert d.req_id == "req_1"
        assert d.risk == "high"
        assert d.auto is False

    def test_fast_path(self):
        d = PolicyDecision.fast_path("快速放行")
        assert d.action == PolicyAction.FAST_PATH
        assert d.reason == "快速放行"

    def test_to_legacy_dict_allow(self):
        d = PolicyDecision.allow()
        ld = d.to_legacy_dict()
        assert ld["allowed"] is True
        assert ld["approach"] == "auto_approve"

    def test_to_legacy_dict_deny(self):
        d = PolicyDecision.deny()
        ld = d.to_legacy_dict()
        assert ld["allowed"] is False
        assert ld["approach"] == "deny_rule"

    def test_to_legacy_dict_escalate(self):
        d = PolicyDecision.escalate()
        ld = d.to_legacy_dict()
        assert ld["allowed"] is None
        assert ld["approach"] == "pending_approval"


class TestPolicyManagerDecide:

    def setup_method(self):
        self.pm = PolicyManager()

    def test_readonly_tools_auto_allow(self):
        d = self.pm.decide("read_file", {"path": "test.txt"})
        assert d.action == PolicyAction.ALLOW

    def test_hard_deny_tools(self):
        d = self.pm.decide("shutdown", {})
        assert d.action == PolicyAction.DENY

    def test_safe_terminal_fast_path(self):
        d = self.pm.decide("terminal", {"command": "ls -la"})
        assert d.action == PolicyAction.FAST_PATH

    def test_unknown_tool_auto_allow(self):
        """未知工具走自动模式，低风险放行"""
        d = self.pm.decide("unknown_tool", {})
        assert d.action in (PolicyAction.ALLOW, PolicyAction.ESCALATE)


class TestGlobalPolicy:

    def test_get_policy_singleton(self):
        p1 = get_policy()
        p2 = get_policy()
        assert p1 is p2

    def test_pretooluse_check_backward_compat(self):
        """pretooluse_check 返回旧格式"""
        result = pretooluse_check("read_file", {"path": "test.txt"})
        assert "allowed" in result
        assert "approach" in result
        assert "reason" in result

    def test_pretooluse_check_hard_deny(self):
        result = pretooluse_check("shutdown", {})
        assert result["allowed"] is False
        assert result["approach"] == "deny_rule"

    def test_pretooluse_check_safe_terminal(self):
        result = pretooluse_check("terminal", {"command": "ls -la"})
        assert result["allowed"] is True
        assert result["approach"] == "fast_path"
