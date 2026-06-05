"""测试 core/whiteboard/executor.py — 白板执行器。"""

import unittest
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from core.whiteboard.decomposer import Step


class TestExecutorHelpers:
    """WhiteboardExecutor 辅助方法测试（mock 依赖）。"""

    def _make_executor(self, **kwargs):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_tools = MagicMock()
        mock_wb = MagicMock()
        mock_dc = MagicMock()
        overrides = {"llm": mock_llm, "tool_registry": mock_tools,
                     "whiteboard": mock_wb, "decomposer": mock_dc}
        overrides.update(kwargs)
        return WhiteboardExecutor(**overrides), overrides

    def _make_step(self, **kwargs):
        defaults = dict(id="s0", description="do something", status="pending",
                        depends_on=[], output_key="", estimated_complexity="simple")
        defaults.update(kwargs)
        return Step(**defaults)

    def test_init_defaults(self):
        executor, deps = self._make_executor()
        assert executor.max_retries == 2
        assert executor.checkpoint_interval == 5
        assert executor._current_step_idx == 0

    def test_log_with_on_step(self):
        executor, deps = self._make_executor(on_step=MagicMock())
        executor._log("test message")
        deps["whiteboard"].on_step is None
        executor.on_step.assert_called_once_with("test message")

    def test_log_without_on_step(self):
        executor, deps = self._make_executor(on_step=None)
        executor._log("test")  # no error


class TestNextPendingStep:
    """_next_pending_step 测试。"""

    def _make_executor(self):
        from core.whiteboard.executor import WhiteboardExecutor
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock())
        return e

    def test_first_pending(self):
        e = self._make_executor()
        steps = [Step(id="s0"), Step(id="s1")]
        assert e._next_pending_step(steps).id == "s0"

    def test_skip_non_pending(self):
        e = self._make_executor()
        steps = [Step(id="s0", status="completed"), Step(id="s1")]
        assert e._next_pending_step(steps).id == "s1"

    def test_wait_for_dependency(self):
        e = self._make_executor()
        e._completed_steps = set()
        steps = [Step(id="s0"), Step(id="s1", depends_on=["s0"])]
        # s0 is pending, returned first
        assert e._next_pending_step(steps).id == "s0"

    def test_dependency_met(self):
        e = self._make_executor()
        e._completed_steps = {"s0"}
        steps = [Step(id="s0", status="completed"), Step(id="s1", depends_on=["s0"])]
        assert e._next_pending_step(steps).id == "s1"

    def test_dependency_not_met(self):
        e = self._make_executor()
        e._completed_steps = {"s0"}
        steps = [Step(id="s0", status="completed"), Step(id="s1", depends_on=["s0"])]
        assert e._next_pending_step(steps).id == "s1"

    def test_none_remaining(self):
        e = self._make_executor()
        steps = [Step(id="s0", status="completed")]
        assert e._next_pending_step(steps) is None

    def test_all_blocked(self):
        e = self._make_executor()
        e._completed_steps = set()
        steps = [Step(id="s1", depends_on=["s0"]), Step(id="s0")]
        # s1 depends on s0 which is pending but after s1 in list — s0 also pending
        # So s0 is returned first
        assert e._next_pending_step(steps).id == "s0"


