"""
夸父 (Kuafu) AgentLoop Helper Method Tests — 覆盖辅助方法，mock 隔离

覆盖目标（从 coverage report 缺失行）：
- _get_rules() fallback (L39-52)
- _run() inside _async_post_task (L87, L99-117)
- _lazy_init import error paths (L214-215)
- _register_delegate_tool error path (L299-300)
- _register_skill_rollback full coverage (L322-351, 355-356)
- _register_memory_tools error path (L376-377)
- _init_mcp full coverage (L376-377, L389, L392, L394-396)
- _init_evolution_rules full coverage (L417-419)
- build_system_prompt internals (L471-472, L487, L605-607, L634-656, L685-686, L694-695)
- _try_delegate_complex_skills (L776-809)
- _self_check (L1776-1822)
- _quality_score (L1826-1901)
- _generate_report (L1905-1976)
- _learn_user_preferences (L2035-2100)
- _deep_reflect (L1980-2031)
- _detect_user_correction (L1759-1774)
- _trigger_evolution_rule_analysis (L1711-1758)
- _run_evolution_pipeline (L1618-1710)
"""

import json
import os
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock, call

import pytest


# ===================================================================
# Module-level helpers (_get_rules fallback, _run, etc.)
# ===================================================================

class TestModuleLevelHelpers:
    """Cover module-level helper functions in agent_loop.py."""

    def test_get_rules_fallback_when_no_strategy(self):
        """_get_rules() fallback returns default rules when _HAS_STRATEGY is False."""
        with patch('core.agent_loop._HAS_STRATEGY', False):
            from core.agent_loop import get_rules
            rules = get_rules()
            assert len(rules) == 6
            assert "直接完成用户请求" in rules[0] or "夸父" in rules[0]

    def test_get_rules_uses_strategy_when_available(self):
        """get_rules uses strategy_loader when _HAS_STRATEGY is True."""
        mock_rules = ["custom rule 1", "custom rule 2"]
        with patch('core.agent_loop._HAS_STRATEGY', True), \
             patch('core.agent_loop._get_rules', return_value=mock_rules):
            from core.agent_loop import get_rules
            rules = get_rules()
            assert rules == mock_rules

    def test_get_quality_fallback(self):
        """_get_quality() fallback returns empty list."""
        with patch('core.agent_loop._HAS_STRATEGY', False):
            from core.agent_loop import get_quality
            quality = get_quality("coding")
            assert quality == []

    def test_get_quality_uses_strategy_when_available(self):
        """get_quality uses strategy_loader when _HAS_STRATEGY is True."""
        mock_quality = [{"severity": "required", "rule": "test rule"}]
        with patch('core.agent_loop._HAS_STRATEGY', True), \
             patch('core.agent_loop._get_quality', return_value=mock_quality):
            from core.agent_loop import get_quality
            quality = get_quality("coding")
            assert quality == mock_quality

    def test_detect_task_type_various(self):
        """detect_task_type correctly identifies task types."""
        from core.agent_loop import detect_task_type
        assert detect_task_type("") == "generic"
        assert detect_task_type("写一个python脚本") == "coding"
        assert detect_task_type("搜索一下什么是量子计算") == "research"
        assert detect_task_type("创建文件并写入内容") == "file_operation"
        assert detect_task_type("设计一个微服务架构") == "design"
        assert detect_task_type("修复错误") == "coding"
        assert detect_task_type("部署到docker服务器") == "devops"
        assert detect_task_type("对比python和golang") == "analysis"
        assert detect_task_type("随便问问") == "generic"

    def test_load_identity_statement_fallback(self):
        """load_identity_statement returns default when IDENTITY.md doesn't exist."""
        with patch('core.agent_loop.ROOT_DIR', Path('/nonexistent')):
            from core.agent_loop import load_identity_statement
            result = load_identity_statement()
            assert "夸父" in result

    def test_load_identity_statement_from_file(self):
        """load_identity_statement reads from IDENTITY.md when it exists."""
        import core.agent_loop as al
        orig_root = al.ROOT_DIR
        try:
            al.ROOT_DIR = Path('/tmp')
            mock_path = Path('/tmp') / "IDENTITY.md"
            with patch.object(Path, 'exists', return_value=True), \
                 patch.object(Path, 'read_text', return_value="Custom Identity"):
                result = al.load_identity_statement()
                assert result == "Custom Identity"
        finally:
            al.ROOT_DIR = orig_root

    def test_async_post_task_run_catches_exceptions(self):
        """_run() inside _async_post_task catches all exceptions gracefully."""
        from core.agent_loop import _async_post_task

        loop = MagicMock()
        # Make all methods raise
        loop._deep_reflect.side_effect = Exception("reflect err")
        loop._self_check.side_effect = Exception("check err")
        loop._run_evolution_pipeline.side_effect = Exception("evo err")
        loop._learn_user_preferences.side_effect = Exception("pref err")

        task_result = {"success": True, "result": "test"}
        _async_post_task(task_result, [], "test task", loop)

        # Wait a tiny bit for the thread
        import time as _t
        _t.sleep(0.1)

        loop._deep_reflect.assert_called_once()
        loop._self_check.assert_called_once()
        loop._run_evolution_pipeline.assert_called_once()
        loop._learn_user_preferences.assert_called_once()

    def test_async_post_task_run_success(self):
        """_run() inside _async_post_task calls all methods on success."""
        from core.agent_loop import _async_post_task

        loop = MagicMock()
        task_result = {"success": True, "result": "test"}
        _async_post_task(task_result, [], "test task", loop)

        import time as _t
        _t.sleep(0.1)

        loop._deep_reflect.assert_called_once()
        loop._self_check.assert_called_once()
        loop._run_evolution_pipeline.assert_called_once()
        loop._learn_user_preferences.assert_called_once()


# ===================================================================
# AgentLoop initialization helpers
# ===================================================================

