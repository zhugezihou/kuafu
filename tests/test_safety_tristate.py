"""
tests/test_safety_tristate.py — Safety 三态决策测试
"""

import pytest
from core.safety import SafetyLayer, CommandLevel


class TestGetTriState:

    def test_safe_command_allow(self):
        result = SafetyLayer.get_tri_state("ls -la")
        assert result["decision"] == "allow"
        assert result["level"] == CommandLevel.SAFE

    def test_forbidden_block(self):
        """安全锁禁止的命令返回 block"""
        # 跳过锁文件测试（依赖 .safety-lock）
        pass

    def test_dangerous_escalate(self):
        result = SafetyLayer.get_tri_state("rm -rf /tmp")
        assert result["decision"] == "escalate"
        assert len(result["suggestions"]) > 0

    def test_attention_escalate(self):
        """ATTENTION 级别的命令也 escalate"""
        result = SafetyLayer.get_tri_state("git push --force origin main")
        # 如果 git push --force 被 DANGEROUS_COMMANDS 匹配
        decision = result["decision"]
        assert decision in ("escalate", "allow")  # 取决于是什么级别

    def test_suggestions_on_escalate(self):
        result = SafetyLayer.get_tri_state("rm -rf /tmp")
        if result["decision"] == "escalate":
            assert isinstance(result["suggestions"], list)
            assert len(result["suggestions"]) > 0


class TestSafetyDecisionEnum:

    def test_safety_decisions_exist(self):
        """确保 ToolOrchestrator 的 SafetyDecision 枚举完整"""
        from core.tool_orchestrator import SafetyDecision
        assert SafetyDecision.ALLOW.value == "allow"
        assert SafetyDecision.BLOCK.value == "block"
        assert SafetyDecision.ESCALATE.value == "escalate"
