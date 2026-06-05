"""Tests for DenyRules class and helper functions in core/approval.py."""

import json
import os
import time
import sys
import re
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# ===================================================================
# DenyRules tests
# ===================================================================


class TestDenyRules:
    """100% branch coverage for DenyRules class."""

    def test_load_path_not_exists(self, monkeypatch, tmp_path):
        """load() when DENY_RULES_PATH doesn't exist returns []."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        result = DenyRules.load()
        assert result == []

    def test_load_valid_json(self, monkeypatch, tmp_path):
        """load() with valid JSON returns parsed rules."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        rules_data = [
            {"id": "r1", "tool": "write_file", "pattern": "bad", "reason": "test",
             "created_at": 100.0, "expires_at": None}
        ]
        fake_path.write_text(json.dumps(rules_data), encoding="utf-8")
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        result = DenyRules.load()
        assert len(result) == 1
        assert isinstance(result[0], DenyRule)
        assert result[0].id == "r1"

    def test_load_json_decode_error(self, monkeypatch, tmp_path):
        """load() with invalid JSON returns []."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        fake_path.write_text("not json", encoding="utf-8")
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        result = DenyRules.load()
        assert result == []

    def test_load_key_error(self, monkeypatch, tmp_path):
        """load() with missing keys returns []."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        fake_path.write_text('[{"id": "r1"}]', encoding="utf-8")
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        result = DenyRules.load()
        assert result == []

    def test_save_creates_dir_and_file(self, monkeypatch, tmp_path):
        """save() creates parent dir and writes file."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        from core.approval import DenyRule
        rule = DenyRule(id="r1", tool="t", pattern="p", reason="r",
                        created_at=100.0, expires_at=None)
        DenyRules._rules.append(rule)
        DenyRules.save()
        assert fake_path.exists()
        data = json.loads(fake_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["id"] == "r1"

    def test_add_returns_rule_id(self, monkeypatch, tmp_path):
        """add() returns a rule_id in deny_ format."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        rule_id = DenyRules.add(tool="write_file", pattern="secret",
                                reason="prevent secrets")
        assert rule_id.startswith("deny_")
        assert len(DenyRules._rules) == 1
        assert DenyRules._rules[0].tool == "write_file"
        assert fake_path.exists()

    def test_add_with_expires_at(self, monkeypatch, tmp_path):
        """add() with expires_at stores it."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        fut = time.time() + 3600
        DenyRules.add(tool="*", pattern="danger", reason="dangerous",
                      expires_at=fut)
        assert DenyRules._rules[0].expires_at == fut

    def test_remove_existing_rule(self, monkeypatch, tmp_path):
        """remove() on existing rule returns True and saves."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="t", pattern="p", reason="r",
                        created_at=100.0, expires_at=None)
        DenyRules._rules = [rule]
        result = DenyRules.remove("r1")
        assert result is True
        assert DenyRules._rules == []
        assert fake_path.exists()

    def test_remove_nonexistent_rule(self, monkeypatch, tmp_path):
        """remove() on non-existent rule returns False."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="t", pattern="p", reason="r",
                        created_at=100.0, expires_at=None)
        DenyRules._rules = [rule]
        result = DenyRules.remove("nonexistent")
        assert result is False
        assert len(DenyRules._rules) == 1

    def test_check_expired_rule_cleaned(self, monkeypatch, tmp_path):
        """check() removes expired rules and continues."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        expired = DenyRule(id="expired", tool="*", pattern="old",
                           reason="old", created_at=0.0, expires_at=1.0)
        active = DenyRule(id="active", tool="*", pattern="new",
                          reason="new", created_at=0.0, expires_at=None)
        DenyRules._rules = [expired, active]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("any_tool", {"key": "new"})
        assert result is not None
        assert result.id == "active"
        assert len(DenyRules._rules) == 1
        assert DenyRules._rules[0].id == "active"

    def test_check_exact_tool_mismatch(self, monkeypatch, tmp_path):
        """check() skips if tool doesn't match exact."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="write_file", pattern="bad",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("read_file", {"key": "bad"})
        assert result is None

    def test_check_wildcard_tool_match(self, monkeypatch, tmp_path):
        """check() matches tool wildcard ending with *."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="mcp_*", pattern="danger",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("mcp_execute", {"key": "danger"})
        assert result is not None
        assert result.id == "r1"

    def test_check_wildcard_tool_no_match(self, monkeypatch, tmp_path):
        """check() does not match tool wildcard if prefix differs."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="mcp_*", pattern="danger",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("write_file", {"key": "danger"})
        assert result is None

    def test_check_regex_match(self, monkeypatch, tmp_path):
        """check() re.search matches against args JSON string."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="*", pattern=r"secret_key",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("any", {"key": "my_secret_key_value"})
        assert result is not None
        assert result.id == "r1"

    def test_check_regex_no_match(self, monkeypatch, tmp_path):
        """check() returns None when regex doesn't match."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="*", pattern=r"forbidden",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("any", {"key": "allowed_value"})
        assert result is None

    def test_check_regex_error_fallback_exact_match(self, monkeypatch, tmp_path):
        """check() on re.error falls back to exact string match."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        # [ is not escaped properly - will cause re.error
        rule = DenyRule(id="r1", tool="*", pattern=r"[invalid",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        arg_str = json.dumps({"a": "b"}, ensure_ascii=False)
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("any", {"a": "b"})
        # arg_str == '{"a": "b"}' but rule.pattern is "[invalid"
        # re.error happens, then it checks rule.pattern == arg_str -> False
        assert result is None

    def test_check_regex_error_fallback_exact_match_success(self, monkeypatch, tmp_path):
        """check() re.error fallback: pattern equals arg_str exactly and both cause re.error."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        # Args containing '[' makes JSON string an invalid regex
        args = {"content": "["}
        arg_str = json.dumps(args, ensure_ascii=False)
        # arg_str = '{"content": "["}' which is an invalid regex
        # Create a rule whose pattern is exactly the arg_str
        rule = DenyRule(id="r1", tool="*", pattern=arg_str,
                        reason="exact", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("any", args)
        assert result is not None
        assert result.id == "r1"

    def test_check_star_tool(self, monkeypatch, tmp_path):
        """check() with '*' tool matches any tool."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="*", pattern="hit",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("anything", {"x": "hit"})
        assert result is not None

    def test_check_no_rules(self, monkeypatch, tmp_path):
        """check() with empty rules returns None."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("any", {"a": 1})
        assert result is None

    def test_list_rules_filters_expired(self, monkeypatch, tmp_path):
        """list_rules() excludes expired rules."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule_valid = DenyRule(id="valid", tool="*", pattern="p", reason="r",
                              created_at=0.0, expires_at=None)
        rule_expired = DenyRule(id="expired", tool="*", pattern="p", reason="r",
                                created_at=0.0, expires_at=50.0)
        DenyRules._rules = [rule_valid, rule_expired]
        with patch("time.time", return_value=100.0):
            valid = DenyRules.list_rules()
        assert len(valid) == 1
        assert valid[0].id == "valid"

    def test_list_rules_all_valid(self, monkeypatch, tmp_path):
        """list_rules() returns all when none are expired."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        r1 = DenyRule(id="r1", tool="*", pattern="p", reason="r",
                      created_at=0.0, expires_at=200.0)
        r2 = DenyRule(id="r2", tool="*", pattern="p", reason="r",
                      created_at=0.0, expires_at=None)
        DenyRules._rules = [r1, r2]
        with patch("time.time", return_value=100.0):
            valid = DenyRules.list_rules()
        assert len(valid) == 2

    def test_list_rules_empty(self, monkeypatch, tmp_path):
        """list_rules() with empty rules returns []."""
        from core.approval import DenyRules
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        DenyRules._rules = []
        with patch("time.time", return_value=100.0):
            valid = DenyRules.list_rules()
        assert valid == []


# ===================================================================
# Helper function tests
# ===================================================================


class TestIsInteractive:
    """100% branch coverage for _is_interactive()."""

    def test_gateway_running(self):
        """KUAFFU_GATEWAY_RUNNING=1 returns False."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {"KUAFFU_GATEWAY_RUNNING": "1"}, clear=True):
            assert _is_interactive() is False

    def test_feishu_app_id(self):
        """FEISHU_APP_ID set returns False."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {"FEISHU_APP_ID": "my_app"}, clear=True):
            assert _is_interactive() is False

    def test_wechat_env(self):
        """WECHAT_ILINK_DATA_DIR set returns False."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {"WECHAT_ILINK_DATA_DIR": "/data"}, clear=True):
            assert _is_interactive() is False

    def test_kuaifu_interactive_env(self):
        """KUAFFU_INTERACTIVE=1 returns True."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {"KUAFFU_INTERACTIVE": "1"}, clear=True):
            assert _is_interactive() is True

    def test_tty_both_isatty(self):
        """Both stdin/stdout isatty() returns True."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, 'isatty', return_value=True):
                with patch.object(sys.stdout, 'isatty', return_value=True):
                    assert _is_interactive() is True

    def test_tty_stdin_only_false(self):
        """Only stdin isatty, stdout not, returns False."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, 'isatty', return_value=True):
                with patch.object(sys.stdout, 'isatty', return_value=False):
                    assert _is_interactive() is False

    def test_tty_stdout_only_false(self):
        """Only stdout isatty, stdin not, returns False."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, 'isatty', return_value=False):
                with patch.object(sys.stdout, 'isatty', return_value=True):
                    assert _is_interactive() is False

    def test_tty_neither(self):
        """Neither stdin nor stdout isatty returns False."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, 'isatty', return_value=False):
                with patch.object(sys.stdout, 'isatty', return_value=False):
                    assert _is_interactive() is False

    def test_kuaifu_interactive_overrides_tty(self):
        """KUAFFU_INTERACTIVE=1 returns True even without TTY."""
        from core.approval import _is_interactive
        with patch.dict(os.environ, {"KUAFFU_INTERACTIVE": "1"}, clear=True):
            with patch.object(sys.stdin, 'isatty', return_value=False):
                with patch.object(sys.stdout, 'isatty', return_value=False):
                    assert _is_interactive() is True


class TestGetApprovalTimeout:
    """100% branch coverage for _get_approval_timeout()."""

    def test_import_success(self, monkeypatch):
        """When APPROVAL_TIMEOUT importable, returns its value."""
        from core.approval import _get_approval_timeout
        mock_config = MagicMock()
        mock_config.APPROVAL_TIMEOUT = 120
        monkeypatch.setitem(sys.modules, 'core.config', mock_config)
        result = _get_approval_timeout()
        assert result == 120

    def test_import_failure(self, monkeypatch):
        """When APPROVAL_TIMEOUT not importable, returns 300."""
        from core.approval import _get_approval_timeout
        # Remove core.config from sys.modules to trigger ImportError
        monkeypatch.delitem(sys.modules, 'core.config', raising=False)
        # Also make sure the import itself fails
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'core.config':
                raise ImportError("No module named 'core.config'")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            result = _get_approval_timeout()
        assert result == 300


class TestSaveAndLoad:
    """100% branch coverage for _save() and _load()."""

    def test_save_creates_file(self, monkeypatch, tmp_path):
        """_save creates dir and writes JSON."""
        from core.approval import _save, APPROVALS_DIR
        from core.approval import ApprovalRequest
        fake_dir = tmp_path / "approvals"
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        req = ApprovalRequest(
            id="test_1", title="T", detail="D", risk="low",
            status="pending", created_at=100.0,
        )
        _save(req)
        path = fake_dir / "test_1.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["id"] == "test_1"

    def test_load_not_exists(self, monkeypatch, tmp_path):
        """_load returns None when file doesn't exist."""
        from core.approval import _load, APPROVALS_DIR
        fake_dir = tmp_path / "approvals"
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        result = _load("nonexistent")
        assert result is None

    def test_load_valid(self, monkeypatch, tmp_path):
        """_load returns ApprovalRequest for valid file."""
        from core.approval import _load, APPROVALS_DIR, ApprovalRequest
        fake_dir = tmp_path / "approvals"
        fake_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        data = {
            "id": "test_2", "title": "Hello", "detail": "World",
            "risk": "medium", "status": "pending", "created_at": 200.0,
        }
        (fake_dir / "test_2.json").write_text(json.dumps(data), encoding="utf-8")
        result = _load("test_2")
        assert result is not None
        assert isinstance(result, ApprovalRequest)
        assert result.title == "Hello"

    def test_load_json_decode_error(self, monkeypatch, tmp_path):
        """_load returns None for invalid JSON."""
        from core.approval import _load, APPROVALS_DIR
        fake_dir = tmp_path / "approvals"
        fake_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        (fake_dir / "bad.json").write_text("not json", encoding="utf-8")
        result = _load("bad")
        assert result is None

    def test_load_key_error(self, monkeypatch, tmp_path):
        """_load returns None when JSON has missing keys."""
        from core.approval import _load, APPROVALS_DIR
        fake_dir = tmp_path / "approvals"
        fake_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        (fake_dir / "bad2.json").write_text('{"id": "x"}', encoding="utf-8")
        result = _load("bad2")
        assert result is None


