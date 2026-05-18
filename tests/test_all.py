#!/usr/bin/env python3
"""夸父核心模块测试"""

import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_identity():
    """测试身份系统"""
    from core.identity import load_identity_statement, get_agent_name

    statement = load_identity_statement()
    assert "夸父" in statement, "身份声明中应包含 '夸父'"
    assert "Kuafu" in statement, "身份声明中应包含 'Kuafu'"
    assert get_agent_name() == "夸父"
    print("✅ identity: 身份声明加载正常")


def test_sandbox():
    """测试沙盒系统"""
    from core.sandbox import is_path_allowed_for_write, validate_command

    # 拒绝写 core/
    allowed, reason_allowed = is_path_allowed_for_write("/home/asus/kuafu/strategy/test.txt")
    assert allowed, f"strategy/ 应允许写入: {reason_allowed}"

    denied, reason_denied = is_path_allowed_for_write("/home/asus/kuafu/core/test.txt")
    assert not denied, f"core/ 应禁止写入: {reason_denied}"

    # 命令安全
    safe, risk, _ = validate_command("ls -la")
    assert safe, f"安全命令被拦截: {risk}"

    safe2, risk2, _ = validate_command("rm -rf /")
    assert not safe2, f"高危命令未拦截: {risk2}"

    print("✅ sandbox: 路径白名单 + 命令安全检查正常")


def test_memory_api():
    """测试记忆系统"""
    from core.memory_api import MemoryAPI

    api = MemoryAPI()

    # 写入
    assert api.remember("test:hello", "这是一个测试记忆", tags=["test"])
    
    # 检索
    results = api.recall("测试")
    assert len(results) >= 1, f"应检索到记忆，得到 {len(results)}"
    assert any("测试记忆" in r.get("content", "") for r in results)

    # 反思
    reflection = api.reflect("测试")
    assert reflection is not None
    assert "测试" in reflection

    print("✅ memory_api: 写入/检索/反思正常")


def test_evolution():
    """测试进化引擎"""
    from core.evolution import EvolutionEngine

    engine = EvolutionEngine()

    # 正常任务 — 不触发进化（刚开始，历史不足5次）
    result = engine.evaluate_and_evolve({
        "success": True,
        "errors": [],
        "tool_calls": 3,
        "task_type": "coding",
        "duration": 5.0,
        "user_correction": None,
    })
    assert result is None, f"刚开始不应触发进化: {result}"

    # 连续失败任务（不同类型避免误触L2成功条件）
    for i in range(5):
        engine.evaluate_and_evolve({
            "success": False,
            "errors": ["语法错误"],
            "tool_calls": 2,
            "task_type": f"fail_test_{i}",  # 不同类型避免同类型成功计数
            "duration": 3.0,
            "user_correction": None,
        })

    # 再触发2次 — 连续3次失败应触发L2
    for _ in range(2):
        engine.evaluate_and_evolve({
            "success": False,
            "errors": ["语法错误"],
            "tool_calls": 2,
            "task_type": "fail_test_5",
            "duration": 3.0,
            "user_correction": None,
        })

    # 应触发L2进化（连续3次失败）
    event = engine.evaluate_and_evolve({
        "success": False,
        "errors": ["语法错误"],
        "tool_calls": 2,
        "task_type": "fail_test_5",
        "duration": 3.0,
        "user_correction": None,
    })
    assert event is not None, "连续失败应触发进化"
    assert event.level == 2, f"应触发 L2 进化，得到 L{event.level}"
    assert "失败" in event.trigger

    # 检查统计
    stats = engine.get_evolution_stats()
    assert stats["total_evolutions"] >= 1
    assert 2 in stats["by_level"]

    stats2 = engine.get_task_stats()
    assert stats2["total"] >= 8

    print("✅ evolution: 触发条件 & 统计正常")


def test_agent_repr():
    """测试 Agent 表示"""
    from core.main import KuafuAgent

    agent = KuafuAgent()
    assert "KuafuAgent" in repr(agent)
    assert "夸父" in agent.name
    assert "0.1" in agent.version

    print("✅ main: Agent 初始化正常")


def test_full_flow():
    """端到端流程：任务 → 记忆 → 进化"""
    from core.main import KuafuAgent

    agent = KuafuAgent()
    
    # 执行几次成功任务
    for i in range(6):
        result = agent.run(f"测试任务 #{i+1}", task_type="coding")
        assert isinstance(result, dict)
        assert "success" in result
        assert "duration" in result

    # 检查任务统计
    stats = agent.evolution.get_task_stats()
    assert stats["total"] >= 6

    # 检查进化统计
    evo_stats = agent.evolution.get_evolution_stats()
    print(f"    进化统计: {evo_stats}")

    # 检查系统 prompt 组装
    prompt = agent.build_system_prompt()
    assert "夸父" in prompt
    assert "进化" in prompt

    print("✅ 端到端: 任务→记忆→进化循环正常")


def test_core_charter():
    """检查核心宪章文件是否存在"""
    root = Path(__file__).resolve().parent.parent
    assert (root / "CORE_CHARTER.md").exists(), "CORE_CHARTER.md 必须存在"
    assert (root / "IDENTITY.md").exists(), "IDENTITY.md 必须存在"
    # 确认 core/ 下所有模块
    core_files = ["identity.py", "sandbox.py", "memory_api.py", "evolution.py", "main.py"]
    for f in core_files:
        assert (root / "core" / f).exists(), f"core/{f} 必须存在"
    print("✅ core/ 结构完整")


if __name__ == "__main__":
    tests = [
        test_identity,
        test_sandbox,
        test_memory_api,
        test_evolution,
        test_agent_repr,
        test_full_flow,
        test_core_charter,
    ]
    
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print(f"\n{'='*40}")
    print(f"结果: {passed} 通过, {failed} 失败")
    if failed > 0:
        sys.exit(1)
