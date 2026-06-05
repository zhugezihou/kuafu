"""
tests/test_rollout_log.py — Rollout 事件日志测试
"""

import json
import tempfile
from pathlib import Path
import pytest

from core.rollout_log import RolloutLog, RolloutEvent


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def log(tmp_dir):
    return RolloutLog(rollout_dir=tmp_dir / "rollout")


class TestRolloutEvent:

    def test_create_event_defaults(self):
        e = RolloutEvent(type="meta", session_id="sess_1")
        assert e.type == "meta"
        assert e.session_id == "sess_1"
        assert e.timestamp > 0
        assert e.data == {}

    def test_create_event_with_data(self):
        e = RolloutEvent("user_message", "sess_1",
                         data={"role": "user", "content": "hello"})
        assert e.data["role"] == "user"
        assert e.data["content"] == "hello"

    def test_to_json(self):
        e = RolloutEvent("meta", "sess_1", data={"name": "test"})
        j = json.loads(e.to_json())
        assert j["type"] == "meta"
        assert j["session_id"] == "sess_1"
        assert j["data"]["name"] == "test"


class TestRolloutLogWrite:

    def test_append_creates_file(self, log):
        log.append(RolloutEvent("meta", "sess_1", data={"name": "test"}))
        path = log._get_path("sess_1")
        assert path.exists()

    def test_append_raw(self, log):
        log.append_raw("sess_1", "user_message", {"role": "user", "content": "hi"})
        assert log.count("sess_1") == 1

    def test_multiple_events(self, log):
        for i in range(5):
            log.append_raw("sess_1", "meta", {"seq": i})
        assert log.count("sess_1") == 5


class TestRolloutLogQuery:

    def test_query_all(self, log):
        for i in range(10):
            log.append_raw("sess_1", "meta", {"seq": i})
        events = log.query("sess_1", limit=5)
        assert len(events) == 5
        assert events[0].data["seq"] == 0
        assert events[4].data["seq"] == 4

    def test_query_with_offset(self, log):
        for i in range(10):
            log.append_raw("sess_1", "meta", {"seq": i})
        events = log.query("sess_1", limit=3, offset=5)
        assert len(events) == 3
        assert events[0].data["seq"] == 5
        assert events[2].data["seq"] == 7

    def test_query_by_type(self, log):
        log.append_raw("sess_1", "meta")
        log.append_raw("sess_1", "user_message", {"text": "hi"})
        log.append_raw("sess_1", "tool_call", {"tool": "ls"})
        log.append_raw("sess_1", "user_message", {"text": "bye"})

        user_msgs = log.query_by_type("sess_1", "user_message")
        assert len(user_msgs) == 2
        assert user_msgs[0].data["text"] == "hi"

    def test_empty_session(self, log):
        events = log.query("nonexistent", limit=10)
        assert events == []


class TestRolloutLogCursor:

    def test_cursor_iteration(self, log):
        for i in range(5):
            log.append_raw("sess_1", "meta", {"seq": i})

        events = list(log.cursor("sess_1"))
        assert len(events) == 5

    def test_cursor_from_offset(self, log):
        for i in range(10):
            log.append_raw("sess_1", "meta", {"seq": i})

        events = list(log.cursor("sess_1", start_from=5))
        assert len(events) == 5
        assert events[0].data["seq"] == 5


class TestRolloutLogArchive:

    def test_archive_restore(self, log, tmp_dir):
        log.append_raw("sess_1", "meta", {"name": "test"})

        assert log.archive("sess_1") is True
        assert not log._get_path("sess_1").exists()

        assert log.restore("sess_1") is True
        assert log._get_path("sess_1").exists()
        assert log.count("sess_1") == 1

    def test_archive_nonexistent(self, log):
        assert log.archive("fake") is False

    def test_list_sessions(self, log):
        log.append_raw("sess_a", "meta")
        log.append_raw("sess_b", "meta")
        sessions = log.list_sessions()
        assert "sess_a" in sessions
        assert "sess_b" in sessions


class TestRolloutLogMeta:

    def test_get_meta(self, log):
        log.append_raw("sess_1", "meta", {"name": "test session"})
        meta = log.get_meta("sess_1")
        assert meta is not None
        assert meta["name"] == "test session"

    def test_get_meta_nonexistent(self, log):
        assert log.get_meta("fake") is None


class TestEdgeCases:

    def test_safe_path(self, log):
        """session_id 中的特殊字符不导致路径穿越"""
        path = log._get_path("../evil")
        assert "../" not in str(path)
        assert "_evil" in str(path.name)

    def test_append_nonexistent(self, log):
        path = log._get_path("sess_nonexistent")
        assert not path.exists()
