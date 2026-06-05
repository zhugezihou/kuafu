"""
Extra coverage tests for core/approval.py — DenyRules, dead code, edge cases.
"""
import json
import os
import time
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_deny_rules():
    from core.approval import DenyRules, DENY_RULES_PATH
    DenyRules._rules = []
    if DENY_RULES_PATH.exists():
        DENY_RULES_PATH.unlink()


@pytest.fixture(autouse=True)
def clean_approvals_dir():
    from core.approval import APPROVALS_DIR
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)


# ===================================================================
# DenyRules — load error paths, remove, check regex error, list_rules
# ===================================================================

class TestDenyRulesLoadErrors:
    """DenyRules.load() — all error branches (lines 111-118)."""

    def test_load_no_file(self):
        from core.approval import DenyRules, DENY_RULES_PATH
        if DENY_RULES_PATH.exists():
            DENY_RULES_PATH.unlink()
        result = DenyRules.load()
        assert result == []

    def test_load_valid_json(self):
        from core.approval import DenyRules, DENY_RULES_PATH
        DenyRules.add("tool_a", "pat_a", "reason_a")
        DenyRules._rules = []
        loaded = DenyRules.load()
        assert len(loaded) == 1
        assert loaded[0].tool == "tool_a"

    def test_load_json_decode_error(self):
        from core.approval import DenyRules, DENY_RULES_PATH
        DENY_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        DENY_RULES_PATH.write_text("not valid json{{{", encoding="utf-8")
        result = DenyRules.load()
        assert result == []

    def test_load_key_error(self):
        from core.approval import DenyRules, DENY_RULES_PATH
        DENY_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        DENY_RULES_PATH.write_text('[{"tool": "only", "bad": "data"}]', encoding="utf-8")
        result = DenyRules.load()
        assert result == []

    def test_load_type_error(self):
        from core.approval import DenyRules, DENY_RULES_PATH
        DENY_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        DENY_RULES_PATH.write_text('{"not": "a list"}', encoding="utf-8")
        result = DenyRules.load()
        assert result == []


class TestDenyRulesRemove:
    """DenyRules.remove() — all branches (lines 152-158)."""

    def test_remove_success(self):
        from core.approval import DenyRules
        rid = DenyRules.add("tool_x", "pat", "reason")
        assert DenyRules.remove(rid) is True
        assert len(DenyRules._rules) == 0

    def test_remove_not_found(self):
        from core.approval import DenyRules
        assert DenyRules.remove("nonexistent_id") is False


class TestDenyRulesCheckRegexError:
    """DenyRules.check() — regex error fallback (lines 191-194)."""

    def test_regex_error_exact_match_none(self):
        """Invalid regex pattern that doesn't match args."""
        from core.approval import DenyRules
        import re as re_mod
        # Find an invalid regex pattern
        bad = None
        for c in ["(unmatched", "[", "{", "(?P<name", "\\z"]:
            try:
                re_mod.compile(c)
            except re_mod.error:
                bad = c
                break
        if bad is None:
            return  # skip
        DenyRules.add("test_tool", bad, "bad regex")
        match = DenyRules.check("test_tool", {"key": "val"})
        assert match is None

    def test_regex_error_exact_match_success(self):
        """Invalid regex that exactly matches the arg_str."""
        from core.approval import DenyRules
        import json
        import re as re_mod
        # Find an invalid regex pattern
        bad = None
        for c in ["(unmatched", "[", "{", "(?P<name", "\\z"]:
            try:
                re_mod.compile(c)
            except re_mod.error:
                bad = c
                break
        if bad is None:
            return  # skip
        args = {"cmd": bad}
        arg_str = json.dumps(args, ensure_ascii=False)
        # The arg_str is like '{"cmd": "(unmatched"}'
        # We need to search for a pattern in the arg_str that is also an invalid regex
        # Actually let's just use the arg_str itself as the pattern
        # But arg_str might be valid regex... let's check
        try:
            re_mod.compile(arg_str)
            # Valid regex, so re.error won't be raised
            # In this case, the regex path works fine, and since arg_str is in the data,
            # re.search should match
            return  # skip this approach
        except re_mod.error:
            pass

        DenyRules.add("test_tool", arg_str, "exact match")
        match = DenyRules.check("test_tool", args)
        assert match is not None


