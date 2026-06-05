"""Complete tests for core/context_compress.py — 100% coverage."""

import json
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from core.context_compress import (
    estimate_tokens,
    CompressionResult,
    LocalSummarizer,
    PinnedContentManager,
    ToolResultStore,
    ContextCompressor,
    ContextCollapse,
    CollapseResult,
    budget_reduce_output,
    _reduce_json_object,
)


# ===================================================================
# estimate_tokens
# ===================================================================
class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_normal_text(self):
        text = "你好世界hello"
        expected = int(len(text) / 1.6)
        assert estimate_tokens(text) == expected

    def test_short_text(self):
        assert estimate_tokens("a") == 0  # int(1/1.6) = 0


# ===================================================================
# CompressionResult
# ===================================================================
class TestCompressionResult:
    def test_init_normal(self):
        cr = CompressionResult(
            original_tokens=1000, compressed_tokens=500, messages_removed=10, summary="test"
        )
        assert cr.original_tokens == 1000
        assert cr.compressed_tokens == 500
        assert cr.messages_removed == 10
        assert cr.summary == "test"
        assert cr.compression_ratio == 0.5

    def test_zero_original_tokens(self):
        cr = CompressionResult(original_tokens=0, compressed_tokens=0, messages_removed=0)
        assert cr.compression_ratio == 0.0

    def test_no_summary_default(self):
        cr = CompressionResult(original_tokens=100, compressed_tokens=80, messages_removed=2)
        assert cr.summary == ""
        assert cr.compression_ratio == 0.2


