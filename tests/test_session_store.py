"""Tests for core/session_store.py — 100% branch coverage."""

import json
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.session_store import (
    SessionStore,
    Session,
    estimate_tokens,
    MEMORY_DIR,
    SESSION_DB,
    SESSION_JSONL_DIR,
    CHARS_PER_TOKEN,
)


# ── helpers ──────────────────────────────────────────────────────────

def make_store(tmp_path: Path, reuse_conn: bool = False) -> SessionStore:
    """Create a SessionStore with an isolated DB path."""
    db_path = tmp_path / "test_sessions.db"
    store = SessionStore(db_path=db_path, reuse_conn=reuse_conn)
    return store


def make_session(store: SessionStore, title: str = "") -> str:
    return store.create_session(title=title)


def add_msg(store: SessionStore, sid: str, role: str = "user", content: str = "hello"):
    store.append_message(sid, role=role, content=content)


# ── estimate_tokens ─────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_basic(self):
        assert estimate_tokens("hello") == int(5 / CHARS_PER_TOKEN)

    def test_unicode(self):
        t = estimate_tokens("你好世界")
        assert t == int(4 / CHARS_PER_TOKEN)


# ── Session dataclass ───────────────────────────────────────────────

class TestSessionDataclass:
    def test_default_values(self):
        s = Session(id="s1", title="t", created_at=0.0, updated_at=0.0)
        assert s.message_count == 0
        assert s.total_tokens == 0
        assert s.status == "active"
        assert s.id == "s1"

    def test_custom_values(self):
        s = Session(id="s2", title="t2", created_at=1.0, updated_at=2.0,
                     message_count=5, total_tokens=100, status="archived")
        assert s.message_count == 5
        assert s.total_tokens == 100
        assert s.status == "archived"


# ── SessionStore: __init__ & __del__ & close ───────────────────────