class TestReqId:
    """Coverage for _req_id()."""

    def test_req_id_format(self):
        """_req_id returns 'appr_<ts>_<hash>' format."""
        from core.approval import _req_id
        with patch("time.time", return_value=12345.67):
            result = _req_id("my title")
        assert result.startswith("appr_")
        parts = result.split("_")
        assert len(parts) == 3
        assert parts[1] == "12345"


class TestIsSafeTerminal:
    """100% branch coverage for _is_safe_terminal()."""

    def test_not_a_string(self):
        """Non-string input returns False."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal(42) is False
        assert _is_safe_terminal(None) is False
        assert _is_safe_terminal(["ls"]) is False

    def test_safe_prefix_ls(self):
        """'ls -la' starts with safe prefix."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("ls -la /tmp") is True

    def test_safe_prefix_cat(self):
        """'cat /etc/hostname' starts with safe prefix."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("cat /etc/hostname") is True

    def test_safe_prefix_pwd(self):
        """'pwd' matches exactly."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("pwd") is True

    def test_unsafe_command(self):
        """'rm -rf /' is not safe."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("rm -rf /") is False

    def test_empty_string(self):
        """Empty string is not safe."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("") is False

    def test_case_insensitive(self):
        """Command is lowercased before check."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("LS -la") is True

    def test_leading_whitespace(self):
        """Leading whitespace is stripped."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("  ls -la") is True

    def test_grep_safe(self):
        """'grep pattern file' is safe."""
        from core.approval import _is_safe_terminal
        assert _is_safe_terminal("grep foo bar.txt") is True

    def test_unsafe_matches_substring(self):
        """Substring match doesn't make a command unsafe if it doesn't start with safe prefix."""
        from core.approval import _is_safe_terminal
        # 'not_ls' doesn't start with any safe prefix
        assert _is_safe_terminal("not_ls_anything") is False


