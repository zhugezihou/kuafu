"""
Tests for DenialTracker class in core/safety.py — 100% branch coverage.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TIME_NOW = 12345.0
TIME_LATER = 67890.0


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tracker(tmp_path):
    """Create a DenialTracker with an isolated tmp_path as root_dir,
    default config (auto_trust_threshold=3, degraded_action="allow")."""
    from core.safety import DenialTracker, DenialConfig
    cfg = DenialConfig(state_file="denial_state.json")
    with patch("time.time", return_value=TIME_NOW):
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
    return dt


@pytest.fixture
def tracker_custom_config(tmp_path):
    """Create a DenialTracker with a custom config (threshold=1, degraded_action="block")."""
    from core.safety import DenialTracker, DenialConfig
    cfg = DenialConfig(auto_trust_threshold=1, degraded_action="block", state_file="custom_state.json")
    with patch("time.time", return_value=TIME_NOW):
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
    return dt


# ── __init__ ──────────────────────────────────────────────────────────

class TestInit:
    def test_init_defaults(self, tmp_path):
        """Default root_dir and config should be used when not provided."""
        from core.safety import DenialTracker, ROOT_DIR, DenialConfig
        with patch("core.safety.DenialTracker._load", return_value={}):
            dt = DenialTracker()
        assert dt.root_dir == ROOT_DIR
        assert isinstance(dt.config, DenialConfig)
        assert dt.state_path == ROOT_DIR / dt.config.state_file

    def test_init_custom_root_dir_and_config(self):
        """Custom root_dir and config should be used."""
        from core.safety import DenialTracker, DenialConfig
        custom_root = Path("/custom/root")
        custom_cfg = DenialConfig(state_file="custom.json")
        with patch("core.safety.DenialTracker._load", return_value={}):
            dt = DenialTracker(root_dir=custom_root, config=custom_cfg)
        assert dt.root_dir == custom_root
        assert dt.config == custom_cfg
        assert dt.state_path == custom_root / "custom.json"

    def test_init_loads_existing_data(self, tmp_path):
        """Existing state file should be loaded on init."""
        state_file = tmp_path / "denial_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        existing_data = {"pip install": {"count": 5, "degraded": True}}
        state_file.write_text(json.dumps(existing_data))

        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="denial_state.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == existing_data

    def test_init_loads_corrupted_file_returns_empty(self, tmp_path):
        """Corrupted JSON file should result in empty _data."""
        state_file = tmp_path / "denial_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("not valid json{")

        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="denial_state.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == {}

    def test_init_loads_file_not_dict_returns_empty(self, tmp_path):
        """JSON file containing non-dict (e.g. list) should result in empty _data."""
        state_file = tmp_path / "denial_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps([1, 2, 3]))

        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="denial_state.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == {}

    def test_init_loads_nonexistent_file_returns_empty(self, tmp_path):
        """Non-existent state file should result in empty _data."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="nonexistent.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == {}


# ── record_denial ─────────────────────────────────────────────────────