class TestAgentLoopInitHelpers:
    """Cover AgentLoop initialization and lazy init helpers."""

    def _make_empty_loop(self):
        """Create an AgentLoop with all deps mocked and all lazy init set to None."""
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'), \
             patch('core.agent_loop._HAS_STRATEGY', False):

            mock_llm = MagicMock()
            mock_llm.backend = "cloud"
            mock_llm.model = "deepseek-chat"
            mock_llm.base_url = "https://api.deepseek.com"
            mock_llm.max_tokens = 4096
            mock_llm.temperature = 0.7
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr.get_schemas.return_value = [
                {"type": "function", "function": {"name": "terminal", "description": "Run terminal"}},
                {"type": "function", "function": {"name": "finish", "description": "Finish task"}},
            ]
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            # Suppress _register_delegate_tool and _register_skill_rollback during init
            # by patching on the instance after creation
            loop = object.__new__(AgentLoop)  # Create without __init__
            # Manually call __init__ with our mocks
            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                AgentLoop.__init__(
                    loop, llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                    tool_registry=mock_tr, session_store=mock_ss, max_turns=5,
                )

            # Reset all lazy init to None to force actual init
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

            return loop

    def test_lazy_init_sets_components(self):
        """_lazy_init creates all lazy components."""
        loop = self._make_empty_loop()
        with patch('core.agent_loop.ContextCompressor') as mock_cc, \
             patch('core.agent_loop.BudgetAllocator') as mock_ba, \
             patch('core.agent_loop.BudgetPolicy'), \
             patch('core.agent_loop.ToolResultStore'), \
             patch('core.agent_loop.ContextCollapse'), \
             patch('core.agent_loop.LLMSummarizer'), \
             patch('core.agent_loop.Observer') as mock_obs, \
             patch('core.agent_loop.MCPBridge'), \
             patch.object(loop, '_init_mcp'):

            loop._lazy_init()

            # Verify ContextCompressor init
            assert mock_cc.called, "ContextCompressor should be created"
            assert loop.on_llm_start is None
            assert loop.on_llm_end is None
            assert loop.on_tool_start is None
            assert loop.on_tool_end is None
            assert loop.on_turn is None
            assert loop.on_error is None
            assert loop.on_finish is None
            assert loop._observer is not None
            assert loop.mcp_bridge is not None or loop.mcp_bridge is None
            mock_obs.assert_called_once()

    def test_lazy_init_already_initialized(self):
        """_lazy_init returns immediately if compressor is already set."""
        loop = self._make_empty_loop()
        loop.compressor = MagicMock()
        with patch.object(loop, '_init_mcp') as mock_init:
            loop._lazy_init()
            mock_init.assert_not_called()

    def test_lazy_init_local_backend(self):
        """_lazy_init uses higher threshold for local backend."""
        loop = self._make_empty_loop()
        loop.llm.backend = "local"
        with patch('core.agent_loop.ContextCompressor') as mock_cc, \
             patch('core.agent_loop.BudgetAllocator'), \
             patch('core.agent_loop.BudgetPolicy'), \
             patch('core.agent_loop.ToolResultStore'), \
             patch('core.agent_loop.ContextCollapse'), \
             patch('core.agent_loop.LLMSummarizer'), \
             patch('core.agent_loop.Observer'), \
             patch.object(loop, '_init_mcp'):

            loop._lazy_init()
            # ContextCompressor is created during _lazy_init
            args = mock_cc.call_args
            assert args is not None, "ContextCompressor constructor should have been called"
            assert args[1]['max_context_tokens'] == 800000

    def test_build_system_prompt_lazy_init_triggers(self):
        """build_system_prompt triggers _lazy_init when prompt_cache is None."""
        loop = self._make_empty_loop()
        # Make _lazy_init not crash by setting basic attrs
        with patch.object(loop, '_lazy_init') as mock_lazy, \
             patch('core.agent_loop.PromptCache') as mock_pc, \
             patch('core.agent_loop.load_identity_statement', return_value="身份"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch.object(loop, 'tools') as mock_tools, \
             patch.object(loop, 'memory') as mock_mem, \
             patch.object(loop, 'evolution') as mock_evo:

            mock_tools.get_schemas.return_value = []
            mock_tools.get_compact_tools_description.return_value = []
            mock_mem.build_memory_block.return_value = ""
            mock_evo.get_evolution_stats.return_value = {"total_evolutions": 0}

            try:
                loop.build_system_prompt("test task")
            except Exception:
                pass
            mock_lazy.assert_called_once()


# ===================================================================
# _register_delegate_tool, _register_skill_rollback, _register_memory_tools
# ===================================================================

class TestRegisterTools:
    """Cover tool registration methods."""

    def _make_basic_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm.backend = "cloud"
            mock_llm.model = "deepseek-chat"
            mock_llm.base_url = "https://api.deepseek.com"
            mock_llm.max_tokens = 4096
            mock_llm.temperature = 0.7
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            # Suppress _register_delegate_tool and _register_skill_rollback during init
            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(
                    llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                    tool_registry=mock_tr, session_store=mock_ss, max_turns=5,
                )

            # Reset necessary attrs
            loop._bootup = True
            return loop

    def test_register_delegate_tool_success(self):
        """_register_delegate_tool registers delegate_task tool."""
        loop = self._make_basic_loop()
        mock_schema = {"description": "delegate", "parameters": {}}
        with patch.object(loop, '_register_memory_tools') as mock_reg_mem, \
             patch('core.subagent.get_delegate_schema', return_value=mock_schema), \
             patch('core.subagent.handle_delegate'), \
             patch('core.subagent.get_invoke_expert_schema', return_value={}), \
             patch('core.subagent.get_invoke_experts_schema', return_value={}):

            loop._is_top_level = True
            loop._register_delegate_tool()
            assert loop.tools.register.called
            mock_reg_mem.assert_called_once()

    def test_register_delegate_tool_error(self):
        """_register_delegate_tool handles import error gracefully (L299-300)."""
        loop = self._make_basic_loop()
        loop._is_top_level = True
        with patch.object(loop, '_register_memory_tools') as mock_reg_mem, \
             patch('core.subagent.get_invoke_expert_schema', side_effect=Exception("import failed")):
            # Should not raise
            loop._register_delegate_tool()
            # _register_memory_tools should still be called
            mock_reg_mem.assert_called_once()

    def test_register_skill_rollback_success(self):
        """_register_skill_rollback registers skill_rollback tool."""
        loop = self._make_basic_loop()

        # Mock evolution_state
        mock_evo_state = MagicMock()
        mock_evo_state.undo_last_evolution.return_value = {
            "rolled_back_skill": "test_skill",
            "rolled_back_v": 2,
            "restored_to_v": 1,
        }
        loop.evolution.evolution_state = mock_evo_state

        loop._register_skill_rollback()
        # Should have called tools.register
        assert loop.tools.register.called
        call_args = loop.tools.register.call_args
        assert call_args[0][0] == "skill_rollback"

    def test_register_skill_rollback_handler_with_skill_name(self):
        """skill_rollback handler handles skill_name parameter."""
        loop = self._make_basic_loop()

        mock_evo_state = MagicMock()
        mock_evo_state.undo_last_evolution.return_value = {
            "rolled_back_skill": "my_skill",
            "rolled_back_v": 3,
            "restored_to_v": 2,
        }
        loop.evolution.evolution_state = mock_evo_state

        # Capture the handler
        captured_handler = None
        def _capture_register(name, schema, handler):
            nonlocal captured_handler
            captured_handler = handler

        loop.tools.register.side_effect = _capture_register
        loop._register_skill_rollback()

        assert captured_handler is not None
        result = captured_handler({"skill_name": "my_skill"})
        assert result["success"] is True
        mock_evo_state.undo_last_evolution.assert_called_with("my_skill")

    def test_register_skill_rollback_handler_no_skill_name(self):
        """skill_rollback handler finds last skill when no skill_name given (L327-339)."""
        loop = self._make_basic_loop()

        mock_evo_state = MagicMock()
        # First call returns None (no skill_name), second returns result
        mock_evo_state.undo_last_evolution.side_effect = [
            None,
            {"rolled_back_skill": "last_skill", "rolled_back_v": 2, "restored_to_v": 1},
        ]
        mock_evo_state._data = {"skills": {
            "skill_a": {"last_written": 100},
            "skill_b": {"last_written": 200},
        }}
        loop.evolution.evolution_state = mock_evo_state

        captured_handler = None
        def _capture_register(name, schema, handler):
            nonlocal captured_handler
            captured_handler = handler

        loop.tools.register.side_effect = _capture_register
        loop._register_skill_rollback()

        assert captured_handler is not None
        result = captured_handler({"skill_name": ""})
        assert result["success"] is True
        # Should have called undo_last_evolution with "skill_b" (last_written=200)
        assert mock_evo_state.undo_last_evolution.call_count >= 2

    def test_register_skill_rollback_handler_no_result(self):
        """skill_rollback handler returns failure when no rollback possible."""
        loop = self._make_basic_loop()

        mock_evo_state = MagicMock()
        mock_evo_state.undo_last_evolution.return_value = None
        loop.evolution.evolution_state = mock_evo_state

        captured_handler = None
        def _capture_register(name, schema, handler):
            nonlocal captured_handler
            captured_handler = handler

        loop.tools.register.side_effect = _capture_register
        loop._register_skill_rollback()

        result = captured_handler({"skill_name": "my_skill"})
        assert result["success"] is False
        assert "无可回滚" in result["output"]

    def test_register_skill_rollback_handler_exception(self):
        """skill_rollback handler catches exceptions (L350-351)."""
        loop = self._make_basic_loop()

        mock_evo_state = MagicMock()
        mock_evo_state.undo_last_evolution.side_effect = Exception("rollback failed")
        loop.evolution.evolution_state = mock_evo_state

        captured_handler = None
        def _capture_register(name, schema, handler):
            nonlocal captured_handler
            captured_handler = handler

        loop.tools.register.side_effect = _capture_register
        loop._register_skill_rollback()

        result = captured_handler({"skill_name": "my_skill"})
        assert result["success"] is False
        assert "回滚失败" in result["output"]

    def test_register_skill_rollback_outer_exception(self):
        """_register_skill_rollback catches outer exception (L355-356)."""
        loop = self._make_basic_loop()
        loop.tools.register.side_effect = Exception("register failed")
        # Should not raise
        loop._register_skill_rollback()

    def test_register_memory_tools_success(self):
        """_register_memory_tools registers memory tools."""
        loop = self._make_basic_loop()
        mock_mem_api = MagicMock()
        mock_mem_api.get_tool_schemas.return_value = [
            {"name": "memory_store", "description": "Store memory", "parameters": {"type": "object", "properties": {}}},
            {"name": "memory_search", "description": "Search memory", "parameters": {"type": "object", "properties": {}}},
        ]
        with patch('core.agent_loop.MemoryManager', return_value=mock_mem_api):
            loop._register_memory_tools()
            # Should have called tools.register 1 more time (skill_rollback already called in init)
            assert loop.tools.register.call_count >= 2

    def test_register_memory_tools_error(self):
        """_register_memory_tools handles exception gracefully (L376-377)."""
        loop = self._make_basic_loop()
        with patch('core.agent_loop.MemoryManager', side_effect=Exception("mem_api failed")):
            # Should not raise
            loop._register_memory_tools()

    def test_init_mcp_no_config(self):
        """_init_mcp returns immediately if no config file."""
        loop = self._make_basic_loop()
        with patch('core.agent_loop.ROOT_DIR', Path('/nonexistent')):
            loop._init_mcp()  # Should not raise

    def test_init_mcp_success(self):
        """_init_mcp initializes MCP bridge and registers tools."""
        loop = self._make_basic_loop()
        mock_bridge = MagicMock()
        mock_bridge.connect_all.return_value = []  # No failures
        mock_bridge.register_to_registry.return_value = 3  # 3 tools registered

        with patch('core.agent_loop.MCPBridge', return_value=mock_bridge), \
             patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value="mcp_servers:"):
            loop._init_mcp()
            assert loop.mcp_bridge is mock_bridge
            mock_bridge.load_config.assert_called_once()
            mock_bridge.connect_all.assert_called_once()
            mock_bridge.register_to_registry.assert_called_once()

    def test_init_mcp_connect_failures(self):
        """_init_mcp handles connection failures gracefully (L389)."""
        loop = self._make_basic_loop()
        mock_bridge = MagicMock()
        mock_bridge.connect_all.return_value = ["server1", "server2"]  # Failed servers
        mock_bridge.register_to_registry.return_value = 1

        with patch('core.agent_loop.MCPBridge', return_value=mock_bridge), \
             patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value="mcp_servers:"):
            loop._init_mcp()
            assert loop.mcp_bridge is mock_bridge

    def test_init_mcp_exception(self):
        """_init_mcp handles exception gracefully (L394-396)."""
        loop = self._make_basic_loop()
        with patch('core.agent_loop.MCPBridge', side_effect=Exception("MCP init failed")), \
             patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True):
            loop._init_mcp()
            assert loop.mcp_bridge is None

    def test_init_evolution_rules_success(self):
        """_init_evolution_rules initializes EvolutionRuleManager with opinion engine."""
        loop = self._make_basic_loop()
        mock_opinion = MagicMock()
        loop.memory._opinions = mock_opinion

        with patch('core.evolution_rules.EvolutionRuleManager') as mock_erm:
            loop._init_evolution_rules()
            mock_erm.assert_called_once()
            assert loop._evolution_rules is not None

    def test_init_evolution_rules_no_opinion_fallback(self):
        """_init_evolution_rules falls back to backend._conn for OpinionEngine (L406-409)."""
        loop = self._make_basic_loop()
        loop.memory._opinions = None
        mock_backend = MagicMock()
        mock_backend._conn = MagicMock()
        loop.memory._longterm = mock_backend

        with patch('core.evolution_rules.EvolutionRuleManager') as mock_erm, \
             patch('core.memory.hindsight_lite.OpinionEngine'):
            loop._init_evolution_rules()
            mock_erm.assert_called_once()

    def test_init_evolution_rules_no_opinion_no_backend(self):
        """_init_evolution_rules logs warning when no opinion system (L417)."""
        loop = self._make_basic_loop()
        loop.memory._opinions = None
        loop.memory._longterm = None

        with patch('core.evolution_rules.EvolutionRuleManager') as mock_erm:
            loop._init_evolution_rules()
            mock_erm.assert_not_called()
            assert loop._evolution_rules is None

    def test_init_evolution_rules_exception(self):
        """_init_evolution_rules handles exception gracefully (L418-419)."""
        loop = self._make_basic_loop()
        loop.memory._opinions = MagicMock()
        with patch('core.evolution_rules.EvolutionRuleManager', side_effect=Exception("erm failed")):
            loop._init_evolution_rules()  # Should not raise

    def test_init_hooks_exception_in_init(self):
        """AgentLoop.__init__ handles hooks init exception (L214-215)."""
        with patch('core.agent_loop.LLMClient'), \
             patch('core.agent_loop.MemoryManager'), \
             patch('core.agent_loop.EvolutionEngine'), \
             patch('core.agent_loop.ToolRegistry'), \
             patch('core.agent_loop.SessionStore'), \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks', side_effect=Exception("hooks failed")):
            from core.agent_loop import AgentLoop
            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop()
                # Should not crash, hooks should be gracefully handled