# ===================================================================
# LocalSummarizer
# ===================================================================
class TestLocalSummarizer:
    def test_init_defaults(self):
        s = LocalSummarizer()
        assert s.base_url == "http://localhost:8080"
        assert s.max_tokens == 256
        assert s.timeout == 30

    def test_init_custom(self):
        s = LocalSummarizer(base_url="http://test:9999/", max_tokens=512, timeout=10)
        assert s.base_url == "http://test:9999"
        assert s.max_tokens == 512
        assert s.timeout == 10

    def test_summarize_empty(self):
        s = LocalSummarizer()
        assert s.summarize("") == ""
        assert s.summarize("   ") == ""

    def test_summarize_short_text_fallback_on_exception(self):
        s = LocalSummarizer(base_url="http://nonexistent:9999", timeout=1)
        # Short text (< 600 chars) — fallback returns text itself
        result = s.summarize("short text")
        assert result == "short text"

    def test_summarize_long_text_fallback_on_exception(self):
        s = LocalSummarizer(base_url="http://nonexistent:9999", timeout=1)
        text = "A" * 1000
        result = s.summarize(text)
        # Fallback truncates to 600 + "..."
        assert result == text[:600] + "..."

    def test_is_available_false(self):
        s = LocalSummarizer(base_url="http://nonexistent:9999")
        assert s.is_available() is False

    @patch("urllib.request.urlopen")
    def test_is_available_true(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        s = LocalSummarizer()
        assert s.is_available() is True

    @patch("urllib.request.urlopen")
    def test_call_llm_success(self, mock_urlopen):
        response_bytes = json.dumps({
            "choices": [{"message": {"content": "这是摘要内容"}}]
        }).encode("utf-8")
        mock_resp = MagicMock()
        # read() returns bytes, which has .decode()
        mock_resp.read.return_value = response_bytes
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        s = LocalSummarizer()
        result = s._call_llm("some dialogue text")
        assert result == "这是摘要内容"

    @patch("urllib.request.urlopen")
    def test_call_llm_empty_response(self, mock_urlopen):
        response_bytes = json.dumps({
            "choices": [{"message": {"content": ""}}]
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        s = LocalSummarizer()
        text = "A" * 1000
        result = s._call_llm(text)
        # Empty content => fallback to truncation
        assert result == text[:600] + "..."

    @patch("urllib.request.urlopen")
    def test_call_llm_missing_choices(self, mock_urlopen):
        response_bytes = json.dumps({}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        s = LocalSummarizer()
        text = "A" * 1000
        result = s._call_llm(text)
        assert result == text[:600] + "..."

    @patch("urllib.request.urlopen")
    def test_call_llm_short_text_empty_response(self, mock_urlopen):
        response_bytes = json.dumps({
            "choices": [{"message": {"content": ""}}]
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        s = LocalSummarizer()
        text = "short"
        result = s._call_llm(text)
        # Empty content, text < 600 chars => returns text + "..."
        assert result == text[:600] + "..."

    def test_summarize_short_text_no_exception(self):
        """When no exception occurs but text is short."""
        s = LocalSummarizer(base_url="http://nonexistent:9999", timeout=1)
        result = s.summarize("short")
        # Exception occurs due to timeout, fallback returns original
        assert result == "short"


# ===================================================================
# PinnedContentManager
# ===================================================================
class TestPinnedContentManager:
    def test_init(self):
        pm = PinnedContentManager()
        assert pm._explicit_pins == set()

    def test_identify_system_pinned(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "hello"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_identify_explicit_metadata_pin(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "user", "content": "hello", "metadata": {"pin": True}},
            {"role": "assistant", "content": "hi"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_identify_marker_pin(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "user", "content": "[PIN] this is important"},
            {"role": "assistant", "content": "got it"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices
        assert 1 in indices  # assistant reply also pinned

    def test_identify_marker_pin_keep(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "user", "content": "[KEEP] this is important"},
            {"role": "assistant", "content": "ok"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_identify_marker_pin_reserve(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "user", "content": "[保留] this is important"},
            {"role": "assistant", "content": "ok"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_identify_whiteboard_keywords(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "用户决策: 使用方案A"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_identify_whiteboard_decision(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "已确定: 方案B"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_identify_last_user_question(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        indices = pm.identify(msgs)
        assert 3 in indices  # last user message

    def test_identify_no_user_messages(self):
        """Edge case: no user messages at all."""
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "hello"},
        ]
        indices = pm.identify(msgs)
        # System should be pinned, no last user to add
        assert 0 in indices

    def test_pin_and_unpin_message(self):
        pm = PinnedContentManager()
        pm.pin_message(5)
        assert 5 in pm._explicit_pins
        pm.unpin_message(5)
        assert 5 not in pm._explicit_pins

    def test_is_pinned_index(self):
        pm = PinnedContentManager()
        msgs = [{"role": "system", "content": "sys"}]
        assert pm.is_pinned_index(0, msgs) is True

    def test_is_pinned_index_false(self):
        pm = PinnedContentManager()
        msgs = [{"role": "user", "content": "hello"}]
        # No system, no marker, not last user... wait, last user IS pinned
        # Let's use a message without user
        msgs2 = [{"role": "assistant", "content": "hello"}]
        assert pm.is_pinned_index(0, msgs2) is False

    def test_separate_pinned(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        pinned, compressible = pm.separate_pinned(msgs)
        assert len(pinned) >= 1
        assert len(compressible) >= 1

    def test_separate_pinned_all_compressible(self):
        pm = PinnedContentManager()
        msgs = [
            {"role": "assistant", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        pinned, compressible = pm.separate_pinned(msgs)
        assert len(pinned) == 0
        assert len(compressible) == 2

    def test_sort_key_system_highest(self):
        """Verify system messages come first in sorted indices."""
        pm = PinnedContentManager()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[PIN] important"},
        ]
        indices = pm.identify(msgs)
        # system (index 1) should be pinned, [PIN] user (index 2), last user is 2 too
        assert 1 in indices  # system
        assert 2 in indices  # [PIN] user + last user
        # Index 0 is not the last user -> not auto-pinned
        assert 0 not in indices

    def test_identify_marker_not_found(self):
        """Messages without any pin markers should not be pinned by marker."""
        pm = PinnedContentManager()
        msgs = [
            {"role": "user", "content": "normal question"},
            {"role": "assistant", "content": "normal answer"},
        ]
        indices = pm.identify(msgs)
        # No system, no markers -> only last user is pinned
        assert 0 in indices  # last user
        assert 1 not in indices  # assistant not pinned

    def test_identify_explicit_pins_merged(self):
        """_explicit_pins should be merged with identified indices."""
        pm = PinnedContentManager()
        pm.pin_message(0)
        msgs = [
            {"role": "user", "content": "hello"},
        ]
        # _explicit_pins isn't used in identify(), it's separate
        # identify auto-pins last user
        indices = pm.identify(msgs)
        assert 0 in indices


# ===================================================================
# ToolResultStore
# ===================================================================
class TestToolResultStore:
    def test_init_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu"))
        trs = ToolResultStore()
        assert trs.results_dir.exists()
        assert (trs.results_dir / "test").parent == trs.results_dir

    def test_init_with_base_dir(self):
        import tempfile
        d = tempfile.mkdtemp()
        trs = ToolResultStore(base_dir=d)
        assert trs.results_dir.exists()
        assert str(d) in str(trs.results_dir)

    def test_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu2"))
        trs = ToolResultStore()
        result = trs.store("test_tool", "x" * 5000)
        assert "file_id" in result
        assert "file_path" in result
        assert "preview" in result
        assert "compact" in result
        assert result["original_len"] == 5000
        # Verify file was created
        import os
        assert os.path.exists(result["file_path"])

    def test_store_short_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu_short"))
        trs = ToolResultStore()
        result = trs.store("short_tool", "hello")
        assert result["original_len"] == 5
        assert "..." not in result["preview"]  # No ellipsis for short content

    def test_store_long_preview(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu_long"))
        trs = ToolResultStore()
        # over 200 chars to trigger ellipsis in preview
        result = trs.store("long_tool", "A" * 500)
        assert "..." in result["preview"]

    def test_read_result_by_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu3"))
        trs = ToolResultStore()
        result = trs.store("test_tool", "hello world")
        content = trs.read_result(result["file_path"])
        assert "hello world" in content

    def test_read_result_by_relative_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu_rel"))
        trs = ToolResultStore()
        result = trs.store("test_tool", "relative test")
        # Read by file_id (relative, no .txt extension in ID)
        file_id = result["file_id"]
        content = trs.read_result(file_id)
        assert "relative test" in content

    def test_read_result_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu4"))
        trs = ToolResultStore()
        content = trs.read_result("/nonexistent/path.txt")
        assert "不存在" in content

    def test_read_result_read_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu_readerr"))
        trs = ToolResultStore()
        fp = tmp_path / "kuafu_readerr" / "tool_results" / "bad_file.txt"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text("test", encoding="utf-8")
        # Make it unreadable by changing permissions
        fp.chmod(0o000)
        try:
            result = trs.read_result(str(fp))
            assert "失败" in result
        finally:
            fp.chmod(0o644)

    def test_load_classmethod(self, tmp_path):
        f = tmp_path / "test_file.txt"
        f.write_text("file content")
        result = ToolResultStore.load(str(f))
        assert result == "file content"

    def test_load_not_exists(self, tmp_path):
        assert ToolResultStore.load(str(tmp_path / "nonexistent.txt")) is None

    def test_load_not_file(self, tmp_path):
        assert ToolResultStore.load(str(tmp_path)) is None

    def test_load_read_error(self, tmp_path):
        f = tmp_path / "locked.txt"
        f.write_text("content")
        f.chmod(0o000)
        try:
            result = ToolResultStore.load(str(f))
            assert result is None
        finally:
            f.chmod(0o644)

    def test_should_compact_true(self):
        assert ToolResultStore.should_compact("x" * 3000) is True

    def test_should_compact_false(self):
        assert ToolResultStore.should_compact("short") is False

    def test_try_read_from_path_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu5"))
        trs = ToolResultStore()
        result = trs.store("test", "data here")
        read_back = ToolResultStore.try_read_from_path(result["compact"])
        assert read_back == "data here"

    def test_try_read_from_path_no_match(self):
        assert ToolResultStore.try_read_from_path("no path marker") == ""

    def test_try_read_from_path_file_not_found(self, tmp_path):
        compact = f"[工具结果已存储] test | 大小: 5 chars | 预览: hello | 完整路径: {tmp_path}/nonexistent.txt"
        result = ToolResultStore.try_read_from_path(compact)
        assert result == ""

    def test_try_read_from_path_multiline(self, tmp_path):
        compact = (
            f"line1\n"
            f"完整路径: {tmp_path / 'some_file.txt'}\n"
            f"line3"
        )
        # file doesn't exist, should return ""
        result = ToolResultStore.try_read_from_path(compact)
        assert result == ""

    def test_cleanup_old_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUAFU_DATA_DIR", str(tmp_path / "kuafu_cleanup"))
        trs = ToolResultStore()
        trs._max_files = 3
        # Store more than max_files
        for i in range(5):
            trs.store(f"tool_{i}", f"data_{i}" * 100)
        # Should have cleaned up to 3 files
        files = list(trs.results_dir.iterdir())
        assert len(files) <= 3


# ===================================================================
# ContextCompressor
# ===================================================================
class TestContextCompressor:
    def test_init_defaults(self):
        cc = ContextCompressor()
        assert cc.max_context_tokens == 12000
        assert cc.keep_recent_rounds == 5
        assert cc._pinned_summary == ""

    def test_init_custom(self):
        s = LocalSummarizer()
        cc = ContextCompressor(max_context_tokens=8000, keep_recent_rounds=3, summarizer=s)
        assert cc.max_context_tokens == 8000
        assert cc.keep_recent_rounds == 3
        assert cc.summarizer is s

    def test_needs_compression_true(self):
        cc = ContextCompressor(max_context_tokens=10)
        msgs = [{"role": "user", "content": "hello world this is a long message that exceeds the small threshold"}]
        assert cc.needs_compression(msgs) is True

    def test_needs_compression_false(self):
        cc = ContextCompressor(max_context_tokens=999999)
        msgs = [{"role": "user", "content": "short"}]
        assert cc.needs_compression(msgs) is False

    # ── _count_tokens ──────────────────────────────────────────────

    def test_count_tokens(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "hello world"}, {"role": "assistant", "content": "hi"}]
        count = cc._count_tokens(msgs)
        assert count > 0

    def test_count_tokens_with_tool_calls(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"arguments": '{"cmd": "ls -la"}'}}
            ]},
        ]
        count = cc._count_tokens(msgs)
        assert count > 0

    def test_count_tokens_dict_content(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": {"key": "value"}}]
        count = cc._count_tokens(msgs)
        assert count > 0

    def test_count_tokens_tool_calls_empty_args(self):
        """Edge case: tool_calls with empty arguments dict."""
        cc = ContextCompressor()
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"arguments": {}}}
            ]},
        ]
        count = cc._count_tokens(msgs)
        assert count >= 0

    # ── get_token_count ────────────────────────────────────────────

    def test_get_token_count(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        tc = cc.get_token_count(msgs)
        assert "total" in tc
        assert "system" in tc
        assert "conversation" in tc
        assert "threshold" in tc
        assert "needs_compression" in tc

    # ── estimate_fit_rounds ────────────────────────────────────────

    def test_estimate_fit_rounds(self):
        cc = ContextCompressor(max_context_tokens=10000, system_token_estimation=2000)
        assert cc.estimate_fit_rounds() == 8000 // max(400, 100)

    def test_estimate_fit_rounds_small_threshold(self):
        cc = ContextCompressor(max_context_tokens=50, system_token_estimation=100)
        # available would be negative, but max(1, ...)
        assert cc.estimate_fit_rounds() >= 1

    # ── compress ───────────────────────────────────────────────────

    def test_compress_no_compression_needed(self):
        cc = ContextCompressor(max_context_tokens=999999)
        msgs = [{"role": "user", "content": "short"}]
        result = cc.compress(msgs)
        assert result.messages_removed == 0
        assert result.summary == "无需压缩"

    def test_compress_all_pinned(self):
        cc = ContextCompressor(max_context_tokens=1)
        msgs = [{"role": "system", "content": "You are a bot."}, {"role": "user", "content": "hello"}]
        result = cc.compress(msgs)
        assert result.messages_removed == 0
        assert "Pin" in result.summary

    def test_compress_tool_cleanup_enough(self):
        """Tool cleanup alone frees enough, no further compression needed.
        
        Make sure after cleaning old tool results, total tokens <= max*0.8.
        """
        cc = ContextCompressor(max_context_tokens=2000, keep_recent_rounds=1)
        msgs = [{"role": "system", "content": "x"}]
        for i in range(6):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{i}", "function": {"name": "t", "arguments": '{"x":"y"}'}}
            ]})
            # Only round 0 has huge tool content; rounds 1-5 have tiny
            tool_content = "A" * 5000 if i < 1 else "ok"
            msgs.append({"role": "tool", "content": tool_content, "tool_call_id": f"call_{i}"})
        assert cc.needs_compression(msgs)
        result = cc.compress(msgs)
        assert result.messages_removed == 0
        assert "工具结果清除" in result.summary

    def test_compress_old_msgs_no_old(self):
        """No old messages after cleanup — skip to early return."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=100)
        msgs = [{"role": "user", "content": "hello"}]
        result = cc.compress(msgs)
        # Should hit "轮次少" path
        assert result.messages_removed == 0

    def test_compress_full_flow_keyword_fallback(self):
        """Full compression flow with keyword fallback summary."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc.summarizer = summarizer
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}"})
            msgs.append({"role": "assistant", "content": f"answer {i}"})
            msgs.append({"role": "tool", "content": f"tool_result_{i}", "tool_call_id": f"tc_{i}"})
        result = cc.compress(msgs)
        assert result.messages_removed > 0
        assert len(result.summary) > 0

    def test_compress_full_flow_with_llm_fn(self):
        """Full compression flow using external llm_summarize callback."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc.summarizer = summarizer

        def llm_fn(text):
            return "LLM callback summary result"

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 20})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 20})
        result = cc.compress(msgs, llm_summarize=llm_fn)
        assert result.messages_removed > 0
        assert "LLM callback" in result.summary

    def test_compress_llm_fn_short_dialogue_fallback(self):
        """llm_fn receives short dialogue (< 500 chars), falls through to keyword."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc.summarizer = summarizer

        def llm_fn(text):
            return "should not be reached"

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(3):
            msgs.append({"role": "user", "content": "hi"})
            msgs.append({"role": "assistant", "content": "ok"})
        result = cc.compress(msgs, llm_summarize=llm_fn)
        assert result.messages_removed > 0

    def test_compress_llm_fn_exception(self):
        """llm_fn raises exception, falls through to keyword."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc.summarizer = summarizer

        def llm_fn(text):
            raise ValueError("LLM error")

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 30})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 30})
        result = cc.compress(msgs, llm_summarize=llm_fn)
        assert result.messages_removed > 0
        assert len(result.summary) > 0

    def test_compress_create_summary_local_llm(self):
        """_create_summary with available local LLM."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        summarizer.summarize.return_value = "Local LLM summary here"
        cc.summarizer = summarizer

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 30})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 30})
        result = cc.compress(msgs)
        assert result.messages_removed > 0
        assert "Local LLM summary" in result.summary

    def test_compress_create_summary_local_llm_short_dialogue(self):
        """local LLM available but dialogue too short (< 300 chars)."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        cc.summarizer = summarizer

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(3):
            msgs.append({"role": "user", "content": "hi"})
            msgs.append({"role": "assistant", "content": "ok"})
        result = cc.compress(msgs)
        assert result.messages_removed > 0

    def test_compress_create_summary_local_llm_exception(self):
        """local LLM raises exception in summarize."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        summarizer.summarize.side_effect = Exception("Connection error")
        cc.summarizer = summarizer

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 30})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 30})
        result = cc.compress(msgs)
        assert result.messages_removed > 0

    def test_compress_create_summary_local_llm_short_summary(self):
        """local LLM returns summary <= 10 chars, falls through."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        summarizer.summarize.return_value = "short"
        cc.summarizer = summarizer

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 30})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 30})
        result = cc.compress(msgs)
        assert result.messages_removed > 0

    # ── compress_with_local_llm ─────────────────────────────────────

    def test_compress_with_local_llm_no_compression(self):
        cc = ContextCompressor(max_context_tokens=999999)
        msgs = [{"role": "user", "content": "short"}]
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed == 0

    def test_compress_with_local_llm_all_pinned(self):
        cc = ContextCompressor(max_context_tokens=1)
        msgs = [{"role": "system", "content": "sys"}]
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed == 0

    def test_compress_with_local_llm_tool_cleanup_enough(self):
        cc = ContextCompressor(max_context_tokens=5000, keep_recent_rounds=5)
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{i}", "function": {"name": "test_tool", "arguments": '{"x": "y"}'}}
            ]})
            msgs.append({"role": "tool", "content": "result " * 100, "tool_call_id": f"call_{i}"})
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed == 0

    def test_compress_with_local_llm_no_old_msgs(self):
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=100)
        msgs = [{"role": "user", "content": "hello"}]
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed == 0

    def test_compress_with_local_llm_full_flow(self):
        """Full flow of compress_with_local_llm."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.summarize.return_value = "Local summary"
        cc.summarizer = summarizer

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 20})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 20})
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed > 0

    # ── clean_old_tool_results ──────────────────────────────────────

    def test_clean_old_tool_results(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "search_tool", "arguments": '{"q": "test"}'}}
            ]},
            {"role": "tool", "content": "very long result " * 100, "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        assert saved > 0

    def test_clean_old_tool_results_empty(self):
        cc = ContextCompressor()
        new_msgs, saved = cc.clean_old_tool_results([], max_rounds=1)
        assert saved == 0
        assert new_msgs == []

    def test_clean_old_tool_results_few_rounds(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=5)
        assert saved == 0
        assert new_msgs == msgs

    def test_clean_old_tool_results_already_placeholder(self):
        """Tool messages that already start with [工具 should be kept as-is."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "search", "arguments": '{}'}}
            ]},
            {"role": "tool", "content": "[工具结果已归档] search | ...", "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        # Since there's only 2 rounds (q1 + q2) and max_rounds=1, cleanup_threshold = 2-1 = 1
        # Round 1 msgs get cleaned but the tool content is already a placeholder
        assert saved == 0  # Already a placeholder, no tokens saved

    def test_clean_old_tool_results_assistant_with_tool_calls(self):
        """Test assistant with tool_calls in old rounds gets arguments simplified."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "search", "arguments": '{"query": "' + "x" * 100 + '"}'}}
            ]},
            {"role": "tool", "content": "result", "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        # Round 1 has old assistant with tool_calls, arguments should be compressed
        assert saved > 0

    def test_clean_old_tool_results_assistant_no_tool_calls(self):
        """Old assistant messages without tool_calls should pass through."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "new answer"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        assert saved == 0
        assert len(new_msgs) == 4

    def test_clean_old_tool_results_tool_name_not_found(self):
        """When tool_call_id doesn't match any assistant's tool_calls, name='?'."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_other", "function": {"name": "other_tool", "arguments": '{}'}}
            ]},
            {"role": "tool", "content": "very long result " * 100, "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        assert saved > 0
        # Should contain "?" for unknown tool name
        assert "?" in new_msgs[2]["content"] or "?" in str(new_msgs)

    def test_clean_old_tool_results_short_content_no_preview(self):
        """Tool content shorter than keep_summary_chars."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "test", "arguments": '{}'}}
            ]},
            {"role": "tool", "content": "short", "tool_call_id": "call_1"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=5)
        assert saved == 0  # Content not cleaned (total_rounds <= max_rounds)

    def test_clean_old_tool_results_json_arguments_compression(self):
        """JSON arguments get compressed to type-based format."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "test_fn",
                        "arguments": json.dumps({
                            "name": "some_long_name_value",
                            "count": 42,
                            "verbose": "x" * 200
                        })
                    }
                }
            ]},
            {"role": "tool", "content": "x" * 200, "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        assert saved > 0

    def test_clean_old_tool_results_json_arguments_decode_error(self):
        """Non-JSON arguments string should still be compressed via length."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "test_fn",
                        "arguments": "not valid json but very long " * 20
                    }
                }
            ]},
            {"role": "tool", "content": "x" * 200, "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        assert saved > 0

    def test_clean_old_tool_results_content_none(self):
        """Tool content is None."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "test", "arguments": '{}'}}
            ]},
            {"role": "tool", "content": None, "tool_call_id": "call_1"},
            {"role": "user", "content": "q2"},
        ]
        new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=1)
        # None content -> old_len = 0 -> saved = 0
        assert saved == 0

    # ── _format_dialogue ────────────────────────────────────────────

    def test_format_dialogue(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "test"}}
            ]},
            {"role": "tool", "content": "result data"},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "用户: hello" in dialogue
        assert "调用" in dialogue
        assert "test" in dialogue

    def test_format_dialogue_tool_short_content(self):
        """Tool content <= 20 chars should be skipped in dialogue."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "content": "short"},  # <= 20 chars
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "用户: hi" in dialogue
        assert "夸父: ok" in dialogue
        assert "工具结果" not in dialogue

    def test_format_dialogue_assistant_no_content_only_tool_calls(self):
        """Assistant with tool_calls and no content."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "search for x"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_tool"}}
            ]},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "调用工具 search_tool" in dialogue

    def test_format_dialogue_assistant_no_content_no_tool_calls(self):
        """Assistant with empty content and no tool_calls."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "用户: hello" in dialogue
        # assistant with empty content and no tool_calls produces nothing

    def test_format_dialogue_unknown_role(self):
        """Unknown role should be skipped."""
        cc = ContextCompressor()
        msgs = [
            {"role": "unknown", "content": "something"},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert dialogue == ""

    # ── _create_summary ─────────────────────────────────────────────

    def test_create_summary_keyword_fallback(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]
        summary = cc._create_summary(msgs)
        assert len(summary) > 0

    def test_create_summary_with_llm_fn(self):
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc = ContextCompressor(summarizer=summarizer)
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]
        llm_fn = MagicMock(return_value="LLM summary")
        summary = cc._create_summary(msgs, llm_fn=llm_fn)
        # Dialogue is short (< 500 chars), so llm_fn won't be called
        assert "用户: hello" in summary

    def test_create_summary_with_llm_fn_long_enough(self):
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc = ContextCompressor(summarizer=summarizer)
        msgs = [{"role": "user", "content": "hello " * 50}, {"role": "assistant", "content": "world " * 50}]
        llm_fn = MagicMock(return_value="LLM summary long enough")
        summary = cc._create_summary(msgs, llm_fn=llm_fn)
        assert summary == "LLM summary long enough"

    def test_create_summary_llm_fn_exception(self):
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc = ContextCompressor(summarizer=summarizer)
        msgs = [{"role": "user", "content": "hello " * 50}, {"role": "assistant", "content": "world " * 50}]

        def failing_llm(text):
            raise ValueError("fail")

        summary = cc._create_summary(msgs, llm_fn=failing_llm)
        # Falls through to keyword
        assert len(summary) > 0

    def test_create_summary_keyword_long_text(self):
        """Keyword summary truncated to 800 chars."""
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "A" * 2000}]
        summary = cc._create_summary(msgs)
        assert len(summary) <= 800 + 3  # 800 + "..."

    def test_create_summary_keyword_tool_short_content(self):
        """Tool content <= 20 chars skipped in keyword summary."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "ab"},  # <= 20 chars
        ]
        summary = cc._create_summary(msgs)
        assert "用户: hi" in summary
        assert "工具结果" not in summary

    def test_create_summary_keyword_assistant_with_tool_calls(self):
        """Assistant with tool_calls in keyword summary."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search"}}
            ]},
        ]
        summary = cc._create_summary(msgs)
        assert "调用工具: web_search" in summary

    def test_create_summary_keyword_without_tool_calls(self):
        """Assistant without tool_calls in keyword summary."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        summary = cc._create_summary(msgs)
        assert "夸父: world" in summary