class TestFormatApproval:
    """Coverage for format_approval()."""

    def test_format_low_risk(self):
        """Low risk shows 🟢 icon."""
        from core.approval import format_approval, ApprovalRequest
        req = ApprovalRequest(
            id="appr_1", title="Test Title", detail="Some details here",
            risk="low", status="pending", created_at=100.0,
        )
        result = format_approval(req)
        assert "🟢" in result
        assert "Test Title" in result
        assert "appr_1" in result
        assert "Some details here" in result

    def test_format_medium_risk(self):
        """Medium risk shows 🟡 icon."""
        from core.approval import format_approval, ApprovalRequest
        req = ApprovalRequest(
            id="appr_2", title="Medium", detail="Details",
            risk="medium", status="pending", created_at=100.0,
        )
        result = format_approval(req)
        assert "🟡" in result

    def test_format_high_risk(self):
        """High risk shows 🔴 icon."""
        from core.approval import format_approval, ApprovalRequest
        req = ApprovalRequest(
            id="appr_3", title="High", detail="Details",
            risk="high", status="pending", created_at=100.0,
        )
        result = format_approval(req)
        assert "🔴" in result

    def test_format_unknown_risk(self):
        """Unknown risk defaults to 🟡."""
        from core.approval import format_approval, ApprovalRequest
        req = ApprovalRequest(
            id="appr_4", title="Unknown", detail="Details",
            risk="unknown", status="pending", created_at=100.0,
        )
        result = format_approval(req)
        assert "🟡" in result

    def test_detail_truncated(self):
        """Detail is truncated to 300 chars."""
        from core.approval import format_approval, ApprovalRequest
        long_detail = "A" * 500
        req = ApprovalRequest(
            id="appr_5", title="Long", detail=long_detail,
            risk="low", status="pending", created_at=100.0,
        )
        result = format_approval(req)
        # Should contain first 300 chars
        assert "A" * 300 in result
        # Should not have all 500 chars
        assert "A" * 500 not in result