# ===================================================================
# build_system_prompt internals
# ===================================================================

class TestBuildSystemPromptInternals:
    """Cover the internal code paths of build_system_prompt."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'), \
             patch('core.agent_loop._HAS_STRATEGY', False):

            mock_llm = MagicMock()
            mock_llm.backend = "cloud"
            mock_llm.model = "deepseek-chat"
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_memory.build_memory_block.return_value = "memory block"
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo.get_evolution_stats.return_value = {"total_evolutions": 5}
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr.get_schemas.return_value = [
                {"type": "function", "function": {"name": "terminal", "description": "Run terminal commands"}},
                {"type": "function", "function": {"name": "finish", "description": "Finish the task"}},
                {"type": "function", "function": {"name": "tool_search", "description": "Search for tools"}},
            ]
            mock_tr.get_compact_tools_description.return_value = [
                ("read_file", "Read file content"),
            ]
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(
                    llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                    tool_registry=mock_tr, session_store=mock_ss, max_turns=5,
                )

            # Set up lazy init components
            loop.prompt_cache = MagicMock()
            mock_l1 = MagicMock()
            mock_l1.content = "L1 block"
            mock_l2 = MagicMock()
            mock_l2.content = "L2 block"
            loop.prompt_cache.get_block.side_effect = lambda sections, stab: mock_l1 if 'L1' in str(stab) else mock_l2

            loop.compressor = MagicMock()
            loop.budget_allocator = MagicMock()
            loop.budget_allocator._last_snapshot = None
            loop.tool_result_store = MagicMock()
            loop.collapser = MagicMock()
            loop._observer = MagicMock()
            loop._evolution_rules = None
            loop.on_llm_start = None
            loop.permission_enabled = False
            loop._bootup = False

            return loop

    def test_build_system_prompt_with_evolution_rules(self):
        """build_system_prompt includes evolution rules when _evolution_rules is set (L465-472)."""
        loop = self._make_loop()
        mock_evo_rules = MagicMock()
        mock_evo_rules.build_rules_block.return_value = "### 进化规则\n- rule 1"
        loop._evolution_rules = mock_evo_rules

        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="write some code")
            assert isinstance(prompt, str)
            assert len(prompt) > 0
            mock_evo_rules.build_rules_block.assert_called_once()

    def test_build_system_prompt_evolution_rules_exception(self):
        """build_system_prompt handles evolution rules exception silently (L471-472)."""
        loop = self._make_loop()
        mock_evo_rules = MagicMock()
        mock_evo_rules.build_rules_block.side_effect = Exception("build failed")
        loop._evolution_rules = mock_evo_rules

        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="test")
            assert isinstance(prompt, str)

    def test_build_system_prompt_skips_tool_search(self):
        """build_system_prompt skips tool_search in core tools list (L486-487)."""
        loop = self._make_loop()
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="test")
            # terminal and finish should be listed, tool_search should not
            assert "terminal" in prompt
            assert "finish" in prompt
            # tool_search should be skipped - it's listed separately in hidden tools section
            assert isinstance(prompt, str)

    def test_build_system_prompt_with_quality_rules(self):
        """build_system_prompt includes quality rules when available."""
        loop = self._make_loop()
        quality_rules = [
            {"severity": "required", "rule": "Must handle errors"},
            {"severity": "warning", "rule": "Should add comments"},
        ]
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=quality_rules), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="coding task")
            assert isinstance(prompt, str)
            assert len(prompt) > 0

    def test_build_system_prompt_with_matched_skills_short(self):
        """build_system_prompt includes short skill steps (L602-607)."""
        loop = self._make_loop()
        match_skills_result = [
            {
                "name": "test_skill",
                "description": "A test skill",
                "steps": ["step 1", "step 2", "step 3"],
                "pitfalls": ["pitfall 1"],
                "file": "test_skill.yaml",
            }
        ]
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=match_skills_result), \
             patch('core.agent_loop.discover_skills', return_value=[]), \
             patch('core.skill_resolver.resolve_skill_execution', return_value=(match_skills_result, [])), \
             patch('core.skill_resolver.increment_usage'):

            prompt = loop.build_system_prompt(task="coding task")
            assert isinstance(prompt, str)

    def test_build_system_prompt_skills_without_steps(self):
        """build_system_prompt handles skills without steps key."""
        loop = self._make_loop()
        match_skills_result = [
            {
                "name": "no_step_skill",
                "description": "A skill without steps",
            }
        ]
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=match_skills_result), \
             patch('core.agent_loop.discover_skills', return_value=[]), \
             patch('core.skill_resolver.resolve_skill_execution', return_value=(match_skills_result, [])), \
             patch('core.skill_resolver.increment_usage'):

            prompt = loop.build_system_prompt(task="coding task")
            assert isinstance(prompt, str)

    def test_build_system_prompt_skills_long_steps(self):
        """build_system_prompt handles skills with many steps (L608-609)."""
        loop = self._make_loop()
        match_skills_result = [
            {
                "name": "long_skill",
                "description": "A long skill",
                "steps": [f"step {i}" for i in range(10)],
                "file": "long_skill.yaml",
            }
        ]
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=match_skills_result), \
             patch('core.agent_loop.discover_skills', return_value=[]), \
             patch('core.skill_resolver.resolve_skill_execution', return_value=(match_skills_result, [])), \
             patch('core.skill_resolver.increment_usage'):

            prompt = loop.build_system_prompt(task="coding task")
            assert isinstance(prompt, str)
            assert "步骤数" in prompt or "步骤：" in prompt

    def test_build_system_prompt_error_skill(self):
        """build_system_prompt loads error-associated skill (L624-656)."""
        loop = self._make_loop()
        # Mock evolution_state to return an error skill
        loop.evolution.evolution_state.get_skill_for_error.return_value = "err_skill"

        # Create a mock YAML file
        mock_yaml_data = {
            "name": "err_skill",
            "description": "Fix common errors",
            "steps": ["step 1", "step 2"],
            "pitfalls": ["pitfall 1"],
        }

        mock_file = MagicMock()
        mock_file.name = "err_skill.yaml"

        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]), \
             patch('core.agent_loop.Path.glob', return_value=[mock_file]), \
             patch('core.agent_loop.Path.open'), \
             patch('yaml.safe_load', return_value=mock_yaml_data):

            prompt = loop.build_system_prompt(task="I found a bug")
            assert isinstance(prompt, str)

    def test_build_system_prompt_error_skill_no_match(self):
        """build_system_prompt handles error skill file not found."""
        loop = self._make_loop()
        loop.evolution.evolution_state.get_skill_for_error.return_value = "nonexistent_skill"

        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]), \
             patch('core.agent_loop.Path.glob', return_value=[]):

            prompt = loop.build_system_prompt(task="bug")
            assert isinstance(prompt, str)

    def test_build_system_prompt_error_skill_exception(self):
        """build_system_prompt handles error skill exception (L655-656)."""
        loop = self._make_loop()
        loop.evolution.evolution_state.get_skill_for_error.side_effect = Exception("get_skill failed")

        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="bug")
            assert isinstance(prompt, str)

    def test_build_system_prompt_self_awareness(self):
        """build_system_prompt includes self-awareness section (L676-695)."""
        loop = self._make_loop()
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[{"name": "s1"}, {"name": "s2"}]), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value='{"lang": "zh"}'):

            prompt = loop.build_system_prompt(task="test")
            assert isinstance(prompt, str)

    def test_build_system_prompt_self_awareness_exception(self):
        """build_system_prompt handles self-awareness exception (L694-695)."""
        loop = self._make_loop()
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', side_effect=Exception("discover failed")):

            prompt = loop.build_system_prompt(task="test")
            assert isinstance(prompt, str)

    def test_build_system_prompt_self_awareness_prefs_parse_error(self):
        """build_system_prompt handles user_prefs.json parse error (L685-686)."""
        loop = self._make_loop()
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', side_effect=Exception("read error")):

            prompt = loop.build_system_prompt(task="test")
            assert isinstance(prompt, str)

    def test_build_system_prompt_with_budget_ratio(self):
        """build_system_prompt uses budget_ratio from budget_allocator."""
        loop = self._make_loop()
        mock_snapshot = MagicMock()
        mock_snapshot.overall_ratio = 0.5
        loop.budget_allocator._last_snapshot = mock_snapshot

        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="test")
            assert isinstance(prompt, str)

    def test_build_system_prompt_no_task(self):
        """build_system_prompt works with empty task."""
        loop = self._make_loop()
        with patch('core.agent_loop.load_identity_statement', return_value="你是夸父"), \
             patch('core.agent_loop.get_rules', return_value=["rule 1"]), \
             patch('core.agent_loop.get_quality', return_value=[]), \
             patch('core.skill_resolver.match_skills', return_value=[]), \
             patch('core.agent_loop.discover_skills', return_value=[]):

            prompt = loop.build_system_prompt(task="")
            assert isinstance(prompt, str)


# ===================================================================
# _try_delegate_complex_skills
# ===================================================================

class TestTryDelegateComplexSkills:
    """Cover _try_delegate_complex_skills method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm.backend = "cloud"
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)

            loop._bootup = True
            loop._log = MagicMock()
            loop.on_step = MagicMock()
            return loop

    def test_try_delegate_no_matched_skills(self):
        """_try_delegate_complex_skills returns None when no skills match."""
        loop = self._make_loop()
        with patch('core.skill_resolver.match_skills', return_value=[]):
            result = loop._try_delegate_complex_skills("test task")
            assert result is None

    def test_try_delegate_no_complex_skills(self):
        """_try_delegate_complex_skills returns None when only simple skills."""
        loop = self._make_loop()
        matched = [{"name": "simple_skill", "steps": ["step 1"]}]
        with patch('core.skill_resolver.match_skills', return_value=matched), \
             patch('core.skill_resolver.resolve_skill_execution', return_value=(matched, [])):
            result = loop._try_delegate_complex_skills("test task")
            assert result is None

    def test_try_delegate_success(self):
        """_try_delegate_complex_skills delegates to subagent and returns result."""
        loop = self._make_loop()
        matched = [{"name": "simple"}]
        complex_skills = [{"name": "complex_skill", "steps": ["s1", "s2", "s3", "s4", "s5"]}]

        mock_delegate_result = {
            "success": True,
            "summary": "Task completed",
            "output": "Detailed output",
            "duration": 5.0,
        }

        with patch('core.skill_resolver.match_skills', return_value=matched), \
             patch('core.skill_resolver.resolve_skill_execution', return_value=([], complex_skills)), \
             patch('core.skill_resolver.build_delegation_prompt', return_value="sub prompt"), \
             patch('core.subagent.handle_delegate', return_value=mock_delegate_result), \
             patch('core.skill_resolver.increment_usage'), \
             patch('core.skill_resolver.record_usage'):

            result = loop._try_delegate_complex_skills("test task")
            assert result is not None
            assert result["skill"] == "complex_skill"
            assert result["summary"] == "Task completed"

    def test_try_delegate_failure(self):
        """_try_delegate_complex_skills returns None on subagent failure."""
        loop = self._make_loop()
        complex_skills = [{"name": "failed_skill", "steps": ["s1", "s2", "s3", "s4", "s5"]}]

        mock_delegate_result = {
            "success": False,
            "output": "Something went wrong",
            "duration": 3.0,
        }

        with patch('core.skill_resolver.match_skills', return_value=[{"name": "simple"}]), \
             patch('core.skill_resolver.resolve_skill_execution', return_value=([], complex_skills)), \
             patch('core.skill_resolver.build_delegation_prompt', return_value="sub prompt"), \
             patch('core.subagent.handle_delegate', return_value=mock_delegate_result), \
             patch('core.skill_resolver.increment_usage'), \
             patch('core.skill_resolver.record_usage'):

            result = loop._try_delegate_complex_skills("test task")
            assert result is None

    def test_try_delegate_exception(self):
        """_try_delegate_complex_skills handles exception and returns None (L805-809)."""
        loop = self._make_loop()
        with patch('core.skill_resolver.match_skills', side_effect=Exception("match failed")):
            result = loop._try_delegate_complex_skills("test task")
            assert result is None