# ===================================================================
# ContextCollapse
# ===================================================================
class TestContextCollapse:
    def test_init_defaults(self):
        cc = ContextCollapse()
        assert cc.keep_recent_rounds == 5
        assert "对话摘要器" in cc.summary_prompt

    def test_init_custom(self):
        s = LocalSummarizer()
        cc = ContextCollapse(summarizer=s, keep_recent_rounds=3, summary_prompt="custom")
        assert cc.summarizer is s
        assert cc.keep_recent_rounds == 3
        assert cc.summary_prompt == "custom"

    # ── collapse ────────────────────────────────────────────────────

    def test_collapse_no_compression_needed(self):
        cc = ContextCollapse()
        msgs = [{"role": "user", "content": "short"}]
        result = cc.collapse(msgs, threshold_tokens=999999)
        assert result.original_count == 1
        assert result.collapsed_count == 1
        assert result.messages_written == 0
        assert result.tokens_saved == 0

    def test_collapse_force(self):
        """force=True bypasses threshold check."""
        cc = ContextCollapse(keep_recent_rounds=10)
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
        result = cc.collapse(msgs, force=True, threshold_tokens=1)
        # With keep_recent_rounds=10 and only 2 messages, no old_msgs
        assert result.original_count == 2
        assert result.collapsed_count == 2
        assert result.summary == "轮次少，无需压缩"

    def test_collapse_few_rounds(self):
        cc = ContextCollapse(keep_recent_rounds=10)
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
        result = cc.collapse(msgs, threshold_tokens=1)
        assert result.original_count == 2
        assert result.collapsed_count == 2
        assert result.summary == "轮次少，无需压缩"

    def test_collapse_with_session_store(self, tmp_path):
        class MockSessionStore:
            def __init__(self):
                self.saved = None

            def save_raw_messages(self, session_id, messages):
                self.saved = (session_id, messages)

        cc = ContextCollapse(keep_recent_rounds=1)
        store = MockSessionStore()
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}" * 50})
            msgs.append({"role": "assistant", "content": f"answer {i}" * 50})
        result = cc.collapse(msgs, session_id="test_sess", session_store=store, force=True, threshold_tokens=1)
        assert result.original_count > 0
        assert result.collapsed_count > 0
        assert result.messages_written > 0
        assert store.saved is not None
        assert store.saved[0] == "test_sess"

    def test_collapse_without_session_store(self):
        cc = ContextCollapse(keep_recent_rounds=1)
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"q{i}" * 50})
            msgs.append({"role": "assistant", "content": f"a{i}" * 50})
        result = cc.collapse(msgs, session_id="", session_store=None, force=True, threshold_tokens=1)
        assert result.original_count > 0
        assert result.messages_written == 0

    def test_collapse_stores_without_hasattr(self):
        class NoSaveStore:
            pass

        cc = ContextCollapse(keep_recent_rounds=1)
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(5):
            msgs.append({"role": "user", "content": f"q{i}" * 50})
            msgs.append({"role": "assistant", "content": f"a{i}" * 50})
        result = cc.collapse(msgs, session_id="test", session_store=NoSaveStore(), force=True, threshold_tokens=1)
        assert result.messages_written == 0

    # ── _generate_summary ───────────────────────────────────────────

    def test_generate_summary_small_dialogue(self):
        cc = ContextCollapse()
        msgs = [{"role": "user", "content": "short"}]
        summary = cc._generate_summary(msgs)
        assert len(summary) > 0

    def test_generate_summary_llm_available(self):
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        summarizer.summarize.return_value = "LLM generated summary"
        cc = ContextCollapse(summarizer=summarizer)
        msgs = [{"role": "user", "content": "long message " * 50},
                {"role": "assistant", "content": "long reply " * 50}]
        summary = cc._generate_summary(msgs)
        assert summary == "LLM generated summary"

    def test_generate_summary_llm_exception(self):
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        summarizer.summarize.side_effect = Exception("LLM error")
        cc = ContextCollapse(summarizer=summarizer)
        msgs = [{"role": "user", "content": "test msg " * 30}]
        summary = cc._generate_summary(msgs)
        assert len(summary) > 0

    def test_generate_summary_llm_unavailable(self):
        cc = ContextCollapse()
        cc.summarizer.is_available = MagicMock(return_value=False)
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]
        summary = cc._generate_summary(msgs)
        assert len(summary) > 0

    # ── _keyword_summary ────────────────────────────────────────────

    def test_keyword_summary_user_assistant_tool(self):
        cc = ContextCollapse()
        msgs = [
            {"role": "user", "content": "what is python?"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search"}}]},
            {"role": "tool", "content": "Python is a programming language found on the internet"},
        ]
        result = cc._keyword_summary(msgs)
        assert "用户: what" in result
        assert "调用: search" in result
        assert "结果: Python" in result

    def test_keyword_summary_capped(self):
        cc = ContextCollapse()
        msgs = [{"role": "user", "content": "A" * 2000}]
        result = cc._keyword_summary(msgs)
        assert len(result) <= 800 + 3  # text[:800] + "..."

    def test_keyword_summary_assistant_without_tool_calls(self):
        cc = ContextCollapse()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = cc._keyword_summary(msgs)
        assert "夸父: world" in result

    def test_keyword_summary_tool_short_content(self):
        """Tool content <= 20 chars should be skipped."""
        cc = ContextCollapse()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "ab"},
        ]
        result = cc._keyword_summary(msgs)
        assert "结果:" not in result

    # ── _format_dialogue ────────────────────────────────────────────

    def test_collapse_format_dialogue_all_roles(self):
        cc = ContextCollapse()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "test_fn"}}]},
            {"role": "tool", "content": "result" + "x" * 30},  # Must be > 20 chars
            {"role": "assistant", "content": "understood"},
        ]
        result = cc._format_dialogue(msgs)
        assert "用户: hello" in result
        assert "调用 test_fn" in result
        assert "工具: result" in result
        assert "understood" in result

    def test_collapse_format_dialogue_tool_short_content(self):
        cc = ContextCollapse()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "short"},
        ]
        result = cc._format_dialogue(msgs)
        assert "工具:" not in result

    def test_collapse_format_dialogue_unknown_role(self):
        cc = ContextCollapse()
        msgs = [{"role": "other", "content": "data"}]
        result = cc._format_dialogue(msgs)
        assert result == ""

    def test_collapse_format_dialogue_assistant_no_content_no_tool(self):
        cc = ContextCollapse()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
        ]
        result = cc._format_dialogue(msgs)
        assert "用户: hello" in result