class TestRecordDenial:
    def test_record_new_pattern(self, tracker, tmp_path):
        """First denial on a new pattern should create entry with count=1, consecutive=1, first_seen set."""
        with patch("time.time", return_value=TIME_NOW):
            result = tracker.record_denial("pip install")

        assert result["count"] == 1
        assert result["consecutive_denials"] == 1
        assert result["first_seen"] == TIME_NOW
        assert result["last_seen"] == TIME_NOW
        assert result["degraded"] is False
        assert tracker._data["pip install"] == result

    def test_record_existing_pattern(self, tracker, tmp_path):
        """Existing pattern should increment counts and NOT reset first_seen."""
        # First denial
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        # Second denial at later time
        with patch("time.time", return_value=TIME_LATER):
            result = tracker.record_denial("pip install")

        assert result["count"] == 2
        assert result["consecutive_denials"] == 2
        assert result["first_seen"] == TIME_NOW  # unchanged
        assert result["last_seen"] == TIME_LATER
        assert result["degraded"] is False

    def test_first_seen_zero_gets_set_to_now(self, tracker, tmp_path):
        """If first_seen is somehow 0 when recording, it should be set to now."""
        # Manually inject a record with first_seen=0
        tracker._data["test_cmd"] = {
            "count": 0,
            "first_seen": 0,
            "last_seen": 0,
            "consecutive_denials": 0,
            "degraded": False,
        }
        with patch("time.time", return_value=TIME_NOW):
            result = tracker.record_denial("test_cmd")

        assert result["first_seen"] == TIME_NOW
        assert result["count"] == 1
        assert result["consecutive_denials"] == 1

    def test_auto_degrade_at_threshold(self, tracker, tmp_path):
        """After auto_trust_threshold (3) consecutive denials, degraded should become True."""
        for i in range(1, 5):
            with patch("time.time", return_value=TIME_NOW + i):
                result = tracker.record_denial("pip install")

            if i < 3:
                assert result["degraded"] is False, f"Not degraded yet at denial #{i}"
            else:
                assert result["degraded"] is True, f"Should be degraded at denial #{i}"
                assert result["count"] == i
                assert result["consecutive_denials"] == i

    def test_no_degrade_below_threshold(self, tracker, tmp_path):
        """With threshold=3, 2 denials should NOT trigger degraded."""
        for i in range(1, 3):
            with patch("time.time", return_value=TIME_NOW + i):
                result = tracker.record_denial("pip install")
            assert result["degraded"] is False

    def test_auto_degrade_with_threshold_1(self, tracker_custom_config, tmp_path):
        """With threshold=1, first denial should trigger degraded immediately."""
        with patch("time.time", return_value=TIME_NOW):
            result = tracker_custom_config.record_denial("rm -rf /")

        assert result["degraded"] is True
        assert result["count"] == 1
        assert result["consecutive_denials"] == 1

    def test_already_degraded_stays_true(self, tracker, tmp_path):
        """Once degraded, further denials should keep degraded=True."""
        # 3 denials to trigger degradation
        for i in range(1, 4):
            with patch("time.time", return_value=TIME_NOW + i):
                tracker.record_denial("pip install")
        assert tracker._data["pip install"]["degraded"] is True

        # 4th denial — should still be degraded
        with patch("time.time", return_value=TIME_NOW + 100):
            result = tracker.record_denial("pip install")
        assert result["degraded"] is True

    def test_persists_to_disk(self, tracker, tmp_path):
        """record_denial should save to disk."""
        state_path = tracker.state_path

        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("npm install")

        assert state_path.exists()
        saved = json.loads(state_path.read_text())
        assert "npm install" in saved
        assert saved["npm install"]["count"] == 1

    def test_save_oserror_silently_ignored(self, tmp_path):
        """OSError during _save should be silently ignored (tested via _save directly)."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="state.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        dt._data = {"pip install": {"count": 1, "consecutive_denials": 1, "first_seen": 1, "last_seen": 1, "degraded": False}}

        # Make parent directory unwritable so json.dump raises OSError
        state_path = dt.state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.parent.chmod(0o444)  # read-only

        try:
            dt._save()  # Should not raise — caught by except OSError
        finally:
            state_path.parent.chmod(0o755)  # restore


# ── record_approval ───────────────────────────────────────────────────

class TestRecordApproval:
    def test_approval_resets_consecutive(self, tracker, tmp_path):
        """Approval should reset consecutive_denials to 0 on existing entry."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")
            tracker.record_denial("pip install")

        assert tracker._data["pip install"]["consecutive_denials"] == 2

        tracker.record_approval("pip install")
        assert tracker._data["pip install"]["consecutive_denials"] == 0
        # Other fields should not be affected
        assert tracker._data["pip install"]["count"] == 2
        assert tracker._data["pip install"]["degraded"] is False

    def test_approval_saves_to_disk(self, tracker, tmp_path):
        """Approval should trigger _save."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        tracker.record_approval("pip install")
        saved = json.loads(tracker.state_path.read_text())
        assert saved["pip install"]["consecutive_denials"] == 0

    def test_approval_nonexistent_pattern_does_nothing(self, tracker, tmp_path):
        """Approval on a non-existent pattern should not create an entry."""
        tracker.record_approval("nonexistent")
        assert "nonexistent" not in tracker._data

    def test_approval_on_existing_after_degrade(self, tracker, tmp_path):
        """Approval should reset consecutive even after degraded."""
        for i in range(3):
            with patch("time.time", return_value=TIME_NOW + i):
                tracker.record_denial("pip install")

        assert tracker._data["pip install"]["degraded"] is True

        tracker.record_approval("pip install")
        assert tracker._data["pip install"]["consecutive_denials"] == 0
        # degraded is NOT reset by approval
        assert tracker._data["pip install"]["degraded"] is True

    def test_approval_save_oserror_silently_ignored(self, tmp_path):
        """OSError during approval save should be silently ignored."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="approval_os.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        with patch("time.time", return_value=TIME_NOW):
            dt.record_denial("pip install")

        # Make parent directory unwritable so json.dump raises OSError
        state_path = dt.state_path
        state_path.parent.chmod(0o444)  # read-only

        try:
            dt.record_approval("pip install")  # Should not raise
        finally:
            state_path.parent.chmod(0o755)  # restore

        assert dt._data["pip install"]["consecutive_denials"] == 0


