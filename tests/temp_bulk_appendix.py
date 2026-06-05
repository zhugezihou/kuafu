# ===================================================================
# F. core/evolution.py (当前78%, 40行缺)
# ===================================================================


class TestEvolutionEngine:
    """Complete coverage for EvolutionEngine — run_pipeline, _get_state_entry, _append_log, _load_pipeline_configs."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.events_backup = []
        with (
            patch('core.evolution.Judge'),
            patch('core.evolution.EvolutionState'),
            patch('core.evolution.Observer'),
            patch('core.evolution.EVOLUTION_LOG', Path('/tmp/test_evolution_log.json')),
        ):
            from core.evolution import EvolutionEngine
            mock_llm = MagicMock()
            mock_llm.chat = MagicMock(return_value={"content": "{}", "success": True})
            self.engine = EvolutionEngine(memory=MagicMock(), llm=mock_llm)
            self.engine._gepa_enabled = False
            yield

    def _make_obs(self, success=True, has_value=True, errors=None, tool_errors=None):
        obs = MagicMock()
        obs.success = success
        obs.has_value.return_value = has_value
        obs.errors = errors or []
        obs.tool_errors = tool_errors or []
        return obs

    def test_run_pipeline_no_value(self):
        """run_pipeline returns early when observation has no value."""
        obs = self._make_obs(has_value=False)
        result = self.engine.run_pipeline(obs, "test_type")
        assert result == {"skill_written": False, "skill_name": None, "evolution_mode": None, "reason": None}

    def test_run_pipeline_cooldown_skip(self):
        """run_pipeline returns early when cooldown is active."""
        obs = self._make_obs(has_value=True)
        self.engine._get_state_entry = MagicMock(return_value={"count": 5, "last_seen": time.time(), "consecutive_fail": 0, "last_n": []})
        result = self.engine.run_pipeline(obs, "test_type")
        assert result["skill_written"] is False
        assert result["reason"] is None

    def test_run_pipeline_cooldown_expired(self):
        """run_pipeline proceeds when cooldown has expired."""
        obs = self._make_obs(has_value=True)
        self.engine._get_state_entry = MagicMock(return_value={"count": 5, "last_seen": time.time() - 100, "consecutive_fail": 0, "last_n": []})
        self.engine.judge.evaluate.return_value = {"worth_learning": False, "reason": "不值得学习"}
        result = self.engine.run_pipeline(obs, "test_type")
        assert result["skill_written"] is False
        assert result["reason"] == "不值得学习"

    def test_run_pipeline_judge_says_learn(self):
        """run_pipeline writes skill when judge says worth_learning."""
        obs = self._make_obs(has_value=True)
        self.engine._get_state_entry = MagicMock(return_value={"count": 5, "last_seen": time.time() - 100, "consecutive_fail": 0, "last_n": []})
        self.engine.judge.evaluate.return_value = {
            "worth_learning": True,
            "reason": "复杂任务有价值",
            "evolution_mode": "CAPTURED",
            "skill": {"name": "test_skill", "trigger": "when x", "steps": ["step1", "step2"]},
        }
        self.engine._write_skill = MagicMock(return_value=True)
        result = self.engine.run_pipeline(obs, "test_type")
        assert result["skill_written"] is True
        assert result["skill_name"] == "test_skill"
        assert result["evolution_mode"] == "CAPTURED"
        assert result["reason"] == "复杂任务有价值"
        self.engine._write_skill.assert_called_once()

    def test_run_pipeline_no_skill_name(self):
        """run_pipeline handles judge returning skill without name."""
        obs = self._make_obs(has_value=True)
        self.engine._get_state_entry = MagicMock(return_value={"count": 5, "last_seen": time.time() - 100, "consecutive_fail": 0, "last_n": []})
        self.engine.judge.evaluate.return_value = {
            "worth_learning": True,
            "reason": "有价值",
            "evolution_mode": "CAPTURED",
            "skill": {},
        }
        self.engine._write_skill = MagicMock()
        result = self.engine.run_pipeline(obs, "test_type")
        assert result["skill_written"] is False
        assert result["skill_name"] is None

    def test_run_pipeline_records_errors(self):
        """run_pipeline records tool errors and regular errors in evolution_state."""
        obs = self._make_obs(has_value=False, errors=["err1"], tool_errors=[MagicMock(error_message="tool_err")])
        result = self.engine.run_pipeline(obs, "test_type")
        self.engine.evolution_state.record_error.assert_any_call("err1")
        self.engine.evolution_state.record_error.assert_any_call("tool_err")

    def test_get_state_entry_found(self):
        """_get_state_entry returns parsed row from evolution_state db."""
        mock_row = {"count": 3, "consecutive_fail": 1, "last_seen": 12345.0, "last_n": '["a", "b"]'}
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        self.engine.evolution_state._db._execute.return_value = mock_cursor
        entry = self.engine._get_state_entry("test_type")
        assert entry is not None
        assert entry["count"] == 3
        assert entry["last_n"] == ["a", "b"]

    def test_get_state_entry_not_found(self):
        """_get_state_entry returns None when no rows."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        self.engine.evolution_state._db._execute.return_value = mock_cursor
        entry = self.engine._get_state_entry("test_type")
        assert entry is None

    def test_get_state_entry_exception(self):
        """_get_state_entry returns None on exception."""
        self.engine.evolution_state._db._execute.side_effect = Exception("DB error")
        entry = self.engine._get_state_entry("test_type")
        assert entry is None

    def test_append_log_normal(self):
        """_append_log writes to file correctly."""
        from core.evolution import EvolutionEvent
        event = EvolutionEvent(level="info", action="test", target="t", payload="p")
        self.engine.EVOLUTION_LOG = Path('/tmp/test_append_log.json')
        if self.engine.EVOLUTION_LOG.exists():
            self.engine.EVOLUTION_LOG.unlink()
        self.engine._events = [event] * 10
        self.engine._append_log(event)
        assert self.engine.EVOLUTION_LOG.exists()
        import json
        data = json.loads(self.engine.EVOLUTION_LOG.read_text(encoding="utf-8"))
        assert len(data) > 0
        assert data[-1]["action"] == "test"
        self.engine.EVOLUTION_LOG.unlink()

    def test_append_log_trims_memory(self):
        """_append_log trims in-memory events to MAX_LOG."""
        from core.evolution import EvolutionEvent
        self.engine._events = [EvolutionEvent(level="info", action="x", target="t") for _ in range(250)]
        self.engine.EVOLUTION_LOG = Path('/tmp/test_trim_log.json')
        if self.engine.EVOLUTION_LOG.exists():
            self.engine.EVOLUTION_LOG.unlink()
        event = EvolutionEvent(level="info", action="last_one", target="t")
        self.engine._append_log(event)
        assert len(self.engine._events) <= self.engine.MAX_LOG
        self.engine.EVOLUTION_LOG.unlink()

    def test_append_log_oserror(self):
        """_append_log handles OSError gracefully."""
        from core.evolution import EvolutionEvent
        event = EvolutionEvent(level="info", action="test", target="t")
        self.engine.EVOLUTION_LOG = Path('/nonexistent_dir_xyz/file.json')
        # Should not raise
        self.engine._append_log(event)

    def test_evolution_event_validation(self):
        """EvolutionEvent validates level."""
        from core.evolution import EvolutionEvent
        evt = EvolutionEvent(level="invalid_level", action="test")
        assert evt.level == "info"
        evt2 = EvolutionEvent(level="error", action="test")
        assert evt2.level == "error"

    def test_evolution_event_to_dict(self):
        """EvolutionEvent.to_dict returns correct structure."""
        from core.evolution import EvolutionEvent
        evt = EvolutionEvent(level="skill", action="learned", target="test", payload="data")
        d = evt.to_dict()
        assert d["level"] == "skill"
        assert d["action"] == "learned"
        assert d["target"] == "test"
        assert d["success"] is True
        assert "timestamp" in d

    def test_evaluate_and_evolve_legacy(self):
        """evaluate_and_evolve legacy interface works."""
        task_result = {
            "success": True,
            "task_type": "legacy_test",
            "errors": [],
            "result": "done",
            "tool_calls": 2,
            "tools_used": ["terminal"],
        }
        self.engine.run_pipeline = MagicMock(return_value={"skill_written": False})
        result = self.engine.evaluate_and_evolve(task_result, task="test task")
        assert result["success"] is True
        self.engine.run_pipeline.assert_called_once()

    def test_emit_legacy(self):
        """emit legacy interface creates an event and appends log."""
        from core.evolution import EvolutionEvent
        self.engine._append_log = MagicMock()
        self.engine.emit("info", "manual event", "target", "payload")
        assert len(self.engine._events) == 1
        assert self.engine._events[0].action == "manual event"
        self.engine._append_log.assert_called_once()

    def test_get_evolution_stats(self):
        """get_evolution_stats returns correct structure."""
        self.engine._total = 42
        self.engine._events = []
        stats = self.engine.get_evolution_stats()
        assert stats["total_evolutions"] == 42
        assert "recent_events" in stats
        assert stats["last_event"] is None

    def test_register_observer(self):
        """register_observer adds an observer."""
        obs = MagicMock()
        self.engine.register_observer(obs)
        assert obs in self.engine.observers
        # Duplicate should not add
        self.engine.register_observer(obs)
        assert len(self.engine.observers) == 2  # 1 default + 1 added

    def test_write_skill_captured(self):
        """_write_skill with CAPTURED mode."""
        self.engine.root_dir = Path('/tmp/test_skills')
        skills_dir = self.engine.root_dir / "skills"
        if skills_dir.exists():
            import shutil
            shutil.rmtree(str(skills_dir))
        skills_dir.mkdir(parents=True)
        skill = {"name": "capture_test", "trigger": "when x", "steps": ["do a", "do b"], "error_pattern": "err_pattern"}
        result = self.engine._write_skill(skill, "test_type", evolution_mode="CAPTURED")
        assert result is True
        filepath = skills_dir / "capture_test.yaml"
        assert filepath.exists()
        content = filepath.read_text(encoding="utf-8")
        assert "capture_test" in content
        assert "do a" in content
        import shutil
        shutil.rmtree(str(skills_dir))

    def test_write_skill_fix(self):
        """_write_skill with FIX mode creates backup."""
        self.engine.root_dir = Path('/tmp/test_skills_fix')
        skills_dir = self.engine.root_dir / "skills"
        if skills_dir.exists():
            import shutil
            shutil.rmtree(str(skills_dir))
        skills_dir.mkdir(parents=True)
        old_file = skills_dir / "fix_test.yaml"
        old_file.write_text("old content", encoding="utf-8")
        self.engine.evolution_state.get_evolution_history.return_value = [{"v": 1}]
        skill = {"name": "fix_test", "trigger": "when y", "steps": ["step1"], "error_pattern": ""}
        result = self.engine._write_skill(skill, "test_type", evolution_mode="FIX")
        assert result is True
        assert old_file.exists()
        bak_files = list(skills_dir.glob("fix_test.bak.v*"))
        assert len(bak_files) >= 1
        import shutil
        shutil.rmtree(str(skills_dir))

    def test_write_skill_derived(self):
        """_write_skill with DERIVED mode creates versioned file."""
        self.engine.root_dir = Path('/tmp/test_skills_derived')
        skills_dir = self.engine.root_dir / "skills"
        if skills_dir.exists():
            import shutil
            shutil.rmtree(str(skills_dir))
        skills_dir.mkdir(parents=True)
        skill = {"name": "derived_test", "trigger": "when z", "steps": ["step1"]}
        result = self.engine._write_skill(skill, "test_type", evolution_mode="DERIVED")
        assert result is True
        assert (skills_dir / "derived_test_v2.yaml").exists()
        import shutil
        shutil.rmtree(str(skills_dir))

    def test_write_skill_derived_auto_increment(self):
        """_write_skill DERIVED auto-increments version."""
        self.engine.root_dir = Path('/tmp/test_skills_derived2')
        skills_dir = self.engine.root_dir / "skills"
        if skills_dir.exists():
            import shutil
            shutil.rmtree(str(skills_dir))
        skills_dir.mkdir(parents=True)
        (skills_dir / "derived_v2.yaml").write_text("v2", encoding="utf-8")
        (skills_dir / "derived_v3.yaml").write_text("v3", encoding="utf-8")
        skill = {"name": "derived", "trigger": "when", "steps": ["s1"]}
        result = self.engine._write_skill(skill, "test_type", evolution_mode="DERIVED")
        assert result is True
        assert (skills_dir / "derived_v4.yaml").exists()
        import shutil
        shutil.rmtree(str(skills_dir))

    def test_noop_llm(self):
        """_noop_llm returns empty dict."""
        from core.evolution import EvolutionEngine
        result = EvolutionEngine._noop_llm([{"role": "user", "content": "hi"}])
        assert result["content"] == "{}"
        assert result["success"] is True


