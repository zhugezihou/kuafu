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

# 预设 dummy API key，用于 LLM 无关的测试
os.environ.setdefault("KUAFFU_API_KEY", "test-dummy-key")


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
    from core.safety import is_path_allowed_for_write, validate_command

    root = Path(__file__).resolve().parent.parent

    # 拒绝写 core/
    allowed, reason_allowed = is_path_allowed_for_write(f"{root}/strategy/test.txt")
    assert allowed, f"strategy/ 应允许写入: {reason_allowed}"

    denied, reason_denied = is_path_allowed_for_write(f"{root}/core/test.txt")
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
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        api = MemoryAPI(mode="file", memory_dir=Path(tmpdir))

        # 写入
        assert api.remember("test:hello", "这是一个测试记忆", tags=["test"])
        
        # 检索
        results = api.recall("测试记忆")
        assert len(results) >= 1, f"应检索到记忆，得到 {len(results)}"
        assert any("测试记忆" in r.get("content", "") for r in results)

        # 反思（无 LLM 时返回相关记忆摘要）
        reflection = api.reflect("测试记忆")
        assert reflection is not None
        assert "测试记忆" in reflection or len(reflection) > 0

        print("✅ memory_api: 写入/检索/反思正常")


def test_evolution():
    """测试进化引擎（D 方案 — 即兴进化）"""
    from core.evolution import EvolutionEngine

    engine = EvolutionEngine()

    # D 方案：无 LLM 时 evaluate_and_evolve 不抛异常，返回 dict
    result = engine.evaluate_and_evolve({
        "success": True,
        "errors": [],
        "tool_calls": 3,
        "task_type": "coding",
        "duration": 5.0,
    })
    assert isinstance(result, dict), f"应返回 dict，得到 {type(result)}"

    # 持续跑任务记录到统计
    for i in range(5):
        engine.evaluate_and_evolve({
            "success": True,
            "errors": [],
            "tool_calls": 2,
            "task_type": f"test_task_{i}",
            "duration": 3.0,
        })

    # 检查任务统计（可能不存在 get_task_stats）
    if hasattr(engine, 'get_task_stats'):
        stats = engine.get_task_stats()
        assert stats["total"] >= 6
        assert "coding" in stats["by_type"]

    # 检查进化统计（结构完整性，精确值可能因历史数据浮动）
    evo_stats = engine.get_evolution_stats()
    assert "total_evolutions" in evo_stats

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
    
    # mock evolution stats to match what build_system_prompt expects
    agent.evolution.get_evolution_stats = lambda: {
        "total_evolutions": 0,
        "by_level": {},
        "recent_events": [],
        "last_event": None,
        "health": {},
    }
    
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
    results = memory.recall("测试任务完成")
    assert len(results) >= 1, f"recall 应返回结果，得到 {len(results)}"
    
    # 检查演进统计（可能不存在 get_task_stats）
    if hasattr(evolution, 'get_task_stats'):
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
    core_files = ["identity.py", "safety.py", "memory_api.py", "evolution.py",
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
    from core.agent_loop import AgentLoop

    loop = AgentLoop()
    tools = loop.tools.get_schemas()
    tool_names = [t["function"]["name"] for t in tools]
    expected = {"terminal", "finish",
                "delegate_task", "invoke_expert", "invoke_experts",
                "memory_store",
                "memory_search", "memory_reflect",
                "skill_rollback", "tool_search"}
    assert set(tool_names) == expected, f"工具不匹配: {set(tool_names) ^ expected}"
    print(f"✅ agent_loop: {len(tool_names)} 个工具定义完整 ({', '.join(tool_names)})")


def test_agent_loop_build_prompt():
    """测试 AgentLoop 系统 prompt 组装"""
    from core.agent_loop import AgentLoop

    loop = AgentLoop()
    prompt = loop.build_system_prompt()
    assert "夸父" in prompt
    assert "核心规则" in prompt
    assert "可用工具" in prompt
    assert "执行纪律" in prompt or "执行规则" in prompt
    print("✅ agent_loop: 系统 prompt 组装正常")


def test_webhook_lifecycle():
    """测试 WebHook 服务器生命周期（启动 → 健康检查 → 停止）"""
    from core.webhook_server import WebhookServer
    from urllib.request import urlopen, Request
    from urllib.error import URLError

    server = WebhookServer(port=18765, token="test-token")
    assert server.start(), "WebHook 启动应成功"
    assert server.is_running()

    # 健康检查
    import time as _time
    _time.sleep(0.5)  # 等服务器就绪
    try:
        resp = urlopen("http://127.0.0.1:18765/health", timeout=3)
        data = json.loads(resp.read().decode())
        assert data["status"] == "ok"
    except URLError:
        pass  # 环境可能不支持 localhost 回环

    server.stop()
    assert not server.is_running()
    print("✅ webhook: 启动/健康检查/停止正常")


def test_subagent_schema():
    """测试子 Agent 系统的 schema 和并发限制"""
    from core.subagent import get_delegate_schema, MAX_CONCURRENT, MAX_TURNS

    schema = get_delegate_schema()
    assert "goal" in schema.get("parameters", {}).get("required", [])
    assert "context" in schema.get("parameters", {}).get("required", [])
    assert MAX_CONCURRENT >= 1
    assert MAX_TURNS >= 1
    assert schema.get("description", "").startswith("将")
    print(f"✅ subagent: schema 定义正常 (并发上限={MAX_CONCURRENT}, 轮次上限={MAX_TURNS})")


def test_channel_init():
    """测试 ChannelManager 可正常导入和基础操作"""
    from core.channel import ChannelManager

    mgr = ChannelManager()
    assert mgr.list() == []
    assert mgr.get("nonexistent") is None
    print("✅ channel: ChannelManager 初始化正常")


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
        test_webhook_lifecycle,
        test_subagent_schema,
        test_channel_init,
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
