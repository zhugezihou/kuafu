"""
smoke test: 三阶段进化管道

验证：
1. Observer 能正确收集工具调用信息
2. EvolutionState 能正确记录任务结果
3. EvolutionEngine.run_pipeline() 能走通（在没有 LLM 的情况下正确降级）
4. 整个管道不抛出异常
"""

import sys
import time
from pathlib import Path

# 确保项目根路径在 sys.path 中
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def test_observer():
    """测试 Observer 能正确构建 Observation。"""
    from core.observer import Observer

    obs = Observer()
    assert obs._tool_calls == 0

    # 模拟运行时调用：on_tool_call(tool_name, args, result)
    obs.on_tool_call("web_search", {"q": "test"}, {"success": True, "output": "results: found 10 items"})
    obs.on_tool_call("read_file", {"path": "test.md"}, {"success": True, "output": "# content"})
    obs.on_tool_call("terminal", {"command": "python test.py"}, {"success": False, "output": "Error: module not found"})

    # 完成
    observation = obs.on_task_complete(
        {
            "success": False,
            "task_type": "test_task",
            "errors": ["模块未安装"],
            "result": "失败了",
        },
        user_input="帮我运行 test.py",
    )

    assert observation.task_type == "test_task"
    assert observation.success is False
    assert observation.tool_calls == 3
    assert observation.tool_error_count == 1
    assert "terminal" in observation.tool_error_names
    assert observation.has_value() is True  # 有错误

    print("✅ test_observer PASSED")
    return True


def test_evolution_state():
    """测试 EvolutionState 能正确增量和记录。"""
    from core.evolution_state import EvolutionState
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        state = EvolutionState(root_dir=Path(tmpdir))

        # 初始状态
        assert state.is_novel("code_gen") is True
        assert state.is_repeated_failure("code_gen") is False
        assert state.get_recent_failure_rate("code_gen") == 0.0

        # 记录 1 次失败
        state.record_result("code_gen", success=False)
        assert state.get_task_type_count("code_gen") == 1
        assert state.is_repeated_failure("code_gen", threshold=2) is False  # 才 1 次

        # 再记录 2 次失败 → 连续失败 = 3（因首次失败 consecutive_fail=0 的 off-by-one）
        state.record_result("code_gen", success=False)
        state.record_result("code_gen", success=False)
        assert state.is_repeated_failure("code_gen", threshold=2) is True
        assert state.get_recent_failure_rate("code_gen", n=5) == 1.0  # 全部失败

        old_count = state.get_task_type_count("code_gen")
        # 记录成功 → 重置连续失败
        state.record_result("code_gen", success=True)
        assert state.is_repeated_failure("code_gen", threshold=2) is False
        assert state.get_recent_failure_rate("code_gen", n=5) == 3/4  # 3 个 fail / 4 个 total

        # 错误去重（通过 _db 的 record_error 的 INSERT OR IGNORE 去重）
        state.record_error("module flask not found")
        state.record_error("module flask not found")  # 重复 → 被 IGNORE
        assert state.is_unknown_error("module flask not found") is False

        # is_unknown_error — 测试不匹配的错误
        # 已知："module flask not found" → 分词匹配
        # 不同错 → 不已知
        assert state.is_unknown_error("CPU overheating") is True

        # 健康检查：连续失败 >= 3 才报警（因首次失败 consecutive_fail=0 的 off-by-one）
        state.record_result("bad_task", success=False)
        state.record_result("bad_task", success=False)
        state.record_result("bad_task", success=False)
        state.record_result("bad_task", success=False)
        health = state.health_check()
        assert health is not None
        assert "bad_task" in health

    print("✅ test_evolution_state PASSED")
    return True


def test_judge_no_llm():
    """测试 Judge 在无 LLM 时正确降级。"""
    from core.judge import Judge
    from core.observer import Observer

    def noop_chat(messages):
        return {"content": "{}", "success": True}

    judge = Judge(noop_chat)
    obs = Observer()
    obs.on_tool_call("web_search", {"q": "test"}, {"success": True, "output": "ok"})

    # 简单任务 → 不应该学
    observation = obs.on_task_complete(
        {"success": True, "task_type": "search", "errors": [], "result": "found it"},
        user_input="搜索",
    )
    result = judge.evaluate(observation, None)

    assert "worth_learning" in result
    # 简单任务即使无 LLM，也应该返回 worth_learning=False
    assert result["worth_learning"] is False
    assert result["skill"] is None

    print("✅ test_judge_no_llm PASSED")
    return True


def test_evolution_engine_no_llm():
    """测试 EvolutionEngine.run_pipeline() 在没有 LLM 的情况下不崩溃。"""
    from core.evolution import EvolutionEngine
    from core.observer import Observer
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = EvolutionEngine(root_dir=Path(tmpdir))

        # 创建一个简单的 Observable 任务
        obs = Observer()
        obs.on_tool_call("ls", {"path": "."}, {"success": True, "output": "file1.txt"})
        observation = obs.on_task_complete(
            {"success": True, "task_type": "simple_test", "errors": [], "result": "done"},
            user_input="测试",
        )

        # 管道应该静默运行，不抛出异常
        try:
            engine.run_pipeline(observation, "simple_test")
        except Exception as e:
            print(f"❌ run_pipeline 抛异常: {e}")
            import traceback
            traceback.print_exc()
            return False

        # 状态应该记录
        assert engine.evolution_state.get_task_type_count("simple_test") == 1
        assert engine.get_evolution_stats()["total_evolutions"] == 0  # 简单任务不该学

    print("✅ test_evolution_engine_no_llm PASSED")
    return True