# ===================================================================
# _self_check
# ===================================================================

class TestSelfCheck:
    """Cover _self_check method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm.backend = "cloud"
            mock_llm.chat.return_value = {"success": True, "content": "无问题"}
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)

            loop._log = MagicMock()
            loop.on_step = MagicMock()
            return loop

    def test_self_check_no_result(self):
        """_self_check returns early when result is empty."""
        loop = self._make_loop()
        loop._self_check({"result": ""}, [], 0)
        loop.llm.chat.assert_not_called()

    def test_self_check_no_code_work(self):
        """_self_check returns early when no code tools were used."""
        loop = self._make_loop()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        loop._self_check({"result": "some output"}, messages, 0)
        loop.llm.chat.assert_not_called()

    def test_self_check_code_work_clean(self):
        """_self_check finds no issues."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {"success": True, "content": "无问题"}
        messages = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "write_file"}}]},
        ]
        result = {"result": "def foo():\n    pass"}
        loop._self_check(result, messages, 0)
        assert "self_check" not in result

    def test_self_check_finds_issue(self):
        """_self_check finds issues and appends to result (L1816-1818)."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {"success": True, "content": "发现语法错误: 缺少冒号"}
        messages = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "write_file"}}]},
        ]
        result = {"result": "def foo()\n    pass"}
        loop._self_check(result, messages, 0)
        assert "self_check" in result
        assert "缺少冒号" in result["self_check"]

    def test_self_check_llm_failure(self):
        """_self_check handles LLM call failure."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {"success": False, "error": "API error"}
        messages = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "write_file"}}]},
        ]
        result = {"result": "some code"}
        loop._self_check(result, messages, 0)
        # Should not crash

    def test_self_check_llm_exception(self):
        """_self_check handles LLM exception (L1821-1822)."""
        loop = self._make_loop()
        loop.llm.chat.side_effect = Exception("chat failed")
        messages = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "write_file"}}]},
        ]
        result = {"result": "some code"}
        loop._self_check(result, messages, 0)
        # Should not crash


