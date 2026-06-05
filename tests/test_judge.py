"""测试 core/judge.py — 进化判断器。"""

import json
import pytest
from unittest.mock import MagicMock


class TestBuildDigest:
    """build_digest 测试。"""

    def test_basic_observation(self):
        """基本 Observation 构建。"""
        from core.judge import build_digest
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "coding"
        obs.success = True
        obs.tool_calls = 5
        obs.tools_used = {"terminal"}
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = "success"
        obs.skill_name = ""

        digest = build_digest(obs, None)
        assert digest["task_type"] == "coding"
        assert digest["success"] is True
        assert digest["tool_calls"] == 5
        assert digest["error_count"] == 0
        assert digest["consecutive_failures"] == 0

    def test_with_state(self):
        """带 EvolutionState 参数。"""
        from core.judge import build_digest
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 2
        obs.tools_used = set()
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = ""
        obs.skill_name = ""

        state = {"consecutive_fail": 3, "count": 5}
        digest = build_digest(obs, state)
        assert digest["consecutive_failures"] == 3
        assert digest["task_history"] == 5

    def test_with_errors(self):
        """带错误信息。"""
        from core.judge import build_digest
        obs = MagicMock()
        obs.errors = ["error1", "error2"]
        obs.tool_errors = []
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 1
        obs.tools_used = set()
        obs.has_user_correction = True
        obs.has_unknown_error = True
        obs.result = "failed"
        obs.skill_name = "my-skill"

        digest = build_digest(obs, None)
        assert digest["error_count"] == 2
        assert digest["has_user_correction"] is True
        assert digest["existing_skill"] == "my-skill"

    def test_tool_errors_merged(self):
        """tool_errors 也合并统计。"""
        from core.judge import build_digest
        obs = MagicMock()
        obs.errors = ["e1"]
        obs.tool_errors = [MagicMock(error_message="tool fail")]
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 1
        obs.tools_used = set()
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = ""
        obs.skill_name = ""

        digest = build_digest(obs, None)
        assert digest["error_count"] == 2
        assert "tool fail" in digest["error_summary"]


class TestJudge:
    """Judge 类测试。"""

    def test_parse_content_from_dict(self):
        """从 dict 格式解析。"""
        from core.judge import Judge
        result = Judge._parse_content({"content": "hello"})
        assert result == "hello"

    def test_parse_content_from_string(self):
        """从字符串格式解析。"""
        from core.judge import Judge
        result = Judge._parse_content("hello")
        assert result == "hello"

    def test_parse_content_empty(self):
        """空结果返回空字符串。"""
        from core.judge import Judge
        assert Judge._parse_content({}) == ""
        assert Judge._parse_content(None) == ""

    def test_fallback(self):
        """降级结果格式正确。"""
        from core.judge import Judge
        result = Judge._default_fallback("test reason")
        assert result["worth_learning"] is False
        assert "test reason" in result["reason"]
        assert result["skill"] is None

    def test_evaluate_success(self):
        """evaluate 成功解析 LLM 返回。"""
        mock_llm = MagicMock()
        mock_llm.return_value = json.dumps({
            "worth_learning": True,
            "evolution_mode": "CAPTURED",
            "reason": "用户纠正",
            "skill": {"name": "fix-pip", "steps": ["step1"]},
        })

        from core.judge import Judge
        judge = Judge(mock_llm)
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "coding"
        obs.success = True
        obs.tool_calls = 5
        obs.tools_used = {"terminal"}
        obs.has_user_correction = True
        obs.has_unknown_error = False
        obs.result = "done"
        obs.skill_name = ""

        result = judge.evaluate(obs)
        assert result["worth_learning"] is True
        assert result["evolution_mode"] == "CAPTURED"
        assert result["skill"]["name"] == "fix-pip"

    def test_evaluate_json_decode_error(self):
        """JSON 解析失败降级。"""
        mock_llm = MagicMock()
        mock_llm.return_value = "not valid json{"

        from core.judge import Judge
        judge = Judge(mock_llm)
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 1
        obs.tools_used = set()
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = ""
        obs.skill_name = ""

        result = judge.evaluate(obs)
        assert result["worth_learning"] is False

    def test_evaluate_llm_exception(self):
        """LLM 异常降级。"""
        mock_llm = MagicMock(side_effect=RuntimeError("LLM crash"))

        from core.judge import Judge
        judge = Judge(mock_llm)
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 1
        obs.tools_used = set()
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = ""
        obs.skill_name = ""

        result = judge.evaluate(obs)
        assert result["worth_learning"] is False
        assert "LLM crash" in result["reason"]

    def test_evaluate_empty_content(self):
        """LLM 返回空内容降级。"""
        mock_llm = MagicMock(return_value="")

        from core.judge import Judge
        judge = Judge(mock_llm)
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 1
        obs.tools_used = set()
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = ""
        obs.skill_name = ""

        result = judge.evaluate(obs)
        assert result["worth_learning"] is False

    def test_evaluate_missing_field(self):
        """缺少 worth_learning 字段降级。"""
        mock_llm = MagicMock(return_value=json.dumps({"reason": "no field"}))

        from core.judge import Judge
        judge = Judge(mock_llm)
        obs = MagicMock()
        obs.errors = []
        obs.tool_errors = []
        obs.task_type = "test"
        obs.success = False
        obs.tool_calls = 1
        obs.tools_used = set()
        obs.has_user_correction = False
        obs.has_unknown_error = False
        obs.result = ""
        obs.skill_name = ""

        result = judge.evaluate(obs)
        assert result["worth_learning"] is False