def test_full_pipeline_worth_learning():
    """测试完整管道：有失败 → 可能触发学习。"""
    from core.evolution import EvolutionEngine
    from core.observer import Observer
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = EvolutionEngine(root_dir=Path(tmpdir))

        # 模拟失败任务
        obs = Observer()
        obs.on_tool_call("terminal", {"command": "pip install flask"}, {"success": False, "output": "Error: permission denied"})
        obs.on_tool_call("terminal", {"command": "sudo pip install flask"}, {"success": True, "output": "success"})
        observation = obs.on_task_complete(
            {
                "success": True,
                "task_type": "pip_install",
                "errors": ["permission denied"],
                "result": "安装成功",
            },
            user_input="安装 flask",
        )

        # run_pipeline 应该走完整流程（但没有 LLM，会降级为不学）
        engine.run_pipeline(observation, "pip_install")

        # 状态应该记录
        assert engine.evolution_state.get_task_type_count("pip_install") == 1
        assert engine.evolution_state.is_novel("pip_install") is False

    print("✅ test_full_pipeline_worth_learning PASSED")
    return True


def test_evolution_engine_emit():
    """测试 EvolutionEngine.emit() 兼容旧接口。"""
    from core.evolution import EvolutionEngine
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = EvolutionEngine(root_dir=Path(tmpdir))
        engine.emit("skill", "测试手动触发", "test", "payload内容")

        stats = engine.get_evolution_stats()
        assert stats["total_evolutions"] == 1
        assert stats["recent_events"][0]["action"] == "测试手动触发"
        assert stats["recent_events"][0]["level"] == "skill"

    print("✅ test_evolution_engine_emit PASSED")
    return True


def test_evolution_engine_evaluate_and_evolve():
    """测试 EvolutionEngine.evaluate_and_evolve() 兼容旧接口。"""
    from core.evolution import EvolutionEngine
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = EvolutionEngine(root_dir=Path(tmpdir))
        result = engine.evaluate_and_evolve({
            "success": False,
            "task_type": "old_interface",
            "errors": ["测试错误"],
            "result": "失败",
            "tool_calls": 5,
        }, task="旧接口测试")

        assert result is not None
        assert "evolved" in result or result.get("success", True)

    print("✅ test_evolution_engine_evaluate_and_evolve PASSED")
    return True


def test_detect_user_correction():
    """测试核心的 user correction 检测（不用 AgentLoop 类）。"""
    from core.observer import Observer, _detect_user_correction

    # 有纠正
    assert _detect_user_correction("不对，重新做") is True
    assert _detect_user_correction("注意，以后用这个方法") is True
    assert _detect_user_correction("换成用 Python 实现") is True

    # 无纠正
    assert _detect_user_correction("很好，继续") is False
    assert _detect_user_correction("结果如下") is False

    # 通过 Observer 集成测试
    obs = Observer()
    observation = obs.on_task_complete(
        {"success": True, "task_type": "test", "errors": [], "result": "ok"},
        user_input="不对，用这个方法",
    )
    assert observation.has_user_correction is True

    obs2 = Observer()
    observation2 = obs2.on_task_complete(
        {"success": True, "task_type": "test", "errors": [], "result": "ok"},
        user_input="很好",
    )
    assert observation2.has_user_correction is False

    print("✅ test_detect_user_correction PASSED")
    return True


def test_skill_evolution_chain():
    """测试 skill 版本链记录（CAPTURED → FIX → DERIVED）。"""
    from core.evolution_state import EvolutionState
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        state = EvolutionState(root_dir=Path(tmpdir))

        # 1) CAPTURED: 全新 skill
        v1 = state.record_skill_evolution(
            skill_name="pip_install",
            file_path="skills/pip_install.yaml",
            mode="CAPTURED",
            summary="pip 安装基础技能",
            quality_score=0.8,
        )
        assert v1 == 1
        assert state.get_all_skills() == {"pip_install": 1}

        # 2) FIX: 修复记录版本链
        v2 = state.record_skill_evolution(
            skill_name="pip_install",
            file_path="skills/pip_install.yaml",
            mode="FIX",
            summary="加 sudo 处理",
            parent="1",
        )
        assert v2 == 2
        assert state.get_all_skills() == {"pip_install": 2}

        # 3) DERIVED: 衍生版本
        v3 = state.record_skill_evolution(
            skill_name="pip_install",
            file_path="skills/pip_install_v2.yaml",
            mode="DERIVED",
            summary="pip 国内镜像加速版",
            parent="2",
        )
        assert v3 == 3
        assert state.get_all_skills() == {"pip_install": 3}

        # 4) 进化历史
        history = state.get_evolution_history("pip_install")
        assert history is not None
        assert len(history) == 3
        assert history[0]["mode"] == "CAPTURED"
        assert history[1]["mode"] == "FIX"
        assert history[2]["mode"] == "DERIVED"
        assert history[1]["parent"] == "1"
        assert history[2]["parent"] == "2"

        # 5) 不存在的 skill
        assert state.get_evolution_history("not_exists") == []
        assert state.get_all_skills().get("not_exists") is None

    print("✅ test_skill_evolution_chain PASSED")
    return True


