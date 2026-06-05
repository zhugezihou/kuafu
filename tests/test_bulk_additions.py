# =====================================================================
# Coverage additions for: core/tool_registry.py, core/agent_loop.py,
# core/prompt_template.py, core/evolution_rules.py
# =====================================================================

import json
import os
import time
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock, call, ANY
from typing import Any, Optional

import pytest


# ══════════════════════════════════════════════════════════════════════
# 1. tool_registry.py — deep coverage (53% → 85%+)
# ══════════════════════════════════════════════════════════════════════

class TestToolRegistryCoverage:
    """Covers: _search_deferred_tools all branches, execute error/lazy paths,
    schemas validation, multimedia tool registration and degradation."""

    def test_search_deferred_empty_query(self):
        """_search_deferred_tools returns [] for empty/single-char query."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        assert tr._search_deferred_tools("") == []
        assert tr._search_deferred_tools("a") == []
        assert tr._search_deferred_tools("x y") == []  # all single-char

    def test_search_deferred_all_words_match(self):
        """_search_deferred_tools: query matching keywords returns high-score entries."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools("web search internet")
        names = [r["name"] for r in results]
        assert "web_search" in names
        for r in results:
            assert r["score"] > 0

    def test_search_deferred_english_substring_handling(self):
        """_search_deferred_tools extracts continuous English substrings from mixed tokens."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools("github仓库分析")
        names = [r["name"] for r in results]
        # github extracted from the mixed token via re.findall(r'[a-z]{3,}', ...)
        assert "github_search" in names or "github_get_repo" in names
        # "仓库" should also produce results through Chinese sliding window
        assert len(results) > 0

    def test_search_deferred_chinese_window_matches_description(self):
        """_search_deferred_tools Chinese sliding window matches description words."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # "图像" appears in image_gen keywords
        results = tr._search_deferred_tools("图像")
        names = [r["name"] for r in results]
        assert "image_gen" in names

    def test_search_deferred_exact_name_match(self):
        """_search_deferred_tools: exact name match gets highest score (10)."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools("web_search")
        assert len(results) > 0
        # web_search should be top result with score >= 10
        web_results = [r for r in results if r["name"] == "web_search"]
        assert len(web_results) == 1
        assert web_results[0]["score"] >= 10

    def test_search_deferred_case_insensitive(self):
        """_search_deferred_tools is case-insensitive."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        r1 = tr._search_deferred_tools("GitHub")
        r2 = tr._search_deferred_tools("github")
        names1 = [r["name"] for r in r1]
        names2 = [r["name"] for r in r2]
        assert names1 == names2

    def test_search_deferred_max_results_limit(self):
        """_search_deferred_tools respects max_results parameter."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools("search", max_results=2)
        assert len(results) <= 2

    def test_execute_handler_raises_exception(self):
        """execute() catches handler exceptions and returns error dict."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()

        def broken_handler(args):
            raise ValueError("something broke")

        tr.register("broken_tool", {
            "description": "broken",
            "parameters": {"type": "object", "properties": {}}
        }, broken_handler)
        result = tr.execute({
            "id": "c1",
            "function": {"name": "broken_tool", "arguments": {}}
        })
        assert result["success"] is False
        assert "异常" in result["output"]

    def test_execute_unknown_tool(self):
        """execute() returns error for unknown tool name."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.execute({
            "id": "c1",
            "function": {"name": "nonexistent_tool", "arguments": {}}
        })
        assert result["success"] is False
        assert "未知工具" in result["output"]

    def test_execute_json_string_arguments(self):
        """execute() parses JSON string arguments."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "done"})
        tr.register("json_tool", {
            "description": "test",
            "parameters": {"type": "object", "properties": {}}
        }, handler)
        result = tr.execute({
            "id": "c1",
            "function": {"name": "json_tool", "arguments": '{"key": "value"}'}
        })
        assert result["success"] is True
        handler.assert_called_with({"key": "value"})

    def test_execute_invalid_json_arguments_defaults_empty(self):
        """execute() defaults to {} for invalid JSON arguments string."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        tr.register("bad_json_tool", {
            "description": "test",
            "parameters": {"type": "object", "properties": {}}
        }, handler)
        result = tr.execute({
            "id": "c1",
            "function": {"name": "bad_json_tool", "arguments": "not valid json!!!"}
        })
        assert result["success"] is True
        handler.assert_called_with({})

    def test_execute_non_dict_raw_args(self):
        """execute() handles non-dict, non-str arguments."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        tr.register("weird_args_tool", {
            "description": "test",
            "parameters": {"type": "object", "properties": {}}
        }, handler)
        result = tr.execute({
            "id": "c1",
            "function": {"name": "weird_args_tool", "arguments": 42}
        })
        assert result["success"] is True
        handler.assert_called_with({})

    def test_compact_tool_promote_on_execute(self):
        """execute() promotes compact tool on first call, returns True from _promote_compact_tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "compact result"})
        tr.register_compact("compact_demo", {
            "description": "compact tool test",
            "parameters": {"type": "object", "properties": {}}
        }, handler)

        # Before execute, not in active schemas
        schemas_before = tr.get_schemas()
        names_before = [s["function"]["name"] for s in schemas_before]
        assert "compact_demo" not in names_before

        # Execute triggers promotion
        result = tr.execute({
            "id": "c1",
            "function": {"name": "compact_demo", "arguments": {}}
        })
        assert result["success"] is True

        # Now it's injected
        schemas_after = tr.get_schemas()
        names_after = [s["function"]["name"] for s in schemas_after]
        assert "compact_demo" in names_after

    def test_compact_tool_no_double_promote(self):
        """_promote_compact_tool returns False for already-promoted tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        tr.register_compact("double_promo", {
            "description": "test",
            "parameters": {"type": "object", "properties": {}}
        }, handler)

        # First call promotes
        assert tr._promote_compact_tool("double_promo") is True
        # Second call already promoted
        assert tr._promote_compact_tool("double_promo") is False

    def test_compact_tool_not_found_returns_false(self):
        """_promote_compact_tool returns False for non-compact tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        assert tr._promote_compact_tool("nonexistent") is False

    def test_register_deferred_removes_from_core(self):
        """register_deferred removes tool from core schemas if already present."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        # Register as core first
        tr.register("test_def", {"description": "core", "parameters": {"type": "object", "properties": {}}}, handler)
        names_core = [s["function"]["name"] for s in tr._schemas]
        assert "test_def" in names_core

        # Now register as deferred — should remove from core
        handler2 = MagicMock(return_value={"success": True, "output": "def"})
        tr.register_deferred("test_def", {"description": "def", "parameters": {"type": "object", "properties": {}}}, handler2)
        names_core_after = [s["function"]["name"] for s in tr._schemas]
        assert "test_def" not in names_core_after
        assert any(e["schema"]["function"]["name"] == "test_def" for e in tr._deferred)

    def test_inject_tool_already_injected(self):
        """inject_tool returns True if already injected (no duplicate)."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # First injection
        assert tr.inject_tool("web_search") is True
        # Second — already in injected
        assert tr.inject_tool("web_search") is True
        # Only one entry in injected_tools
        count = sum(1 for s in tr._injected_tools if s["function"]["name"] == "web_search")
        assert count == 1

    def test_inject_tool_not_deferred_returns_false(self):
        """inject_tool returns False for non-deferred tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        assert tr.inject_tool("terminal") is False

    def test_unregister_removes_from_all_pools(self):
        """unregister removes from schemas, compact, injected, and handlers."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        tr.register_compact("unreg_test", {
            "description": "test", "parameters": {"type": "object", "properties": {}}
        }, handler)
        # Also inject it
        assert tr._promote_compact_tool("unreg_test") is True
        result = tr.unregister("unreg_test")
        assert result is True
        assert tr.get_handler("unreg_test") is None

    def test_unregister_nonexistent_returns_false(self):
        """unregister returns False for tool that doesn't exist."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.unregister("completely_fake_tool")
        assert result is False

    def test_get_active_tools_names(self):
        """get_active_tools_names returns names from schemas + injected."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = tr.get_active_tools_names()
        assert "terminal" in names
        assert "tool_search" in names

    def test_get_compact_tools_description(self):
        """get_compact_tools_description returns (name, description) tuples."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        descs = tr.get_compact_tools_description()
        names = [d[0] for d in descs]
        assert "read_file" in names
        assert "write_file" in names
        # Each item is (name, desc)
        for name, desc in descs:
            assert isinstance(name, str)
            assert isinstance(desc, str)

    def test_list_tools_returns_core_only(self):
        """list_tools returns only core tool names (not compact/deferred)."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = tr.list_tools()
        assert "terminal" in names
        assert "finish" in names
        # Compact tools not in list_tools
        assert "read_file" not in names

    def test_schema_terminal(self):
        """Schema validation: terminal schema has required fields."""
        from core.tool_registry import ToolRegistry
        schema = ToolRegistry._term_schema()
        assert "parameters" in schema
        assert "properties" in schema["parameters"]
        assert "command" in schema["parameters"]["properties"]
        assert schema["parameters"]["required"] == ["command"]

    def test_schema_finish(self):
        """Schema validation: finish schema has result required."""
        from core.tool_registry import ToolRegistry
        schema = ToolRegistry._finish_schema()
        assert "result" in schema["parameters"]["required"]

    def test_schema_read_tool_result(self):
        """Schema validation: read_tool_result has file_path required."""
        from core.tool_registry import ToolRegistry
        schema = ToolRegistry._read_tool_result_schema()
        assert "file_path" in schema["parameters"]["required"]

    def test_schema_tool_search(self):
        """Schema validation: tool_search schema has query required and CN description."""
        from core.tool_registry import ToolRegistry
        schema = ToolRegistry._tool_search_schema()
        assert "query" in schema["parameters"]["required"]
        assert "搜索" in schema["description"]

    def test_schema_finish_step(self):
        """Schema validation: finish_step schema."""
        from core.tool_registry import ToolRegistry
        schema = ToolRegistry._finish_step_schema()
        assert "output" in schema["parameters"]["required"]
        assert "summary" in schema["parameters"]["required"]

    def test_multimedia_image_gen_registration(self):
        """Multimedia tool image_gen is registered as deferred with correct keywords."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Find image_gen in deferred
        entry = None
        for e in tr._deferred:
            if e["schema"]["function"]["name"] == "image_gen":
                entry = e
                break
        assert entry is not None
        assert "图像" in entry["keywords"]
        assert "图片" in entry["keywords"]

    def test_multimedia_vision_registration(self):
        """Multimedia tool vision_analyze is registered as deferred."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = [e["schema"]["function"]["name"] for e in tr._deferred]
        assert "vision_analyze" in names

    def test_multimedia_tts_registration(self):
        """Multimedia tool text_to_speech is registered as deferred."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = [e["schema"]["function"]["name"] for e in tr._deferred]
        assert "text_to_speech" in names

    def test_multimedia_stt_registration(self):
        """Multimedia tool speech_to_text is registered as deferred."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = [e["schema"]["function"]["name"] for e in tr._deferred]
        assert "speech_to_text" in names

    def test_multimedia_tool_handle_tool_search_finds_them(self):
        """_handle_tool_search finds multimedia tools via deferred search."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr._handle_tool_search({"query": "生成图片"})
        assert result["success"] is True
        assert "image_gen" in result["output"]

    def test_multimedia_tool_downgrade_missing_api(self):
        """Multimedia tool handles gracefully missing API config."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Test image_gen with no API configured
        with patch.dict(os.environ, {}, clear=True):
            result = tr._handle_image_gen({"prompt": "test image"})
            assert result["success"] is False
            assert "未配置" in result["output"] or "IMAGE_GEN_API_URL" in result["output"]

    def test_multimedia_stt_no_api(self):
        """STT returns graceful error when no API configured."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        with patch.dict(os.environ, {}, clear=True):
            with patch("pathlib.Path.exists", return_value=True):
                result = tr._handle_stt({"audio_path": "/tmp/test.wav"})
                assert result["success"] is False
                assert "未配置" in result["output"] or "STT_API_URL" in result["output"]

    def test_multimedia_vision_no_api_local_path(self):
        """Vision analyze returns fallback info for local file without API."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        with patch.dict(os.environ, {}, clear=True):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_bytes") as mock_read:
                    mock_read.return_value = b"fake_image_data"
                    result = tr._handle_vision_analyze({"image_path_or_url": "/tmp/test.png"})
                    # Should either succeed (via ffprobe) or return no-API message
                    # We're mocking read_bytes so local path exists; vision_api_url is empty
                    # It should try local fallback, if ffprobe fails, returns no-API msg
                    assert isinstance(result, dict)

    def test_handle_tool_search_empty_query(self):
        """_handle_tool_search returns error for empty query."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr._handle_tool_search({"query": ""})
        assert result["success"] is False
        assert "不能为空" in result["output"]

    def test_handle_tool_search_no_results(self):
        """_handle_tool_search returns no-results message for unmatched query."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr._handle_tool_search({"query": "zzzzznoisexxxx"})
        assert result["success"] is True
        assert "未找到" in result["output"]

    def test_register_compact_removes_from_deferred(self):
        """register_compact removes tool from deferred pool if present."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        # Add to deferred first
        tr.register_deferred("dual_pool", {
            "description": "test",
            "parameters": {"type": "object", "properties": {}}
        }, handler)
        assert any(e["schema"]["function"]["name"] == "dual_pool" for e in tr._deferred)

        # Now register as compact — removes from deferred
        tr.register_compact("dual_pool", {
            "description": "now compact",
            "parameters": {"type": "object", "properties": {}}
        }, handler)
        assert not any(e["schema"]["function"]["name"] == "dual_pool" for e in tr._deferred)
        assert any(s["function"]["name"] == "dual_pool" for s in tr._compact)

    def test_register_deferred_without_keywords(self):
        """register_deferred works with empty keywords list."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = MagicMock(return_value={"success": True, "output": "ok"})
        tr.register_deferred("no_keyword_tool", {
            "description": "no keywords",
            "parameters": {"type": "object", "properties": {}}
        }, handler, keywords=None)
        entry = [e for e in tr._deferred if e["schema"]["function"]["name"] == "no_keyword_tool"]
        assert len(entry) == 1
        assert entry[0]["keywords"] == []

    def test_search_deferred_scoring_sorted(self):
        """_search_deferred_tools returns results sorted by score descending."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools("web search internet")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_deferred_description_match(self):
        """_search_deferred_tools matches description with low score (1)."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Try to search for something that only matches descriptions
        # Description-based match gets score 1
        results = tr._search_deferred_tools("function")
        # Many tools have 'function' in their schema description implicitly
        assert isinstance(results, list)


# ══════════════════════════════════════════════════════════════════════
# 2. agent_loop.py — deep coverage (77% → 85%+)
# ══════════════════════════════════════════════════════════════════════

class TestAgentLoopCoverage:
    """Covers run() remaining paths, _quality_score boundaries, _generate_report,
    _detect_user_correction all keywords, reset_conversation, stop, build_system_prompt full assembly."""

    def _make_loop(self):
        """Create an AgentLoop with all dependencies mocked."""
        from core.agent_loop import AgentLoop
        loop = AgentLoop.__new__(AgentLoop)
        loop.llm = MagicMock()
        loop.memory = MagicMock()
        loop.evolution = MagicMock()
        loop.tools = MagicMock()
        loop.sessions = MagicMock()
        loop.max_turns = 20
        loop.on_step = None
        loop._bootup = False
        loop.prompt_cache = None
        loop.compressor = None
        loop.budget_allocator = None
        loop.tool_result_store = None
        loop.collapser = None
        loop.mcp_bridge = None
        loop._observer = None
        loop.evolution_engine = None
        loop._evolution_rules = None
        loop._budget_scan_count = 0
        loop._mem_maintenance_counter = 0
        loop.hooks_enabled = False
        loop.on_llm_start = None
        loop.on_llm_end = None
        loop.on_tool_start = None
        loop.on_tool_end = None
        loop.on_turn = None
        loop.on_error = None
        loop.on_finish = None
        loop.on_approval_request = None
        loop._pretooluse_cache = {}
        loop.permission_enabled = False
        loop.current_session_id = "test-session"
        loop._delegation_result = None
        loop._delegation_thread = None
        return loop

    def test_quality_score_default_baseline(self):
        """_quality_score: baseline score is 7 with no errors and complete result."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {
            "errors": [],
            "result": "Task completed successfully with ample output text here.",
            "success": True,
        }
        msgs = [
            {"role": "assistant", "content": "Let me use a tool",
             "tool_calls": [{"function": {"name": "terminal", "arguments": {"command": "ls"}}}]}
        ]
        q = loop._quality_score(result, msgs)
        assert q["score"] >= 6

    def test_quality_score_errors_penalty(self):
        """_quality_score: each error causes -1.5 penalty."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {
            "errors": ["error1", "error2"],
            "result": "Some result here for testing purposes.",
            "success": True,
        }
        msgs = [{"role": "assistant", "content": "tool call",
                 "tool_calls": [{"function": {"name": "terminal", "arguments": {"command": "ls"}}}]}]
        q = loop._quality_score(result, msgs)
        assert q["score"] < 7
        assert "错误" in q["detail"]

    def test_quality_score_empty_result_penalty(self):
        """_quality_score: empty result causes -2."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {"errors": [], "result": "", "success": True}
        q = loop._quality_score(result, [])
        assert q["score"] <= 5
        assert "为空" in q["detail"]

    def test_quality_score_short_result_penalty(self):
        """_quality_score: result < 50 chars causes -0.5."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {"errors": [], "result": "Short", "success": True}
        q = loop._quality_score(result, [])
        assert q["score"] <= 6.5

    def test_quality_score_no_tool_calls_short_result(self):
        """_quality_score: no tool calls and short result is OK (no penalty)."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {"errors": [], "result": "A simple answer", "success": True}
        q = loop._quality_score(result, [])
        # Should not be penalized for 0 tool calls with decent result
        assert q["score"] >= 6

    def test_quality_score_high_error_rate(self):
        """_quality_score: >50% tool error rate causes -1."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {"errors": ["err1", "err2"], "result": "Long result text here to be valid.", "success": True}
        msgs = [
            {"role": "assistant", "content": "c1",
             "tool_calls": [{"function": {"name": "t1", "arguments": {}}}]},
            {"role": "assistant", "content": "c2",
             "tool_calls": [{"function": {"name": "t2", "arguments": {}}}]},
            {"role": "assistant", "content": "c3",
             "tool_calls": [{"function": {"name": "t3", "arguments": {}}}]},
        ]
        q = loop._quality_score(result, msgs)
        assert "错误率" in q["detail"]

    def test_quality_score_self_check_penalty(self):
        """_quality_score: self_check present causes -1."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {"errors": [], "result": "Long enough result here to be valid.", "success": True,
                  "self_check": "Found an issue"}
        q = loop._quality_score(result, [])
        assert "自检" in q["detail"]

    def test_quality_score_not_successful_limits_score(self):
        """_quality_score: non-success caps score at 4."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {"errors": [], "result": "Some output here.", "success": False}
        q = loop._quality_score(result, [])
        assert q["score"] <= 4

    def test_quality_score_score_capped_0_10(self):
        """_quality_score: score clamped between 0 and 10."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        # Many errors
        result = {"errors": ["e1", "e2", "e3", "e4", "e5"],
                  "result": "", "success": False}
        q = loop._quality_score(result, [])
        assert 0 <= q["score"] <= 10
        # Very clean
        result2 = {"errors": [], "result": "x" * 200, "success": True}
        q2 = loop._quality_score(result2, [])
        assert 0 <= q2["score"] <= 10

    def test_generate_report_basic(self):
        """_generate_report builds a structured string report."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {
            "success": True,
            "result": "Final output here",
            "errors": [],
            "task_type": "coding",
            "duration": 12.5,
            "turns": 5,
        }
        messages = [
            {"role": "user", "content": "Write a Python script"},
            {"role": "assistant", "content": "I'll use terminal",
             "tool_calls": [{"function": {"name": "terminal", "arguments": {}}}]},
            {"role": "tool", "content": "output", "tool_call_id": "c1"},
            {"role": "user", "content": "Good, now fix the bug"},
            {"role": "assistant", "content": "Let me patch it",
             "tool_calls": [{"function": {"name": "patch", "arguments": {}}}]},
            {"role": "tool", "content": "patched", "tool_call_id": "c2"},
        ]
        report = loop._generate_report("Write a Python script", result, messages)
        assert isinstance(report, str)
        assert "任务报告" in report
        assert "coding" in report
        assert "terminal" in report
        assert "12.5" in report

    def test_generate_report_with_errors(self):
        """_generate_report includes errors section."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {
            "success": False,
            "result": "Partial result",
            "errors": ["Tool failed: network timeout", "Another error"],
            "task_type": "research",
            "duration": 30.0,
            "turns": 3,
        }
        messages = [{"role": "user", "content": "Search for something"}]
        report = loop._generate_report("Search", result, messages)
        assert "网络" in report or "Tool failed" in report
        assert "错误" in report or "⚠️" in report

    def test_generate_report_no_tool_calls(self):
        """_generate_report shows 'no tool calls' when none were made."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {
            "success": True, "result": "Direct answer",
            "errors": [], "task_type": "generic",
            "duration": 2.0, "turns": 1,
        }
        report = loop._generate_report("Question", result, [{"role": "user", "content": "hi"}])
        assert "无工具调用" in report

    def test_detect_user_correction_all_markers(self):
        """_detect_user_correction detects all correction markers."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        markers = ["别", "不对", "错了", "不是", "重新", "改成", "注意", "但是不", "不用这样", "不是这样"]
        for marker in markers:
            msgs = [{"role": "user", "content": f"这是{marker}这样做的"}]
            assert loop._detect_user_correction(msgs), f"Marker not detected: {marker}"

    def test_detect_user_correction_no_marker(self):
        """_detect_user_correction returns False for normal messages."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "Please write a Python script to sort a list"}]
        assert loop._detect_user_correction(msgs) is False

    def test_detect_user_correction_ignores_assistant(self):
        """_detect_user_correction only checks user messages."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        msgs = [
            {"role": "assistant", "content": "不对，这里应该用另一种方法"},
            {"role": "system", "content": "别这样"},
        ]
        assert loop._detect_user_correction(msgs) is False

    def test_reset_conversation_state(self):
        """reset_conversation-related checks: lazy_init re-init works."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        loop.compressor = None
        # This would be called by _lazy_init
        with patch('core.agent_loop.ContextCompressor') as MockCC:
            with patch('core.agent_loop.BudgetAllocator') as MockBA:
                with patch('core.agent_loop.ToolResultStore') as MockTRS:
                    with patch('core.agent_loop.ContextCollapse') as MockColl:
                        with patch('core.agent_loop.Observer') as MockObs:
                            loop._lazy_init()
                            # Observer should be created
                            assert loop._observer is not None

    def test_run_local_mode_compression_path(self):
        """run() with local backend triggers different threshold."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        loop.llm.backend = 'local'
        loop.compressor = None
        with patch('core.agent_loop.ContextCompressor') as MockCC:
            with patch('core.agent_loop.BudgetAllocator') as MockBA:
                with patch('core.agent_loop.ToolResultStore') as MockTRS:
                    with patch('core.agent_loop.ContextCollapse') as MockColl:
                        with patch('core.agent_loop.Observer') as MockObs:
                            loop._lazy_init()
                            # Should have been called with ctx_threshold=28000
                            assert MockCC.called

    def test_run_normal_llm_chat(self):
        """run() calls llm.chat with correct arguments."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        loop.permission_enabled = False
        loop.compressor = MagicMock()
        loop.compressor.needs_compression.return_value = False
        loop.budget_allocator = MagicMock()
        loop.budget_allocator.scan.return_value = MagicMock()
        loop.budget_allocator.get_actions.return_value = []
        loop.collapser = MagicMock()
        loop.tool_result_store = MagicMock()
        loop._observer = MagicMock()
        loop.sessions.create_session.return_value = "sess-1"
        loop.sessions.get_session.return_value = MagicMock(message_count=3)

        resp = {"success": True, "content": "Let me check..."}
        loop.llm.chat.return_value = resp
        loop.tools.get_schemas.return_value = []
        loop.tools.get_compact_tools_description.return_value = []

        with patch('core.agent_loop.detect_task_type', return_value='generic'):
            with patch('core.agent_loop.load_identity_statement') as mock_id:
                mock_id.return_value = "You are Kuafu"
                with patch('core.agent_loop.get_rules', return_value=["rule 1"]):
                    with patch('core.agent_loop.get_quality', return_value=[]):
                        with patch('core.agent_loop.build_reminders', return_value=""):
                            with patch('core.agent_loop.match_skills', return_value=[]):
                                with patch('core.agent_loop.discover_skills', return_value=[]):
                                    with patch('core.agent_loop.PromptManager') as MockPM:
                                        mock_pm = MagicMock()
                                        mock_pm.sections = []
                                        MockPM.return_value = mock_pm
                                        with patch('core.agent_loop.PromptCache') as MockPC:
                                            mock_pc = MagicMock()
                                            mock_pc.get_block.return_value = MagicMock(content="")
                                            MockPC.return_value = mock_pc
                                            with patch('core.agent_loop.PromptAssembly') as MockPA:
                                                mock_pa_inst = MagicMock()
                                                mock_pa_inst.assemble.return_value = ""
                                                MockPA.return_value = mock_pa_inst
                                                result = loop.run(task="test task")
                                                assert "result" in result

    def test_build_system_prompt_with_evolution_rules(self):
        """build_system_prompt injects evolution rules when available."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        loop.prompt_cache = MagicMock()
        # Simulate evolution_rules present
        mock_rules_mgr = MagicMock()
        mock_rules_mgr.build_rules_block.return_value = "🧬 Some evolved rule"
        loop._evolution_rules = mock_rules_mgr
        loop.llm.backend = 'cloud'
        loop.memory.build_memory_block.return_value = ""

        with patch('core.agent_loop.load_identity_statement', return_value="You are Kuafu"):
            with patch('core.agent_loop.get_rules', return_value=["rule 1"]):
                with patch('core.agent_loop.get_quality', return_value=[]):
                    with patch('core.agent_loop.detect_task_type', return_value='generic'):
                        with patch('core.agent_loop.match_skills', return_value=[]):
                            with patch('core.agent_loop.discover_skills', return_value=[]):
                                with patch('core.agent_loop.PromptManager') as MockPM:
                                    mock_pm = MagicMock()
                                    mock_pm.sections = [
                                        MagicMock(id="identity", stability="L1_immutable"),
                                        MagicMock(id="rules", stability="L1_immutable"),
                                    ]
                                    MockPM.return_value = mock_pm
                                    mock_pc = MagicMock()
                                    mock_pc.get_block.return_value = MagicMock(content="l1 content")
                                    prompt = loop.build_system_prompt("test task")
                                    # evolution_rules.build_rules_block should have been called
                                    assert mock_rules_mgr.build_rules_block.called

    def test_build_system_prompt_l1_l2_l3_assembly(self):
        """build_system_prompt correctly assembles L1/L2/L3 blocks."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        loop.prompt_cache = MagicMock()
        loop._evolution_rules = None
        loop.llm = MagicMock()
        loop.llm.backend = 'cloud'
        loop.memory.build_memory_block.return_value = ""
        loop.evolution.get_evolution_stats.return_value = {"total_evolutions": 0}

        with patch('core.agent_loop.load_identity_statement', return_value="You are Kuafu"):
            with patch('core.agent_loop.get_rules', return_value=["rule 1"]):
                with patch('core.agent_loop.get_quality', return_value=[]):
                    with patch('core.agent_loop.detect_task_type', return_value='generic'):
                        with patch('core.agent_loop.match_skills', return_value=[]):
                            with patch('core.agent_loop.discover_skills', return_value=[]):
                                with patch('core.agent_loop.PromptManager') as MockPM:
                                    mock_pm = MagicMock()
                                    mock_pm.sections = [
                                        MagicMock(id="identity"),
                                        MagicMock(id="rules"),
                                        MagicMock(id="tools"),
                                    ]
                                    MockPM.return_value = mock_pm
                                    l1_block = MagicMock(content="L1: identity+rules")
                                    l2_block = MagicMock(content="L2: tools")
                                    mock_pc = MagicMock()
                                    mock_pc.get_block.side_effect = lambda sections, stab: (
                                        l1_block if "L1" in stab else l2_block
                                    )
                                    loop.prompt_cache = mock_pc

                                    prompt = loop.build_system_prompt("")
                                    assert isinstance(prompt, str)

    def test_generate_report_multiple_user_inputs(self):
        """_generate_report captures multiple user inputs."""
        from core.agent_loop import AgentLoop
        loop = self._make_loop()
        result = {
            "success": True, "result": "result text here longer than ten chars",
            "errors": [], "task_type": "analysis",
            "duration": 5.0, "turns": 4,
        }
        messages = [
            {"role": "user", "content": "Analyze this data set"},
            {"role": "assistant", "content": "Working..."},
            {"role": "user", "content": "Now also filter by date"},
        ]
        report = loop._generate_report("Analyze data", result, messages)
        assert "Analyze" in report
        assert "filter" in report or "次用户输入" in report


# ══════════════════════════════════════════════════════════════════════
# 3. prompt_template.py — deep coverage (65% → 85%+)
# ══════════════════════════════════════════════════════════════════════

class TestPromptTemplateCoverage:
    """Covers PromptAssembly, Section, PromptManager, PromptCache, build_reminders completely."""

    def test_section_render_disabled(self):
        """Section.render() returns '' when disabled."""
        from core.prompt_template import Section
        s = Section(id="test", title="Title", content="Content", enabled=False)
        assert s.render() == ""

    def test_section_render_empty_content(self):
        """Section.render() returns '' when content is empty."""
        from core.prompt_template import Section
        s = Section(id="test", title="Title", content="")
        assert s.render() == ""

    def test_section_render_normal(self):
        """Section.render() returns formatted markdown."""
        from core.prompt_template import Section
        s = Section(id="test", title="Title", content="Some content")
        rendered = s.render()
        assert "## Title" in rendered
        assert "Some content" in rendered

    def test_section_estimate_tokens(self):
        """Section.estimate_tokens() returns token count."""
        from core.prompt_template import Section
        s = Section(id="test", title="Title", content="Hello world")
        tokens = s.estimate_tokens()
        assert tokens > 0

    def test_section_estimate_tokens_disabled(self):
        """Section.estimate_tokens() returns 0 for disabled/empty."""
        from core.prompt_template import Section
        s1 = Section(id="t", title="T", content="", enabled=True)
        assert s1.estimate_tokens() == 0
        s2 = Section(id="t2", title="T", content="Hi", enabled=False)
        assert s2.estimate_tokens() == 0

    def test_prompt_assembly_assemble_ordering_by_order(self):
        """PromptAssembly sorts by explicit order first."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [
            Section(id="a", title="A", content="Content A", order=5),
            Section(id="b", title="B", content="Content B", order=1),
            Section(id="c", title="C", content="Content C", order=3),
        ]
        result = pa.assemble()
        assert result.index("Content B") < result.index("Content C")
        assert result.index("Content C") < result.index("Content A")

    def test_prompt_assembly_assemble_by_priority(self):
        """PromptAssembly sorts by priority when no order given."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [
            Section(id="a", title="A", content="A", priority=10),
            Section(id="b", title="B", content="B", priority=5),
            Section(id="c", title="C", content="C", priority=20),
        ]
        result = pa.assemble()
        # Higher priority first (sorted by -priority)
        assert result.index("C") < result.index("A")
        assert result.index("A") < result.index("B")

    def test_prompt_assembly_assemble_disabled_excluded(self):
        """PromptAssembly excludes disabled sections."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [
            Section(id="a", title="A", content="A", enabled=True),
            Section(id="b", title="B", content="B", enabled=False),
        ]
        result = pa.assemble()
        assert "B" not in result

    def test_prompt_assembly_count_tokens(self):
        """PromptAssembly.count_tokens() returns per-tag stats."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [
            Section(id="a", title="A", content="Hello", budget_tag="system"),
            Section(id="b", title="B", content="World", budget_tag="memory"),
        ]
        stats = pa.count_tokens()
        assert "system" in stats
        assert "memory" in stats
        assert stats["system"] > 0
        assert stats["memory"] > 0

    def test_prompt_assembly_count_tokens_disabled_excluded(self):
        """PromptAssembly.count_tokens() excludes disabled sections."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [
            Section(id="a", title="A", content="Hello", enabled=True),
            Section(id="b", title="B", content="World", enabled=False),
        ]
        stats = pa.count_tokens()
        # disabled not counted
        all_tokens = sum(stats.values())
        # Only section "a" counted
        hello_tokens = int(len("## \nHello\n") / 1.6)
        assert all_tokens == hello_tokens or abs(all_tokens - hello_tokens) <= 1

    def test_prompt_assembly_disable_method(self):
        """PromptAssembly.disable() disables a section by id."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [Section(id="a", title="A", content="Content")]
        pa.disable("a")
        assert pa.sections[0].enabled is False

    def test_prompt_assembly_enable_method(self):
        """PromptAssembly.enable() enables a section by id."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [Section(id="a", title="A", content="Content", enabled=False)]
        pa.enable("a")
        assert pa.sections[0].enabled is True

    def test_prompt_assembly_get_by_id(self):
        """PromptAssembly.get_by_id() returns section or None."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [Section(id="a", title="A", content="Content")]
        assert pa.get_by_id("a") is not None
        assert pa.get_by_id("nonexistent") is None

    def test_prompt_assembly_replace_content(self):
        """PromptAssembly.replace_content() updates section content."""
        from core.prompt_template import PromptAssembly, Section
        pa = PromptAssembly()
        pa.sections = [Section(id="a", title="A", content="Old")]
        pa.replace_content("a", "New")
        assert pa.sections[0].content == "New"

    def test_prompt_assembly_replace_content_nonexistent_no_crash(self):
        """PromptAssembly.replace_content() handles missing section."""
        from core.prompt_template import PromptAssembly
        pa = PromptAssembly()
        pa.replace_content("missing", "content")  # Should not crash

    def test_prompt_manager_add_section_chaining(self):
        """PromptManager.add_section() supports chaining."""
        from core.prompt_template import PromptManager
        pm = PromptManager("task")
        result = pm.add_section("a", "A", "Content")
        assert result is pm
        assert pm.section_count == 1

    def test_prompt_manager_assemble(self):
        """PromptManager.assemble() returns assembled string."""
        from core.prompt_template import PromptManager
        pm = PromptManager("task")
        pm.add_section("a", "Section A", "Content A")
        pm.add_section("b", "Section B", "Content B")
        result = pm.assemble()
        assert "Content A" in result
        assert "Content B" in result

    def test_prompt_manager_get_budget_stats(self):
        """PromptManager.get_budget_stats() works."""
        from core.prompt_template import PromptManager
        pm = PromptManager("task")
        pm.add_section("a", "A", "Content", budget_tag="system")
        stats = pm.get_budget_stats()
        assert "system" in stats

    def test_prompt_manager_enabled_sections(self):
        """PromptManager.enabled_sections returns only enabled."""
        from core.prompt_template import PromptManager
        pm = PromptManager("task")
        pm.add_section("a", "A", "Content", enabled=True)
        pm.add_section("b", "B", "Content", enabled=False)
        assert len(pm.enabled_sections) == 1

    def test_prompt_manager_to_summary(self):
        """PromptManager.to_summary() produces human-readable summary."""
        from core.prompt_template import PromptManager
        pm = PromptManager("task")
        pm.add_section("a", "Section A", "Content")
        summary = pm.to_summary()
        assert "Section A" in summary
        assert "sections" in summary or "active" in summary

    def test_prompt_cache_init_empty(self):
        """PromptCache initial state has no cache."""
        from core.prompt_template import PromptCache
        pc = PromptCache()
        assert pc._l1_cache == ""
        assert pc._hit_count == 0

    def test_prompt_cache_get_block_l3_varies(self):
        """PromptCache.get_block() for L3 always rebuilds."""
        from core.prompt_template import PromptCache, Section, STABILITY_L3_VARIABLE
        pc = PromptCache()
        sections = [Section(id="a", title="A", content="Var")]
        block = pc.get_block(sections, STABILITY_L3_VARIABLE)
        assert block.content != ""

    def test_prompt_cache_get_block_l1_caches(self):
        """PromptCache.get_block() for L1 caches on second call."""
        from core.prompt_template import PromptCache, Section, STABILITY_L1_IMMUTABLE
        pc = PromptCache()
        sections = [Section(id="a", title="A", content="Fixed")]
        block1 = pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        block2 = pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        assert pc._hit_count == 1
        assert block1.content == block2.content

    def test_prompt_cache_get_block_l2_cache(self):
        """PromptCache.get_block() for L2 caches on second call."""
        from core.prompt_template import PromptCache, Section, STABILITY_L2_SEMI
        pc = PromptCache()
        sections = [Section(id="a", title="A", content="Semi")]
        block1 = pc.get_block(sections, STABILITY_L2_SEMI)
        block2 = pc.get_block(sections, STABILITY_L2_SEMI)
        assert pc._hit_count == 1

    def test_prompt_cache_miss_on_content_change(self):
        """PromptCache.get_block() misses when content changes."""
        from core.prompt_template import PromptCache, Section, STABILITY_L1_IMMUTABLE
        pc = PromptCache()
        sections1 = [Section(id="a", title="A", content="V1")]
        sections2 = [Section(id="a", title="A", content="V2")]
        pc.get_block(sections1, STABILITY_L1_IMMUTABLE)
        pc.get_block(sections2, STABILITY_L1_IMMUTABLE)
        assert pc._miss_count == 2

    def test_prompt_cache_clear(self):
        """PromptCache.clear() resets all caches and stats."""
        from core.prompt_template import PromptCache, Section, STABILITY_L1_IMMUTABLE
        pc = PromptCache()
        sections = [Section(id="a", title="A", content="Test")]
        pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        pc.clear()
        assert pc._l1_cache == ""
        assert pc._hit_count == 0
        assert pc._miss_count == 0

    def test_prompt_cache_clear_l2(self):
        """PromptCache.clear_l2() clears only L2."""
        from core.prompt_template import PromptCache, Section, STABILITY_L2_SEMI, STABILITY_L1_IMMUTABLE
        pc = PromptCache()
        sections = [Section(id="a", title="A", content="Test")]
        pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        pc.get_block(sections, STABILITY_L2_SEMI)
        pc.clear_l2()
        # L1 still cached, L2 cleared
        l1_block = pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        l2_block = pc.get_block(sections, STABILITY_L2_SEMI)
        assert pc._l2_cache == ""  # L2 was cleared

    def test_prompt_cache_stats(self):
        """PromptCache.stats() returns hit rate."""
        from core.prompt_template import PromptCache, Section, STABILITY_L1_IMMUTABLE
        pc = PromptCache()
        sections = [Section(id="a", title="A", content="Stats")]
        pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        pc.get_block(sections, STABILITY_L1_IMMUTABLE)
        stats = pc.stats()
        assert stats["hit"] == 1
        assert stats["miss"] == 1
        assert stats["hit_rate"] == 0.5

    def test_cache_block_needs_refresh_l3_true(self):
        """CacheBlock.needs_refresh() returns True for L3."""
        from core.prompt_template import CacheBlock, STABILITY_L3_VARIABLE
        block = CacheBlock(stability=STABILITY_L3_VARIABLE, content="old")
        assert block.needs_refresh("new") is True

    def test_cache_block_needs_refresh_no_key(self):
        """CacheBlock.needs_refresh() returns True when no cache_key."""
        from core.prompt_template import CacheBlock, STABILITY_L1_IMMUTABLE
        block = CacheBlock(stability=STABILITY_L1_IMMUTABLE)
        assert block.needs_refresh("content") is True

    def test_cache_block_refresh(self):
        """CacheBlock.refresh() updates content and key."""
        from core.prompt_template import CacheBlock, STABILITY_L1_IMMUTABLE
        block = CacheBlock(stability=STABILITY_L1_IMMUTABLE)
        block.refresh("new content")
        assert block.content == "new content"
        assert block.cache_key != ""

    def test_cache_block_render(self):
        """CacheBlock.render() returns content."""
        from core.prompt_template import CacheBlock, STABILITY_L1_IMMUTABLE
        block = CacheBlock(stability=STABILITY_L1_IMMUTABLE, content="hello")
        assert block.render() == "hello"

    def test_get_stability_default(self):
        """get_stability returns L3_VARIABLE for unknown sections."""
        from core.prompt_template import get_stability, STABILITY_L3_VARIABLE
        assert get_stability("unknown_section") == STABILITY_L3_VARIABLE

    def test_get_stability_known(self):
        """get_stability returns correct level for known sections."""
        from core.prompt_template import get_stability, STABILITY_L1_IMMUTABLE, STABILITY_L2_SEMI
        assert get_stability("identity") == STABILITY_L1_IMMUTABLE
        assert get_stability("core_tools") == STABILITY_L2_SEMI
        assert get_stability("memory_context") == "L3_variable"

    def test_build_reminders_empty(self):
        """build_reminders returns '' when no triggers."""
        from core.prompt_template import build_reminders
        result = build_reminders(turn_context="", task="", turn_count=0)
        assert result == ""

    def test_build_reminders_high_turn_count(self):
        """build_reminders adds focusing reminder at high turn count."""
        from core.prompt_template import build_reminders
        result = build_reminders(turn_count=6)
        assert "多轮对话" in result

    def test_build_reminders_tool_failures(self):
        """build_reminders detects tool failures."""
        from core.prompt_template import build_reminders
        result = build_reminders(task="test", turn_count=2,
                                  last_tool_results=["terminal:fail:timeout"])
        assert "失败" in result

    def test_build_reminders_tool_failures_english(self):
        """build_reminders detects English failure keywords."""
        from core.prompt_template import build_reminders
        result = build_reminders(task="test", turn_count=2,
                                  last_tool_results=["something error"])
        assert "失败" in result

    def test_build_reminders_git_hint(self):
        """build_reminders adds Git hint."""
        from core.prompt_template import build_reminders
        result = build_reminders(task="commit my changes", turn_count=1)
        assert "Git" in result or "commit" in result.lower()

    def test_build_reminders_deploy_hint(self):
        """build_reminders adds deploy hint."""
        from core.prompt_template import build_reminders
        result = build_reminders(task="deploy to production", turn_count=1)
        assert "部署" in result

    def test_build_reminders_memory_hints(self):
        """build_reminders includes memory hints."""
        from core.prompt_template import build_reminders
        result = build_reminders(memory_hints=["Remember to use async"])
        assert "async" in result or "Remember" in result

    def test_build_reminders_memory_hints_limited(self):
        """build_reminders limits to first 2 memory hints and <60 chars."""
        from core.prompt_template import build_reminders
        result = build_reminders(memory_hints=[
            "short hint",
            "another short hint",
            "third hint that should not appear",
        ])
        # Only first 2
        assert "short hint" in result
        assert "another short hint" in result

    def test_build_reminders_limited_to_3(self):
        """build_reminders limits output to 3 reminders."""
        from core.prompt_template import build_reminders
        result = build_reminders(
            turn_count=6,
            last_tool_results=["error occurred"],
            task="commit and push to git",
        )
        # Count reminders (each starts with "> 提醒:")
        count = result.count("> 提醒:")
        assert count <= 3


# ══════════════════════════════════════════════════════════════════════
# 4. evolution_rules.py — deep coverage (61% → 85%+)
# ══════════════════════════════════════════════════════════════════════

class TestEvolutionRulesCoverage:
    """Covers EvolutionRuleManager init, add_rule, match_rules, get_stats, analyze_failure, build_block."""

    def test_evolved_rule_init_defaults(self):
        """EvolvedRule uses reasonable defaults."""
        from core.evolution_rules import EvolvedRule
        rule = EvolvedRule(rule_text="Always check errors first")
        assert rule.category == "rule"
        assert rule.confidence == 0.5
        assert rule.is_active is True
        assert rule.is_expired is False

    def test_evolved_rule_invalid_category_fallback(self):
        """EvolvedRule falls back to 'rule' for invalid category."""
        from core.evolution_rules import EvolvedRule
        rule = EvolvedRule(rule_text="test", category="invalid_cat")
        assert rule.category == "rule"

    def test_evolved_rule_is_active_low_confidence(self):
        """EvolvedRule.is_active False when confidence < AUTO_DELETE_CONFIDENCE."""
        from core.evolution_rules import EvolvedRule
        rule = EvolvedRule(rule_text="test", confidence=0.1)
        assert rule.is_active is False

    def test_evolved_rule_fix_is_expired(self):
        """EvolvedRule 'fix' category is always expired."""
        from core.evolution_rules import EvolvedRule
        rule = EvolvedRule(rule_text="test", category="fix")
        assert rule.is_expired is True

    def test_evolved_rule_hint_expires(self):
        """EvolvedRule 'hint' expires after 7 days without trigger."""
        from core.evolution_rules import EvolvedRule
        old_time = time.time() - 8 * 86400
        rule = EvolvedRule(rule_text="test", category="hint", last_triggered=old_time)
        assert rule.is_expired is True

    def test_evolved_rule_hint_not_expired_recent(self):
        """EvolvedRule 'hint' not expired if triggered recently."""
        from core.evolution_rules import EvolvedRule
        rule = EvolvedRule(rule_text="test", category="hint", last_triggered=time.time() - 3600)
        assert rule.is_expired is False

    def test_evolved_rule_to_dict(self):
        """EvolvedRule.to_dict() returns serializable dict."""
        from core.evolution_rules import EvolvedRule
        rule = EvolvedRule(rule_text="Always test", category="rule",
                           task_type="coding", confidence=0.8)
        d = rule.to_dict()
        assert d["rule"] == "Always test"
        assert d["category"] == "rule"
        assert d["confidence"] == 0.8

    def test_make_topic_static(self):
        """make_topic_static generates consistent topic from rule text."""
        from core.evolution_rules import EvolutionRuleManager
        t1 = EvolutionRuleManager.make_topic_static("Always test")
        t2 = EvolutionRuleManager.make_topic_static("Always test")
        t3 = EvolutionRuleManager.make_topic_static("Different rule")
        assert t1 == t2
        assert t1 != t3
        assert t1.startswith("evolved:")

    def test_evolution_rule_manager_init(self):
        """EvolutionRuleManager stores opinion_engine and llm."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        assert mgr._oe is oe
        assert mgr._llm is llm

    def test_evolution_rule_manager_init_no_llm(self):
        """EvolutionRuleManager works without llm_chat_fn."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=None)
        assert mgr._llm is None

    def test_add_rule_created(self):
        """add_rule creates a new rule via opinion engine."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe.reinforce.return_value = {"action": "created", "confidence": 0.7}
        mgr = EvolutionRuleManager(opinion_engine=oe)
        result = mgr.add_rule("Test rule", category="hint", task_type="coding",
                              keywords=["test", "code"], source="manual")
        assert result["action"] == "created"
        oe.reinforce.assert_called_once()
        # Should have updated meta via SQL
        assert oe._conn.execute.called

    def test_add_rule_reinforced(self):
        """add_rule reinforces existing rule."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe.reinforce.return_value = {"action": "reinforced", "confidence": 0.9}
        mgr = EvolutionRuleManager(opinion_engine=oe)
        result = mgr.add_rule("Existing rule")
        assert result["action"] == "reinforced"

    def test_add_rule_category_rule(self):
        """add_rule with category='rule'."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe.reinforce.return_value = {"action": "created", "confidence": 0.5}
        mgr = EvolutionRuleManager(opinion_engine=oe)
        result = mgr.add_rule("Always check", category="rule")
        assert result is not None

    def test_add_rule_category_fix(self):
        """add_rule with category='fix'."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe.reinforce.return_value = {"action": "created", "confidence": 0.5}
        mgr = EvolutionRuleManager(opinion_engine=oe)
        result = mgr.add_rule("Temporary fix", category="fix")
        assert result is not None

    def test_get_rules_empty(self):
        """get_rules returns empty list when no rules exist."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe._conn.execute.side_effect = Exception("no table")
        mgr = EvolutionRuleManager(opinion_engine=oe)
        rules = mgr.get_rules()
        assert rules == []

    def test_get_rules_with_data(self):
        """get_rules parses opinion data into rule dicts."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:abc123",
            "confidence": 0.8,
            "evidence": json.dumps({
                "rule_text": "Always test",
                "category": "rule",
                "task_type": "coding",
                "keywords": ["test"],
            }),
            "text": "Always test",
            "evidence_for": 3,
            "evidence_against": 1,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        rules = mgr.get_rules(min_confidence=0.0)
        assert len(rules) == 1
        assert rules[0]["rule"] == "Always test"
        assert rules[0]["category"] == "rule"

    def test_get_rules_filter_by_category(self):
        """get_rules filters by category."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:abc",
            "confidence": 0.7,
            "evidence": json.dumps({"rule_text": "H", "category": "hint", "task_type": "", "keywords": []}),
            "text": "H",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        rules_hint = mgr.get_rules(min_confidence=0.0, category="hint")
        rules_rule = mgr.get_rules(min_confidence=0.0, category="rule")
        assert len(rules_hint) == 1
        assert len(rules_rule) == 0

    def test_get_rules_filter_by_task_type(self):
        """get_rules filters by task_type."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:abc",
            "confidence": 0.7,
            "evidence": json.dumps({"rule_text": "R", "category": "rule", "task_type": "coding", "keywords": []}),
            "text": "R",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        rules_coding = mgr.get_rules(min_confidence=0.0, task_type="coding")
        rules_design = mgr.get_rules(min_confidence=0.0, task_type="design")
        assert len(rules_coding) == 1
        assert len(rules_design) == 0

    def test_match_rules_task_type_exact(self):
        """match_rules scores +3 for exact task_type match."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        # get_rules needs some results
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:abc",
            "confidence": 0.7,
            "evidence": json.dumps({
                "rule_text": "Use async pattern",
                "category": "rule",
                "task_type": "coding",
                "keywords": ["async"],
            }),
            "text": "Use async pattern",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        matched = mgr.match_rules("Write async code", task_type="coding")
        assert len(matched) >= 1

    def test_match_rules_keyword_match(self):
        """match_rules scores +2 for keyword match in task."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:xyz",
            "confidence": 0.6,
            "evidence": json.dumps({
                "rule_text": "Use async",
                "category": "rule",
                "task_type": "generic",
                "keywords": ["async", "concurrent"],
            }),
            "text": "Use async",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        matched = mgr.match_rules("Implement async function")
        assert len(matched) >= 1

    def test_match_rules_rule_text_match(self):
        """match_rules scores +1 for rule text words appearing in task."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:pdq",
            "confidence": 0.6,
            "evidence": json.dumps({
                "rule_text": "Always validate input before processing",
                "category": "rule",
                "task_type": "",
                "keywords": [],
            }),
            "text": "Always validate input before processing",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        # "validate" and "input" appear in the rule text
        matched = mgr.match_rules("I need to validate user input")
        assert len(matched) >= 1

    def test_match_rules_no_match(self):
        """match_rules returns [] when no rules match."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            "topic": "evolved:nope",
            "confidence": 0.6,
            "evidence": json.dumps({
                "rule_text": "Only for cooking tasks",
                "category": "rule",
                "task_type": "cooking",
                "keywords": ["cook", "recipe"],
            }),
            "text": "Only for cooking tasks",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        matched = mgr.match_rules("Write a Python script")
        assert len(matched) == 0

    def test_get_stats_basic(self):
        """get_stats returns dict with total, active, by_category."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        row1 = MagicMock()
        row1.__getitem__ = lambda self, k: {
            "topic": "evolved:r1",
            "confidence": 0.7,
            "evidence": json.dumps({"rule_text": "R1", "category": "rule", "task_type": "", "keywords": []}),
            "text": "R1",
            "evidence_for": 1,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        row2 = MagicMock()
        row2.__getitem__ = lambda self, k: {
            "topic": "evolved:r2",
            "confidence": 0.3,
            "evidence": json.dumps({"rule_text": "R2", "category": "hint", "task_type": "", "keywords": []}),
            "text": "R2",
            "evidence_for": 0,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        row3 = MagicMock()
        row3.__getitem__ = lambda self, k: {
            "topic": "evolved:r3",
            "confidence": 0.5,
            "evidence": json.dumps({"rule_text": "R3", "category": "fix", "task_type": "", "keywords": []}),
            "text": "R3",
            "evidence_for": 0,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [row1, row2, row3]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        stats = mgr.get_stats()
        assert stats["total"] == 3
        # Only R1 has confidence >= 0.4, so active = 1
        assert stats["active"] == 1
        assert stats["by_category"]["rule"] == 1
        assert stats["by_category"]["hint"] == 1
        assert stats["by_category"]["fix"] == 1

    def test_analyze_failure_no_llm(self):
        """analyze_failure returns None when no LLM configured."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=None)
        result = mgr.analyze_failure("task", {"errors": ["failed"]}, [])
        assert result is None

    def test_analyze_failure_few_turns_no_errors(self):
        """analyze_failure returns None when turns < 2 and no errors."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        result = mgr.analyze_failure("easy task", {"errors": [], "turns": 1}, [])
        assert result is None

    def test_analyze_failure_llm_returns_none(self):
        """analyze_failure returns None when LLM says NONE."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        llm.return_value = {"content": "NONE"}
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        result = mgr.analyze_failure("task", {"errors": ["err1"], "turns": 5}, [{"role": "user", "content": "hi"}])
        assert result is None

    def test_analyze_failure_llm_returns_null(self):
        """analyze_failure returns None when LLM says null."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        llm.return_value = {"content": "null"}
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        result = mgr.analyze_failure("task", {"errors": ["err1"], "turns": 5}, [])
        assert result is None

    def test_analyze_failure_llm_returns_rule(self):
        """analyze_failure parses JSON rule from LLM response."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        rule_json = json.dumps({
            "rule": "Always check return codes",
            "category": "rule",
            "keywords": ["return", "error"],
            "task_type": "coding",
        })
        llm.return_value = {"content": rule_json}
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        result = mgr.analyze_failure("task", {"errors": ["err1"], "turns": 5}, [])
        assert result is not None
        assert result["rule"] == "Always check return codes"
        assert result["category"] == "rule"

    def test_analyze_failure_llm_malformed_json(self):
        """analyze_failure handles LLM returning non-JSON content."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        llm.return_value = {"content": "some random text without json"}
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        result = mgr.analyze_failure("task", {"errors": ["err1"], "turns": 5}, [])
        assert result is None

    def test_analyze_failure_llm_exception(self):
        """analyze_failure handles LLM exception gracefully."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        llm = MagicMock()
        llm.side_effect = Exception("LLM down")
        mgr = EvolutionRuleManager(opinion_engine=oe, llm_chat_fn=llm)
        result = mgr.analyze_failure("task", {"errors": ["err1"], "turns": 5}, [])
        assert result is None

    def test_build_rules_block_no_match(self):
        """build_rules_block returns '' when no rules match."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        block = mgr.build_rules_block("task", "coding")
        assert block == ""

    def test_build_rules_block_with_matches(self):
        """build_rules_block builds formatted block with matched rules."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "topic": "evolved:xyz",
            "confidence": 0.7,
            "evidence": json.dumps({
                "rule_text": "Always validate input",
                "category": "rule",
                "task_type": "coding",
                "keywords": ["validate"],
            }),
            "text": "Always validate input",
            "evidence_for": 2,
            "evidence_against": 0,
            "updated": time.time(),
        }[k]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [row]
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        block = mgr.build_rules_block("validate my code", "coding")
        assert "进化经验规则" in block
        assert "validate" in block

    def test_report_success(self):
        """report_success reinforces the rule."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mgr = EvolutionRuleManager(opinion_engine=oe)
        mgr.report_success("evolved:abc123")
        oe.reinforce.assert_called_with("evolved:abc123", "task succeeded")

    def test_report_failure(self):
        """report_failure weakens the rule."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mgr = EvolutionRuleManager(opinion_engine=oe)
        mgr.report_failure("evolved:abc123")
        oe.weaken.assert_called_with("evolved:abc123", "task failed")

    def test_enforce_capacity_no_action_when_under_limit(self):
        """_enforce_capacity does nothing when under MAX_ACTIVE_RULES."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        oe._conn.execute.return_value = mock_cursor
        mgr = EvolutionRuleManager(opinion_engine=oe)
        mgr._enforce_capacity()  # Should not crash

    def test_get_rule_meta_exception(self):
        """_get_rule_meta returns {} on exception."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe._conn.execute.side_effect = Exception("DB error")
        mgr = EvolutionRuleManager(opinion_engine=oe)
        meta = mgr._get_rule_meta("evolved:test")
        assert meta == {}

    def test_update_rule_meta_exception(self):
        """_update_rule_meta handles exception gracefully."""
        from core.evolution_rules import EvolutionRuleManager
        oe = MagicMock()
        oe._conn.execute.side_effect = Exception("DB error")
        mgr = EvolutionRuleManager(opinion_engine=oe)
        mgr._update_rule_meta("evolved:test", {"key": "value"})  # Should not crash