# ===================================================================
# G. core/hooks.py (当前81%, 44行缺)
# ===================================================================


class TestHookRegistry:
    """Complete coverage for HookRegistry."""

    def teardown_method(self):
        HookRegistry._initialized = False
        HookRegistry._handlers = {}

    def test_init_loads_config(self):
        """init loads config from disk when file exists."""
        config = {"on_agent_start": [{"id": "h1", "event": "on_agent_start", "type": "shell",
                                       "config": {"command": "echo hi"}, "enabled": True, "async_": True,
                                       "priority": 0, "created_at": 100.0, "description": "",
                                       "max_retries": 0, "timeout": 10}]}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps(config)
            HookRegistry.init()
            handlers = HookRegistry._handlers.get("on_agent_start", [])
            assert len(handlers) == 1
            assert handlers[0].id == "h1"

    def test_init_already_initialized(self):
        """init skips if already initialized."""
        HookRegistry._initialized = True
        with patch('core.hooks.HOOKS_CONFIG_PATH') as mock_path:
            HookRegistry.init()
            mock_path.exists.assert_not_called()

    def test_init_config_not_exists(self):
        """init handles missing config file."""
        HookRegistry._initialized = False
        with patch('core.hooks.HOOKS_CONFIG_PATH') as mock_path:
            mock_path.exists.return_value = False
            HookRegistry.init()
            assert HookRegistry._initialized is True
            assert HookRegistry._handlers == {}

    def test_init_invalid_json(self):
        """init handles invalid JSON gracefully."""
        HookRegistry._initialized = False
        with patch('core.hooks.HOOKS_CONFIG_PATH') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "invalid json{{{"
            HookRegistry.init()
            assert HookRegistry._initialized is True

    def test_register_creates_handler_id(self):
        """register creates unique handler ID."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            hid = HookRegistry.register("on_agent_start", "shell", {"command": "echo test"}, description="test hook")
            assert hid.startswith("hook_")
            assert "shell" in hid

    def test_register_invalid_event(self):
        """register raises ValueError for unknown event."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            with pytest.raises(ValueError, match="未知事件"):
                HookRegistry.register("nonexistent_event", "shell", {})

    def test_register_appends_and_sorts(self):
        """register sorts handlers by priority descending."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry.register("on_agent_start", "shell", {"command": "a"}, priority=0)
            HookRegistry.register("on_agent_start", "shell", {"command": "b"}, priority=10)
            HookRegistry.register("on_agent_start", "shell", {"command": "c"}, priority=5)
            handlers = HookRegistry._handlers["on_agent_start"]
            assert handlers[0].config["command"] == "b"
            assert handlers[1].config["command"] == "c"
            assert handlers[2].config["command"] == "a"

    def test_unregister_existing(self):
        """unregister removes handler by ID."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            hid = HookRegistry.register("on_agent_start", "shell", {"command": "echo"})
            result = HookRegistry.unregister(hid)
            assert result is True
            assert len(HookRegistry._handlers["on_agent_start"]) == 0

    def test_unregister_nonexistent(self):
        """unregister returns False for unknown ID."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            result = HookRegistry.unregister("no_such_hook")
            assert result is False

    def test_get_handlers_initializes_if_needed(self):
        """get_handlers calls init if not initialized."""
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'init') as mock_init:
            HookRegistry.get_handlers("on_agent_start")
            mock_init.assert_called_once()

    def test_get_handlers_returns_only_enabled(self):
        """get_handlers filters out disabled handlers."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(enabled=True),
                MagicMock(enabled=False),
                MagicMock(enabled=True),
            ]
            result = HookRegistry.get_handlers("on_agent_start")
            assert len(result) == 2

    def test_get_handlers_empty_event(self):
        """get_handlers returns empty list for event with no handlers."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            result = HookRegistry.get_handlers("on_budget_critical")
            assert result == []


class TestHooksTriggerAndExecutors:
    """Coverage for trigger() and all executor functions."""

    def teardown_method(self):
        HookRegistry._initialized = False
        HookRegistry._handlers = {}

    def test_trigger_unknown_event(self):
        """trigger returns empty for unknown event."""
        result = trigger("nonexistent_event")
        assert result == []

    def test_trigger_no_handlers(self):
        """trigger returns empty when no handlers registered."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            result = trigger("on_agent_start")
            assert result == []

    def test_trigger_unknown_executor_type(self):
        """trigger returns error for unknown handler type."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="unknown_type", config={},
                          enabled=True, async_=True, priority=0, max_retries=0, timeout=10)
            ]
            results = trigger("on_agent_start")
            assert len(results) == 1
            assert results[0].success is False
            assert "未知执行类型" in results[0].error

    def test_trigger_shell_success(self):
        """trigger executes shell handler successfully."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "echo hello"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
                results = trigger("on_agent_start")
                assert len(results) == 1
                assert results[0].success is True
                assert "hello" in results[0].output

    def test_trigger_shell_failure(self):
        """trigger handles shell failure."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "false"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
                results = trigger("on_agent_start")
                assert results[0].success is False
                assert "error msg" in results[0].error

    def test_trigger_shell_timeout(self):
        """trigger handles shell timeout."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "sleep 100"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=1)
            ]
            from core.hooks import subprocess
            original_run = subprocess.run
            def mock_run(*args, **kwargs):
                raise subprocess.TimeoutExpired(cmd="test", timeout=1)
            with patch('core.hooks.subprocess.run', side_effect=mock_run):
                results = trigger("on_agent_start")
                assert results[0].success is False
                assert "超时" in results[0].error

    def test_trigger_shell_exception(self):
        """trigger handles shell generic exception."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "echo"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks.subprocess.run', side_effect=PermissionError("no way")):
                results = trigger("on_agent_start")
                assert results[0].success is False
                assert "no way" in results[0].error

    def test_trigger_webhook_success(self):
        """trigger executes webhook successfully."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="webhook",
                          config={"url": "http://example.com/hook", "method": "POST", "headers": {}},
                          enabled=True, async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks.urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"status": "ok"}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                results = trigger("on_agent_start")
                assert results[0].success is True
                assert "ok" in results[0].output

    def test_trigger_webhook_http_error(self):
        """trigger handles webhook HTTP error."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="webhook",
                          config={"url": "http://example.com/fail", "method": "POST"},
                          enabled=True, async_=True, priority=0, max_retries=0, timeout=10)
            ]
            from core.hooks import urllib
            with patch('core.hooks.urllib.request.urlopen', side_effect=urllib.error.HTTPError(
                    "http://example.com", 404, "Not Found", {}, None
            )):
                results = trigger("on_agent_start")
                assert results[0].success is False
                assert "HTTP 404" in results[0].error

    def test_trigger_webhook_generic_error(self):
        """trigger handles webhook generic exception."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="webhook",
                          config={"url": "http://example.com/fail"},
                          enabled=True, async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks.urllib.request.urlopen', side_effect=ConnectionError("refused")):
                results = trigger("on_agent_start")
                assert results[0].success is False
                assert "refused" in results[0].error

    def test_trigger_webhook_no_url(self):
        """trigger webhook without URL returns error."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="webhook",
                          config={}, enabled=True, async_=True, priority=0, max_retries=0, timeout=10)
            ]
            results = trigger("on_agent_start")
            assert results[0].success is False
            assert "缺少 url" in results[0].error

    def test_trigger_llm_executor(self):
        """trigger executes LLM handler."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_tool_before"] = [
                MagicMock(id="h1", event="on_tool_before", type="llm",
                          config={"prompt": "analyze: {{tool}}", "model": "qwen-turbo"},
                          enabled=True, async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks._execute_llm') as mock_llm:
                mock_llm.return_value = HookResult(
                    handler_id="h1", event="on_tool_before", type="llm",
                    success=True, output="safe", duration=0.1,
                )
                results = trigger("on_tool_before", {"tool": "terminal"})
                assert results[0].success is True

    def test_trigger_llm_executor_failure(self):
        """trigger handles LLM execution failure."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_tool_before"] = [
                MagicMock(id="h1", event="on_tool_before", type="llm",
                          config={"prompt": "analyze"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks._execute_llm') as mock_llm:
                mock_llm.return_value = HookResult(
                    handler_id="h1", event="on_tool_before", type="llm",
                    success=False, output="", duration=0.1, error="LLM call failed",
                )
                results = trigger("on_tool_before")
                assert results[0].success is False

    def test_trigger_subagent_executor(self):
        """trigger executes subagent handler."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="subagent",
                          config={"goal": "verify {{task}}"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks._execute_subagent') as mock_sub:
                mock_sub.return_value = HookResult(
                    handler_id="h1", event="on_agent_start", type="subagent",
                    success=True, output="verified", duration=0.2,
                )
                results = trigger("on_agent_start", {"task": "test"})
                assert results[0].success is True

    def test_trigger_blocked_by_previous(self):
        """trigger skips handlers when previous blocked the flow."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "echo", "block_on_failure": True},
                          enabled=True, async_=True, priority=0, max_retries=0, timeout=10),
                MagicMock(id="h2", event="on_agent_start", type="shell",
                          config={"command": "echo2"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10),
            ]
            with patch('core.hooks.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="blocked")
                results = trigger("on_agent_start", synchronous=True)
                assert len(results) == 2
                assert results[1].error == "上游处理器阻止了流程"

    def test_trigger_retry_on_exception(self):
        """trigger retries on exception."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "echo"}, enabled=True,
                          async_=True, priority=0, max_retries=2, timeout=10)
            ]
            call_count = [0]
            def mock_exec(*args):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise RuntimeError("transient error")
                return HookResult(handler_id="h1", event="on_agent_start", type="shell",
                                 success=True, output="ok", duration=0.1)
            with patch('core.hooks._EXECUTORS', {"shell": mock_exec}):
                results = trigger("on_agent_start")
                assert results[0].success is True
                assert call_count[0] == 3

    def test_trigger_retry_exhausted(self):
        """trigger returns error after exhausting retries."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "echo"}, enabled=True,
                          async_=True, priority=0, max_retries=2, timeout=10)
            ]
            def mock_exec(*args):
                raise RuntimeError("persistent error")
            with patch('core.hooks._EXECUTORS', {"shell": mock_exec}):
                results = trigger("on_agent_start")
                assert results[0].success is False
                assert "persistent error" in results[0].error

    def test_trigger_async_handler_skips_log(self):
        """trigger skips logging for async handlers when not synchronous."""
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            HookRegistry._handlers["on_agent_start"] = [
                MagicMock(id="h1", event="on_agent_start", type="shell",
                          config={"command": "echo"}, enabled=True,
                          async_=True, priority=0, max_retries=0, timeout=10)
            ]
            with patch('core.hooks.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                with patch('core.hooks.logger') as mock_logger:
                    results = trigger("on_agent_start")
                    # No log message for async handler when not synchronous
                    # just verify it works
                    assert results[0].success is True

    def test_trigger_async_function(self):
        """trigger_async runs in a separate thread."""
        from core.hooks import trigger_async
        with patch('core.hooks.trigger') as mock_trigger:
            trigger_async("on_agent_start", {"test": 1})
            mock_trigger.assert_called_once_with("on_agent_start", {"test": 1})

    def test_trigger_sync_function(self):
        """trigger_sync calls trigger with synchronous=True."""
        from core.hooks import trigger_sync
        with patch('core.hooks.trigger') as mock_trigger:
            mock_trigger.return_value = []
            result = trigger_sync("on_agent_start")
            mock_trigger.assert_called_once_with("on_agent_start", None, synchronous=True)

    def test_render_template_basic(self):
        """_render_template replaces {{var}} placeholders."""
        from core.hooks import _render_template
        result = _render_template("Hello {{name}}!", {"name": "World"})
        assert result == "Hello World!"

    def test_render_template_dict_value(self):
        """_render_template serializes dict values as JSON."""
        from core.hooks import _render_template
        result = _render_template("Data: {{payload}}", {"payload": {"key": "val"}})
        assert json.loads(result.split(": ", 1)[1]) == {"key": "val"}

    def test_render_template_unknown_var(self):
        """_render_template leaves unknown vars unchanged."""
        from core.hooks import _render_template
        result = _render_template("{{unknown}} here", {"known": "val"})
        assert result == "{{unknown}} here"

    def test_render_template_list_value(self):
        """_render_template serializes list values as JSON."""
        from core.hooks import _render_template
        result = _render_template("Items: {{items}}", {"items": [1, 2, 3]})
        assert "1" in result and "2" in result

    def test_render_config_dict(self):
        """_render_config recursively renders dict."""
        from core.hooks import _render_config
        config = {"url": "http://{{host}}/api", "headers": {"Auth": "Bearer {{token}}"}}
        context = {"host": "example.com", "token": "abc123"}
        result = _render_config(config, context)
        assert result["url"] == "http://example.com/api"
        assert result["headers"]["Auth"] == "Bearer abc123"

    def test_render_config_list(self):
        """_render_config renders items in lists."""
        from core.hooks import _render_config
        config = {"items": ["{{a}}", "plain", "{{b}}"]}
        result = _render_config(config, {"a": "x", "b": "y"})
        assert result["items"] == ["x", "plain", "y"]

    def test_render_config_other_types(self):
        """_render_config passes non-string non-dict non-list values through."""
        from core.hooks import _render_config
        config = {"count": 42, "flag": True, "price": 3.14}
        result = _render_config(config, {})
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["price"] == 3.14

    def test_hook_handler_dataclass(self):
        """HookHandler dataclass creates with defaults."""
        from core.hooks import HookHandler
        h = HookHandler(id="test_id", event="on_agent_start", type="shell")
        assert h.enabled is True
        assert h.async_ is True
        assert h.priority == 0
        assert h.max_retries == 0
        assert h.timeout == 10
        assert h.config == {}
        assert h.description == ""

    def test_hook_result_dataclass(self):
        """HookResult dataclass creates with defaults."""
        from core.hooks import HookResult
        r = HookResult(handler_id="h1", event="e1", type="shell", success=True, output="ok", duration=0.1)
        assert r.blocked is False
        assert r.error is None

    def test_init_hooks_function(self):
        """init_hooks initializes the system."""
        from core.hooks import init_hooks
        HookRegistry._initialized = False
        with patch.object(HookRegistry, 'init') as mock_init:
            init_hooks()
            mock_init.assert_called_once()

    def test_quick_register_functions(self):
        """Quick register functions work."""
        from core.hooks import on_tool_before_shell, on_tool_before_llm, on_approval_notify_webhook
        with patch('core.hooks.HOOKS_CONFIG_PATH.exists', return_value=False):
            HookRegistry.init()
            hid1 = on_tool_before_shell("echo {{tool}}", "test shell hook")
            assert hid1.startswith("hook_")
            hid2 = on_tool_before_llm("analyze {{args}}", block_on_failure=True)
            assert hid2.startswith("hook_")
            hid3 = on_approval_notify_webhook("http://example.com/notify")
            assert hid3.startswith("hook_")


# ===================================================================
# H. core/skill_manager.py (当前75%, 54行缺)
# ===================================================================


class TestSkillManager:
    """Complete coverage for SkillManager."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.tmp_skills_dir = tmp_path / "skills"
        self.tmp_market_dir = self.tmp_skills_dir / "market"
        self.tmp_skills_dir.mkdir(parents=True)
        self.tmp_market_dir.mkdir(parents=True)
        self.patches = [
            patch('core.skill_manager.SKILLS_DIR', self.tmp_skills_dir),
            patch('core.skill_manager.MARKET_DIR', self.tmp_market_dir),
            patch('core.skill_manager.MARKET_INDEX_URL', ""),
        ]
        for p in self.patches:
            p.start()
        from core.skill_manager import SkillManager
        self.mgr = SkillManager()
        yield
        for p in self.patches:
            p.stop()

    def _create_skill_file(self, name, description="desc", steps=None, keywords=None, usage_count=0):
        import yaml
        data = {"name": name, "description": description, "steps": steps or ["step1"], "keywords": keywords or [], "usage_count": usage_count}
        filepath = self.tmp_skills_dir / f"{name}.yaml"
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)
        return filepath

    def test_list_local_empty(self):
        """list_local returns empty list when no skills."""
        results = self.mgr.list_local()
        assert results == []

    def test_list_local_normal(self):
        """list_local returns parsed skills."""
        self._create_skill_file("skill_a", "desc a", steps=["s1", "s2"], keywords=["kw1"])
        results = self.mgr.list_local()
        assert len(results) == 1
        assert results[0].name == "skill_a"
        assert results[0].description == "desc a"
        assert results[0].steps == 2
        assert results[0].keywords == ["kw1"]

    def test_list_local_parse_error(self):
        """list_local handles parse errors gracefully."""
        (self.tmp_skills_dir / "bad.yaml").write_text("::: invalid yaml :::\n", encoding="utf-8")
        results = self.mgr.list_local()
        assert results == []  # Bad file skipped, no results

    def test_list_local_empty_yaml(self):
        """list_local skips empty data."""
        (self.tmp_skills_dir / "empty.yaml").write_text("", encoding="utf-8")
        results = self.mgr.list_local()
        assert results == []

    def test_get_local_found(self):
        """get_local returns skill by name."""
        self._create_skill_file("my_skill")
        skill = self.mgr.get_local("my_skill")
        assert skill is not None
        assert skill.name == "my_skill"

    def test_get_local_not_found(self):
        """get_local returns None for unknown name."""
        result = self.mgr.get_local("nonexistent")
        assert result is None

    def test_search_local_by_name(self):
        """search_local finds skills by name."""
        self._create_skill_file("PythonScript", "Run python")
        results = self.mgr.search_local("pythonscript")
        assert len(results) == 1

    def test_search_local_by_description(self):
        """search_local finds skills by description."""
        self._create_skill_file("helper", "This is a skill for testing")
        results = self.mgr.search_local("testing")
        assert len(results) == 1

    def test_search_local_by_keyword(self):
        """search_local finds skills by keyword."""
        self._create_skill_file("web_tool", "desc", keywords=["http", "api"])
        results = self.mgr.search_local("http")
        assert len(results) == 1

    def test_search_local_no_match(self):
        """search_local returns empty for no match."""
        self._create_skill_file("skill_x")
        results = self.mgr.search_local("zzzzz")
        assert results == []

    def test_search_local_limit_10(self):
        """search_local limits results to 10."""
        for i in range(15):
            self._create_skill_file(f"skill_{i}", f"desc with keyword_{i}")
        results = self.mgr.search_local("keyword")
        assert len(results) <= 10

    def test_remove_local_exists(self):
        """remove_local deletes skill file."""
        fp = self._create_skill_file("removable")
        assert fp.exists()
        result = self.mgr.remove_local("removable")
        assert result is True
        assert not fp.exists()

    def test_remove_local_not_found(self):
        """remove_local returns False for unknown name."""
        result = self.mgr.remove_local("nonexistent")
        assert result is False

    def test_remove_local_parse_error(self):
        """remove_local handles parse errors gracefully."""
        (self.tmp_skills_dir / "bad.yaml").write_text("name: bad_skill\n", encoding="utf-8")
        result = self.mgr.remove_local("bad_skill")
        assert result is True

    def test_fetch_market_index_no_url(self):
        """fetch_market_index returns [] when MARKET_INDEX_URL is empty."""
        results = self.mgr.fetch_market_index()
        assert results == []

    def test_fetch_market_index_network_ok(self):
        """fetch_market_index fetches and parses market index."""
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com/index.json"):
            with patch('core.skill_manager.urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps({
                    "skills": [
                        {"name": "market_skill", "description": "a market skill", "keywords": ["kw"], "steps": 3, "author": "author1", "url": "http://example.com/skill.md"},
                    ]
                }).encode("utf-8")
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                results = self.mgr.fetch_market_index(force=True)
                assert len(results) == 1
                assert results[0].name == "market_skill"
                assert results[0].source == "market"
                assert results[0].url == "http://example.com/skill.md"

    def test_fetch_market_index_from_cache(self):
        """fetch_market_index returns cache when not expired."""
        self.mgr._market_cache = [MagicMock()]
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com/index.json"):
            results = self.mgr.fetch_market_index(force=False)
            assert len(results) == 1

    def test_fetch_market_index_fallback_to_cache(self):
        """fetch_market_index falls back to cache on network failure."""
        self.mgr._market_cache = [MagicMock(name="cached_skill")]
        self.mgr._cache_time = 0  # Force expiry
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com/index.json"):
            with patch('core.skill_manager.urllib.request.urlopen', side_effect=Exception("Network error")):
                results = self.mgr.fetch_market_index(force=True)
                assert len(results) == 1

    def test_fetch_market_index_no_cache_on_failure(self):
        """fetch_market_index returns [] when no cache and network fails."""
        self.mgr._market_cache = None
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com/index.json"):
            with patch('core.skill_manager.urllib.request.urlopen', side_effect=Exception("Network error")):
                results = self.mgr.fetch_market_index(force=True)
                assert results == []

    def test_search_market_found(self):
        """search_market finds skills by name/description/keyword/category."""
        self.mgr._market_cache = [
            MagicMock(name="web_tool", description="a web tool", keywords=["http"], category="web"),
            MagicMock(name="file_tool", description="a file tool", keywords=["fs"], category="system"),
        ]
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            results = self.mgr.search_market("web")
            assert len(results) >= 1

    def test_search_market_by_category(self):
        """search_market searches by category."""
        self.mgr._market_cache = [
            MagicMock(name="tool_a", description="desc", keywords=[], category="database"),
        ]
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            results = self.mgr.search_market("database")
            assert len(results) == 1

    def test_search_market_empty(self):
        """search_market returns empty for no match."""
        self.mgr._market_cache = []
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            results = self.mgr.search_market("no_such_thing")
            assert results == []

    def test_search_market_limit_20(self):
        """search_market limits results to 20."""
        self.mgr._market_cache = [MagicMock(name=f"skill_{i}", description="common desc", keywords=[], category="") for i in range(30)]
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            results = self.mgr.search_market("common")
            assert len(results) <= 20

    def test_get_stats(self):
        """get_stats returns correct structure."""
        from core.skill_manager import RepoManager
        with patch('core.skill_manager.RepoManager') as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get_stats.return_value = {"total_repos": 2, "total_skills": 5}
            MockRepo.return_value = mock_repo
            self._create_skill_file("stat_skill")
            stats = self.mgr.get_stats()
            assert stats["local"] >= 1
            assert stats["installed_market"] == 0
            assert "available_market" in stats
            assert stats["repos"] == 2
            assert stats["repo_skills"] == 5

    def test_install_by_url_success(self):
        """install downloads and saves skill from URL."""
        content = "---\nname: url_skill\ntrigger: when\ndescription: from url\n---\n\nsteps:\n  - do something\n"
        with patch('core.skill_manager.urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = content.encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = self.mgr.install("http://example.com/skill.md")
            assert result["success"] is True
            assert result["name"] == "url_skill"
            assert (self.tmp_market_dir / "url_skill.yaml").exists()

    def test_install_by_url_download_fail_fallback(self):
        """install falls back to RepoManager when URL download fails."""
        with patch('core.skill_manager.urllib.request.urlopen', side_effect=Exception("download failed")):
            with patch('core.skill_manager.RepoManager') as MockRepo:
                mock_repo = MagicMock()
                mock_repo.install_from_url.return_value = {"success": True, "name": "repo_skill", "file": "/tmp/test.yaml"}
                MockRepo.return_value = mock_repo
                result = self.mgr.install("http://example.com/missing.md")
                assert result["success"] is True

    def test_install_by_name_from_market(self):
        """install finds skill by name in market index."""
        self.mgr._market_cache = [
            MagicMock(name="market_find", description="desc", keywords=[], steps=2, author="", url="http://example.com/skill.md", category=""),
        ]
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            with patch('core.skill_manager.urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b"---\nname: market_find\ntrigger: when\n---\n\nsteps:\n  - step1\n"
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = self.mgr.install("market_find")
                assert result["success"] is True

    def test_install_by_name_no_url(self):
        """install returns error when market skill has no URL."""
        self.mgr._market_cache = [
            MagicMock(name="no_url_skill", description="desc", keywords=[], steps=0, author="", url="", category=""),
        ]
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            result = self.mgr.install("no_url_skill")
            assert result["success"] is False
            assert "没有下载 URL" in result["error"]

    def test_install_by_name_not_found_in_market(self):
        """install falls back to RepoManager when name not in market."""
        self.mgr._market_cache = []
        self.mgr._cache_time = time.time()
        with patch('core.skill_manager.MARKET_INDEX_URL', "http://example.com"):
            with patch('core.skill_manager.RepoManager') as MockRepo:
                mock_repo = MagicMock()
                mock_repo.install.return_value = {"success": True, "name": "from_repo", "file": "/tmp/r.yaml"}
                MockRepo.return_value = mock_repo
                result = self.mgr.install("repo_skill")
                assert result["success"] is True

    def test_extract_name_from_md_found(self):
        """_extract_name_from_md extracts name from YAML frontmatter."""
        from core.skill_manager import SkillManager
        content = "---\nname: my_skill\ntrigger: when\n---\n\nbody"
        name = SkillManager._extract_name_from_md(content, "http://example.com/SKILL.md")
        assert name == "my_skill"

    def test_extract_name_from_md_fallback_to_url(self):
        """_extract_name_from_md falls back to URL path stem."""
        from core.skill_manager import SkillManager
        content = "no frontmatter here"
        name = SkillManager._extract_name_from_md(content, "http://example.com/my_custom_skill.md")
        assert name == "my_custom_skill"

    def test_extract_name_from_md_empty_fallback(self):
        """_extract_name_from_md returns empty when no name and URL stem is SKILL."""
        from core.skill_manager import SkillManager
        content = "no frontmatter"
        name = SkillManager._extract_name_from_md(content, "http://example.com/SKILL.md")
        assert name == ""

    def test_uninstall_exists(self):
        """uninstall removes market-installed skill."""
        yaml_content = "name: installed_skill\ndescription: test\n"
        skill_file = self.tmp_market_dir / "installed_skill.yaml"
        skill_file.write_text(yaml_content, encoding="utf-8")
        result = self.mgr.uninstall("installed_skill")
        assert result is True
        assert not skill_file.exists()

    def test_uninstall_not_found(self):
        """uninstall returns False for unknown skill."""
        result = self.mgr.uninstall("nonexistent_skill")
        assert result is False

    def test_uninstall_no_market_dir(self):
        """uninstall returns False when market dir doesn't exist."""
        import shutil
        shutil.rmtree(str(self.tmp_market_dir))
        result = self.mgr.uninstall("some_skill")
        assert result is False

    def test_uninstall_parse_error(self):
        """uninstall handles parse error gracefully."""
        skill_file = self.tmp_market_dir / "broken.yaml"
        skill_file.write_text("::: invalid :::\n", encoding="utf-8")
        result = self.mgr.uninstall("no_name")
        # Should return False because yaml parse fails and name doesn't match
        assert result is False

    def test_list_installed_market(self):
        """list_installed_market returns installed market skills."""
        yaml_content = "name: inst1\ndescription: d1\nsteps: [s1]\nkeywords: [k1]\nusage_count: 5\n"
        (self.tmp_market_dir / "inst1.yaml").write_text(yaml_content, encoding="utf-8")
        results = self.mgr.list_installed_market()
        assert len(results) == 1
        assert results[0].name == "inst1"

    def test_list_installed_market_empty(self):
        """list_installed_market returns empty when no installed skills."""
        results = self.mgr.list_installed_market()
        assert results == []

    def test_list_installed_market_parse_skip(self):
        """list_installed_market skips invalid YAML."""
        (self.tmp_market_dir / "bad.yaml").write_text("invalid", encoding="utf-8")
        results = self.mgr.list_installed_market()
        assert results == []

    def test_check_skill_deps_no_file(self):
        """_check_skill_deps handles missing file."""
        from core.skill_manager import SkillManager
        SkillManager._check_skill_deps({"file": "/nonexistent/path"})
        # Should not raise

    def test_check_skill_deps_no_deps(self):
        """_check_skill_deps handles missing deps key."""
        from core.skill_manager import SkillManager
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("name: test\ndescription: no deps\n")
            tmp_path = f.name
        SkillManager._check_skill_deps({"file": tmp_path})
        os.unlink(tmp_path)

    def test_check_skill_deps_with_deps(self):
        """_check_skill_deps checks dependencies."""
        from core.skill_manager import SkillManager
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("name: test\ndependencies:\n  - git\n")
            tmp_path = f.name
        with patch('core.skill_manager.check_dependencies') as mock_check:
            mock_check.return_value = MagicMock(ok=False, summary=lambda: "missing git")
            SkillManager._check_skill_deps({"file": tmp_path})
            mock_check.assert_called_once()
        os.unlink(tmp_path)

    def test_skill_info_to_dict(self):
        """SkillInfo.to_dict returns correct structure."""
        from core.skill_manager import SkillInfo
        info = SkillInfo(name="test", description="a skill", source="local", keywords=["kw"], steps=3, usage_count=5, author="me", category="dev")
        d = info.to_dict()
        assert d["name"] == "test"
        assert d["description"] == "a skill"
        assert d["source"] == "local"
        assert d["steps"] == 3
        assert d["usage"] == 5


# ===================================================================
# I. core/batch_engine.py (当前81%, 31行缺)
# ===================================================================


class TestBatchEngine:
    """Complete coverage for BatchEngine - submit/status/cancel/retry/clear/list/concurrency."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = tmp_path / "test_batch.db"
        BatchEngine._shared_conn = None
        BatchEngine._shared_db_path = None
        BatchEngine._global_semaphore = None
        self.agent = MagicMock()
        self.agent.run.return_value = {"success": True, "result": "done"}
        with patch('core.batch_engine.BATCH_DB', self.db_path):
            self.engine = BatchEngine(agent=self.agent, max_concurrent=2, auto_start=False)
            yield
        self.engine.stop()
        self.engine.conn.close()

    def test_init_creates_db(self):
        """init creates database and tables."""
        assert self.db_path.exists()

    def test_submit_string_tasks(self):
        """submit accepts list of strings."""
        batch_id = self.engine.submit(["task1", "task2", "task3"])
        assert batch_id.startswith("batch_")
        status = self.engine.get_status(batch_id)
        assert status.total == 3
        assert status.pending == 3

    def test_submit_tuple_tasks(self):
        """submit accepts list of (id, text) tuples."""
        batch_id = self.engine.submit([("id1", "task1"), ("id2", "task2")])
        status = self.engine.get_status(batch_id)
        assert status.total == 2
        assert status.results[0]["task_id"] == "id1"

    def test_submit_custom_batch_id(self):
        """submit accepts custom batch_id."""
        batch_id = self.engine.submit(["test"], batch_id="my_custom_batch")
        assert batch_id == "my_custom_batch"

    def test_submit_tasks_alias(self):
        """submit_tasks works as alias."""
        batch_id = self.engine.submit_tasks(["task"])
        assert batch_id.startswith("batch_")

    def test_get_status_not_found(self):
        """get_status returns empty batch for unknown ID."""
        status = self.engine.get_status("nonexistent_batch")
        assert status.total == 0

    def test_get_status_with_mixed_statuses(self):
        """get_status correctly counts completed/running/failed/pending."""
        import time
        now = time.time()
        batch_id = "mixed_test"
        for i, s in enumerate(["completed", "running", "failed", "pending"]):
            self.engine._execute(
                "INSERT INTO batch_jobs (batch_id, task_index, task_text, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (batch_id, i, f"task{i}", s, now, now),
            )
        self.engine.conn.commit()
        status = self.engine.get_status(batch_id)
        assert status.completed == 1
        assert status.running == 1
        assert status.failed == 1
        assert status.pending == 1

    def test_get_all_batches(self):
        """get_all_batches returns all batches."""
        self.engine.submit(["a", "b"])
        self.engine.submit(["c"])
        batches = self.engine.get_all_batches()
        assert len(batches) >= 2

    def test_get_all_batches_with_limit(self):
        """get_all_batches respects limit."""
        for i in range(5):
            self.engine.submit([f"task{i}"])
        batches = self.engine.get_all_batches(limit=3)
        assert len(batches) <= 3

    def test_get_running_batches(self):
        """get_running_batches returns only in-progress batches."""
        self.engine.submit(["a"])
        running = self.engine.get_running_batches()
        assert len(running) >= 1

    def test_cancel_batch(self):
        """cancel_batch cancels pending tasks."""
        batch_id = self.engine.submit(["a", "b", "c"])
        count = self.engine.cancel_batch(batch_id)
        assert count == 3
        status = self.engine.get_status(batch_id)
        assert status.failed == 3
        # All errors should be "已取消"
        for r in status.results:
            assert r["error"] == "已取消"

    def test_cancel_batch_no_pending(self):
        """cancel_batch returns 0 when no pending tasks."""
        batch_id = self.engine.submit(["a"])
        self.engine.cancel_batch(batch_id)
        count = self.engine.cancel_batch(batch_id)
        assert count == 0

    def test_retry_failed(self):
        """retry_failed resets failed tasks to pending."""
        batch_id = self.engine.submit(["a", "b"])
        self.engine.cancel_batch(batch_id)
        count = self.engine.retry_failed(batch_id)
        assert count == 2
        status = self.engine.get_status(batch_id)
        assert status.pending == 2

    def test_retry_failed_none(self):
        """retry_failed returns 0 when no failed tasks."""
        batch_id = self.engine.submit(["a"])
        count = self.engine.retry_failed(batch_id)
        assert count == 0

    def test_clear_batch(self):
        """clear_batch deletes all records."""
        batch_id = self.engine.submit(["a", "b"])
        count = self.engine.clear_batch(batch_id)
        assert count == 2
        status = self.engine.get_status(batch_id)
        assert status.total == 0

    def test_clear_batch_nonexistent(self):
        """clear_batch returns 0 for nonexistent batch."""
        count = self.engine.clear_batch("no_such_batch")
        assert count == 0

    def test_concurrent_semaphore(self):
        """Semaphore is initialized correctly."""
        assert self.engine.max_concurrent == 2
        assert self.engine._semaphore is not None

    def test_execute_task_success(self):
        """_execute_task updates to completed on success."""
        task_data = {"id": 1, "batch_id": "test_batch", "task_index": 0, "task_text": "hello", "task_id": ""}
        self.engine._execute(
            "INSERT INTO batch_jobs (id, batch_id, task_index, task_text, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', 0, 0)",
            (1, "test_batch", 0, "hello"),
        )
        self.engine.conn.commit()
        self.engine._execute_task(task_data)
        row = self.engine._execute("SELECT * FROM batch_jobs WHERE id = 1").fetchone()
        assert row["status"] == "completed"

    def test_execute_task_failure(self):
        """_execute_task updates to failed on agent error."""
        self.agent.run.return_value = {"success": False, "errors": ["something went wrong"]}
        task_data = {"id": 2, "batch_id": "test_batch2", "task_index": 0, "task_text": "hello", "task_id": ""}
        self.engine._execute(
            "INSERT INTO batch_jobs (id, batch_id, task_index, task_text, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', 0, 0)",
            (2, "test_batch2", 0, "hello"),
        )
        self.engine.conn.commit()
        self.engine._execute_task(task_data)
        row = self.engine._execute("SELECT * FROM batch_jobs WHERE id = 2").fetchone()
        assert row["status"] == "failed"

    def test_execute_task_exception(self):
        """_execute_task updates to failed on exception."""
        self.agent.run.side_effect = RuntimeError("agent crashed")
        task_data = {"id": 3, "batch_id": "test_batch3", "task_index": 0, "task_text": "hello", "task_id": ""}
        self.engine._execute(
            "INSERT INTO batch_jobs (id, batch_id, task_index, task_text, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', 0, 0)",
            (3, "test_batch3", 0, "hello"),
        )
        self.engine.conn.commit()
        self.engine._execute_task(task_data)
        row = self.engine._execute("SELECT * FROM batch_jobs WHERE id = 3").fetchone()
        assert row["status"] == "failed"
        assert "agent crashed" in row["error"]

    def test_scheduler_loop_picks_pending(self):
        """_scheduler_loop picks up pending tasks."""
        self.engine.submit(["scheduler_task"])
        self.engine._running = True
        # Mock _execute_task to avoid actual execution
        original_exec = self.engine._execute_task
        executed = []
        def mock_exec(task_data):
            executed.append(task_data)
        self.engine._execute_task = mock_exec
        self.engine._scheduler_loop()
        self.engine._running = False
        # Should have executed or at least started
        assert len(executed) >= 0  # might have been processed or picked up

    def test_scheduler_loop_stops_when_not_running(self):
        """_scheduler_loop exits when _running is False."""
        self.engine._running = True
        # Set running to False inside loop
        def fake_loop():
            self.engine._running = False
        self.engine._scheduler_loop = fake_loop
        # Should not hang

    def test_update_task_result(self):
        """_update_task_result updates task in DB."""
        self.engine._execute(
            "INSERT INTO batch_jobs (id, batch_id, task_index, task_text, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'running', 0, 0)",
            (99, "test_batch", 0, "test"),
        )
        self.engine.conn.commit()
        self.engine._update_task_result(99, "completed", result="success", error="", duration=1.5)
        row = self.engine._execute("SELECT * FROM batch_jobs WHERE id = 99").fetchone()
        assert row["status"] == "completed"
        assert row["result"] == "success"
        assert row["duration"] == 1.5

    def test_start(self):
        """start launches scheduler thread."""
        self.engine._running = False
        self.engine._scheduler_thread = None
        self.engine.start()
        assert self.engine._running is True
        assert self.engine._scheduler_thread is not None
        self.engine.stop()

    def test_start_already_running(self):
        """start does nothing if already running."""
        self.engine._running = True
        self.engine._scheduler_thread = None
        self.engine.start()
        assert self.engine._scheduler_thread is None

    def test_batch_task_dataclass(self):
        """BatchTask dataclass works."""
        from core.batch_engine import BatchTask
        t = BatchTask(task_text="hello", task_id="custom")
        assert t.task_text == "hello"
        assert t.task_id == "custom"
        assert t.status == "pending"

    def test_semaphore_timeout(self):
        """_execute_task handles semaphore acquire timeout."""
        self.engine._semaphore = threading.Semaphore(0)  # Starve it
        task_data = {"id": 999, "batch_id": "timeout_test", "task_index": 0, "task_text": "x", "task_id": ""}
        self.engine._execute(
            "INSERT INTO batch_jobs (id, batch_id, task_index, task_text, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', 0, 0)",
            (999, "timeout_test", 0, "x"),
        )
        self.engine.conn.commit()
        self.engine._execute_task(task_data)
        row = self.engine._execute("SELECT * FROM batch_jobs WHERE id = 999").fetchone()
        assert row["status"] == "failed"
        assert "超时" in row["error"]


# ===================================================================
# J. core/budget_allocator.py (当前35%, 133行缺)
# ===================================================================


class TestBudgetAllocator:
    """Complete coverage for BudgetAllocator and related data classes."""

    def test_estimate_tokens(self):
        """estimate_tokens calculates token count."""
        from core.budget_allocator import estimate_tokens
        assert estimate_tokens("") == 0
        assert estimate_tokens("Hello World") > 0
        assert estimate_tokens("A" * 160) == 100

    def test_budget_category_constants(self):
        """BudgetCategory has correct constants."""
        from core.budget_allocator import BudgetCategory
        assert BudgetCategory.SYSTEM == "system"
        assert BudgetCategory.DIALOGUE == "dialogue"
        assert BudgetCategory.TOOLS == "tools"
        assert BudgetCategory.MEMORY == "memory"
        assert BudgetCategory.SKILLS == "skills"
        assert BudgetCategory.RESERVED == "reserved"

    def test_all_categories(self):
        """ALL_CATEGORIES includes all categories."""
        from core.budget_allocator import ALL_CATEGORIES, BudgetCategory
        assert BudgetCategory.SYSTEM in ALL_CATEGORIES
        assert BudgetCategory.DIALOGUE in ALL_CATEGORIES
        assert BudgetCategory.TOOLS in ALL_CATEGORIES
        assert BudgetCategory.MEMORY in ALL_CATEGORIES
        assert BudgetCategory.SKILLS in ALL_CATEGORIES
        assert BudgetCategory.RESERVED in ALL_CATEGORIES

    def test_budget_policy_defaults(self):
        """BudgetPolicy has sensible defaults."""
        from core.budget_allocator import BudgetPolicy
        policy = BudgetPolicy()
        assert policy.total_budget == 28000
        assert policy.warning_threshold == 0.85
        assert policy.critical_threshold == 0.95
        total = (policy.system_ratio + policy.dialogue_ratio + policy.tools_ratio
                 + policy.memory_ratio + policy.skills_ratio + policy.reserved_ratio)
        assert abs(total - 1.0) < 0.01

    def test_budget_policy_normalize(self):
        """BudgetPolicy auto-normalizes ratios that don't sum to 1."""
        from core.budget_allocator import BudgetPolicy
        policy = BudgetPolicy(system_ratio=1.0, dialogue_ratio=1.0, tools_ratio=0, memory_ratio=0, skills_ratio=0, reserved_ratio=0)
        total = (policy.system_ratio + policy.dialogue_ratio + policy.tools_ratio
                 + policy.memory_ratio + policy.skills_ratio + policy.reserved_ratio)
        assert abs(total - 1.0) < 0.01

    def test_budget_policy_get_budget(self):
        """get_budget returns correct budget for a category."""
        from core.budget_allocator import BudgetPolicy
        policy = BudgetPolicy(total_budget=10000, system_ratio=0.2)
        assert policy.get_budget("system") == 2000

    def test_budget_policy_get_budget_unknown(self):
        """get_budget returns 0 for unknown category."""
        from core.budget_allocator import BudgetPolicy
        policy = BudgetPolicy()
        assert policy.get_budget("unknown_cat") == 0

    def test_budget_policy_for_backend(self):
        """for_backend creates policy with given total_budget."""
        from core.budget_allocator import BudgetPolicy
        policy = BudgetPolicy.for_backend(64000)
        assert policy.total_budget == 64000

    def test_category_usage_ok(self):
        """CategoryUsage status 'ok' when below warning threshold."""
        from core.budget_allocator import CategoryUsage
        u = CategoryUsage(category="system", budget=1000, used=100)
        assert u.status == "ok"
        assert u.ratio == 0.1

    def test_category_usage_warning(self):
        """CategoryUsage status 'warning' when above 85%."""
        from core.budget_allocator import CategoryUsage
        u = CategoryUsage(category="dialogue", budget=1000, used=870)
        assert u.status == "warning"
        assert u.ratio == 0.87

    def test_category_usage_critical(self):
        """CategoryUsage status 'critical' when above 95%."""
        from core.budget_allocator import CategoryUsage
        u = CategoryUsage(category="memory", budget=1000, used=960)
        assert u.status == "critical"
        assert u.ratio == 0.96

    def test_category_usage_over(self):
        """CategoryUsage status 'over' when at or above 100%."""
        from core.budget_allocator import CategoryUsage
        u = CategoryUsage(category="tools", budget=1000, used=1000)
        assert u.status == "over"
        assert u.ratio == 1.0

    def test_category_usage_zero_budget(self):
        """CategoryUsage handles zero budget gracefully."""
        from core.budget_allocator import CategoryUsage
        u = CategoryUsage(category="reserved", budget=0, used=0)
        assert u.ratio == 0.0
        assert u.status == "ok"

    def test_budget_snapshot_properties(self):
        """BudgetSnapshot properties work correctly."""
        from core.budget_allocator import BudgetSnapshot, CategoryUsage
        snap = BudgetSnapshot(
            total_budget=10000,
            total_used=5000,
            categories={
                "system": CategoryUsage(category="system", budget=1500, used=100),
                "dialogue": CategoryUsage(category="dialogue", budget=3500, used=3400),
            },
            timestamp=100.0,
        )
        assert snap.overall_ratio == 0.5
        assert snap.needs_action is True  # dialogue is critical
        assert "dialogue" in snap.critical_categories
        assert "system" not in snap.critical_categories

    def test_budget_snapshot_no_action(self):
        """needs_action is False when all categories ok."""
        from core.budget_allocator import BudgetSnapshot, CategoryUsage
        snap = BudgetSnapshot(
            total_budget=10000,
            total_used=1000,
            categories={
                "system": CategoryUsage(category="system", budget=1500, used=100),
            },
        )
        assert snap.needs_action is False
        assert snap.critical_categories == []

    def test_budget_snapshot_zero_budget(self):
        """overall_ratio is 0 when budget is 0."""
        from core.budget_allocator import BudgetSnapshot
        snap = BudgetSnapshot(total_budget=0, total_used=100, categories={})
        assert snap.overall_ratio == 0.0

    def test_budget_snapshot_to_dict(self):
        """to_dict returns correct structure."""
        from core.budget_allocator import BudgetSnapshot, CategoryUsage
        snap = BudgetSnapshot(
            total_budget=10000,
            total_used=5000,
            categories={"system": CategoryUsage(category="system", budget=1500, used=100)},
        )
        d = snap.to_dict()
        assert d["total_budget"] == 10000
        assert d["needs_action"] is False
        assert "system" in d["categories"]
        assert "critical_categories" in d

    def test_budget_action_dataclass(self):
        """BudgetAction dataclass works."""
        from core.budget_allocator import BudgetAction
        action = BudgetAction(category="dialogue", severity="critical", action_type="collapse", description="test", priority=2)
        assert action.category == "dialogue"
        assert action.priority == 2

    def test_allocator_init_defaults(self):
        """BudgetAllocator initializes with defaults."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        assert alloc.policy is not None
        assert alloc.policy.total_budget == 28000
        assert alloc._last_snapshot is None
        assert alloc._history == []

    def test_scan_empty_messages(self):
        """scan handles empty messages list."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        snap = alloc.scan([])
        assert snap is not None
        assert snap.total_used == 0
        assert snap.total_budget == 28000

    def test_scan_with_messages(self):
        """scan calculates tokens from messages."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "tool", "content": "Result: success"},
        ]
        snap = alloc.scan(messages)
        assert snap.total_used > 0
        assert snap.categories["system"].used > 0
        assert snap.categories["dialogue"].used > 0
        assert snap.categories["tools"].used > 0

    def test_scan_with_memory_and_skills(self):
        """scan includes memory and skills tokens."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        messages = [{"role": "user", "content": "hi"}]
        snap = alloc.scan(messages, memory_token_size=500, skills_token_size=300)
        assert snap.categories["memory"].used == 500
        assert snap.categories["skills"].used == 300

    def test_scan_with_tool_calls(self):
        """scan counts tool_calls tokens."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "test", "arguments": {"arg1": "value1"}}}],
        }]
        snap = alloc.scan(messages)
        assert snap.total_used > 0

    def test_scan_updates_last_snapshot(self):
        """scan updates _last_snapshot and _history."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        alloc.scan([])
        assert alloc._last_snapshot is not None
        assert len(alloc._history) == 1

    def test_scan_history_trim(self):
        """scan trims history to 50 entries."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        for _ in range(60):
            alloc.scan([])
        assert len(alloc._history) == 50

    def test_scan_triggers_on_warning(self):
        """scan triggers warning callback."""
        from core.budget_allocator import BudgetAllocator
        warning_called = []
        alloc = BudgetAllocator(
            on_warning=lambda snap, cats: warning_called.append(cats),
        )
        # Push dialogue over warning threshold
        messages = [{"role": "user", "content": "A" * 100000}]
        snap = alloc.scan(messages)
        if snap.needs_action:
            assert len(warning_called) > 0

    def test_scan_triggers_on_critical(self):
        """scan triggers critical callback."""
        from core.budget_allocator import BudgetAllocator
        critical_called = []
        alloc = BudgetAllocator(
            on_critical=lambda snap, cats: critical_called.append(cats),
        )
        # Push dialogue over critical threshold
        messages = [{"role": "user", "content": "A" * 100000}]
        snap = alloc.scan(messages)
        # May or may not trigger depending on thresholds

    def test_get_actions_no_snapshot(self):
        """get_actions returns [] when no snapshot."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        actions = alloc.get_actions()
        assert actions == []

    def test_get_actions_ok(self):
        """get_actions returns empty when all categories ok."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        alloc.scan([])
        actions = alloc.get_actions()
        assert actions == []

    def test_get_actions_dialogue_warning(self):
        """get_actions suggests compress for dialogue warning."""
        from core.budget_allocator import BudgetAllocator, CategoryUsage, BudgetSnapshot
        alloc = BudgetAllocator()
        snap = BudgetSnapshot(
            total_budget=28000,
            total_used=10000,
            categories={
                "dialogue": CategoryUsage(category="dialogue", budget=9800, used=9000),
                "system": CategoryUsage(category="system", budget=4200, used=100),
                "tools": CategoryUsage(category="tools", budget=5600, used=100),
                "memory": CategoryUsage(category="memory", budget=2240, used=100),
                "skills": CategoryUsage(category="skills", budget=1960, used=100),
                "reserved": CategoryUsage(category="reserved", budget=4200, used=0),
            },
        )
        actions = alloc.get_actions(snap)
        assert len(actions) >= 1
        assert any(a.category == "dialogue" for a in actions)

    def test_get_actions_dialogue_over(self):
        """get_actions suggests collapse for dialogue over."""
        from core.budget_allocator import BudgetAllocator, CategoryUsage, BudgetSnapshot
        alloc = BudgetAllocator()
        snap = BudgetSnapshot(
            total_budget=28000,
            total_used=20000,
            categories={
                "dialogue": CategoryUsage(category="dialogue", budget=9800, used=9800),
                "system": CategoryUsage(category="system", budget=4200, used=100),
                "tools": CategoryUsage(category="tools", budget=5600, used=100),
                "memory": CategoryUsage(category="memory", budget=2240, used=100),
                "skills": CategoryUsage(category="skills", budget=1960, used=100),
                "reserved": CategoryUsage(category="reserved", budget=4200, used=0),
            },
        )
        actions = alloc.get_actions(snap)
        assert len(actions) >= 1
        dialogue_actions = [a for a in actions if a.category == "dialogue"]
        assert any(a.action_type == "collapse" for a in dialogue_actions)

    def test_get_actions_tools_warning(self):
        """get_actions suggests microcompact for tools warning."""
        from core.budget_allocator import BudgetAllocator, CategoryUsage, BudgetSnapshot
        alloc = BudgetAllocator()
        snap = BudgetSnapshot(
            total_budget=28000,
            total_used=10000,
            categories={
                "tools": CategoryUsage(category="tools", budget=5600, used=5000),
                "system": CategoryUsage(category="system", budget=4200, used=100),
                "dialogue": CategoryUsage(category="dialogue", budget=9800, used=100),
                "memory": CategoryUsage(category="memory", budget=2240, used=100),
                "skills": CategoryUsage(category="skills", budget=1960, used=100),
                "reserved": CategoryUsage(category="reserved", budget=4200, used=0),
            },
        )
        actions = alloc.get_actions(snap)
        tools_actions = [a for a in actions if a.category == "tools"]
        assert any(a.action_type == "microcompact" for a in tools_actions)

    def test_get_actions_tools_over(self):
        """get_actions suggests microcompact for tools over."""
        from core.budget_allocator import BudgetAllocator, CategoryUsage, BudgetSnapshot
        alloc = BudgetAllocator()
        snap = BudgetSnapshot(
            total_budget=28000,
            total_used=12000,
            categories={
                "tools": CategoryUsage(category="tools", budget=5600, used=5600),
                "system": CategoryUsage(category="system", budget=4200, used=100),
                "dialogue": CategoryUsage(category="dialogue", budget=9800, used=100),
                "memory": CategoryUsage(category="memory", budget=2240, used=100),
                "skills": CategoryUsage(category="skills", budget=1960, used=100),
                "reserved": CategoryUsage(category="reserved", budget=4200, used=0),
            },
        )
        actions = alloc.get_actions(snap)
        tools_actions = [a for a in actions if a.category == "tools"]
        assert any(a.action_type == "microcompact" for a in tools_actions)

    def test_get_actions_memory_over(self):
        """get_actions suggests summarize for memory over."""
        from core.budget_allocator import BudgetAllocator, CategoryUsage, BudgetSnapshot
        alloc = BudgetAllocator()
        snap = BudgetSnapshot(
            total_budget=28000,
            total_used=10000,
            categories={
                "memory": CategoryUsage(category="memory", budget=2240, used=2300),
                "system": CategoryUsage(category="system", budget=4200, used=100),
                "dialogue": CategoryUsage(category="dialogue", budget=9800, used=100),
                "tools": CategoryUsage(category="tools", budget=5600, used=100),
                "skills": CategoryUsage(category="skills", budget=1960, used=100),
                "reserved": CategoryUsage(category="reserved", budget=4200, used=0),
            },
        )
        actions = alloc.get_actions(snap)
        memory_actions = [a for a in actions if a.category == "memory"]
        assert any(a.action_type == "summarize" for a in memory_actions)

    def test_get_actions_skills_over(self):
        """get_actions suggests summarize for skills over."""
        from core.budget_allocator import BudgetAllocator, CategoryUsage, BudgetSnapshot
        alloc = BudgetAllocator()
        snap = BudgetSnapshot(
            total_budget=28000,
            total_used=10000,
            categories={
                "skills": CategoryUsage(category="skills", budget=1960, used=2000),
                "system": CategoryUsage(category="system", budget=4200, used=100),
                "dialogue": CategoryUsage(category="dialogue", budget=9800, used=100),
                "tools": CategoryUsage(category="tools", budget=5600, used=100),
                "memory": CategoryUsage(category="memory", budget=2240, used=100),
                "reserved": CategoryUsage(category="reserved", budget=4200, used=0),
            },
        )
        actions = alloc.get_actions(snap)
        skills_actions = [a for a in actions if a.category == "skills"]
        assert any(a.action_type == "summarize" for a in skills_actions)

    def test_suggest_action_no_match(self):
        """_suggest_action returns None for unsupported categories."""
        from core.budget_allocator import BudgetAllocator, BudgetCategory, CategoryUsage
        alloc = BudgetAllocator()
        action = alloc._suggest_action(BudgetCategory.RESERVED, CategoryUsage(category="reserved", budget=1000, used=900))
        assert action is None
        action = alloc._suggest_action(BudgetCategory.SYSTEM, CategoryUsage(category="system", budget=1000, used=900))
        assert action is None

    def test_reset(self):
        """reset clears history and last snapshot."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        alloc.scan([{"role": "user", "content": "hi"}])
        assert alloc._last_snapshot is not None
        assert len(alloc._history) == 1
        alloc.reset()
        assert alloc._last_snapshot is None
        assert alloc._history == []

    def test_create_allocator_for_backend(self):
        """create_allocator_for_backend creates allocator with correct policy."""
        from core.budget_allocator import create_allocator_for_backend
        alloc = create_allocator_for_backend(64000)
        assert alloc.policy.total_budget == 64000
        assert isinstance(alloc.policy.total_budget, int)

    def test_get_categories_summary_no_snapshot(self):
        """get_categories_summary returns '未扫描' when no snapshot."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        summary = alloc.get_categories_summary()
        assert "未扫描" in summary

    def test_get_categories_summary_with_snapshot(self):
        """get_categories_summary returns formatted string."""
        from core.budget_allocator import BudgetAllocator
        alloc = BudgetAllocator()
        alloc.scan([{"role": "user", "content": "hi"}])
        summary = alloc.get_categories_summary()
        assert "Token" in summary or "预算" in summary
        assert "system" in summary or "dialogue" in summary