# ── should_degrade ────────────────────────────────────────────────────

class TestShouldDegrade:
    def test_should_degrade_true_when_degraded(self, tracker, tmp_path):
        """should_degrade returns True for degraded pattern."""
        for i in range(3):
            with patch("time.time", return_value=TIME_NOW + i):
                tracker.record_denial("pip install")
        assert tracker.should_degrade("pip install") is True

    def test_should_degrade_false_when_not_degraded(self, tracker, tmp_path):
        """should_degrade returns False for non-degraded pattern."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")
        assert tracker.should_degrade("pip install") is False

    def test_should_degrade_false_for_nonexistent(self, tracker, tmp_path):
        """should_degrade returns False for unknown pattern."""
        assert tracker.should_degrade("unknown") is False


# ── get_decision ──────────────────────────────────────────────────────

class TestGetDecision:
    def test_get_decision_ask_when_not_degraded(self, tracker, tmp_path):
        """Non-degraded patterns return 'ask'."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")
        assert tracker.get_decision("pip install") == "ask"

    def test_get_decision_allow_when_degraded_default(self, tracker, tmp_path):
        """Degraded patterns with default config (degraded_action='allow') return 'allow'."""
        for i in range(3):
            with patch("time.time", return_value=TIME_NOW + i):
                tracker.record_denial("pip install")
        assert tracker.get_decision("pip install") == "allow"

    def test_get_decision_block_when_degraded_with_block_config(self, tracker_custom_config, tmp_path):
        """Degraded patterns with degraded_action='block' return 'block'."""
        with patch("time.time", return_value=TIME_NOW):
            tracker_custom_config.record_denial("rm -rf /")
        assert tracker_custom_config.get_decision("rm -rf /") == "block"

    def test_get_decision_ask_for_nonexistent(self, tracker, tmp_path):
        """Unknown patterns return 'ask'."""
        assert tracker.get_decision("unknown") == "ask"


# ── get_stats ─────────────────────────────────────────────────────────

class TestGetStats:
    def test_get_stats_empty(self, tracker, tmp_path):
        """Empty tracker should return zeros."""
        stats = tracker.get_stats()
        assert stats["total_patterns"] == 0
        assert stats["degraded_count"] == 0
        assert stats["total_denials"] == 0
        assert stats["patterns"] == {}

    def test_get_stats_with_data(self, tracker, tmp_path):
        """Stats should reflect stored data accurately."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")      # 1 denial
            tracker.record_denial("npm install")       # 1 denial
            tracker.record_denial("pip install")       # 2nd denial
            # 3rd pip install to trigger degraded
            tracker.record_denial("pip install")       # 3rd denial -> degraded

        stats = tracker.get_stats()
        assert stats["total_patterns"] == 2
        assert stats["degraded_count"] == 1
        assert stats["total_denials"] == 4
        assert "pip install" in stats["patterns"]
        assert "npm install" in stats["patterns"]

        # Verify the entry has degraded=True
        assert stats["patterns"]["pip install"]["degraded"] is True
        assert stats["patterns"]["pip install"]["count"] == 3

    def test_get_stats_returns_patterns_reference(self, tracker, tmp_path):
        """The patterns value in stats is the internal dict (not a copy)."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        stats = tracker.get_stats()
        # Since it's the same dict, modifying the return affects internal state
        stats["patterns"]["pip install"]["count"] = 999
        assert tracker._data["pip install"]["count"] == 999


# ── match_command ─────────────────────────────────────────────────────

