"""
tests/test_turn_diff_tracker.py — TurnDiffTracker 测试
"""

import pytest
from core.turn_diff_tracker import TurnDiffTracker, FileChange


class TestFileChange:

    def test_create(self):
        c = FileChange("/tmp/test.txt", "modified", "old", "new content", "write_file")
        assert c.path == "/tmp/test.txt"
        assert c.change_type == "modified"
        assert c.tool_name == "write_file"

    def test_significant_if_deleted(self):
        c = FileChange("/tmp/x.txt", "deleted")
        assert c.is_significant() is True

    def test_significant_if_long_content(self):
        c = FileChange("/tmp/x.txt", "modified", new_preview="a" * 100)
        assert c.is_significant() is True

    def test_not_significant_if_short(self):
        c = FileChange("/tmp/x.txt", "modified", new_preview="ok")
        assert c.is_significant() is False

    def test_to_dict(self):
        c = FileChange("/tmp/x.txt", "created", tool_name="touch")
        d = c.to_dict()
        assert d["path"] == "/tmp/x.txt"
        assert d["type"] == "created"
        assert d["tool"] == "touch"


class TestTurnDiffTracker:

    def setup_method(self):
        self.tracker = TurnDiffTracker()

    def test_start_turn(self):
        self.tracker.start_turn()
        assert self.tracker._turn_count == 1

    def test_record_change(self):
        self.tracker.start_turn()
        self.tracker.record_change("/tmp/test.txt", "modified",
                                  old_preview="", new_preview="new content",
                                  tool_name="write_file")
        assert self.tracker.has_changes() is True
        assert len(self.tracker.get_changes()) == 1

    def test_no_changes_initially(self):
        assert self.tracker.has_changes() is False
        assert self.tracker.should_update_memory() is False

    def test_reset(self):
        self.tracker.start_turn()
        self.tracker.record_change("/tmp/x.txt", "created", new_preview="hello")
        assert self.tracker.has_changes() is True
        self.tracker.reset()
        assert self.tracker.has_changes() is False

    def test_should_update_memory_significant(self):
        self.tracker.start_turn()
        self.tracker.record_change("/tmp/x.txt", "modified",
                                  new_preview="a" * 100)
        assert self.tracker.should_update_memory() is True

    def test_should_not_update_memory_insignificant(self):
        self.tracker.start_turn()
        self.tracker.record_change("/tmp/x.txt", "modified",
                                  new_preview="ok")
        assert self.tracker.should_update_memory() is False

    def test_disabled(self):
        self.tracker.set_enabled(False)
        self.tracker.start_turn()
        self.tracker.record_change("/tmp/x.txt", "modified",
                                  new_preview="a" * 100)
        assert self.tracker.should_update_memory() is False

    def test_get_change_summary(self):
        self.tracker.start_turn()
        self.tracker.record_change("/tmp/a.txt", "created")
        self.tracker.record_change("/tmp/b.txt", "modified")
        self.tracker.record_change("/tmp/c.txt", "deleted")
        summary = self.tracker.get_change_summary()
        assert "3 次" in summary
        assert "created" in summary
        assert "deleted" in summary

    def test_record_terminal_output_git_diff(self):
        self.tracker.start_turn()
        output = "diff --git a/file.py b/file.py\nindex abc..def\n--- a/file.py\n+++ b/file.py"
        self.tracker.record_terminal_output("git diff", output)
        assert self.tracker.has_changes() is True