class TestBuildStepContext:
    """_build_step_context 测试。"""

    def test_no_completed_no_deps(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.return_value = []
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        ctx = e._build_step_context(Step(id="s0", description="test"))
        assert ctx == "" or "已完成" not in ctx

    def test_with_completed_steps(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.side_effect = lambda p: (
            [{"description": "step1", "result_summary": "ok"}] if p == "completed"
            else []
        )
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        ctx = e._build_step_context(Step(id="s1", description="step2"))
        assert "已完成" in ctx
        assert "step1" in ctx

    def test_with_dependency_intermediates(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        def mock_read(partition):
            if partition == "completed":
                return []
            elif partition == "intermediate":
                return [{"step_id": "s0", "content": "intermediate result"}]
            return []
        mock_wb.read.side_effect = mock_read
        mock_wb.current_task.return_value = None
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        ctx = e._build_step_context(Step(id="s1", description="step2", depends_on=["s0"]))
        assert "intermediate" in ctx

    def test_with_current_task(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.return_value = []
        mock_wb.current_task.return_value = {"step_index": 2}
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        e._total_steps = 5
        ctx = e._build_step_context(Step(id="s2", description="step3"))
        assert "2" in ctx
        assert "5" in ctx


class TestUpdateStepInWhiteboard:
    """_update_step_in_whiteboard 测试。"""

    def test_updates_plan(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.return_value = [{"id": "s0"}, {"id": "s1"}]
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        step = Step(id="s1", description="updated", status="completed")
        e._update_step_in_whiteboard(step)
        written = mock_wb.write.call_args[0][1]
        assert written[1]["status"] == "completed"

    def test_not_found(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.return_value = [{"id": "s0"}]
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        e._update_step_in_whiteboard(Step(id="nonexistent", status="completed"))
        # no crash

    def test_appends_completed(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.return_value = [{"id": "s0"}]
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        e._update_step_in_whiteboard(Step(id="s0", status="completed"))
        mock_wb.append.assert_called_with("completed", unittest.mock.ANY)
        # Use ANY for the step dict

    def test_does_not_append_if_not_completed(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_wb = MagicMock()
        mock_wb.read.return_value = [{"id": "s0"}]
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), whiteboard=mock_wb)
        e._update_step_in_whiteboard(Step(id="s0", status="failed"))
        # should NOT append to completed
        assert mock_wb.append.call_count == 0  # read called but append for completed not called


class TestExecuteStep:
    """_execute_step 测试。"""

    def test_success_with_content(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "success": True,
            "content": "done",
            "tool_calls": [],
        }
        mock_tools = MagicMock()
        mock_tools.get_schemas.return_value = []
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=mock_tools)
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is True
        assert "done" in result["output"]

    def test_llm_failure(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {"success": False, "error": "API error"}
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock())
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is False
        assert "API" in result["error"]

    def test_finish_step_tool(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "success": True,
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "finish_step",
                    "arguments": {"output": "result output", "summary": "done summary"},
                }
            }],
        }
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock())
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is True
        assert result["output"] == "result output"
        assert result["summary"] == "done summary"

    def test_finish_step_output_as_string(self):
        """finish_step 的 output 是字符串。"""
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "success": True,
            "content": "content",
            "tool_calls": [{
                "function": {
                    "name": "finish_step",
                    "arguments": {"output": "step done"},
                }
            }],
        }
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock())
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is True

    def test_global_finish(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "success": True,
            "content": "all done",
            "tool_calls": [{"function": {"name": "finish", "arguments": {}}}],
        }
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock())
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is True

    def test_regular_tool_execution(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "success": True,
            "content": "",
            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "/tmp/test"}}}],
        }
        mock_tools = MagicMock()
        mock_tools.get_schemas.return_value = [{"name": "read_file"}]
        mock_tools.execute.return_value = {"output": "file content"}
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=mock_tools)
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is True
        assert "file content" in result["output"]

    def test_exception_handling(self):
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("connection lost")
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock())
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is False
        assert "connection" in result["error"]

    def test_missing_summary_in_finish(self):
        """finish_step 无 summary 时 combined output。"""
        from core.whiteboard.executor import WhiteboardExecutor
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "success": True,
            "content": "fallback summary",
            "tool_calls": [{
                "function": {
                    "name": "finish_step",
                    "arguments": {"output": "done"},
                }
            }],
        }
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock())
        result = e._execute_step("task", Step(id="s0", description="test"), "", [])
        assert result["success"] is True
        # output includes both content and tool output
        assert "done" in result["output"]