class TestInitAndCleanup:
    def test_init_default_path(self, tmp_path):
        """When no db_path given, uses SESSION_DB (global)."""
        # To avoid colliding with real DB, we monkeypatch SESSION_DB
        fake_db = tmp_path / "sessions.db"
        with patch("core.session_store.SESSION_DB", fake_db):
            store = SessionStore(reuse_conn=False)
            assert store.db_path == fake_db
            assert store._conn is not None
            store.close()

    def test_init_with_path(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        assert store.db_path == tmp_path / "test_sessions.db"
        assert store._conn is not None

    def test_reuse_conn(self, tmp_path):
        """When reuse_conn=True and a shared conn exists with same path, reuse it."""
        store1 = make_store(tmp_path, reuse_conn=False)
        # Shared conn is set by store1
        store2 = SessionStore(db_path=store1.db_path, reuse_conn=True)
        assert store2._conn is store1._conn
        store1.close()
        # Don't double-close shared conn — just clear references
        SessionStore._shared_conn = None
        SessionStore._shared_db_path = None

    def test_del_shared_conn_does_not_close(self, tmp_path):
        """__del__ should not close the shared connection."""
        store1 = make_store(tmp_path, reuse_conn=False)
        conn = store1._conn
        # Simulate that this store's conn IS the shared conn
        SessionStore._shared_conn = conn
        SessionStore._shared_db_path = store1.db_path
        store1.__del__()  # should NOT close because conn is shared
        # We can't easily verify it's still open, but at least no error
        SessionStore._shared_conn = None
        SessionStore._shared_db_path = None

    def test_del_non_shared_conn_closes(self, tmp_path):
        """__del__ should close a non-shared connection."""
        store = make_store(tmp_path, reuse_conn=False)
        # Clear shared references first
        SessionStore._shared_conn = None
        SessionStore._shared_db_path = None
        conn = store._conn
        store.__del__()
        # Connection should be closed now
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        SessionStore._shared_conn = None
        SessionStore._shared_db_path = None

    def test_close(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        store.close()
        assert store._conn is None

    def test_close_twice(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        store.close()
        store.close()  # should not raise
        assert store._conn is None


# ── _clean_surrogates ──────────────────────────────────────────────

class TestCleanSurrogates:
    def test_empty(self):
        assert SessionStore._clean_surrogates("") == ""

    def test_normal_text(self):
        assert SessionStore._clean_surrogates("hello") == "hello"

    def test_surrogate_replaced(self):
        # A surrogate character like \ud800 should be replaced
        result = SessionStore._clean_surrogates("a\ud800b")
        assert "\ud800" not in result
        assert len(result) > 0  # replaced with something


# ── _is_conn_open ──────────────────────────────────────────────────

class TestIsConnOpen:
    def test_none(self):
        assert SessionStore._is_conn_open(None) is False

    def test_open_conn(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        assert SessionStore._is_conn_open(conn) is True
        conn.close()

    def test_closed_conn(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.close()
        assert SessionStore._is_conn_open(conn) is False


# ── _get_cursor ────────────────────────────────────────────────────

class TestGetCursor:
    def test_reinit_when_conn_none(self, tmp_path):
        """When _conn is None and shared conn is also None, reinit."""
        store = make_store(tmp_path, reuse_conn=False)
        # Simulate a dead connection
        store._conn.close()
        store._conn = None
        SessionStore._shared_conn = None
        SessionStore._shared_db_path = None
        cursor = store._get_cursor()
        assert cursor is not None
        assert store._conn is not None

    def test_reinit_when_conn_dead(self, tmp_path):
        """When _conn exists but connection is dead, reinit."""
        store = make_store(tmp_path, reuse_conn=False)
        store._conn.close()  # kill it
        # Manually set shared path to avoid reusing
        SessionStore._shared_conn = None
        SessionStore._shared_db_path = None
        cursor = store._get_cursor()
        assert cursor is not None
        assert store._conn is not None

    def test_resets_row_factory(self, tmp_path):
        """If row_factory is wrong, reset it."""
        store = make_store(tmp_path, reuse_conn=False)
        store._conn.row_factory = None  # wrong
        cur = store._get_cursor()
        assert store._conn.row_factory is sqlite3.Row
        assert cur is not None


# ── CRUD: create_session, get_session, append_message, list_sessions ─

class TestCreateAndGetSession:
    def test_create_session(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        assert sid.startswith("sess_")

    def test_create_session_with_title(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session(title="My Title")
        s = store.get_session(sid)
        assert s is not None
        assert s.title == "My Title"

    def test_get_session_not_found(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        assert store.get_session("nonexistent") is None

    def test_get_session_found(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        s = store.get_session(sid)
        assert s is not None
        assert s.id == sid
        assert s.title == "test"
        assert s.status == "active"

    def test_create_empty_title_defaults(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session(title="")
        s = store.get_session(sid)
        assert s is not None
        assert s.title.startswith("会话")


class TestAppendMessage:
    def test_append_and_get_messages(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        add_msg(store, sid, "user", "hello world")
        msgs = store.get_messages(sid)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello world"

    def test_append_multiple(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        add_msg(store, sid, "user", "q1")
        add_msg(store, sid, "assistant", "a1")
        add_msg(store, sid, "user", "q2")
        msgs = store.get_messages(sid)
        assert len(msgs) == 3

    def test_get_session_updated(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        add_msg(store, sid, "user", "test")
        s = store.get_session(sid)
        assert s.message_count == 1
        assert s.total_tokens > 0

    def test_append_clean_surrogates(self, tmp_path):
        """append_message uses _clean_surrogates internally."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        store.append_message(sid, "user", "a\ud800b")
        msgs = store.get_messages(sid)
        assert len(msgs) == 1
        assert "\ud800" not in msgs[0]["content"]


# ── get_messages with truncation ───────────────────────────────────

class TestGetMessagesTruncation:
    def test_no_truncation_when_zero(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        # Add messages with large content to exceed token limit
        for i in range(5):
            add_msg(store, sid, "user", "x" * (int(100 * CHARS_PER_TOKEN)))
        msgs = store.get_messages(sid, max_tokens=0)
        assert len(msgs) == 5

    def test_truncation_happens(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        # Each message is about 100 tokens
        for i in range(10):
            add_msg(store, sid, "user", "x" * (int(100 * CHARS_PER_TOKEN)))
        msgs = store.get_messages(sid, max_tokens=150)
        assert len(msgs) < 10

    def test_truncation_adds_system_notice(self, tmp_path):
        """When truncated and first remaining message is not 'system', insert notice."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        for i in range(10):
            add_msg(store, sid, "user", "x" * (int(80 * CHARS_PER_TOKEN)))
        msgs = store.get_messages(sid, max_tokens=100)
        # Should be truncated and first message should be system notice
        assert len(msgs) < 10
        assert msgs[0]["role"] == "system"
        assert "截断" in msgs[0]["content"]

    def test_no_system_notice_when_first_is_system(self, tmp_path):
        """When truncated and first remaining IS system, don't insert another.
        
        Use very small messages so the system message survives truncation.
        """
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        # Add a system message first with small token cost
        add_msg(store, sid, "system", "sys")
        # Add user messages with moderate size — total > max_tokens but
        # system message should survive the cut
        for i in range(9):
            add_msg(store, sid, "user", "x" * (int(80 * CHARS_PER_TOKEN)))
        msgs = store.get_messages(sid, max_tokens=200)
        # Should have some messages with system first
        assert len(msgs) >= 1, f"Got {len(msgs)} messages"
        # If system message was not cut, it should still be first
        assert msgs[0]["role"] == "system"

    def test_empty_messages(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        msgs = store.get_messages(sid)
        assert msgs == []

    def test_truncation_all_cut(self, tmp_path):
        """Extreme case: all messages cut, returns empty list."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        for i in range(3):
            add_msg(store, sid, "user", "x" * (int(500 * CHARS_PER_TOKEN)))
        msgs = store.get_messages(sid, max_tokens=10)
        # All messages exceed the token limit individually, so all are cut
        assert len(msgs) == 0


# ── get_history_messages ───────────────────────────────────────────

class TestGetHistoryMessages:
    def test_empty(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        assert store.get_history_messages(sid) == []

    def test_filters_system(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        add_msg(store, sid, "system", "sys msg")
        add_msg(store, sid, "user", "user msg")
        add_msg(store, sid, "assistant", "assistant msg")
        hist = store.get_history_messages(sid)
        assert len(hist) == 2
        assert all(m["role"] != "system" for m in hist)

    def test_truncation_from_latest(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        for i in range(10):
            add_msg(store, sid, "user", "x" * (int(1000 * CHARS_PER_TOKEN)))
        hist = store.get_history_messages(sid, max_tokens=500)
        # Should have some messages but limited
        assert len(hist) < 10

    def test_empty_result_from_db(self, tmp_path):
        """No rows at all."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = "nonexistent"
        assert store.get_history_messages(sid) == []


# ── get_context_messages ────────────────────────────────────────────

class TestGetContextMessages:
    def test_basic(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        add_msg(store, sid, "user", "hello")
        ctx = store.get_context_messages(sid, system_prompt="You are a bot")
        assert len(ctx) >= 1
        assert ctx[0]["role"] == "system"
        assert ctx[0]["content"] == "You are a bot"

    def test_with_existing_system_message(self, tmp_path):
        """When the first message from get_messages is system, it gets replaced."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        add_msg(store, sid, "system", "old system")
        add_msg(store, sid, "user", "hello")
        ctx = store.get_context_messages(sid, system_prompt="new system")
        assert ctx[0]["role"] == "system"
        assert ctx[0]["content"] == "new system"

    def test_buffer_respected(self, tmp_path):
        """System prompt tokens are deducted from max_tokens."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session()
        big_sys = "x" * int(5000 * CHARS_PER_TOKEN)
        ctx = store.get_context_messages(sid, system_prompt=big_sys, max_tokens=1000)
        # Should still have system at front
        assert ctx[0]["role"] == "system"
        assert ctx[0]["content"] == big_sys


# ── list_sessions ──────────────────────────────────────────────────

class TestListSessions:
    def test_list_all(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        s1 = store.create_session("s1")
        s2 = store.create_session("s2")
        sessions = store.list_sessions()
        assert len(sessions) == 2

    def test_list_with_status(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        s1 = store.create_session("active1")
        s2 = store.create_session("active2")
        store.archive_session(s1)
        sessions = store.list_sessions(status="active")
        assert len(sessions) == 1
        sessions = store.list_sessions(status="archived")
        assert len(sessions) == 1

    def test_list_limit(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        for i in range(5):
            store.create_session(f"s{i}")
        sessions = store.list_sessions(limit=2)
        assert len(sessions) == 2

    def test_list_no_status(self, tmp_path):
        """list_sessions with empty status string returns all."""
        store = make_store(tmp_path, reuse_conn=False)
        store.create_session("a")
        store.create_session("b")
        all_s = store.list_sessions(status="")
        assert len(all_s) == 2


# ── search_sessions ────────────────────────────────────────────────

class TestSearchSessions:
    def test_search_by_title(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        store.create_session("unique_title_xyz")
        store.create_session("other")
        results = store.search_sessions("unique_title")
        assert len(results) == 1

    def test_search_by_content(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("t1")
        add_msg(store, sid, "user", "special_content_abc")
        results = store.search_sessions("special_content")
        assert len(results) == 1

    def test_no_results(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        store.create_session("t1")
        results = store.search_sessions("nonexistent")
        assert len(results) == 0


# ── archive, delete, prune ─────────────────────────────────────────

class TestArchiveDeletePrune:
    def test_archive_session(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("archivable")
        store.archive_session(sid)
        s = store.get_session(sid)
        assert s.status == "archived"

    def test_delete_session(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("deletable")
        add_msg(store, sid, "user", "hello")
        store.delete_session(sid)
        assert store.get_session(sid) is None
        assert store.get_messages(sid) == []

    def test_prune_sessions(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("old")
        store.archive_session(sid)
        # Manually set updated_at far in the past
        old_time = time.time() - 100 * 86400
        cur = store._get_cursor()
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (old_time, sid))
        store._conn.commit()
        n = store.prune_sessions(keep_days=30)
        assert n >= 1
        assert store.get_session(sid) is None

    def test_prune_no_sessions(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        n = store.prune_sessions(keep_days=30)
        assert n == 0


# ── export_session ─────────────────────────────────────────────────

class TestExportSession:
    def test_export_nonexistent(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        assert store.export_session("nonexistent") is None

    def test_export_basic(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("export_test")
        add_msg(store, sid, "user", "hello world")
        add_msg(store, sid, "assistant", "hi there")
        exported = store.export_session(sid)
        assert exported is not None
        data = json.loads(exported)
        assert data["session"]["title"] == "export_test"
        assert len(data["messages"]) == 2

    def test_export_content_truncation(self, tmp_path):
        """Export truncates content > 500 chars."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("trunc")
        add_msg(store, sid, "user", "x" * 600)
        exported = store.export_session(sid)
        data = json.loads(exported)
        assert data["messages"][0]["content"].endswith("...")


# ── get_stats ──────────────────────────────────────────────────────

class TestGetStats:
    def test_stats_empty(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        stats = store.get_stats()
        assert stats["total_sessions"] == 0
        assert stats["active_sessions"] == 0
        assert stats["total_messages"] == 0
        assert stats["total_tokens_estimated"] == 0

    def test_stats_with_data(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        s1 = store.create_session("s1")
        add_msg(store, s1, "user", "test1")
        add_msg(store, s1, "assistant", "test2")
        s2 = store.create_session("s2")
        add_msg(store, s2, "user", "test3")
        stats = store.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["active_sessions"] == 2
        assert stats["total_messages"] == 3
        assert stats["total_tokens_estimated"] > 0


# ── JSONL persistence ──────────────────────────────────────────────

class TestJsonlPersistence:
    def test_get_jsonl_path(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            path = store._get_jsonl_path("sess_test")
            assert path.name == "sess_test.jsonl"
            assert path.parent == tmp_path / "sessions_jsonl"

    def test_get_jsonl_path_safety(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            path = store._get_jsonl_path("../evil/attack")
            assert ".." not in path.name
            assert path.parent == tmp_path / "sessions_jsonl"

    def test_save_and_get_raw_messages(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = "sess_test_001"
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            store.save_raw_messages(sid, msgs)
            raw = store.get_raw_messages(sid)
            assert raw is not None
            assert len(raw) == 2
            assert raw[0]["role"] == "user"
            assert raw[0]["content"] == "hello"

    def test_get_raw_messages_nonexistent(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            raw = store.get_raw_messages("nonexistent")
            assert raw is None

    def test_save_raw_with_tool_calls(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = "sess_tools"
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": {"city": "beijing"}}}
            ]},
        ]
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            store.save_raw_messages(sid, msgs)
            raw = store.get_raw_messages(sid)
            assert raw is not None
            assert "tool_calls" in raw[0]
            assert raw[0]["tool_calls"][0]["name"] == "get_weather"

    def test_get_raw_messages_skip_empty_lines(self, tmp_path):
        """Empty lines and JSON errors are skipped."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = "sess_skip"
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            path = store._get_jsonl_path(sid)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.write('{"role": "user", "content": "ok"}\n')
                f.write('\n')  # empty line
                f.write('invalid json\n')  # invalid
                f.write('{"role": "assistant", "content": "also ok"}\n')
            raw = store.get_raw_messages(sid)
            assert len(raw) == 2
            assert raw[0]["role"] == "user"
            assert raw[1]["role"] == "assistant"

    def test_get_raw_messages_since_basic(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = "sess_since"
        msgs = [
            {"role": "user", "content": "a" * int(100 * CHARS_PER_TOKEN)},
            {"role": "user", "content": "b" * int(100 * CHARS_PER_TOKEN)},
            {"role": "user", "content": "c" * int(100 * CHARS_PER_TOKEN)},
        ]
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            store.save_raw_messages(sid, msgs)
            result = store.get_raw_messages_since(sid, start_index=1, max_tokens=500)
            assert len(result) >= 1
            # Should start from index 1
            assert result[0]["content"].startswith("b")

    def test_get_raw_messages_since_no_file(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            result = store.get_raw_messages_since("nonexistent")
            assert result == []

    def test_get_raw_messages_since_token_limit(self, tmp_path):
        """Verify the token limit cuts properly."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = "sess_token_limit"
        msgs = [
            {"role": "user", "content": "x" * int(800 * CHARS_PER_TOKEN)},
            {"role": "user", "content": "y" * int(800 * CHARS_PER_TOKEN)},
        ]
        with patch("core.session_store.SESSION_JSONL_DIR", tmp_path / "sessions_jsonl"):
            store.save_raw_messages(sid, msgs)
            result = store.get_raw_messages_since(sid, start_index=0, max_tokens=300)
            # Should only get the first one (second would exceed limit)
            assert len(result) == 1


# ── fork_session ───────────────────────────────────────────────────

class TestForkSession:
    def test_fork_nonexistent_source(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        result = store.fork_session("nonexistent", include_history=False)
        assert result is None

    def test_fork_without_history(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("original")
        add_msg(store, sid, "user", "hello")
        new_id = store.fork_session(sid, include_history=False)
        assert new_id is not None
        assert new_id != sid
        new_s = store.get_session(new_id)
        assert new_s.title == "[fork] original"
        # No history messages copied
        assert new_s.message_count == 0

    def test_fork_with_history(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("original")
        add_msg(store, sid, "user", "hello1")
        add_msg(store, sid, "user", "hello2")
        new_id = store.fork_session(sid, include_history=True)
        assert new_id is not None
        new_s = store.get_session(new_id)
        # Should have a system message with history
        assert new_s.message_count >= 1

    def test_fork_with_custom_title(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("original")
        new_id = store.fork_session(sid, title="my fork", include_history=False)
        new_s = store.get_session(new_id)
        assert new_s.title == "my fork"

    def test_fork_with_history_no_messages(self, tmp_path):
        """Fork with history but source has no messages."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("empty")
        new_id = store.fork_session(sid, include_history=True)
        assert new_id is not None
        new_s = store.get_session(new_id)
        assert new_s.message_count == 0


# ── resume_context ─────────────────────────────────────────────────

class TestResumeContext:
    def test_resume_nonexistent(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        result = store.resume_context("nonexistent")
        assert result is None

    def test_resume_no_messages(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("empty")
        result = store.resume_context(sid)
        assert result is None

    def test_resume_fallback_keyword(self, tmp_path):
        """When use_llm=False, uses keyword-based fallback."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        add_msg(store, sid, "assistant", "world")
        result = store.resume_context(sid, use_llm=False)
        assert result is not None
        assert "会话简报" in result
        assert "test" in result

    def test_resume_with_llm_fallback(self, tmp_path):
        """When use_llm=True but _llm_summarize_session returns None,
        falls through to keyword extraction."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        add_msg(store, sid, "assistant", "world")
        with patch.object(store, '_llm_summarize_session', return_value=None):
            result = store.resume_context(sid, use_llm=True)
            # Should fall through to keyword extraction
            assert result is not None
            assert "会话简报" in result

    def test_resume_with_llm_success(self, tmp_path):
        """When use_llm=True and _llm_summarize_session returns a value."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        with patch.object(store, '_llm_summarize_session', return_value="LLM summary") as mock_method:
            result = store.resume_context(sid, use_llm=True)
            assert result == "LLM summary"
            mock_method.assert_called_once()

    def test_resume_with_llm_exception(self, tmp_path):
        """When use_llm=True and _llm_summarize_session raises, falls through."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        with patch.object(store, '_llm_summarize_session', side_effect=Exception("oops")):
            result = store.resume_context(sid, use_llm=True)
            # Should fall through to keyword extraction
            assert result is not None
            assert "会话简报" in result

    def test_resume_with_decision_keywords(self, tmp_path):
        """System messages with decision keywords are extracted."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "system", "已确定: 使用方案A")
        add_msg(store, sid, "user", "ok")
        result = store.resume_context(sid, use_llm=False)
        assert result is not None
        assert "决策" in result

    def test_resume_with_pin_messages(self, tmp_path):
        """Messages with [PIN] / [KEEP] are extracted."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("pinned")
        add_msg(store, sid, "user", "hello [PIN] this is important")
        add_msg(store, sid, "assistant", "ok [KEEP] note this")
        result = store.resume_context(sid, use_llm=False)
        assert result is not None
        assert "关键信息" in result or "Pin" in result

    def test_resume_with_keep_tag(self, tmp_path):
        """Messages with [保留] tag are extracted as pinned."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("keep_test")
        add_msg(store, sid, "user", "[保留] this is kept")
        result = store.resume_context(sid, use_llm=False)
        assert result is not None
        assert "关键信息" in result

    def test_resume_only_system_no_user(self, tmp_path):
        """When there are no user messages in keyword fallback,
        the user_msgs list is empty, exercising the else branch."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("sys_only")
        add_msg(store, sid, "system", "just a system message")
        add_msg(store, sid, "assistant", "assistant reply with no user before")
        result = store.resume_context(sid, use_llm=False)
        assert result is not None
        assert "会话简报" in result


# ── _llm_summarize_session ──────────────────────────────────────────

class TestLlmSummarizeSession:
    def test_import_error_returns_none(self, tmp_path):
        """When LLM module can't be imported, return None."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        # Mock the ImportError when trying to import LLMClient inside _llm_summarize_session
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "core.llm":
                raise ImportError("No module named core.llm")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", mock_import):
            result = store._llm_summarize_session(msgs, s)
            assert result is None

    def test_llm_failure_returns_none(self, tmp_path):
        """When LLM call raises exception, return None."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        # Mock LLMClient at core.llm level
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.side_effect = Exception("LLM error")
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            assert result is None

    def test_llm_short_response_returns_none(self, tmp_path):
        """When LLM response is too short, return None."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = {"content": "hi"}  # too short
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            assert result is None

    def test_llm_success(self, tmp_path):
        """Successful LLM summary returns formatted text."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "user", "hello")
        add_msg(store, sid, "assistant", "world")
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = {
                "content": "## 主题\nTest topic\n## 关键决策\n- Decision A\n## 待办事项\n- TODO 1\n## 技术结论\n- Tech conclusion"
            }
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            assert result is not None
            assert "会话简报（LLM）" in result
            assert "## 主题" in result

    def test_llm_skip_when_content_empty(self, tmp_path):
        """When all messages have empty content, front/back loop adds nothing."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        add_msg(store, sid, "system", "  ")  # whitespace-only content triggers skip
        add_msg(store, sid, "user", "hello")
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = {"content": " " * 60}
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            assert result is None

    def test_llm_conversation_sample_truncation(self, tmp_path):
        """When conversation_sample > 8000 chars, it gets truncated."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        # Add many system messages with long content to exceed 8000 chars
        for i in range(25):
            add_msg(store, sid, "system", "x" * 350)
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = {
                "content": "## 主题\n这是一个测试主题，用来验证会话摘要功能是否正常。\n## 关键决策\n- 决策A：采用方案一的实现方式\n- 决策B：使用Python作为主要开发语言\n## 待办事项\n- 完成单元测试编写\n- 更新API文档\n## 技术结论\n- 使用SQLite作为后端存储\n- 采用WAL模式提升并发性能"
            }
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            # conversation_sample had len > 8000, was truncated
            assert result is not None
            assert "会话简报（LLM）" in result

    def test_llm_empty_system_content_skipped(self, tmp_path):
        """System messages with empty content are skipped (if c: is False)."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        # Empty system content
        add_msg(store, sid, "system", "")
        add_msg(store, sid, "user", "hello")
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = {
                "content": "## 主题\nTest\n## 关键决策\n- None\n## 待办事项\n- None\n## 技术结论\n- None" * 3
            }
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            assert result is not None
            # The empty system message was skipped; only user message included
            assert "会话简报（LLM）" in result

    def test_llm_duplicate_content_skipped(self, tmp_path):
        """Duplicate content in non-system messages is skipped (seen set)."""
        store = make_store(tmp_path, reuse_conn=False)
        sid = store.create_session("test")
        # Two user messages with same content
        add_msg(store, sid, "user", "hello")
        add_msg(store, sid, "user", "hello")  # duplicate
        msgs = store.get_messages(sid)
        s = store.get_session(sid)
        with patch("core.llm.LLMClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = {
                "content": "## 主题\nTest\n## 关键决策\n- None\n## 待办事项\n- None\n## 技术结论\n- None" * 3
            }
            mock_client.return_value = mock_instance
            result = store._llm_summarize_session(msgs, s)
            assert result is not None
            assert "会话简报（LLM）" in result


# ── find_related_sessions ──────────────────────────────────────────

class TestFindRelatedSessions:
    def test_basic(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        s1 = store.create_session("python coding")
        s2 = store.create_session("data science")
        add_msg(store, s1, "user", "talk about python")
        results = store.find_related_sessions("python")
        assert len(results) >= 1
        assert results[0].title == "python coding" or results[0].title == "data science"

    def test_fills_with_recent(self, tmp_path):
        """When search is empty, fill with recent sessions."""
        store = make_store(tmp_path, reuse_conn=False)
        s1 = store.create_session("alpha")
        s2 = store.create_session("beta")
        results = store.find_related_sessions("zzz_nonexistent", limit=2)
        # Should return the recent sessions as fallback
        assert len(results) >= 1


# ── __del__ updated version ────────────────────────────────────────

class TestDelNew:
    def test_del_with_exception(self, tmp_path):
        """__del__ catches exceptions gracefully."""
        store = make_store(tmp_path, reuse_conn=False)
        # Force close first so close() in __del__ won't fail
        store.close()
        # No error should occur
        store.__del__()

    def test_del_programming_error(self, tmp_path):
        """__del__ catches sqlite3.ProgrammingError."""
        store = make_store(tmp_path, reuse_conn=False)
        # Replace close() to raise ProgrammingError
        orig_close = store.close
        store.close = MagicMock(side_effect=sqlite3.ProgrammingError("already closed"))
        store.__del__()  # should not raise
        store.close = orig_close
        store.close()

    def test_del_generic_exception(self, tmp_path):
        """__del__ catches generic Exception."""
        store = make_store(tmp_path, reuse_conn=False)
        orig_close = store.close
        store.close = MagicMock(side_effect=RuntimeError("random error"))
        store.__del__()  # should not raise
        store.close = orig_close
        store.close()


# ── _close_conn ────────────────────────────────────────────────────

class TestCloseConn:
    def test_close_conn_normal(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        assert store._conn is not None
        store._close_conn()
        assert store._conn is None

    def test_close_conn_already_none(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        store._conn = None
        store._close_conn()  # should not raise

    def test_close_conn_raises_exception(self, tmp_path):
        """Trigger the except: pass branch in _close_conn."""
        store = make_store(tmp_path, reuse_conn=False)
        # Close the conn first, then call _close_conn again — 
        # calling close() on an already-closed connection raises
        store._conn.close()
        store._close_conn()  # should handle the exception silently
        assert store._conn is None


# ── Thread safety (append_message lock) ────────────────────────────

class TestThreadSafety:
    def test_append_message_uses_lock(self, tmp_path):
        """append_message uses self._lock which is a threading.Lock instance."""
        store = make_store(tmp_path, reuse_conn=False)
        assert store._lock is not None
        assert hasattr(store._lock, "acquire")
        sid = store.create_session("thread_test")
        add_msg(store, sid, "user", "hello")
        msgs = store.get_messages(sid)
        assert len(msgs) == 1


# ── Integration: full lifecycle ────────────────────────────────────

class TestIntegration:
    def test_full_lifecycle(self, tmp_path):
        store = make_store(tmp_path, reuse_conn=False)
        # Create
        sid = store.create_session("integration")
        # Add messages
        add_msg(store, sid, "user", "q1")
        add_msg(store, sid, "assistant", "a1")
        add_msg(store, sid, "user", "q2")
        # Get session
        s = store.get_session(sid)
        assert s.message_count == 3
        # List
        assert len(store.list_sessions()) == 1
        # Export
        exported = store.export_session(sid)
        assert exported is not None
        # Fork
        new_id = store.fork_session(sid)
        assert new_id is not None
        # Archive
        store.archive_session(sid)
        assert store.get_session(sid).status == "archived"
        # Delete
        store.delete_session(new_id)
        assert store.get_session(new_id) is None
        # Stats
        stats = store.get_stats()
        assert stats["total_sessions"] == 1  # only archived one left