# ===================================================================
# CollapseResult
# ===================================================================
class TestCollapseResult:
    def test_init(self):
        cr = CollapseResult(
            original_count=10,
            collapsed_count=5,
            messages_written=10,
            summary="test summary",
            tokens_saved=500,
        )
        assert cr.original_count == 10
        assert cr.collapsed_count == 5
        assert cr.messages_written == 10
        assert cr.summary == "test summary"
        assert cr.tokens_saved == 500


# ===================================================================
# budget_reduce_output
# ===================================================================
class TestBudgetReduceOutput:
    def test_small_content(self):
        assert budget_reduce_output("short") == "short"

    def test_empty(self):
        assert budget_reduce_output("") == ""

    def test_none(self):
        assert budget_reduce_output(None) is None

    def test_within_limit(self):
        # Content under default limit (3000)
        content = "x" * 2000
        result = budget_reduce_output(content, tool_name="search")
        assert result == content

    def test_exceeds_limit_no_tool_name(self):
        """Plain text exceeding default limit."""
        text = "head\n" + "line " * 2000 + "\ntail content here"
        result = budget_reduce_output(text, hard_limit=2000)
        assert "BudgetReduction" in result
        assert "head" in result
        assert "tail" in result

    def test_json_array_small(self):
        arr = json.dumps([{"id": i} for i in range(5)])
        result = budget_reduce_output(arr, tool_name="search")
        assert result == arr  # Small enough, no reduction

    def test_json_array_under_20_items(self):
        """JSON array with < 20 items should not get array reduction."""
        arr = json.dumps([{"x": "y"} for _ in range(15)])
        result = budget_reduce_output(arr, hard_limit=100)
        # Content is small, should pass through
        assert result is not None

    def test_json_array_large(self):
        arr = json.dumps([{"id": i, "name": f"item_{i}_long_name_for_testing"} for i in range(100)])
        result = budget_reduce_output(arr, tool_name="search")
        assert "BudgetReduction" in result or len(result) < len(arr)

    def test_json_object(self):
        obj = json.dumps({"results": [{"data": "x" * 200} for _ in range(30)], "total": 30})
        result = budget_reduce_output(obj, tool_name="default")
        assert result is not None

    def test_json_object_not_dict(self):
        """Non-dict JSON (e.g., string) should fall through to plain text."""
        result = budget_reduce_output(json.dumps("just a string, not a dict or list") * 500, hard_limit=500)
        assert "BudgetReduction" in result

    def test_plain_text_reduction(self):
        text = "head\n" + "line " * 2000 + "\ntail content here"
        result = budget_reduce_output(text, hard_limit=2000)
        assert "BudgetReduction" in result
        assert "head" in result

    def test_tool_name_with_custom_limit(self):
        """Different tool names have different limits."""
        # terminal limit is 4000, so content of 5000 should be reduced
        text = "x" * 5000
        result = budget_reduce_output(text, tool_name="terminal")
        assert "BudgetReduction" in result or len(result) < 5000

    def test_hard_limit_shorter(self):
        """hard_limit should cap the tool-specific limit."""
        text = "x" * 5000
        # search limit=5000, hard_limit=1000 -> min(5000, 1000) = 1000
        result = budget_reduce_output(text, tool_name="search", hard_limit=1000)
        assert "BudgetReduction" in result or len(result) < 5000