class TestFormatPendingSummary:
    """Coverage for format_pending_summary()."""

    def test_no_pending(self, monkeypatch, tmp_path):
        """No pending returns empty string."""
        from core.approval import format_pending_summary, APPROVALS_DIR
        fake_dir = tmp_path / "approvals"
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        # Directory doesn't exist -> list_pending returns []
        result = format_pending_summary()
        assert result == ""

    def test_with_pending(self, monkeypatch, tmp_path):
        """With pending items, returns formatted summary."""
        from core.approval import format_pending_summary, APPROVALS_DIR, _save
        from core.approval import ApprovalRequest
        fake_dir = tmp_path / "approvals"
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        now = time.time()
        req = ApprovalRequest(
            id="appr_test_1", title="Test Approve", detail="Some detail here",
            risk="high", status="pending", created_at=now, timeout=86400,
        )
        _save(req)
        result = format_pending_summary()
        assert "夸父待审批" in result
        assert "Test Approve" in result
        assert "appr_test_1" in result
        assert "🔴" in result  # high risk

    def test_low_risk_icon(self, monkeypatch, tmp_path):
        """Low risk pending shows 🟢."""
        from core.approval import format_pending_summary, APPROVALS_DIR, _save
        from core.approval import ApprovalRequest
        fake_dir = tmp_path / "approvals"
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        now = time.time()
        req = ApprovalRequest(
            id="appr_low", title="Low Risk", detail="Safe",
            risk="low", status="pending", created_at=now, timeout=86400,
        )
        _save(req)
        result = format_pending_summary()
        assert "🟢" in result

    def test_pending_with_risk_unknown(self, monkeypatch, tmp_path):
        """Unknown risk falls back to 🟡."""
        from core.approval import format_pending_summary, APPROVALS_DIR, _save
        from core.approval import ApprovalRequest
        fake_dir = tmp_path / "approvals"
        monkeypatch.setattr("core.approval.APPROVALS_DIR", fake_dir)
        now = time.time()
        req = ApprovalRequest(
            id="appr_unk", title="Unknown Risk", detail="Hmm",
            risk="unknown", status="pending", created_at=now, timeout=86400,
        )
        _save(req)
        result = format_pending_summary()
        assert "🟡" in result