class TestMatchCommand:
    def test_match_exact_full_command(self, tracker, tmp_path):
        """Exact full command match should return the pattern."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install requests")

        result = tracker.match_command("pip install requests")
        assert result == "pip install requests"

    def test_match_case_insensitive_exact(self, tracker, tmp_path):
        """Exact match should be case-insensitive."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install requests")

        result = tracker.match_command("PIP INSTALL REQUESTS")
        assert result == "pip install requests"

    def test_match_substring(self, tracker, tmp_path):
        """Substring match should return the pattern key if pattern is in command."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        result = tracker.match_command("pip install requests flask")
        assert result == "pip install"

    def test_match_substring_case_insensitive(self, tracker, tmp_path):
        """Substring match should be case-insensitive."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        result = tracker.match_command("PIP INSTALL requests")
        assert result == "pip install"

    def test_no_match(self, tracker, tmp_path):
        """No match should return None."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        result = tracker.match_command("npm install")
        assert result is None

    def test_match_empty_data_returns_none(self, tracker, tmp_path):
        """Empty _data should always return None."""
        assert tracker.match_command("anything") is None

    def test_exact_takes_precedence_over_substring(self, tracker, tmp_path):
        """Exact match should be checked first and take precedence."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip")          # shorter pattern
            tracker.record_denial("pip install")  # longer exact pattern

        # Command exactly matches the longer pattern
        result = tracker.match_command("pip install")
        assert result == "pip install"

    def test_match_first_substring_pattern(self, tracker, tmp_path):
        """Substring matching returns the first pattern that is found in command."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip")
            tracker.record_denial("install")

        # "pip install" contains both "pip" and "install"
        # Should return "pip" because dict iteration order (insertion order) hits it first
        result = tracker.match_command("pip install something")
        assert result == "pip"


# ── reset_pattern ─────────────────────────────────────────────────────

class TestResetPattern:
    def test_reset_existing_pattern(self, tracker, tmp_path):
        """Reset existing pattern should clear it and return True."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")
            tracker.record_denial("pip install")
            tracker.record_denial("pip install")

        assert tracker._data["pip install"]["count"] == 3
        assert tracker._data["pip install"]["degraded"] is True

        result = tracker.reset_pattern("pip install")
        assert result is True

        entry = tracker._data["pip install"]
        assert entry["count"] == 0
        assert entry["consecutive_denials"] == 0
        assert entry["first_seen"] == 0
        assert entry["last_seen"] == 0
        assert entry["degraded"] is False

    def test_reset_nonexistent_pattern(self, tracker, tmp_path):
        """Reset non-existent pattern should return False."""
        result = tracker.reset_pattern("nonexistent")
        assert result is False

    def test_reset_persists_to_disk(self, tracker, tmp_path):
        """Reset should save changes to disk."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        tracker.reset_pattern("pip install")
        saved = json.loads(tracker.state_path.read_text())
        assert saved["pip install"]["count"] == 0
        assert saved["pip install"]["first_seen"] == 0

    def test_reset_save_oserror_silently_ignored(self, tmp_path):
        """OSError during reset save should be silently ignored."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="reset_os.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        with patch("time.time", return_value=TIME_NOW):
            dt.record_denial("pip install")

        state_path = dt.state_path
        state_path.parent.chmod(0o444)  # read-only

        try:
            result = dt.reset_pattern("pip install")
        finally:
            state_path.parent.chmod(0o755)  # restore

        assert result is True
        assert dt._data["pip install"]["count"] == 0


# ── reset_all ─────────────────────────────────────────────────────────

class TestResetAll:
    def test_reset_all_clears_data(self, tracker, tmp_path):
        """reset_all should clear all data."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")
            tracker.record_denial("npm install")

        assert len(tracker._data) == 2

        tracker.reset_all()
        assert tracker._data == {}

    def test_reset_all_saves_to_disk(self, tracker, tmp_path):
        """reset_all should save empty dict to disk."""
        with patch("time.time", return_value=TIME_NOW):
            tracker.record_denial("pip install")

        tracker.reset_all()
        saved = json.loads(tracker.state_path.read_text())
        assert saved == {}

    def test_reset_all_on_empty(self, tracker, tmp_path):
        """reset_all on an already empty tracker should work."""
        tracker.reset_all()
        assert tracker._data == {}

    def test_reset_all_save_oserror_silently_ignored(self, tmp_path):
        """OSError during reset_all save should be silently ignored."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="reset_all_os.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        with patch("time.time", return_value=TIME_NOW):
            dt.record_denial("pip install")

        state_path = dt.state_path
        state_path.parent.chmod(0o444)  # read-only

        try:
            dt.reset_all()  # Should not raise
        finally:
            state_path.parent.chmod(0o755)  # restore

        assert dt._data == {}


# ── _load ─────────────────────────────────────────────────────────────