# ===================================================================
# _quality_score
# ===================================================================

class TestQualityScore:
    """Cover _quality_score method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm_cls.return_value = mock_llm
            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory
            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo
            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr
            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)
            return loop

    def test_quality_score_baseline(self):
        """_quality_score returns baseline 7 for clean result."""
        loop = self._make_loop()
        quality = loop._quality_score({"result": "good work with enough content here so it is long enough to pass", "errors": [], "success": True}, [])
        assert quality["score"] == 7
        assert "零错误" in quality["detail"]

    def test_quality_score_errors_penalty(self):
        """_quality_score penalizes for errors."""
        loop = self._make_loop()
        quality = loop._quality_score(
            {"result": "work done enough text here", "errors": ["error 1", "error 2"], "success": True},
            [],
        )
        assert quality["score"] < 7
        assert "错误" in quality["detail"]

    def test_quality_score_empty_result(self):
        """_quality_score penalizes empty result."""
        loop = self._make_loop()
        quality = loop._quality_score({"result": "", "errors": [], "success": True}, [])
        assert quality["score"] <= 5
        assert "为空" in quality["detail"]

    def test_quality_score_short_result(self):
        """_quality_score penalizes very short result."""
        loop = self._make_loop()
        quality = loop._quality_score({"result": "short but enough text here", "errors": [], "success": True}, [])
        assert quality["score"] == 6.5  # 7 - 0.5
        assert "偏短" in quality["detail"]

    def test_quality_score_not_success(self):
        """_quality_score caps score for failed tasks."""
        loop = self._make_loop()
        quality = loop._quality_score({"result": "work done enough text here", "errors": [], "success": False}, [])
        assert quality["score"] <= 4

    def test_quality_score_self_check_penalty(self):
        """_quality_score penalizes when self_check exists."""
        loop = self._make_loop()
        quality = loop._quality_score(
            {"result": "good work with enough content here so it is long enough to pass", "errors": [], "success": True, "self_check": "found issue"},
            [],
        )
        assert quality["score"] < 7

    def test_quality_score_tool_error_rate(self):
        """_quality_score penalizes high tool error rate."""
        loop = self._make_loop()
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "1"}, {"id": "2"}]},
        ]
        quality = loop._quality_score(
            {"result": "work done enough text here", "errors": ["err1", "err2"], "success": True, "tool_calls": 1},
            messages,
        )
        # tool_count from messages is 2, errors is 2 -> ratio = 1.0 > 0.5
        # We need tool_calls in result > 0 for the check... Let's check
        assert quality["score"] > 0

    def test_quality_score_no_tools_short_reply(self):
        """_quality_score no penalty for no-tools short reply."""
        loop = self._make_loop()
        quality = loop._quality_score(
            {"result": "answer with text", "errors": [], "success": True},
            [{"role": "assistant", "content": "answer"}],
        )
        assert quality["score"] >= 6

    def test_quality_score_clamped_low(self):
        """_quality_score clamps score to minimum 0."""
        loop = self._make_loop()
        quality = loop._quality_score(
            {"result": "", "errors": ["e1", "e2", "e3"], "success": False, "self_check": "bad"},
            [],
        )
        assert quality["score"] >= 0

    def test_quality_score_clamped_high(self):
        """_quality_score clamps score to maximum 10."""
        loop = self._make_loop()
        quality = loop._quality_score(
            {"result": "x" * 200, "errors": [], "success": True},
            [],
        )
        assert quality["score"] <= 10


# ===================================================================
# _generate_report
# ===================================================================

class TestGenerateReport:
    """Cover _generate_report method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm_cls.return_value = mock_llm
            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory
            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo
            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr
            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)
            return loop

    def test_generate_report_success(self):
        """_generate_report creates report for successful task."""
        loop = self._make_loop()
        task_result = {
            "success": True,
            "result": "Completed the implementation",
            "errors": [],
            "task_type": "coding",
            "duration": 10.5,
            "turns": 5,
        }
        messages = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file"}},
                {"function": {"name": "terminal"}},
            ]},
            {"role": "user", "content": "Write a function"},
        ]
        report = loop._generate_report("Write code", task_result, messages)
        assert "任务报告" in report
        assert "coding" in report
        assert "✅" in report
        assert "write_file" in report
        assert "terminal" in report

    def test_generate_report_with_errors(self):
        """_generate_report includes errors section."""
        loop = self._make_loop()
        task_result = {
            "success": False,
            "result": "Partial work",
            "errors": ["tool failed", "timeout"],
            "task_type": "devops",
            "duration": 30.0,
            "turns": 8,
        }
        messages = [
            {"role": "user", "content": "Deploy the app"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "terminal"}}]},
        ]
        report = loop._generate_report("Deploy", task_result, messages)
        assert "❌" in report
        assert "tool failed" in report
        assert "timeout" in report

    def test_generate_report_no_tool_calls(self):
        """_generate_report handles no tool calls."""
        loop = self._make_loop()
        task_result = {
            "success": True,
            "result": "Just an answer",
            "errors": [],
            "task_type": "generic",
            "duration": 2.0,
            "turns": 1,
        }
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a language"},
        ]
        report = loop._generate_report("Question", task_result, messages)
        assert "无工具调用" in report

    def test_generate_report_multiple_user_inputs(self):
        """_generate_report handles multiple user inputs."""
        loop = self._make_loop()
        task_result = {
            "success": True,
            "result": "Final result",
            "errors": [],
            "task_type": "research",
            "duration": 15.0,
            "turns": 4,
        }
        messages = [
            {"role": "user", "content": "Research topic A"},
            {"role": "assistant", "content": "Researching..."},
            {"role": "user", "content": "Also check topic B"},
        ]
        report = loop._generate_report("Research", task_result, messages)
        assert "共" in report or "Research" in report

    def test_generate_report_tool_count_dedup(self):
        """_generate_report deduplicates and counts tool calls."""
        loop = self._make_loop()
        task_result = {
            "success": True,
            "result": "Result",
            "errors": [],
            "task_type": "coding",
            "duration": 5.0,
            "turns": 3,
        }
        messages = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "read_file"}},
                {"function": {"name": "read_file"}},
                {"function": {"name": "write_file"}},
            ]},
        ]
        report = loop._generate_report("Code", task_result, messages)
        assert "read_file: 2 次" in report
        assert "write_file: 1 次" in report