class TestCheckApprovalDecision:
    """100% branch coverage for check_approval_decision()."""

    def test_short_approve(self):
        """'1 abc12345' -> approve with fuzzy=True."""
        from core.approval import check_approval_decision
        result = check_approval_decision("1 abc12345")
        assert result == {"action": "approve", "req_id": "abc12345", "fuzzy": True}

    def test_short_reject(self):
        """'0 abc12345' -> reject with fuzzy=True."""
        from core.approval import check_approval_decision
        result = check_approval_decision("0 abc12345")
        assert result == {"action": "reject", "req_id": "abc12345", "fuzzy": True}

    def test_short_too_short_id(self):
        """'1 ab' (less than 4 chars) doesn't match."""
        from core.approval import check_approval_decision
        result = check_approval_decision("1 ab")
        assert result is None

    def test_chinese_approve(self):
        """'批准 abc12345' -> approve."""
        from core.approval import check_approval_decision
        result = check_approval_decision("批准 abc12345")
        assert result == {"action": "approve", "req_id": "abc12345"}

    def test_chinese_reject(self):
        """'拒绝 abc12345' -> reject."""
        from core.approval import check_approval_decision
        result = check_approval_decision("拒绝 abc12345")
        assert result == {"action": "reject", "req_id": "abc12345"}

    def test_english_approve(self):
        """'approve abc12345' -> approve."""
        from core.approval import check_approval_decision
        result = check_approval_decision("approve abc12345")
        assert result == {"action": "approve", "req_id": "abc12345"}

    def test_english_reject(self):
        """'reject abc12345' -> reject."""
        from core.approval import check_approval_decision
        result = check_approval_decision("reject abc12345")
        assert result == {"action": "reject", "req_id": "abc12345"}

    def test_case_insensitive_english(self):
        """'APPROVE abc12345' -> approve (case insensitive)."""
        from core.approval import check_approval_decision
        result = check_approval_decision("APPROVE abc12345")
        assert result == {"action": "approve", "req_id": "abc12345"}

    def test_no_match(self):
        """Unrelated text returns None."""
        from core.approval import check_approval_decision
        result = check_approval_decision("hello world")
        assert result is None

    def test_empty_text(self):
        """Empty text returns None."""
        from core.approval import check_approval_decision
        result = check_approval_decision("")
        assert result is None

    def test_approve_with_longer_reqid(self):
        """'approve appr_12345_full' works."""
        from core.approval import check_approval_decision
        result = check_approval_decision("approve appr_12345_full")
        assert result == {"action": "approve", "req_id": "appr_12345_full"}


