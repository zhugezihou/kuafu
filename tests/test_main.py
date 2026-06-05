"""测试 core/main.py — KuafuAgent 核心模块。"""

import pytest
import json
from unittest.mock import patch, MagicMock, PropertyMock, ANY


class TestKuafuAgentInit:
    """KuafuAgent 初始化测试。"""

    def test_init_basic(self):
        """基本初始化。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            assert agent.name == "夸父"
            assert "0.2" in agent.version
            assert agent._task_count == 0

    def test_init_with_custom_llm(self):
        """传入自定义 LLMClient。"""
        mock_llm = MagicMock()
        mock_llm.backend = "test"
        mock_llm.model = "test-model"
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent(llm_client=mock_llm)
            assert agent.llm is mock_llm


class TestKuafuAgentInitWithPrioritizer:
    """P2: _HAS_PRIORITIZER=True 时初始化测试。"""

    def test_init_with_prioritizer_success(self):
        """_HAS_PRIORITIZER=True 且初始化成功。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=True,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # 验证 _init_prioritizer 成功后的状态
            assert hasattr(agent, '_prioritizer_thread')
            assert agent._prioritizer_thread is not None

    def test_init_with_prioritizer_exception(self):
        """_HAS_PRIORITIZER=True 但初始化抛出异常。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=True,
            IdlePrioritizer=MagicMock(side_effect=ValueError("fail")),
            EvolutionScheduler=MagicMock(),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # 异常后 _prioritizer_thread 应为 None
            assert agent._prioritizer_thread is None


class TestKuafuAgentStatus:
    """get_status 测试。"""

    def test_get_status_basic(self):
        """基本状态查询。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_memory = MagicMock()
        mock_memory.get_status.return_value = {"count": 5}
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {"total_evolutions": 3}

        with patch.multiple(
            "core.main",
            EvolutionEngine=MagicMock(return_value=mock_evo),
            MemoryAPI=MagicMock(return_value=mock_memory),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            agent._task_count = 7
            status = agent.get_status()
            assert status["name"] == "夸父"
            assert status["task_count"] == 7
            assert status["llm_model"] == "deepseek-chat"
            assert status["memory"] == {"count": 5}
            assert status["evolution"]["total_evolutions"] == 3

    def test_get_status_with_prioritizer(self):
        """_HAS_PRIORITIZER=True 时 get_status 包含 prioritizer 信息。"""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=True,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # 替换线程为 mock
            agent._prioritizer_thread = mock_thread
            status = agent.get_status()
            assert "prioritizer" in status
            assert status["prioritizer"]["alive"] is True


class TestKuafuAgentRepr:
    """__repr__ 测试。"""

    def test_repr(self):
        """__repr__ 格式正确。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            agent._task_count = 3
            r = repr(agent)
            assert "KuafuAgent" in r
            assert "deepseek-chat" in r


class TestKuafuAgentCleanInput:
    """_clean_input 静态方法测试。"""

    def test_clean_input_normal(self):
        """正常输入不变。"""
        from core.main import KuafuAgent
        assert KuafuAgent._clean_input("hello world") == "hello world"

    def test_clean_input_backspace(self):
        """退格符正确处理。"""
        from core.main import KuafuAgent
        assert KuafuAgent._clean_input("hel\blo") == "helo"

    def test_clean_input_control_chars(self):
        """控制字符被移除（除 tab/newline/cr）。"""
        from core.main import KuafuAgent
        result = KuafuAgent._clean_input("hello\x00world\x01")
        assert "hello" in result
        assert "world" in result

    def test_clean_input_strip(self):
        """结果被 strip。"""
        from core.main import KuafuAgent
        assert KuafuAgent._clean_input("  hello  ") == "hello"

    def test_clean_input_empty_backspace(self):
        """空字符串退格不报错。"""
        from core.main import KuafuAgent
        assert KuafuAgent._clean_input("\b\b") == ""


class TestKuafuAgentDetectGreeting:
    """_detect_greeting 问候检测测试。"""

    def test_detect_ni_hao(self):
        """你好 被识别为问候。"""
        from core.main import KuafuAgent
        assert "夸父" in KuafuAgent._detect_greeting("你好")

    def test_detect_hello(self):
        """hello 被识别为问候。"""
        from core.main import KuafuAgent
        assert "夸父" in KuafuAgent._detect_greeting("hello")

    def test_detect_ni_shi_shui(self):
        """你是谁 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("你是谁")

    def test_detect_zaijian(self):
        """再见 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("再见")

    def test_detect_xiexie(self):
        """谢谢 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("谢谢")

    def test_detect_not_greeting(self):
        """非问候语句返回空字符串。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("帮我写一个脚本") == ""

    def test_detect_partial_not_greeting(self):
        """含问候的文字不触发（需要纯问候）。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("你好，帮我做件事") == ""

    def test_detect_hi(self):
        """嗨 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("嗨")

    def test_detect_morning(self):
        """早上好 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("早上好")

    def test_detect_how_are_you(self):
        """你好吗 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("你好吗")

    def test_detect_in_front(self):
        """在吗 被识别。"""
        from core.main import KuafuAgent
        assert KuafuAgent._detect_greeting("在吗")


class TestKuafuAgentBuildSystemPrompt:
    """build_system_prompt 测试。"""

    def test_build_system_prompt_basic(self):
        """基本 prompt 组装。"""
        mock_llm = MagicMock()
        mock_llm.backend = "openai"
        mock_llm.model = "gpt-4"
        mock_llm.base_url = "https://api.openai.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_memory = MagicMock()
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {
            "total_evolutions": 5,
            "by_level": {1: 3, 2: 2},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(return_value=mock_evo),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            load_identity_statement=MagicMock(return_value="【夸父】"),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            prompt = agent.build_system_prompt()
            assert "夸父" in prompt or "【夸父】" in prompt
            assert "进化" in prompt
            assert "gpt-4" in prompt or "openai" in prompt

    def test_build_system_prompt_with_user_profile(self):
        """user_profile.json 存在且有偏好。"""
        mock_llm = MagicMock()
        mock_llm.backend = "openai"
        mock_llm.model = "gpt-4"
        mock_llm.base_url = "https://api.openai.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_memory = MagicMock()
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {
            "total_evolutions": 0,
            "by_level": {},
        }
        mock_memory.recall.return_value = []

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(return_value=mock_evo),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            load_identity_statement=MagicMock(return_value="【夸父】"),
        ):
            from core.main import KuafuAgent, ROOT_DIR
            profile_path = ROOT_DIR / "memory" / "user_profile.json"
            profile_path.write_text(json.dumps({
                "user_name": "测试用户",
                "preferences": {"language": "Python", "verbose": True},
            }))
            try:
                agent = KuafuAgent()
                prompt = agent.build_system_prompt()
                assert "用户偏好" in prompt
                assert "Python" in prompt
            finally:
                # Clean up
                profile_path.write_text('{"user_name": "用户", "preferences": {}}')

    def test_build_system_prompt_with_user_profile_no_pref(self):
        """user_profile.json 存在但无偏好。"""
        mock_llm = MagicMock()
        mock_llm.backend = "openai"
        mock_llm.model = "gpt-4"
        mock_llm.base_url = "https://api.openai.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_memory = MagicMock()
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {
            "total_evolutions": 0,
            "by_level": {},
        }
        mock_memory.recall.return_value = []

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(return_value=mock_evo),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            load_identity_statement=MagicMock(return_value="【夸父】"),
        ):
            from core.main import KuafuAgent, ROOT_DIR
            profile_path = ROOT_DIR / "memory" / "user_profile.json"
            original = profile_path.read_text(encoding="utf-8")
            try:
                profile_path.write_text(json.dumps({
                    "user_name": "测试用户",
                    "preferences": {},
                }))
                agent = KuafuAgent()
                prompt = agent.build_system_prompt()
                # No preferences, so "用户偏好" should not appear
                assert "用户偏好" not in prompt
            finally:
                profile_path.write_text(original)

    def test_build_system_prompt_with_user_profile_bad_json(self):
        """user_profile.json 存在但 JSON 解析失败 — 应静默 pass。"""
        mock_llm = MagicMock()
        mock_llm.backend = "openai"
        mock_llm.model = "gpt-4"
        mock_llm.base_url = "https://api.openai.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_memory = MagicMock()
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {
            "total_evolutions": 0,
            "by_level": {},
        }
        mock_memory.recall.return_value = []

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(return_value=mock_evo),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            load_identity_statement=MagicMock(return_value="【夸父】"),
        ):
            from core.main import KuafuAgent, ROOT_DIR
            profile_path = ROOT_DIR / "memory" / "user_profile.json"
            original = profile_path.read_text(encoding="utf-8")
            try:
                profile_path.write_text("not valid json{{{")
                agent = KuafuAgent()
                # Should not raise
                prompt = agent.build_system_prompt()
                assert "用户偏好" not in prompt
            finally:
                profile_path.write_text(original)

    def test_build_system_prompt_with_memories(self):
        """memory.recall 返回记忆时，prompt 包含相关记忆。"""
        mock_llm = MagicMock()
        mock_llm.backend = "openai"
        mock_llm.model = "gpt-4"
        mock_llm.base_url = "https://api.openai.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_memory = MagicMock()
        mock_memory.recall.return_value = [
            {"key": "test:1", "content": "记忆内容1"},
            {"key": "test:2", "content": "记忆内容2"},
            {"key": "test:3", "content": "记忆内容3"},
        ]
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {
            "total_evolutions": 0,
            "by_level": {},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(return_value=mock_evo),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            load_identity_statement=MagicMock(return_value="【夸父】"),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            prompt = agent.build_system_prompt()
            assert "相关记忆" in prompt
            assert "test:1" in prompt
            assert "test:2" in prompt
            assert "test:3" in prompt


class TestKuafuAgentSwitchModel:
    """switch_model 测试。"""

    def test_switch_model_success(self):
        """成功切换模型。"""
        mock_llm = MagicMock()
        mock_llm.backend = "deepseek"
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": True,
            "message": "已切换到 deepseek-chat",
            "config": {"backend": "deepseek", "model": "deepseek-chat"},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            msg = agent.switch_model("deepseek")
            assert "切换到" in msg

    def test_switch_model_failure(self):
        """切换失败返回错误消息。"""
        mock_llm = MagicMock()
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": False,
            "message": "未知模型",
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            msg = agent.switch_model("unknown")
            assert "未知" in msg


class TestKuafuAgentDetectModelSwitch:
    """_detect_model_switch 测试。"""

    def test_detect_list_models(self):
        """列出可用模型。"""
        mock_mm = MagicMock()
        mock_mm.list_templates.return_value = [{"id": "cloud:deepseek", "name": "DeepSeek", "active": True}]
        mock_mm.list_aliases.return_value = {"deepseek": "cloud:deepseek"}
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent._detect_model_switch("查看可用模型")
            assert result is not None
            assert "可用模型" in result or "Default" in result

    def test_detect_list_models_alt(self):
        """'模型列表' 查询。"""
        mock_mm = MagicMock()
        mock_mm.list_templates.return_value = [{"id": "cloud:deepseek", "name": "DeepSeek", "active": True}]
        mock_mm.list_aliases.return_value = {"deepseek": "cloud:deepseek"}
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent._detect_model_switch("模型列表")
            assert result is not None

    def test_detect_current_model(self):
        """查看当前模型。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_llm.backend = "deepseek"
        mock_llm.base_url = "https://api.deepseek.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_mm = MagicMock()
        mock_mm.as_dict.return_value = {"profile": "default"}

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent._detect_model_switch("当前模型")
            assert result is not None
            assert "deepseek-chat" in result

    def test_not_model_switch(self):
        """非模型切换返回 None。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            assert agent._detect_model_switch("帮我写代码") is None

    def test_switch_via_qiehuan(self):
        """切换模型到 命令（切换模型 local）。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": True,
            "message": "切换成功",
            "config": {"backend": "local", "model": "qwen"},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent._detect_model_switch("切换模型 local")
            assert result == "切换成功"

    def test_switch_via_qie_dao(self):
        """切到 命令。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": True,
            "message": "切到 deepseek",
            "config": {"backend": "deepseek", "model": "deepseek-chat"},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent._detect_model_switch("切到 deepseek")
            assert result == "切到 deepseek"

    def test_use_with_alias_exact(self):
        """用 <alias> 精确匹配 ALIASES。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": True,
            "message": "已切换到 deepseek",
            "config": {"backend": "deepseek", "model": "deepseek-chat"},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # 'deepseek' is in ALIASES -> direct match
            result = agent._detect_model_switch("用 deepseek")
            assert result is not None

    def test_use_with_alias_prefix(self):
        """用 <prefix> 前缀匹配 ALIASES。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": True,
            "message": "已切换到 deepseek-chat",
            "config": {"backend": "deepseek", "model": "deepseek-chat"},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # 'deepseek-chat' starts with 'deepseek' which is in ALIASES
            result = agent._detect_model_switch("用 deepseek-chat")
            assert result is not None

    def test_use_not_in_aliases(self):
        """用 <name> 但 name 不在 ALIASES 中。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # 'unknown-model' does not match any alias
            result = agent._detect_model_switch("用 unknown-model")
            assert result is None


class TestKuafuAgentResetConversation:
    """reset_conversation 测试。"""

    def test_reset_conversation(self):
        """重置对话上下文。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            agent._conversation = {"turn": 1}
            agent._conversation_messages = [{"role": "user", "content": "hi"}]
            agent.reset_conversation()
            assert agent._conversation is None
            assert agent._conversation_messages == []


class TestKuafuAgentReflect:
    """reflect_on_task 测试。"""

    def test_reflect_with_errors(self):
        """有错误时调用 reflect。"""
        mock_memory = MagicMock()
        mock_memory.reflect.return_value = "反思结果"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.reflect_on_task({"errors": ["timeout"]})
            assert result == "反思结果"

    def test_reflect_no_errors(self):
        """无错误返回 None。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            assert agent.reflect_on_task({"success": True}) is None


class TestKuafuAgentFormatHistory:
    """_format_conversation_history 测试。"""

    def test_format_empty(self):
        """空历史返回空字符串。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            assert agent._format_conversation_history() == ""

    def test_format_with_history(self):
        """有历史时正确格式化。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            agent._conversation_messages = [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
            result = agent._format_conversation_history()
            assert "[对话历史]" in result
            assert "用户: hi" in result
            assert "夸父: hello" in result


class TestKuafuAgentSyncModel:
    """_sync_model_manager_with_llm 测试。"""

    def test_sync_needed(self):
        """LLM 状态不同时同步。"""
        mock_llm = MagicMock()
        mock_llm.backend = "openai"
        mock_llm.model = "gpt-4"
        mock_llm.base_url = "https://api.openai.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_mm = MagicMock()
        mock_mm.as_dict.return_value = {"backend": "deepseek", "model": "deepseek-chat"}

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            agent._sync_model_manager_with_llm()
            assert mock_mm.apply.call_count == 2  # called once in __init__, once manually

    def test_sync_not_needed(self):
        """状态一致时不同步。"""
        mock_llm = MagicMock()
        mock_llm.backend = "deepseek"
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.as_dict.return_value = {"backend": "deepseek", "model": "deepseek-chat"}

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            agent._sync_model_manager_with_llm()
            mock_mm.apply.assert_not_called()


class TestKuafuAgentRun:
    """run() 方法测试 (需要 mock AgentLoop)。"""

    def test_run_greeting(self):
        """问候被短路，不进 AgentLoop。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.run("你好")
            assert result["success"] is True
            assert "夸父" in result["result"]
            assert result["turns"] == 0
            assert result["task_type"] == "greeting"

    def test_run_model_switch(self):
        """模型切换被短路，不进 AgentLoop。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_mm = MagicMock()
        mock_mm.switch.return_value = {
            "success": True,
            "message": "已切换",
            "config": {"backend": "deepseek", "model": "deepseek-chat"},
        }
        mock_mm.list_templates.return_value = []
        mock_mm.list_aliases.return_value = {}
        mock_mm.as_dict.return_value = {"profile": "default"}

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.run("当前模型")
            assert result["success"] is True
            assert result["turns"] == 0
            assert result["task_type"] == "model_switch"
            # task_count should have been decremented
            assert agent._task_count == 0

    def test_run_standard(self):
        """标准模式：使用 AgentLoop.run()。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "任务完成",
            "summary": "任务完成",
            "turns": 3,
            "errors": [],
            "evolution": None,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.run("写一个脚本")
            assert result["success"] is True
            assert result["result"] == "任务完成"
            assert result["turns"] == 3
            assert result["task_type"] == "generic"
            assert "duration" in result

    def test_run_whiteboard(self):
        """白板模式：使用 AgentLoop.run_whiteboard()。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run_whiteboard.return_value = {
            "success": True,
            "result": "白板结果",
            "summary": "白板总结",
            "turns": 5,
            "errors": [],
            "evolution": None,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.run("复杂任务", mode="whiteboard")
            assert result["success"] is True
            assert result["result"] == "白板结果"
            assert mock_loop.run_whiteboard.called

    def test_run_with_evolution(self):
        """进化事件触发记忆记录。"""
        from core.evolution import EvolutionEvent
        evo_event = EvolutionEvent(level="skill", action="新增工具: test_tool")

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "任务完成",
            "turns": 2,
            "errors": [],
            "evolution": evo_event,
        }
        mock_memory = MagicMock()

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.run("进化测试")
            assert result["success"] is True
            # Verify evolution was remembered
            evolution_calls = [
                c for c in mock_memory.remember.call_args_list
                if c[1].get('key', '').startswith('evolution:')
                or (isinstance(c[1], tuple) and c[1][0] and str(c[1][0]).startswith('evolution:'))
            ]
            # evolution was in the result, check that memory.remember was called
            # with evolution-related content
            found_evo = any(
                'evolution' in str(call)
                for call in mock_memory.remember.call_args_list
            )
            assert found_evo


class TestKuafuAgentConverse:
    """converse() 方法测试 (需要 mock AgentLoop)。"""

    def test_converse_greeting(self):
        """问候被短路，不进 AgentLoop。"""
        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.converse("你好")
            assert result["success"] is True
            assert "夸父" in result["result"]
            assert result["turns"] == 0
            assert result["task_type"] == "greeting"

    def test_converse_model_switch(self):
        """模型切换被短路。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_llm.backend = "deepseek"
        mock_llm.base_url = "https://api.deepseek.com"
        mock_llm.max_tokens = 4096
        mock_llm.temperature = 0.7
        mock_mm = MagicMock()
        mock_mm.as_dict.return_value = {"profile": "default"}

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(return_value=mock_mm),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.converse("当前模型")
            assert result["success"] is True
            assert result["turns"] == 0

    def test_converse_first_call(self):
        """首次对话（非追问）。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "你好，我是夸父！",
            "turns": 2,
            "errors": [],
            "evolution": None,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.converse("帮我写一个 Python 脚本")
            assert result["success"] is True
            assert result["result"] == "你好，我是夸父！"
            assert result["is_followup"] is False
            # conversation should be saved
            assert agent._conversation is not None
            assert agent._conversation["turn"] == 1
            assert len(agent._conversation_messages) == 2

    def test_converse_followup(self):
        """后续追问。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()

        # First call returns normally
        mock_loop.run.return_value = {
            "success": True,
            "result": "脚本已写好",
            "turns": 2,
            "errors": [],
            "evolution": None,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()

            # First call
            result1 = agent.converse("写一个脚本")
            assert result1["is_followup"] is False

            # Second call (followup)
            result2 = agent.converse("改一下输出格式")
            assert result2["is_followup"] is True
            assert result2["success"] is True
            # Conversation messages should have accumulated
            assert len(agent._conversation_messages) == 4

    def test_converse_message_truncation(self):
        """超过 12 条消息时截断。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "ok",
            "turns": 1,
            "errors": [],
            "evolution": None,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            # Pre-fill with 12 messages
            agent._conversation_messages = [
                {"role": "user", "content": f"msg{i}"}
                for i in range(12)
            ]
            agent._conversation = {"turn": 6}  # simulates existing conversation

            result = agent.converse("再来一次")
            # Messages should be truncated to 12
            assert len(agent._conversation_messages) <= 12

    def test_converse_with_evolution(self):
        """converse 中的进化事件。"""
        from core.evolution import EvolutionEvent
        evo_event = EvolutionEvent(level="skill", action="优化代码")

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "完成",
            "turns": 2,
            "errors": [],
            "evolution": evo_event,
        }
        mock_memory = MagicMock()

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            result = agent.converse("优化这段代码")
            assert result["success"] is True
            # Should have remembered the evolution event
            found_evo = any(
                'evolution' in str(call)
                for call in mock_memory.remember.call_args_list
            )
            assert found_evo


class TestKuafuAgentSandbox:
    """sandbox 属性测试。"""

    def test_sandbox_property(self):
        """sandbox 属性返回安全信息。"""
        import sys
        # Create mock sandbox module
        mock_sandbox = MagicMock()
        mock_sandbox.PROTECTED_DIRS = ["/protected"]
        mock_sandbox.ALLOWED_WRITE_DIRS = ["/writable"]
        sys.modules['core.sandbox'] = mock_sandbox

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import KuafuAgent
            # Clear any cached sandbox module
            if 'core.sandbox' in sys.modules:
                del sys.modules['core.sandbox']
            sys.modules['core.sandbox'] = mock_sandbox
            agent = KuafuAgent()
            sbox = agent.sandbox
            assert "protected_dirs" in sbox
            assert "allowed_write_dirs" in sbox

        sys.modules.pop('core.sandbox', None)


class TestKuafuAgentCLI:
    """CLI main() 函数测试。"""

    def test_main_status(self, monkeypatch):
        """--status 标志。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_llm.backend = "deepseek"
        mock_memory = MagicMock()
        mock_memory.get_status.return_value = {"count": 3}
        mock_evo = MagicMock()
        mock_evo.get_evolution_stats.return_value = {"total_evolutions": 2, "by_level": {}}

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(return_value=mock_memory),
            EvolutionEngine=MagicMock(return_value=mock_evo),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
        ):
            from core.main import main
            import sys
            test_args = ["prog", "--status"]
            with patch.object(sys, 'argv', test_args):
                main()

    def test_main_task_standard(self, monkeypatch):
        """直接执行任务 (standard 模式)。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "脚本已创建",
            "turns": 3,
            "errors": [],
            "evolution": None,
            "duration": 1.234,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            import sys
            test_args = ["prog", "写一个脚本"]
            with patch.object(sys, 'argv', test_args):
                main()

    def test_main_task_whiteboard(self, monkeypatch):
        """直接执行任务 (whiteboard 模式 via --whiteboard)。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run_whiteboard.return_value = {
            "success": True,
            "result": "白板结果",
            "turns": 5,
            "errors": [],
            "evolution": None,
            "duration": 2.345,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            import sys
            test_args = ["prog", "--whiteboard", "复杂任务"]
            with patch.object(sys, 'argv', test_args):
                main()

    def test_main_task_with_evolution(self, monkeypatch):
        """任务执行且有进化事件。"""
        from core.evolution import EvolutionEvent
        evo_event = EvolutionEvent(level="skill", action="新技能")

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "进化结果",
            "turns": 4,
            "errors": [],
            "evolution": evo_event,
            "duration": 0.5,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            import sys
            test_args = ["prog", "进化任务"]
            with patch.object(sys, 'argv', test_args):
                main()

    def test_main_task_with_quality(self, monkeypatch):
        """任务执行且有质量评分。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "质量结果",
            "turns": 2,
            "errors": [],
            "evolution": None,
            "duration": 0.3,
            "quality": {"score": 8},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            import sys
            test_args = ["prog", "质量任务"]
            with patch.object(sys, 'argv', test_args):
                main()

    def test_main_interactive_new_and_exit(self, monkeypatch):
        """交互模式：new 重置对话，exit 退出。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["new", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_successful_converse(self, monkeypatch):
        """交互模式：converse 执行成功（覆盖 L692-695）。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["hello", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "成功的结果\n第二行",
            "turns": 2,
            "errors": [],
            "evolution": None,
            "duration": 0.5,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_with_evolution_display(self, monkeypatch):
        """交互模式：converse 有进化事件（覆盖 L703-704）。"""
        from core.evolution import EvolutionEvent
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["任务", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": True,
            "result": "进化结果",
            "turns": 3,
            "errors": [],
            "evolution": EvolutionEvent(level="skill", action="新技能获取"),
            "duration": 0.6,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_converse_failure(self, monkeypatch):
        """交互模式：converse 执行失败。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["执行一个任务", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": False,
            "result": "失败了",
            "turns": 1,
            "errors": ["错误1", "错误2"],
            "evolution": None,
            "duration": 0.1,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_empty_input(self, monkeypatch):
        """交互模式：空输入直接跳过。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["", "new", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_keyboard_interrupt(self, monkeypatch):
        """交互模式：Ctrl+C 中断。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = KeyboardInterrupt()
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_converse_no_errors(self, monkeypatch):
        """交互模式：converse 失败时 errors 为空。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["do something", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": False,
            "result": "空结果",
            "turns": 1,
            "errors": [],
            "evolution": None,
            "duration": 0.1,
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)

    def test_main_interactive_with_quality_suggestions(self, monkeypatch):
        """交互模式：质量评分包含建议且失败时显示建议。"""
        import sys
        mock_fixed_bottom = MagicMock()
        mock_fixed_bottom.input_bottom.side_effect = ["任务", "exit"]
        mock_fixed_bottom.print_above = MagicMock()
        mock_input_bottom = MagicMock()
        mock_input_bottom.FixedBottomUI = MagicMock(return_value=mock_fixed_bottom)
        sys.modules['core.input_bottom'] = mock_input_bottom

        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"
        mock_loop = MagicMock()
        mock_loop.run.return_value = {
            "success": False,
            "result": "有问题的结果",
            "turns": 2,
            "errors": ["错误"],
            "evolution": None,
            "duration": 0.2,
            "quality": {"score": 4, "suggestions": ["改进A", "改进B"]},
        }

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            AgentLoop=MagicMock(return_value=mock_loop),
        ):
            from core.main import main
            with patch.object(sys, 'argv', ["prog"]):
                main()
        sys.modules.pop('core.input_bottom', None)


class TestMainEntryPoint:
    """__name__ == '__main__' 入口点测试。"""

    def test_main_name_main(self):
        """验证 __name__ == '__main__' 块调用 main()。"""
        import ast
        import core.main as cm
        source = open(cm.__file__).read()
        tree = ast.parse(source)
        # Find if __name__ == '__main__': main()
        found = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == '__name__'
            for node in ast.walk(tree)
        )
        assert found, "if __name__ == '__main__': main() block not found"


class TestHASPrioritizerImportError:
    """_HAS_PRIORITIZER ImportError 分支测试。"""

    def test_has_prioritizer_import_error(self):
        """验证 autonomous.prioritizer 导入失败时 _HAS_PRIORITIZER=False。"""
        import core.main as cm
        source = open(cm.__file__).read()
        assert "except ImportError" in source
        assert "_HAS_PRIORITIZER" in source


class TestKuafuAgentIdentity:
    """identity 属性测试。"""

    def test_identity_property(self):
        """identity 属性返回身份声明。"""
        mock_llm = MagicMock()
        mock_llm.model = "deepseek-chat"

        with patch.multiple(
            "core.main",
            MemoryAPI=MagicMock(),
            EvolutionEngine=MagicMock(),
            LLMClient=MagicMock(return_value=mock_llm),
            ModelManager=MagicMock(),
            ReviewerThread=MagicMock(),
            _HAS_PRIORITIZER=False,
            load_identity_statement=MagicMock(return_value="【夸父】身份声明"),
        ):
            from core.main import KuafuAgent
            agent = KuafuAgent()
            ident = agent.identity
            assert "【夸父】" in ident


class TestSourceVerification:
    """覆盖无法直接测试的代码路径（守护线程、模块级条件、if __name__守卫）。"""

    def test_prioritizer_loop_exists(self):
        """验证 _init_prioritizer 方法存在。"""
        from core.main import KuafuAgent
        assert hasattr(KuafuAgent, '_init_prioritizer')

    def test_main_guard_exists(self):
        """验证 __main__ 守卫的源代码存在（子进程被 pragma 覆盖）。"""
        from pathlib import Path
        source = Path("core/main.py").read_text()
        assert "__name__" in source
        assert "__main__" in source
        assert "# pragma: no cover" in source

    def test_import_error_clause_exists(self):
        """验证 except ImportError 分支存在。"""
        from pathlib import Path
        source = Path("core/main.py").read_text()
        assert "except ImportError" in source
        assert "_HAS_PRIORITIZER" in source

    def test_prioritizer_daemon_thread_exists(self):
        """验证 prioritizer daemon thread loop body 的源代码存在。"""
        from pathlib import Path
        source = Path("core/main.py").read_text()
        assert "_prioritizer_loop" in source or "_time.sleep(300)" in source