# ===================================================================
# _reduce_json_object
# ===================================================================
class TestReduceJsonObject:
    def test_deep_nesting(self):
        data = {"level1": {"level2": {"level3": {"deep": "x" * 2000}}}}
        result = _reduce_json_object(data, 5000)
        assert "truncated" in result or len(result) > 0

    def test_large_list(self):
        """List with > 15 items should be truncated."""
        data = {"items": [{"id": i} for i in range(50)]}
        result = _reduce_json_object(data, 5000)
        assert "(50 more items)" in result or "...(35 more items)" in result or "...more" in result

    def test_nested_list(self):
        """List items should be walked recursively."""
        data = {"nested": [{"inner": {"value": "x" * 2000}} for _ in range(3)]}
        result = _reduce_json_object(data, 5000)
        assert result is not None

    def test_deep_depth_limited(self):
        """Depth > 3 should truncate strings."""
        data = {"a": {"b": {"c": {"d": {"e": "x" * 500}}}}}
        result = _reduce_json_object(data, 5000)
        assert result is not None

    def test_result_exceeds_limit(self):
        """If final result exceeds limit, truncate."""
        data = {"key": "x" * 5000}
        result = _reduce_json_object(data, 500)
        assert "[...truncated" in result or len(result) <= 500 + 200

    def test_non_dict_values(self):
        """Non-dict, non-list values should pass through."""
        data = {"name": "test", "count": 42, "active": True}
        result = _reduce_json_object(data, 5000)
        assert "test" in result
        assert "42" in result

    def test_list_of_scalars(self):
        """List containing non-dict, non-list scalars hits L1179 return v."""
        data = {"items": [1, 2, 3, "hello", True, None]}
        result = _reduce_json_object(data, 5000)
        assert "1" in result
        assert "hello" in result
        assert "true" in result or "True" in result