class TestLoad:
    def test_load_file_exists_valid(self, tmp_path):
        """_load reads valid JSON dict from existing file."""
        from core.safety import DenialTracker, DenialConfig
        state_file = tmp_path / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        expected = {"cmd": {"count": 1, "degraded": False}}
        state_file.write_text(json.dumps(expected))

        cfg = DenialConfig(state_file="test.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == expected

    def test_load_file_exists_not_dict(self, tmp_path):
        """_load returns {} when JSON is not a dict."""
        from core.safety import DenialTracker, DenialConfig
        state_file = tmp_path / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps("string_value"))

        cfg = DenialConfig(state_file="test.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == {}

    def test_load_file_missing(self, tmp_path):
        """_load returns {} when file doesn't exist."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="missing.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == {}

    def test_load_json_decode_error(self, tmp_path):
        """_load returns {} on JSONDecodeError."""
        from core.safety import DenialTracker, DenialConfig
        state_file = tmp_path / "bad.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{invalid json")

        cfg = DenialConfig(state_file="bad.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        assert dt._data == {}

    def test_load_oserror(self, tmp_path):
        """_load returns {} on OSError."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="unreadable.json")
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", side_effect=OSError("Permission denied")):
                dt = DenialTracker(root_dir=tmp_path, config=cfg)
                assert dt._data == {}


# ── _save ─────────────────────────────────────────────────────────────

class TestSave:
    def test_save_creates_directory_and_file(self, tracker, tmp_path):
        """_save should create parent directories and write JSON."""
        deep_path = tmp_path / "a" / "b" / "c" / "state.json"
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file=str(deep_path.relative_to(tmp_path)))
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        dt._data = {"cmd": {"count": 1}}
        dt._save()

        assert deep_path.exists()
        saved = json.loads(deep_path.read_text())
        assert saved == {"cmd": {"count": 1}}

    def test_save_oserror_silent(self, tracker, tmp_path):
        """OSError during _save should not propagate."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(state_file="state.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)
        dt._data = {"cmd": {"count": 1}}

        # Make the parent directory unwritable
        state_path = dt.state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.parent.chmod(0o444)  # read-only

        try:
            dt._save()  # Should not raise
        finally:
            state_path.parent.chmod(0o755)  # restore

    def test_save_writes_ensure_ascii_false(self, tracker, tmp_path):
        """_save should write with ensure_ascii=False to preserve Unicode."""
        tracker._data = {"命令": {"count": 1, "message": "你好"}}
        tracker._save()
        saved = tracker.state_path.read_text(encoding="utf-8")
        assert "你好" in saved
        assert "命令" in saved


# ── Integration: end-to-end flows ─────────────────────────────────────

class TestIntegration:
    def test_full_denial_then_trust_cycle(self, tmp_path):
        """Complete cycle: denials → degrade → approval → reset."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(auto_trust_threshold=2, state_file="e2e.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        assert dt.get_decision("pip install") == "ask"

        with patch("time.time", return_value=100.0):
            dt.record_denial("pip install")
        assert dt.should_degrade("pip install") is False
        assert dt.get_decision("pip install") == "ask"

        with patch("time.time", return_value=200.0):
            dt.record_denial("pip install")
        assert dt.should_degrade("pip install") is True
        assert dt.get_decision("pip install") == "allow"

        # Approval resets consecutive but NOT degraded
        dt.record_approval("pip install")
        assert dt._data["pip install"]["consecutive_denials"] == 0
        assert dt._data["pip install"]["degraded"] is True

        # Reset pattern fully
        assert dt.reset_pattern("pip install") is True
        assert dt._data["pip install"]["count"] == 0
        assert dt._data["pip install"]["degraded"] is False

        # reset_all
        dt.reset_all()
        assert dt._data == {}
        assert dt.get_stats()["total_patterns"] == 0

    def test_multiple_patterns_independent(self, tmp_path):
        """Different patterns should degrade independently."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(auto_trust_threshold=3, state_file="multi.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        with patch("time.time", return_value=100.0):
            # Degrade pip install
            for _ in range(3):
                dt.record_denial("pip install")

            # npm install only gets 1 denial
            dt.record_denial("npm install")

        assert dt.should_degrade("pip install") is True
        assert dt.should_degrade("npm install") is False
        assert dt.get_decision("pip install") == "allow"
        assert dt.get_decision("npm install") == "ask"

        stats = dt.get_stats()
        assert stats["total_patterns"] == 2
        assert stats["degraded_count"] == 1
        assert stats["total_denials"] == 4

    def test_deny_and_then_allow_reprocess(self, tmp_path):
        """After degrading, match_command should still work."""
        from core.safety import DenialTracker, DenialConfig
        cfg = DenialConfig(auto_trust_threshold=2, state_file="match_test.json")
        dt = DenialTracker(root_dir=tmp_path, config=cfg)

        with patch("time.time", return_value=100.0):
            dt.record_denial("pip install")
            dt.record_denial("pip install")

        # Should be degraded
        assert dt.should_degrade("pip install") is True

        # match_command should find it
        matched = dt.match_command("pip install flask")
        assert matched == "pip install"

        # reset_all, then verify match returns None
        dt.reset_all()
        assert dt.match_command("pip install") is None
