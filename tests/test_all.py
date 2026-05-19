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
    assert "0.2" in agent.version

    print("✅ main: Agent 初始化正常")


def test_agent_prompt():
    """测试 Agent 系统 prompt 组装（不依赖 LLM）"""
    from core.main import KuafuAgent

    agent = KuafuAgent()
    
    # 检查系统 prompt 组装
    prompt = agent.build_system_prompt()
    assert "夸父" in prompt
    assert "进化" in prompt
    
    # 检查状态
    status = agent.get_status()
    assert status["name"] == "夸父"
    assert "version" in status
    assert "memory" in status
    assert "evolution" in status
    assert "task_stats" in status

    print("✅ main: 系统 prompt 组装 + 状态查询正常")


def test_full_flow():
    """端到端流程：直接测试 memory + evolution 配合"""
    from core.memory_api import MemoryAPI
    from core.evolution import EvolutionEngine

    memory = MemoryAPI()
    evolution = EvolutionEngine()
    
    # 记忆 + 进化联合测试
    for i in range(6):
        memory.remember(
            key=f"test:full_flow_{i}",
            content=f"测试任务 #{i+1} 完成",
            tags=["test", "coding"],
        )
        # 通过 evolution 记录任务
        evolution.evaluate_and_evolve({
            "success": True,
            "errors": [],
            "tool_calls": 3,
            "task_type": "coding",
            "duration": 2.0,
            "user_correction": None,
        })
    
    # 检查记忆检索
    results = memory.recall("测试任务")
    assert len(results) >= 1
    
    # 检查演进统计
    stats = evolution.get_task_stats()
    assert stats["total"] >= 6
    assert "coding" in stats["by_type"]
    
    evo_stats = evolution.get_evolution_stats()
    print(f"    进化统计: {evo_stats}")

    print("✅ 端到端: memory + evolution 联合工作正常")


def test_core_charter():
    """检查核心宪章文件是否存在"""
    root = Path(__file__).resolve().parent.parent
    assert (root / "CORE_CHARTER.md").exists(), "CORE_CHARTER.md 必须存在"
    assert (root / "IDENTITY.md").exists(), "IDENTITY.md 必须存在"
    # 确认 core/ 下所有模块（V0.2 新增 agent_loop + llm）
    core_files = ["identity.py", "sandbox.py", "memory_api.py", "evolution.py",
                  "main.py", "agent_loop.py", "llm.py"]
    for f in core_files:
        assert (root / "core" / f).exists(), f"core/{f} 必须存在"
    print("✅ core/ 结构完整")


def test_llm_client_init():
    """测试 LLM 客户端初始化"""
    from core.llm import LLMClient

    # 必须设置 API key 才能初始化
    assert LLMClient.__module__, "LLMClient 模块可导入"

    print("✅ llm: 客户端导入正常")


def test_agent_loop_tools():
    """测试 AgentLoop 工具定义完整性"""
    from core.agent_loop import TOOLS_DEFINITIONS

    tool_names = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
    expected = {"terminal", "read_file", "write_file", "patch",
                "search_files", "web_search", "web_fetch", "finish"}
    assert set(tool_names) == expected, f"工具不匹配: {set(tool_names) ^ expected}"
    print(f"✅ agent_loop: 8 个工具定义完整 ({', '.join(tool_names)})")


def test_agent_loop_build_prompt():
    """测试 AgentLoop 系统 prompt 组装"""
    from core.agent_loop import AgentLoop

    loop = AgentLoop()
    prompt = loop.build_system_prompt()
    assert "夸父" in prompt
    assert "核心规则" in prompt
    assert "可用工具" in prompt
    assert "进化状态" in prompt
    print("✅ agent_loop: 系统 prompt 组装正常")


if __name__ == "__main__":
    tests = [
        test_identity,
        test_sandbox,
        test_memory_api,
        test_evolution,
        test_agent_repr,
        test_agent_prompt,
        test_full_flow,
        test_core_charter,
        test_llm_client_init,
        test_agent_loop_tools,
        test_agent_loop_build_prompt,
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