class TestDenyRulesListRules:
    """DenyRules.list_rules() (lines 201-203)."""

    def test_list_rules_filters_expired(self):
        from core.approval import DenyRules
        DenyRules.add("perm", "p", "permanent")
        DenyRules.add("exp", "p", "expired", expires_at=time.time() - 10)
        DenyRules.add("fut", "p", "future", expires_at=time.time() + 3600)
        rules = DenyRules.list_rules()
        assert len(rules) == 2
        names = [r.tool for r in rules]
        assert "perm" in names
        assert "fut" in names


# ===================================================================
# Dead code: terminal in medium path (lines 349-355)
# ===================================================================

class TestTerminalMediumPathDeadCode:
    """Cover lines 349-355 by temporarily adding terminal to AUTO_TOOLS_MEDIUM."""

    def test_dangerous_command_medium_path(self):
        from core.approval import AutoMode
        AutoMode._history = []
        orig = AutoMode.AUTO_TOOLS_MEDIUM
        AutoMode.AUTO_TOOLS_MEDIUM = set(orig) | {"terminal"}
        try:
            result = AutoMode.should_auto_approve("terminal", {"command": "rm -rf /"})
            assert result is False
        finally:
            AutoMode.AUTO_TOOLS_MEDIUM = orig

    def test_safe_command_medium_path(self):
        from core.approval import AutoMode
        AutoMode._history = []
        orig = AutoMode.AUTO_TOOLS_MEDIUM
        AutoMode.AUTO_TOOLS_MEDIUM = set(orig) | {"terminal"}
        try:
            result = AutoMode.should_auto_approve("terminal", {"command": "ls -la"})
            assert result is True
        finally:
            AutoMode.AUTO_TOOLS_MEDIUM = orig


# ===================================================================
# terminal_prompt line 665 (timeout reprint)
# ===================================================================

class TestTerminalPromptTimeoutReprint:
    """Cover line 665: the reprint of remaining time during select timeout."""

    def test_timeout_reprint_message(self):
        from core.approval import ApprovalManager
        lock = MagicMock()
        with patch.object(ApprovalManager, '_terminal_lock', lock):
            with patch('select.select', return_value=([], [], [])):
                with patch('builtins.print') as mock_print:
                    result = ApprovalManager.terminal_prompt(
                        title="T", detail="D", risk="high", timeout=50
                    )
                    assert result is False
                    reprints = [c for c in mock_print.call_args_list
                                if '是否执行' in str(c) and 's 后自动拒绝' in str(c)]
                    assert len(reprints) >= 1


# ===================================================================
# handle_approval_decision send exception (lines 926-927, 936-937, 951-952)
# ===================================================================

class TestHandleDecisionSendException:
    """Cover send exception branches in handle_approval_decision."""

    def test_fuzzy_multiple_send_exception(self):
        """Lines 926-927: channel.send exception in fuzzy multiple branch."""
        from core.approval import handle_approval_decision, _save, ApprovalRequest
        t = time.time()
        _save(ApprovalRequest(
            id="suffix_1234", title="M1", detail="d",
            risk="high", status="pending", created_at=t, timeout=86400,
        ))
        _save(ApprovalRequest(
            id="other_1234", title="M2", detail="d",
            risk="high", status="pending", created_at=t + 1, timeout=86400,
        ))
        channel = MagicMock()
        channel.send.side_effect = Exception("fail")
        result = handle_approval_decision(
            {"action": "approve", "req_id": "1234", "fuzzy": True},
            chat_id="chat_123", channel=channel
        )
        assert "找到 2 个匹配" in result
        channel.send.assert_called_once()

    def test_fuzzy_no_match_send_exception(self):
        """Lines 936-937: channel.send exception in fuzzy no-match branch."""
        from core.approval import handle_approval_decision
        channel = MagicMock()
        channel.send.side_effect = Exception("fail")
        result = handle_approval_decision(
            {"action": "approve", "req_id": "badid", "fuzzy": True},
            chat_id="chat_123", channel=channel
        )
        assert "未找到" in result
        channel.send.assert_called_once()

    def test_main_path_send_exception(self):
        """Lines 951-952: channel.send exception in main approve path."""
        from core.approval import handle_approval_decision, ApprovalManager
        req_id = ApprovalManager.submit(title="Test", detail="d", risk="high")
        channel = MagicMock()
        channel.send.side_effect = Exception("fail")
        result = handle_approval_decision(
            {"action": "approve", "req_id": req_id},
            chat_id="chat_123", channel=channel
        )
        assert "已批准" in result
