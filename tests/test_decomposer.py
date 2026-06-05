"""测试 core/whiteboard/decomposer.py — 任务分解器。"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


class TestStep:
    """Step dataclass 测试。"""

    def test_defaults(self):
        from core.whiteboard.decomposer import Step
        s = Step()
        assert s.status == "pending"
        assert s.id == ""
        assert s.depends_on == []
        assert s.estimated_complexity == "simple"

    def test_to_dict(self):
        from core.whiteboard.decomposer import Step
        s = Step(id="s0", description="test")
        d = s.to_dict()
        assert d["id"] == "s0"
        assert d["description"] == "test"
        assert d["status"] == "pending"

    def test_from_dict(self):
        from core.whiteboard.decomposer import Step
        d = {"id": "s0", "description": "test", "status": "completed", "extra_field": "ignored"}
        s = Step.from_dict(d)
        assert s.id == "s0"
        assert s.description == "test"
        assert s.status == "completed"
        assert not hasattr(s, "extra_field")


class TestDecomposer:
    """Decomposer 测试。"""

    def _make_dc(self):
        from core.whiteboard.decomposer import Decomposer
        return Decomposer()

    def test_init_no_templates_path(self):
        dc = self._make_dc()
        assert dc._custom_templates == {}

    def test_init_with_templates_path(self):
        from core.whiteboard.decomposer import Decomposer
        with patch("pathlib.Path.exists", return_value=False):
            dc = Decomposer(templates_path=Path("/fake/path.json"))
            assert dc._custom_templates == {}

    def test_init_load_custom(self):
        from core.whiteboard.decomposer import Decomposer
        mock_data = json.dumps({"research": ["step1", "step2"]})
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=mock_data):
                dc = Decomposer(templates_path=Path("/fake/path.json"))
                assert "research" in dc._custom_templates

    def test_init_load_custom_invalid_json(self):
        from core.whiteboard.decomposer import Decomposer
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="not json{{{"):
                dc = Decomposer(templates_path=Path("/fake/path.json"))
                assert dc._custom_templates == {}


class _DecomposerHelper:
    """Helper to create a Decomposer for testing."""

    @staticmethod
    def make():
        from core.whiteboard.decomposer import Decomposer
        return Decomposer()


class TestDecomposerDetect(_DecomposerHelper):
    """_detect_task_types 测试。"""

    def test_coding(self):
        dc = self.make()
        assert "coding" in dc._detect_task_types("write a Python script")

    def test_research(self):
        dc = self.make()
        assert "research" in dc._detect_task_types("research latest AI")

    def test_web(self):
        dc = self.make()
        assert "web" in dc._detect_task_types("search for python")

    def test_api(self):
        dc = self.make()
        assert "api" in dc._detect_task_types("call rest api")

    def test_file(self):
        dc = self.make()
        assert "file" in dc._detect_task_types("read file from disk")

    def test_install(self):
        dc = self.make()
        assert "install" in dc._detect_task_types("install npm package")

    def test_analysis(self):
        dc = self.make()
        assert "analysis" in dc._detect_task_types("analyze sales data")

    def test_file_operation(self):
        dc = self.make()
        assert "file_operation" in dc._detect_task_types("move file to new dir")

    def test_no_match(self):
        dc = self.make()
        assert dc._detect_task_types("hello world") == set()

    def test_multiple(self):
        dc = self.make()
        types = dc._detect_task_types("write script to search and analyze")
        assert "coding" in types
        assert "web" in types
        assert "analysis" in types


class TestTemplateToSteps:
    """_template_to_steps 测试。"""

    def test_simple_template(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc._template_to_steps(["step A", "step B"], "test")
        assert len(steps) == 2
        assert steps[0].id == "test_0"
        assert steps[0].description == "step A"
        assert steps[1].id == "test_1"


class TestGenericDecompose:
    """_generic_decompose 测试。"""

    def test_short_task(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc._generic_decompose("short")
        assert len(steps) == 1
        assert steps[0].estimated_complexity == "simple"

    def test_medium_task(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc._generic_decompose("a" * 50)
        assert len(steps) == 2

    def test_long_task(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc._generic_decompose("a" * 120)
        assert len(steps) == 4
        assert steps[-1].depends_on == ["step_2"]


class TestDecompose:
    """decompose 完整流程测试。"""

    def test_coding_task(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc.decompose("write a Python script to process CSV")
        assert len(steps) >= 4
        assert steps[0].status == "pending"

    def test_unknown_task_fallback(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc.decompose("hello world")
        assert len(steps) == 1
        assert steps[0].description == "hello world"

    def test_fallback_guard(self):
        """兜底分支：所有分解方式都返回空时。"""
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        dc._generic_decompose = lambda task: []
        dc._detect_task_types = lambda task: set()
        steps = dc.decompose("anything")
        assert len(steps) == 1
        assert steps[0].description.startswith("完成任务")

    def test_with_context(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc.decompose("搜索 python 库", context={"source": "test"})
        assert len(steps) >= 1

    def test_subtask_and_master_combined(self):
        """子任务和主模板合并去重。"""
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = dc.decompose("search for data and analyze")
        assert len(steps) >= 2


class TestSetDependencies:
    """_set_dependencies 测试。"""

    def test_no_deps_set(self):
        from core.whiteboard.decomposer import Step
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = [Step(id="s0"), Step(id="s1"), Step(id="s2")]
        dc._set_dependencies(steps)
        assert steps[1].depends_on == ["s0"]
        assert steps[2].depends_on == ["s1"]

    def test_existing_deps_preserved(self):
        from core.whiteboard.decomposer import Step
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = [Step(id="s0"), Step(id="s1", depends_on=["s0"]), Step(id="s2")]
        dc._set_dependencies(steps)
        # has_deps=True so _set_dependencies does nothing
        assert steps[1].depends_on == ["s0"]
        assert steps[2].depends_on == []  # unchanged

    def test_single_step(self):
        from core.whiteboard.decomposer import Step
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        steps = [Step(id="s0")]
        dc._set_dependencies(steps)
        assert steps[0].depends_on == []


class TestReplan:
    """replan 测试。"""

    def test_no_failures(self):
        from core.whiteboard.decomposer import Step, Decomposer
        dc = Decomposer()
        steps = [Step(id="s0"), Step(id="s1"), Step(id="s2")]
        remaining = dc.replan("task", steps, completed_ids={"s0"}, failed_ids=set(),
                              whiteboard_summary={})
        assert len(remaining) == 2
        assert remaining[0].id == "s1"

    def test_with_failures(self):
        from core.whiteboard.decomposer import Step, Decomposer
        dc = Decomposer()
        steps = [Step(id="s0", description="step0"), Step(id="s1", description="step1")]
        remaining = dc.replan("task", steps, completed_ids={"s0"}, failed_ids={"s1"},
                              whiteboard_summary={})
        assert len(remaining) == 2  # repair + remaining
        assert remaining[0].id == "repair_s1"
        assert "修复" in remaining[0].description

    def test_failed_step_marked(self):
        from core.whiteboard.decomposer import Step, Decomposer
        dc = Decomposer()
        steps = [Step(id="s0"), Step(id="s1")]
        remaining = dc.replan("task", steps, completed_ids=set(), failed_ids={"s1"},
                              whiteboard_summary={})
        # s0 still pending, s1 failed — s1 is at index 2 due to repair step
        failed_steps = [s for s in remaining if s.id == "s1"]
        assert len(failed_steps) == 1
        assert failed_steps[0].status == "failed"

    def test_repair_limited_to_2(self):
        from core.whiteboard.decomposer import Step, Decomposer
        dc = Decomposer()
        steps = [Step(id="s0"), Step(id="s1"), Step(id="s2")]
        remaining = dc.replan("task", steps, completed_ids=set(),
                              failed_ids={"s0", "s1", "s2"},
                              whiteboard_summary={})
        # max 2 repair steps
        repair_count = sum(1 for s in remaining if s.id.startswith("repair_"))
        assert repair_count <= 2


class TestSaveTemplates:
    """save_templates 测试。"""

    def test_no_path(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        # no error
        dc.save_templates()

    def test_with_path(self):
        from core.whiteboard.decomposer import Decomposer
        dc = Decomposer()
        with patch.object(Path, 'write_text') as mock_w:
            with patch.object(Path, 'mkdir'):
                dc.save_templates(path=Path("/tmp/templates.json"))
                mock_w.assert_called_once()
                args = mock_w.call_args[0][0]
                assert "coding" in args or "research" in args