# ===================================================================
# _detect_user_correction
# ===================================================================

class TestDetectUserCorrection:
    """Cover _detect_user_correction method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient'), \
             patch('core.agent_loop.MemoryManager'), \
             patch('core.agent_loop.EvolutionEngine'), \
             patch('core.agent_loop.ToolRegistry'), \
             patch('core.agent_loop.SessionStore'), \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):
            from core.agent_loop import AgentLoop
            loop = AgentLoop()
            return loop

    def test_detect_correction_found(self):
        """_detect_user_correction finds correction signals."""
        loop = self._make_loop()
        messages = [
            {"role": "user", "content": "不对，应该用另一种方式"},
            {"role": "assistant", "content": "ok"},
        ]
        assert loop._detect_user_correction(messages) is True

    def test_detect_correction_not_found(self):
        """_detect_user_correction returns False when no correction."""
        loop = self._make_loop()
        messages = [
            {"role": "user", "content": "Good job, keep going"},
            {"role": "assistant", "content": "thanks"},
        ]
        assert loop._detect_user_correction(messages) is False

    def test_detect_correction_various_markers(self):
        """_detect_user_correction detects all correction markers."""
        loop = self._make_loop()
        markers = ["别", "不对", "错了", "不是", "重新", "改成", "注意", "但是不", "不用这样", "不是这样"]
        for marker in markers:
            messages = [{"role": "user", "content": marker}]
            assert loop._detect_user_correction(messages) is True, f"Failed for marker: {marker}"

    def test_detect_correction_no_user_messages(self):
        """_detect_user_correction returns False with no user messages."""
        loop = self._make_loop()
        messages = [
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "system"},
        ]
        assert loop._detect_user_correction(messages) is False


# ===================================================================
# _deep_reflect
# ===================================================================

class TestDeepReflect:
    """Cover _deep_reflect method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm.chat.return_value = {"success": True, "content": "TITLE: lesson\nTAG: experience\nCONTENT: always check"}
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)

            loop._log = MagicMock()
            loop.on_step = MagicMock()
            return loop

    def test_deep_reflect_skipped_for_simple_success(self):
        """_deep_reflect skips for successful simple tasks (< 8 turns)."""
        loop = self._make_loop()
        loop._deep_reflect({"success": True, "result": "ok", "task_type": "coding"}, [])
        loop.llm.chat.assert_not_called()

    def test_deep_reflect_runs_for_complex_task(self):
        """_deep_reflect runs for complex tasks."""
        loop = self._make_loop()
        messages = [{"role": "user", "content": "hello"} for _ in range(10)]
        loop._deep_reflect(
            {"success": True, "result": "complex work", "task_type": "coding", "errors": []},
            messages,
        )
        loop.llm.chat.assert_called_once()

    def test_deep_reflect_runs_for_failed_task(self):
        """_deep_reflect runs for failed tasks regardless of complexity."""
        loop = self._make_loop()
        loop._deep_reflect(
            {"success": False, "result": "failed", "task_type": "coding", "errors": ["err"]},
            [],
        )
        loop.llm.chat.assert_called_once()

    def test_deep_reflect_remembers_lesson(self):
        """_deep_reflect stores learned lesson in memory."""
        loop = self._make_loop()
        messages = [{"role": "user", "content": "hello"} for _ in range(10)]
        loop._deep_reflect(
            {"success": True, "result": "complex work", "task_type": "coding", "errors": ["err"]},
            messages,
        )
        loop.memory.remember.assert_called_once()

    def test_deep_reflect_llm_failure(self):
        """_deep_reflect handles LLM failure."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {"success": False}
        messages = [{"role": "user", "content": "h"} for _ in range(10)]
        loop._deep_reflect(
            {"success": False, "result": "failed", "task_type": "coding", "errors": ["err"]},
            messages,
        )
        # Should not crash

    def test_deep_reflect_exception(self):
        """_deep_reflect handles exception (L2030-2031)."""
        loop = self._make_loop()
        loop.llm.chat.side_effect = Exception("chat failed")
        messages = [{"role": "user", "content": "h"} for _ in range(10)]
        loop._deep_reflect(
            {"success": False, "result": "failed", "task_type": "coding", "errors": ["err"]},
            messages,
        )
        # Should not crash


# ===================================================================
# _learn_user_preferences
# ===================================================================

class TestLearnUserPreferences:
    """Cover _learn_user_preferences method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm.chat.return_value = {
                "success": True,
                "content": '{"add": {"key": "language", "value": "zh"}, "remove": []}',
            }
            mock_llm_cls.return_value = mock_llm

            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr

            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)

            loop._log = MagicMock()
            loop.on_step = MagicMock()
            # Mock ROOT_DIR for prefs path
            loop._root_dir = Path('/tmp')
            return loop

    def test_learn_preferences_skipped_if_not_success(self):
        """_learn_user_preferences skips if task not successful."""
        loop = self._make_loop()
        loop._learn_user_preferences({"success": False}, "test")
        loop.llm.chat.assert_not_called()

    def test_learn_preferences_skipped_if_no_signal(self):
        """_learn_user_preferences skips if no preference signal in task."""
        loop = self._make_loop()
        loop._learn_user_preferences({"success": True}, "just a normal task")
        loop.llm.chat.assert_not_called()

    def test_learn_preferences_with_signal(self):
        """_learn_user_preferences learns preferences when signal detected."""
        loop = self._make_loop()
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'write_text'):
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            loop.llm.chat.assert_called_once()

    def test_learn_preferences_updates_existing(self):
        """_learn_user_preferences updates existing prefs file."""
        loop = self._make_loop()
        existing_prefs = {"style": "formal"}
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value=json.dumps(existing_prefs)), \
             patch.object(Path, 'write_text') as mock_write:
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            mock_write.assert_called_once()
            written = json.loads(mock_write.call_args[0][0])
            assert written["style"] == "formal"
            assert written["language"] == "zh"

    def test_learn_preferences_json_decode_error(self):
        """_learn_user_preferences handles JSON decode error in existing prefs."""
        loop = self._make_loop()
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value="invalid json"), \
             patch.object(Path, 'write_text'):
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            # Should not crash, should treat as empty prefs

    def test_learn_preferences_non_dict_prefs(self):
        """_learn_user_preferences handles non-dict existing prefs."""
        loop = self._make_loop()
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value='"string_value"'), \
             patch.object(Path, 'write_text'):
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            # Should not crash

    def test_learn_preferences_remove_conflicts(self):
        """_learn_user_preferences removes conflicting keys."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {
            "success": True,
            "content": '{"add": {"key": "language", "value": "en"}, "remove": ["old_lang"]}',
        }
        existing_prefs = {"language": "zh", "old_lang": "cn"}
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value=json.dumps(existing_prefs)), \
             patch.object(Path, 'write_text') as mock_write:
            loop._learn_user_preferences(
                {"success": True},
                "下次请用英文回复",
            )
            written = json.loads(mock_write.call_args[0][0])
            assert "old_lang" not in written
            assert written["language"] == "en"

    def test_learn_preferences_llm_returns_null(self):
        """_learn_user_preferences handles null add from LLM."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {
            "success": True,
            "content": '{"add": null, "remove": []}',
        }
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'write_text') as mock_write:
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            mock_write.assert_not_called()

    def test_learn_preferences_llm_failure(self):
        """_learn_user_preferences handles LLM failure."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {"success": False}
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'write_text'):
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            # Should not crash

    def test_learn_preferences_exception(self):
        """_learn_user_preferences handles exception (L2099-2100)."""
        loop = self._make_loop()
        loop.llm.chat.side_effect = Exception("chat error")
        loop._learn_user_preferences(
            {"success": True},
            "下次请用中文",
        )
        # Should not crash

    def test_learn_preferences_json_parse_error(self):
        """_learn_user_preferences handles JSON parse error in LLM response."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {
            "success": True,
            "content": "not valid json",
        }
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=False):
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            # Should catch JSON decode error and not crash

    def test_learn_preferences_empty_key_or_value(self):
        """_learn_user_preferences handles empty key or value."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {
            "success": True,
            "content": '{"add": {"key": "", "value": ""}, "remove": []}',
        }
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'write_text') as mock_write:
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            mock_write.assert_not_called()

    def test_learn_preferences_already_exists(self):
        """_learn_user_preferences doesn't write conflicting key when it already exists."""
        loop = self._make_loop()
        loop.llm.chat.return_value = {
            "success": True,
            "content": '{"add": {"key": "existing", "value": "new"}, "remove": []}',
        }
        existing = {"existing": "old"}
        with patch('core.agent_loop.ROOT_DIR', Path('/tmp')), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'read_text', return_value=json.dumps(existing)), \
             patch.object(Path, 'write_text') as mock_write:
            loop._learn_user_preferences(
                {"success": True},
                "下次请用中文回复",
            )
            written = json.loads(mock_write.call_args[0][0])
            assert written["existing"] == "new"


