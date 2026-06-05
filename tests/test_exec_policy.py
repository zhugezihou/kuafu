"""
tests/test_exec_policy.py — ExecPolicyManager 测试
"""

import pytest
from core.exec_policy import (
    ExecPolicyManager, ExecRule, RuleAction,
    canonicalize_command,
)


class TestCanonicalizeCommand:

    def test_simple_command(self):
        results = canonicalize_command("ls -la")
        assert results == ["ls -la"]

    def test_bash_c_decodes(self):
        results = canonicalize_command("bash -c 'rm -rf /'")
        assert "rm -rf /" in results
        assert "bash -c 'rm -rf /'" in results

    def test_sudo_decodes(self):
        results = canonicalize_command("sudo rm -rf /")
        assert "rm -rf /" in results
        assert "sudo rm -rf /" in results

    def test_sudo_bash_c_decodes(self):
        """套娃降级"""
        results = canonicalize_command("sudo bash -c 'rm -rf /'")
        assert "rm -rf /" in results

    def test_time_decodes(self):
        results = canonicalize_command("time ls")
        assert "ls" in results

    def test_no_false_positive(self):
        """降级不会误识别"""
        results = canonicalize_command("echo hello")
        assert len(results) == 1
        assert results[0] == "echo hello"


class TestExecRule:

    def test_rule_matches(self):
        rule = ExecRule("test", r"rm\s+-rf", "prompt", "测试规则")
        assert rule.matches("rm -rf /tmp")
        assert not rule.matches("ls -la")

    def test_rule_to_from_dict(self):
        rule = ExecRule("t1", r"test.*pattern", "forbid", "测试")
        d = rule.to_dict()
        rule2 = ExecRule.from_dict(d)
        assert rule2.id == "t1"
        assert rule2.action == RuleAction.FORBID
        assert rule2.reason == "测试"


class TestExecPolicyManager:

    def setup_method(self):
        self.mgr = ExecPolicyManager()

    def test_builtin_rules_exist(self):
        rules = self.mgr.list_rules()
        assert len(rules) > 5

    def test_check_allow_safe(self):
        action, rid, reason = self.mgr.check("ls -la")
        assert action == RuleAction.ALLOW

    def test_check_forbid_mkfs(self):
        action, rid, reason = self.mgr.check("mkfs /dev/sda1")
        assert action == RuleAction.FORBID
        assert "格式化" in reason

    def test_check_forbid_shutdown(self):
        action, rid, reason = self.mgr.check("shutdown -h now")
        assert action == RuleAction.FORBID

    def test_check_prompt_rm_rf(self):
        action, rid, reason = self.mgr.check("rm -rf /tmp/test")
        assert action == RuleAction.PROMPT

    def test_check_prompt_git_force(self):
        action, rid, reason = self.mgr.check("git push origin main --force")
        assert action == RuleAction.PROMPT

    def test_canonicalized_bypass(self):
        """降级后匹配"""
        action, rid, reason = self.mgr.check("bash -c 'rm -rf /tmp'")
        assert action == RuleAction.PROMPT

    def test_custom_rule(self):
        self.mgr.add_rule("custom_1", r"docker\s+rm\s+-f", "forbid", "禁止强制删除容器")
        action, rid, reason = self.mgr.check("docker rm -f my_container")
        assert action == RuleAction.FORBID
        assert rid == "custom_1"

    def test_remove_rule(self):
        self.mgr.add_rule("temp", r"temp.*cmd", "forbid")
        assert self.mgr.remove_rule("temp") is True
        assert self.mgr.remove_rule("nonexistent") is False

    def test_disabled_rule(self):
        rule = ExecRule("disabled", r"test", "forbid", enabled=False)
        assert rule.matches("test")
        assert not rule.enabled