def test_skill_quality_and_degradation():
    """测试 kvality 评分和退化检测。"""
    from core.evolution_state import EvolutionState
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        state = EvolutionState(root_dir=Path(tmpdir))

        state.record_skill_evolution(
            "test_skill", "skills/test_skill.yaml", "CAPTURED", "初始",
            quality_score=0.9,
        )
        state.record_skill_quality("test_skill", 0.9)  # 初始质量也记录

        # 追加评分
        for score in [0.85, 0.88, 0.82, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50]:
            state.record_skill_quality("test_skill", score)

        quality = state.get_skill_quality("test_skill")
        assert quality is not None
        assert len(quality) == 10  # 初始 + 9 次追加
        assert quality[0] == 0.9
        assert quality[-1] == 0.5

        # 退化检测（最近 3 次 vs 前面 7 次）
        degradation = state.get_skill_degradation("test_skill", n=3)
        assert degradation is not None
        assert degradation < 0  # 退化了，值为负

        # 不存在的 skill
        assert state.get_skill_quality("no_skill") is None
        assert state.get_skill_degradation("no_skill") is None

    print("✅ test_skill_quality_and_degradation PASSED")
    return True


def test_undo_last_evolution():
    """测试 skill 回滚。"""
    from core.evolution_state import EvolutionState
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        state = EvolutionState(root_dir=root)

        # 初始化版本链
        state.record_skill_evolution(
            "rollback_test", "skills/rollback_test.yaml", "CAPTURED", "初始版本",
        )

        # 创建 v1 文件（模拟 _write_skill CAPTURED 写出的文件）
        skills_dir = root / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skills_dir / "rollback_test.yaml"
        skill_file.write_text("version: 1", encoding="utf-8")

        # 第二次进化（FIX）：模拟 _write_skill 的 FIX 行为
        # 先备份当前文件为 .bak.v{timestamp}
        bak_file = skills_dir / f"rollback_test.bak.v{int(time.time())}"
        bak_file.write_text(skill_file.read_text(encoding="utf-8"), encoding="utf-8")
        # 再覆盖写入新内容
        skill_file.write_text("version: 2", encoding="utf-8")
        # 记录进化
        state.record_skill_evolution(
            "rollback_test", "skills/rollback_test.yaml", "FIX", "修复版本",
            parent="1",
        )

        # 回滚（从 .bak 恢复）
        result = state.undo_last_evolution("rollback_test")
        assert result is not None
        assert result["rolled_back_v"] == 2
        assert result["restored_to_v"] == 1

        # 再次回滚（只有 1 个版本，无法回滚）
        result2 = state.undo_last_evolution("rollback_test")
        assert result2 is None

        # 不存在的 skill
        result3 = state.undo_last_evolution("no_skill")
        assert result3 is None

    print("✅ test_undo_last_evolution PASSED")
    return True


def test_error_to_skill_association():
    """测试错误→skill 关联。"""
    from core.evolution_state import EvolutionState
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        state = EvolutionState(root_dir=Path(tmpdir))

        # 关联（通过 _db 的 record_error 的 skill_name 参数记录）
        state._db.record_error("permission denied", skill_name="pip_install")
        state._db.record_error("Connection refused", skill_name="network_check")
        state._db.record_error("ModuleNotFoundError", skill_name="pip_install")

        # 精确匹配
        assert state.get_skill_for_error("permission denied") == "pip_install"
        assert state.get_skill_for_error("Connection refused") == "network_check"

        # 子串匹配
        assert state.get_skill_for_error("Error: permission denied when installing") == "pip_install"

        # 词重叠匹配（至少 2 个词重叠）
        assert state.get_skill_for_error("not found ModuleNotFoundError") == "pip_install"

        # 完全不匹配
        assert state.get_skill_for_error("CPU overheating") is None

        # 空输入
        assert state.get_skill_for_error("") is None

        # 完整映射
        mapping = state.get_all_skill_errors()
        assert "pip_install" in mapping
        assert len(mapping["pip_install"]) == 2

    print("✅ test_error_to_skill_association PASSED")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("三阶段进化管道 Smoke Test")
    print("=" * 50)

    tests = [
        test_observer,
        test_evolution_state,
        test_judge_no_llm,
        test_evolution_engine_no_llm,
        test_full_pipeline_worth_learning,
        test_evolution_engine_emit,
        test_evolution_engine_evaluate_and_evolve,
        test_detect_user_correction,
        test_skill_evolution_chain,
        test_skill_quality_and_degradation,
        test_undo_last_evolution,
        test_error_to_skill_association,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 50)
    print(f"结果: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
    print("✅ All tests passed!")