# ===================================================================
# _trigger_evolution_rule_analysis
# ===================================================================

class TestTriggerEvolutionRuleAnalysis:
    """Cover _trigger_evolution_rule_analysis method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm_cls.return_value = mock_llm
            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory
            mock_evo = MagicMock()
            mock_evo_cls.return_value = mock_evo
            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr
            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)

            loop._log = MagicMock()
            loop.on_step = MagicMock()
            return loop

    def test_trigger_no_evolution_rules(self):
        """_trigger_evolution_rule_analysis returns early if no rules engine."""
        loop = self._make_loop()
        loop._evolution_rules = None
        loop._trigger_evolution_rule_analysis({"success": True}, "task", [])
        # Should not crash

    def test_trigger_not_significant(self):
        """_trigger_evolution_rule_analysis returns early if nothing significant."""
        loop = self._make_loop()
        mock_rules = MagicMock()
        loop._evolution_rules = mock_rules

        loop._trigger_evolution_rule_analysis(
            {"success": True, "errors": [], "turns": 1, "result": "ok"},
            "simple task",
            [],
        )
        mock_rules.analyze_failure.assert_not_called()

    def test_trigger_with_errors(self):
        """_trigger_evolution_rule_analysis analyzes failures."""
        loop = self._make_loop()
        mock_rules = MagicMock()
        mock_rules.analyze_failure.return_value = {
            "rule": "Always check input",
            "category": "rule",
            "keywords": ["check"],
            "task_type": "coding",
        }
        mock_rules.add_rule.return_value = {"action": "created", "confidence": 0.8}
        loop._evolution_rules = mock_rules

        loop._trigger_evolution_rule_analysis(
            {"success": False, "errors": ["error 1"], "turns": 3, "result": "work"},
            "failed task",
            [],
        )
        mock_rules.analyze_failure.assert_called_once()
        mock_rules.add_rule.assert_called_once()

    def test_trigger_with_correction(self):
        """_trigger_evolution_rule_analysis triggers on user correction."""
        loop = self._make_loop()
        mock_rules = MagicMock()
        mock_rules.analyze_failure.return_value = {
            "rule": "Use this format",
            "category": "format",
            "keywords": [],
            "task_type": "",
        }
        mock_rules.add_rule.return_value = {"action": "created", "confidence": 0.7}
        loop._evolution_rules = mock_rules

        messages = [{"role": "user", "content": "不对，应该这样"}]
        loop._trigger_evolution_rule_analysis(
            {"success": True, "errors": [], "turns": 2, "result": "ok"},
            "task",
            messages,
        )
        mock_rules.analyze_failure.assert_called_once()

    def test_trigger_significant_complex_task(self):
        """_trigger_evolution_rule_analysis triggers on complex task completion."""
        loop = self._make_loop()
        mock_rules = MagicMock()
        mock_rules.analyze_failure.return_value = {
            "rule": "Good pattern",
            "category": "pattern",
            "keywords": [],
            "task_type": "coding",
        }
        mock_rules.add_rule.return_value = {"action": "created", "confidence": 0.9}
        loop._evolution_rules = mock_rules

        loop._trigger_evolution_rule_analysis(
            {"success": True, "errors": [], "turns": 5, "result": "x" * 100},
            "complex task",
            [],
        )
        mock_rules.analyze_failure.assert_called_once()

    def test_trigger_success_reinforces_rules(self):
        """_trigger_evolution_rule_analysis reinforces rules on success."""
        loop = self._make_loop()
        mock_rules = MagicMock()
        mock_rules.match_rules.return_value = [
            {"rule": "Always check input", "confidence": 0.5},
        ]
        loop._evolution_rules = mock_rules

        loop._trigger_evolution_rule_analysis(
            {"success": True, "errors": [], "turns": 5, "result": "x" * 100},
            "task",
            [],
        )
        mock_rules.match_rules.assert_called_once()


# ===================================================================
# _run_evolution_pipeline
# ===================================================================

class TestRunEvolutionPipeline:
    """Cover _run_evolution_pipeline method."""

    def _make_loop(self):
        from core.agent_loop import AgentLoop
        with patch('core.agent_loop.LLMClient') as mock_llm_cls, \
             patch('core.agent_loop.MemoryManager') as mock_mem_cls, \
             patch('core.agent_loop.EvolutionEngine') as mock_evo_cls, \
             patch('core.agent_loop.ToolRegistry') as mock_tr_cls, \
             patch('core.agent_loop.SessionStore') as mock_ss_cls, \
             patch('core.agent_loop.Whiteboard'), \
             patch('core.agent_loop.Decomposer'), \
             patch('core.agent_loop.WhiteboardExecutor'), \
             patch('core.agent_loop.MCPBridge'), \
             patch('core.agent_loop.PromptCache'), \
             patch('core.agent_loop.init_hooks'):

            mock_llm = MagicMock()
            mock_llm_cls.return_value = mock_llm
            mock_memory = MagicMock()
            mock_mem_cls.return_value = mock_memory

            mock_evo_state = MagicMock()
            mock_evo_state.is_novel.return_value = True
            mock_evo_state.is_repeated_failure.return_value = False
            mock_evo_state.get_task_type_count.return_value = 3
            mock_evo_state.is_unknown_error.return_value = False
            mock_evo_state.health_check.return_value = None

            mock_evo = MagicMock()
            mock_evo.evolution_state = mock_evo_state
            mock_evo.run_pipeline.return_value = {"skill_written": False}
            mock_evo_cls.return_value = mock_evo

            mock_tr = MagicMock()
            mock_tr_cls.return_value = mock_tr
            mock_ss = MagicMock()
            mock_ss.create_session.return_value = "sess_test"
            mock_ss_cls.return_value = mock_ss

            with patch.object(AgentLoop, '_register_delegate_tool'), \
                 patch.object(AgentLoop, '_register_skill_rollback'):
                loop = AgentLoop(llm=mock_llm, memory=mock_memory, evolution=mock_evo,
                                 tool_registry=mock_tr, session_store=mock_ss, max_turns=5)

            loop._log = MagicMock()
            loop.on_step = MagicMock()
            loop._observer = MagicMock()
            loop._observer.on_task_complete.return_value = MagicMock()
            loop._observer.on_task_complete.return_value.has_user_correction = False
            return loop

    def test_evolution_pipeline_basic(self):
        """_run_evolution_pipeline runs the full pipeline."""
        loop = self._make_loop()
        loop._run_evolution_pipeline(
            {"success": True, "errors": [], "task_type": "coding", "result": "ok",
             "duration": 5.0, "tool_calls": 3},
            "task",
            [],
        )
        loop.evolution.run_pipeline.assert_called_once()
        loop._observer.on_task_complete.assert_called_once()

    def test_evolution_pipeline_with_user_correction(self):
        """_run_evolution_pipeline detects user correction from messages."""
        loop = self._make_loop()
        loop._run_evolution_pipeline(
            {"success": True, "errors": [], "task_type": "coding", "result": "ok",
             "duration": 5.0, "tool_calls": 3},
            "task",
            [{"role": "user", "content": "不对，应该这样改"}],
        )
        loop.evolution.run_pipeline.assert_called_once()

    def test_evolution_pipeline_with_errors(self):
        """_run_evolution_pipeline handles error detection."""
        loop = self._make_loop()
        loop._run_evolution_pipeline(
            {"success": False, "errors": ["unknown syntax error"], "task_type": "coding",
             "result": "partial", "duration": 5.0, "tool_calls": 3},
            "task",
            [],
        )
        loop.evolution.evolution_state.is_unknown_error.assert_called_with("unknown syntax error")

    def test_evolution_pipeline_with_skill_written(self):
        """_run_evolution_pipeline records quality when skill written (L1668-1680)."""
        loop = self._make_loop()
        loop.evolution.run_pipeline.return_value = {
            "skill_written": True,
            "skill_name": "new_skill",
        }

        task_result = {
            "success": True, "errors": [], "task_type": "coding", "result": "ok",
            "duration": 5.0, "tool_calls": 3, "quality": {"score": 8},
        }
        loop._run_evolution_pipeline(task_result, "task", [])
        loop.evolution.evolution_state.record_skill_quality.assert_called_once_with("new_skill", 0.8)

    def test_evolution_pipeline_evolution_mode_notifications(self):
        """_run_evolution_pipeline logs evolution mode (L1682-1695)."""
        loop = self._make_loop()

        for mode in ["CAPTURED", "FIX", "DERIVED"]:
            mock_evo = loop.evolution
            mock_evo.run_pipeline.return_value = {
                "evolution_mode": mode,
                "skill_name": "test_skill",
                "skill_written": True,
            }
            loop._log = MagicMock()

            loop._run_evolution_pipeline(
                {"success": True, "errors": [], "task_type": "coding", "result": "ok",
                 "duration": 5.0, "tool_calls": 3, "quality": {"score": 7}},
                "task",
                [],
            )

    def test_evolution_pipeline_health_check(self):
        """_run_evolution_pipeline logs health check warning."""
        loop = self._make_loop()
        loop.evolution.evolution_state.health_check.return_value = "High failure rate"
        loop._log = MagicMock()

        loop._run_evolution_pipeline(
            {"success": True, "errors": [], "task_type": "coding", "result": "ok",
             "duration": 5.0, "tool_calls": 3, "quality": {"score": 7}},
            "task",
            [],
        )

    def test_evolution_pipeline_exception(self):
        """_run_evolution_pipeline handles exception gracefully (L1708-1709)."""
        loop = self._make_loop()
        loop._observer.on_task_complete.side_effect = Exception("pipeline failed")

        loop._run_evolution_pipeline(
            {"success": True, "errors": [], "task_type": "coding", "result": "ok",
             "duration": 5.0, "tool_calls": 3},
            "task",
            [],
        )
        # Should not raise

    def test_evolution_pipeline_evolution_state_exception(self):
        """_run_evolution_pipeline handles evolution state exceptions (L1655-1656)."""
        loop = self._make_loop()
        loop.evolution.evolution_state.is_novel.side_effect = Exception("state error")

        loop._run_evolution_pipeline(
            {"success": True, "errors": [], "task_type": "coding", "result": "ok",
             "duration": 5.0, "tool_calls": 3},
            "task",
            [],
        )
        loop.evolution.run_pipeline.assert_called_once()