class TestPretooluseCheck:
    """100% branch coverage for pretooluse_check()."""

    def test_safe_terminal_direct_pass(self):
        """Safe terminal command bypasses check_permission."""
        from core.approval import pretooluse_check
        result = pretooluse_check("terminal", {"command": "ls -la"})
        assert result["allowed"] is True
        assert result["approach"] == "pretooluse_precheck"

    def test_safe_terminal_not_dict_args(self):
        """args is not a dict -> not treated as safe terminal shortcut."""
        from core.approval import pretooluse_check
        with patch("core.approval.DenyRules.load"):
            with patch("core.approval.AutoMode.load"):
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": True, "req_id": None, "reason": "ok",
                                         "approach": "auto", "rule_id": None, "auto": True}):
                    result = pretooluse_check("terminal", "not_a_dict")
                    assert result["allowed"] is True
                    # Must have gone through check_permission since args is not dict
                    # (the isinstance check fails, so we don't enter the safe-terminal shortcut branch)

    def test_unsafe_terminal_goes_through_check(self):
        """Unsafe terminal command goes through check_permission."""
        from core.approval import pretooluse_check
        with patch("core.approval.DenyRules.load"):
            with patch("core.approval.AutoMode.load"):
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": False, "req_id": None, "reason": "denied",
                                         "approach": "deny", "rule_id": None, "auto": True}):
                    result = pretooluse_check("terminal", {"command": "rm -rf /"})
                    assert result["allowed"] is False

    def test_non_terminal_goes_through_check(self):
        """Non-terminal tool goes through check_permission."""
        from core.approval import pretooluse_check
        with patch("core.approval.DenyRules.load"):
            with patch("core.approval.AutoMode.load"):
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": True, "req_id": None, "reason": "ok",
                                         "approach": "auto", "rule_id": None, "auto": True}):
                    result = pretooluse_check("write_file", {"path": "/test.txt"})
                    assert result["allowed"] is True

    def test_cache_initialization(self):
        """First call initializes cache and loads DenyRules/AutoMode."""
        from core.approval import pretooluse_check, _pretooluse_cache
        _pretooluse_cache.clear()
        with patch("core.approval.DenyRules.load") as mock_dl:
            with patch("core.approval.AutoMode.load") as mock_al:
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": True, "req_id": None, "reason": "ok",
                                         "approach": "auto", "rule_id": None, "auto": True}):
                    pretooluse_check("read_file", {"path": "/x"})
                    mock_dl.assert_called_once()
                    mock_al.assert_called_once()

    def test_callback_called_with_req_id(self):
        """When req_id present and ON_APPROVAL_REQUEST_CB set, calls callback."""
        from core.approval import pretooluse_check, ON_APPROVAL_REQUEST_CB, _pretooluse_cache
        _pretooluse_cache.clear()
        cb = MagicMock()
        # Must set it via module reference
        import core.approval
        core.approval.ON_APPROVAL_REQUEST_CB = cb
        with patch("core.approval.DenyRules.load"):
            with patch("core.approval.AutoMode.load"):
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": None, "req_id": "appr_12345",
                                         "reason": "pending", "approach": "pending_approval",
                                         "rule_id": None, "auto": False}):
                    result = pretooluse_check("terminal", {"command": "rm -rf /"})
                    assert result["req_id"] == "appr_12345"
                    cb.assert_called_once_with("terminal", {"command": "rm -rf /"}, "appr_12345")
        # Cleanup
        core.approval.ON_APPROVAL_REQUEST_CB = None

    def test_callback_not_called_without_req_id(self):
        """When req_id is None, callback is not called."""
        from core.approval import pretooluse_check, _pretooluse_cache
        _pretooluse_cache.clear()
        cb = MagicMock()
        import core.approval
        core.approval.ON_APPROVAL_REQUEST_CB = cb
        with patch("core.approval.DenyRules.load"):
            with patch("core.approval.AutoMode.load"):
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": True, "req_id": None,
                                         "reason": "ok", "approach": "auto",
                                         "rule_id": None, "auto": True}):
                    pretooluse_check("read_file", {"path": "/x"})
                    cb.assert_not_called()
        core.approval.ON_APPROVAL_REQUEST_CB = None

    def test_callback_exception_swallowed(self):
        """Exception in callback is silently swallowed."""
        from core.approval import pretooluse_check, _pretooluse_cache
        _pretooluse_cache.clear()
        import core.approval
        def failing_cb(*args):
            raise ValueError("callback error")
        core.approval.ON_APPROVAL_REQUEST_CB = failing_cb
        with patch("core.approval.DenyRules.load"):
            with patch("core.approval.AutoMode.load"):
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": None, "req_id": "appr_cb_err",
                                         "reason": "pending", "approach": "pending_approval",
                                         "rule_id": None, "auto": False}):
                    # Should not raise
                    result = pretooluse_check("terminal", {"command": "danger"})
                    assert result["req_id"] == "appr_cb_err"
        core.approval.ON_APPROVAL_REQUEST_CB = None

    def test_cache_used_on_second_call(self):
        """Second call does not reinitialize cache."""
        from core.approval import pretooluse_check, _pretooluse_cache
        _pretooluse_cache.clear()
        put_sk = _pretooluse_cache.setdefault
        # Set cache non-empty so init is skipped
        _pretooluse_cache["already"] = "initialized"
        with patch("core.approval.DenyRules.load") as mock_dl:
            with patch("core.approval.AutoMode.load") as mock_al:
                with patch("core.approval.ApprovalManager.check_permission",
                           return_value={"allowed": True, "req_id": None, "reason": "ok",
                                         "approach": "auto", "rule_id": None, "auto": True}):
                    pretooluse_check("read_file", {"path": "/x"})
                    mock_dl.assert_not_called()
                    mock_al.assert_not_called()