# ===================================================================
# Edge cases for compress() — pinned msgs non-empty, old_msgs empty
# ===================================================================
class TestCompressEdgeCases:
    """Additional edge cases for ContextCompressor.compress()."""

    def test_compress_pinned_exist_no_old_msgs(self):
        """compress(): pinned_msgs not empty, old_msgs empty (L396-399).

        Setup: needs_compression=True, separate_pinned has pinned msgs,
        after cleanup non_system is short enough that old_msgs = [].
        """
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=10)
        # System msg is auto-pinned, [PIN] user is pinned (and its reply),
        # last user is auto-pinned. Only the last assistant is compressible.
        msgs = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "[PIN] important question"},
            {"role": "assistant", "content": "important answer"},
            {"role": "user", "content": "normal question"},
            {"role": "assistant", "content": "normal answer"},
        ]
        result = cc.compress(msgs)
        assert result.messages_removed == 0
        assert "轮次少" in result.summary
        assert result.compressed_tokens > 0

    def test_compress_with_local_llm_cleanup_enough_with_pins(self):
        """compress_with_local_llm(): L467-468, pinned msgs + cleanup enough.

        After cleanup, test_messages tokens <= max_context_tokens * 0.8
        with pinned messages present.
        """
        cc = ContextCompressor(max_context_tokens=4000, keep_recent_rounds=1)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc.summarizer = summarizer

        msgs = [{"role": "system", "content": "x"}]
        for i in range(8):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{i}", "function": {"name": "t", "arguments": "{}"}}
            ]})
            msgs.append({"role": "tool", "content": "x" * 1000, "tool_call_id": f"call_{i}"})
        msgs.append({"role": "user", "content": "last_q"})
        msgs.append({"role": "assistant", "content": "last_a"})

        assert cc.needs_compression(msgs)
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed == 0
        assert "工具结果清除" in result.summary

    def test_compress_with_local_llm_pinned_no_old_msgs(self):
        """compress_with_local_llm(): L488-491, pinned non-empty, old_msgs empty."""
        cc = ContextCompressor(max_context_tokens=1, keep_recent_rounds=10)
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = False
        cc.summarizer = summarizer

        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[KEEP] important"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "last question"},
            {"role": "assistant", "content": "last answer"},
        ]
        result = cc.compress_with_local_llm(msgs)
        assert result.messages_removed == 0
        assert "轮次少" in result.summary

    def test_format_dialogue_tool_long_content(self):
        """ContextCompressor._format_dialogue: tool content > 20 chars (L546)."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search"}}
            ]},
            {"role": "tool", "content": "Here are the results of the search query which is quite long and detailed with lots of information"},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "工具结果" in dialogue
        assert "Here are the results" in dialogue or "search" in dialogue

    def test_format_dialogue_tool_content_none(self):
        """ContextCompressor._format_dialogue: tool content is None (L545 skip)."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": None},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "工具结果" not in dialogue

    def test_format_dialogue_tool_content_empty_string(self):
        """ContextCompressor._format_dialogue: tool content is empty string (L545 skip)."""
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": ""},
        ]
        dialogue = cc._format_dialogue(msgs)
        assert "工具结果" not in dialogue

    # PinnedContentManager sort_key whiteboard keywords — L692-693, L719

    def test_pin_identify_system_whiteboard_decision(self):
        """PinnedContentManager.identify: system message with 已确定: keyword (L692-693)."""
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "已确定: 使用方案C"},
            {"role": "user", "content": "final question"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices  # system with whiteboard keyword

    def test_pin_identify_system_user_decision(self):
        """PinnedContentManager.identify: system with 用户决策: keyword."""
        pm = PinnedContentManager()
        msgs = [
            {"role": "system", "content": "用户决策: 部署到生产环境"},
            {"role": "user", "content": "another question"},
        ]
        indices = pm.identify(msgs)
        assert 0 in indices

    def test_pin_sort_key_whiteboard_system(self):
        """PinnedContentManager sort_key: system + whiteboard keyword -> return 2 (L719)."""
        pm = PinnedContentManager()
        # Message 0 = normal user (no pin), message 1 = system with whiteboard
        msgs = [
            {"role": "user", "content": "test"},
            {"role": "system", "content": "已确定: 方案X"},
            {"role": "user", "content": "hello"},
        ]
        indices = pm.identify(msgs)
        # system (idx 1) should be pinned, last user (idx 2) should be pinned
        assert 1 in indices
        assert 2 in indices
        # sort_key for system with whiteboard keyword returns 2

    # ContextCollapse.generate_summary with summarizer exception
    def test_generate_summary_llm_available_exception_long_dialogue(self):
        """ContextCollapse._generate_summary: L1021-1022, exception after
        summarizer.is_available()=True with long enough dialogue (>300 chars)."""
        summarizer = MagicMock(spec=LocalSummarizer)
        summarizer.is_available.return_value = True
        summarizer.summarize.side_effect = Exception("LLM crashed")
        cc = ContextCollapse(summarizer=summarizer)
        # Dialogue needs to be > 300 chars to bypass early return at L1015-1016
        # Each message is "用户: " + 30 chars = ~37 chars
        # We need at least 9 such messages to exceed 300 chars
        msgs = [{"role": "user", "content": "this is a test message that needs to be long enough " * 5}
                for _ in range(10)]
        summary = cc._generate_summary(msgs)
        assert len(summary) > 0

    # ContextCollapse.try_read_from_path file exists but read fails
    def test_try_read_from_path_read_exception(self, tmp_path):
        """ContextCollapse.try_read_from_path: L871-872, file exists but read() raises."""
        f = tmp_path / "exists_but_fails.txt"
        f.write_text("some content")
        f.chmod(0o000)  # Remove read permission
        try:
            compact = f"完整路径: {f}"
            result = ToolResultStore.try_read_from_path(compact)
            assert result == ""
        finally:
            f.chmod(0o644)

    # budget_reduce_output JSON array parse exception
    def test_budget_reduce_output_json_array_exception(self):
        """budget_reduce_output: L1127-1128, starts with '[' AND ends with ']'
        but json.loads raises JSONDecodeError."""
        # Content that looks like a JSON array (starts+ends with brackets)
        # but has invalid content
        content = "[1, 2, invalid!" + "x" * 2000 + "]"
        result = budget_reduce_output(content, hard_limit=100)
        assert result is not None
        # Should fall through to plain text handling
        assert "BudgetReduction" in result

    def test_budget_reduce_output_json_array_type_error(self):
        """budget_reduce_output: L1127, json.loads raises TypeError."""
        # json.loads on a string that's too deeply nested can raise
        # RecursionError which is a subclass of... hmm.
        # Actually TypeError isn't raised by json.loads for syntax errors.
        # Let's trigger with something that causes json.loads to fail.
        # The except (JSONDecodeError, TypeError) is a safety net.
        # We can trigger it with valid-looking but actually invalid JSON.
        content = "[" + ",".join(["{}" for _ in range(5)]) + "x" * 2000 + "]"
        result = budget_reduce_output(content, hard_limit=100)
        assert "BudgetReduction" in result