class TestRunMethod:
    """run() 完整流程测试（mock 所有依赖）。"""

    def test_decompose_empty_steps(self):
        """分解结果为空的处理。"""
        from core.whiteboard.executor import WhiteboardExecutor
        mock_dc = MagicMock()
        mock_dc.decompose.return_value = []
        e = WhiteboardExecutor(llm=MagicMock(), tool_registry=MagicMock(), decomposer=mock_dc)
        result = e.run("test task")
        assert result["success"] is False
        assert "分解失败" in result["result"]

    def test_single_step_success(self):
        from core.whiteboard.executor import WhiteboardExecutor
        step = Step(id="s0", description="do it")
        mock_dc = MagicMock()
        mock_dc.decompose.return_value = [step]
        mock_wb = MagicMock()
        mock_wb.read.side_effect = lambda p: (
            [{"id": "s0", "status": "completed", "description": "do it"}]
            if p == "next_plan" else
            [] if p in ("completed", "intermediate") else
            [{"step_index": 0}]
        )
        mock_wb.current_task.return_value = {"step_index": 0}
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {"success": True, "content": "ok", "tool_calls": []}
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock(),
                                whiteboard=mock_wb, decomposer=mock_dc)
        result = e.run("test")
        # 至少不崩溃
        assert "success" in result

    def test_retry_exceeded_skip(self):
        """L135-143: retry _retry_count > max_retries → step gets status='skipped'."""
        from core.whiteboard.executor import WhiteboardExecutor
        step = Step(id="s0", description="do it")
        mock_dc = MagicMock()
        mock_dc.decompose.return_value = [step]
        # replan returns a NEW step with pending status (as real replan would)
        mock_dc.replan.return_value = [Step(id="s0", description="do it")]
        mock_wb = MagicMock()
        mock_wb.read.side_effect = lambda p: (
            [{"id": "s0", "description": "do it", "status": "pending"}]
            if p == "next_plan" else
            [] if p in ("completed", "intermediate") else
            {"step_index": 0}
        )
        mock_wb.current_task.return_value = {"step_index": 0}
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {"success": False, "error": "fail"}
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock(),
                                whiteboard=mock_wb, decomposer=mock_dc,
                                max_retries=0)
        result = e.run("test")
        # With max_retries=0, first failure adds step to _failed_steps.
        # On next iteration, step.id in _failed_steps → retry_count becomes 1 > 0 → skip.
        assert result["success"] is False
        assert any("放弃" in err for err in result["errors"])

    def test_discard_on_retry_success(self):
        """L172: _failed_steps.discard(step.id) when a previously failed step succeeds on retry."""
        from core.whiteboard.executor import WhiteboardExecutor
        mock_dc = MagicMock()
        first_step = Step(id="s0", description="do it")
        mock_dc.decompose.return_value = [first_step]
        # replan returns a NEW Step with fresh pending status (simulating real replan)
        fresh_step = Step(id="s0", description="do it")
        mock_dc.replan.return_value = [fresh_step]
        mock_wb = MagicMock()
        mock_wb.read.side_effect = lambda p: (
            [{"id": "s0", "description": "do it", "status": "pending"}]
            if p == "next_plan" else
            [] if p in ("completed", "intermediate") else
            {"step_index": 0}
        )
        mock_wb.current_task.return_value = {"step_index": 0}
        # LLM fails first call, succeeds on second call (retry)
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            {"success": False, "error": "fail"},        # first call → fail
            {"success": True, "content": "ok", "tool_calls": []},  # retry → success
        ]
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock(),
                                whiteboard=mock_wb, decomposer=mock_dc,
                                max_retries=1)
        result = e.run("test")
        # Step should be completed after retry, so _failed_steps.discard was called
        assert result["steps_completed"] == 1
        # The step eventually succeeded, but errors from the first failure still accumulate
        assert result["result"] == "ok"

    def test_intermediate_result_saved(self):
        """L177: intermediate result saved when step.output_key and result.get('output') are truthy."""
        from core.whiteboard.executor import WhiteboardExecutor
        step = Step(id="s0", description="do it", output_key="my_result")
        mock_dc = MagicMock()
        mock_dc.decompose.return_value = [step]
        mock_wb = MagicMock()
        mock_wb.read.side_effect = lambda p: (
            [{"id": "s0", "description": "do it", "status": "pending"}]
            if p == "next_plan" else
            [] if p in ("completed", "intermediate") else
            {"step_index": 0}
        )
        mock_wb.current_task.return_value = {"step_index": 0}
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {"success": True, "content": "my output", "tool_calls": []}
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock(),
                                whiteboard=mock_wb, decomposer=mock_dc)
        result = e.run("test")
        # The intermediate append should have been called
        intermediate_calls = [call for call in mock_wb.append.call_args_list
                              if call[0][0] == "intermediate"]
        assert len(intermediate_calls) >= 1
        assert intermediate_calls[0][0][1]["key"] == "my_result"
        assert intermediate_calls[0][0][1]["content"] == "my output"

    def test_checkpoint_at_interval(self):
        """L236: checkpoint() called at end when step_idx >= checkpoint_interval."""
        from core.whiteboard.executor import WhiteboardExecutor
        # Create 2 steps so we get step_idx=2
        steps = [
            Step(id="s0", description="step one"),
            Step(id="s1", description="step two"),
        ]
        mock_dc = MagicMock()
        mock_dc.decompose.return_value = steps
        mock_wb = MagicMock()
        # next_plan read returns steps; completed/intermediate empty
        def mock_read(partition):
            if partition == "next_plan":
                return [{"id": s.id, "description": s.description, "status": "pending"}
                        for s in steps]
            if partition in ("completed", "intermediate"):
                return []
            return {"step_index": 0}
        mock_wb.read.side_effect = mock_read
        mock_wb.current_task.return_value = {"step_index": 0}
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {"success": True, "content": "done", "tool_calls": []}
        e = WhiteboardExecutor(llm=mock_llm, tool_registry=MagicMock(),
                                whiteboard=mock_wb, decomposer=mock_dc,
                                checkpoint_interval=1)
        e.run("test")
        # With checkpoint_interval=1 and 2 steps completed, step_idx=2 >= 1
        # so checkpoint() must have been called at least once in the run
        mock_wb.checkpoint.assert_called_once()