class TestCheckDenyRulesWildcardEndswith:
    """Additional edge-case for tool wildcard with endswith pattern."""

    def test_tool_wildcard_endswith_positive(self, monkeypatch, tmp_path):
        """tool.endswith('*') and tool.startswith uses rule.tool[:-1] match."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        # rule.tool doesn't end with *, so it falls through to exact match
        rule = DenyRule(id="r1", tool="test*", pattern="x",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        # "test*" ends with "*" -> test*[:-1] = "test"
        # "test123".startswith("test") -> True
        with patch("time.time", return_value=100.0):
            result = DenyRules.check("test123", {"a": "x"})
        assert result is not None

    def test_tool_wildcard_not_endswith_star(self, monkeypatch, tmp_path):
        """rule.tool doesn't end with *, uses standard != check."""
        from core.approval import DenyRules, DenyRule
        fake_path = tmp_path / "memory" / "deny_rules.json"
        monkeypatch.setattr("core.approval.DENY_RULES_PATH", fake_path)
        rule = DenyRule(id="r1", tool="exact_tool", pattern="x",
                        reason="test", created_at=0.0, expires_at=None)
        DenyRules._rules = [rule]
        with patch("time.time", return_value=100.0):
            # Exact match: should match
            result = DenyRules.check("exact_tool", {"a": "x"})
            assert result is not None
            # Mismatch: should not match
            result2 = DenyRules.check("other_tool", {"a": "x"})
            assert result2 is None
