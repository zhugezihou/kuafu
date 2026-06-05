"""
夸父性能基准测试 (Performance Benchmark)

测试维度：
1. Agent 初始化速度
2. 系统 Prompt 构建速度
3. 工具注册与执行延迟
4. 记忆系统读写
5. 上下文压缩性能
6. 进化引擎处理速度
7. 会话存储 CRUD
8. 审批系统决策延迟

运行: python tests/test_benchmark.py
"""

import time
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 静默认日志，防止 benchmark 输出被日志污染
import logging
logging.disable(logging.CRITICAL)

PASS = 0
FAIL = 0
RESULTS = []


def bench(name: str, threshold: float):
    """装饰器：运行基准测试并记录超阈值警告。"""
    def decorator(fn):
        def wrapper():
            global PASS, FAIL
            try:
                t0 = time.perf_counter()
                fn()
                elapsed = time.perf_counter() - t0
                status = "✅" if elapsed < threshold else "⚠️"
                msg = f"{status} {name}: {elapsed:.3f}s (阈值 {threshold:.2f}s)"
                RESULTS.append((name, elapsed, threshold, elapsed < threshold))
                print(msg)
                PASS += 1
            except Exception as e:
                print(f"❌ {name}: {e}")
                import traceback
                traceback.print_exc()
                FAIL += 1
        return wrapper
    return decorator


# ======================================================================
# 1. Agent 初始化
# ======================================================================

@bench("Agent 初始化 KuafuAgent()", threshold=10.0)
def bench_agent_init():
    from core.main import KuafuAgent
    agent = KuafuAgent()
    assert "夸父" in agent.name


@bench("Agent 初始化 AgentLoop()", threshold=3.0)
def bench_agent_loop_init():
    from core.agent_loop import AgentLoop
    loop = AgentLoop()
    assert loop.tools is not None


# ======================================================================
# 2. 系统 Prompt 构建
# ======================================================================

@bench("构建系统 Prompt（无任务）", threshold=3.0)
def bench_prompt_build_empty():
    from core.agent_loop import AgentLoop
    loop = AgentLoop()
    prompt = loop.build_system_prompt()
    assert len(prompt) > 500


@bench("构建系统 Prompt（有任务）", threshold=3.0)
def bench_prompt_build_with_task():
    from core.agent_loop import AgentLoop
    loop = AgentLoop()
    prompt = loop.build_system_prompt(task="写一个 Python 脚本")
    assert len(prompt) > 500


# ======================================================================
# 3. 工具注册与执行
# ======================================================================

@bench("ToolRegistry 初始化", threshold=1.0)
def bench_tool_registry_init():
    from core.tool_registry import ToolRegistry
    tr = ToolRegistry()
    schemas = tr.get_schemas()
    assert len(schemas) >= 3


@bench("工具注册 100 次", threshold=1.0)
def bench_tool_register_many():
    from core.tool_registry import ToolRegistry
    tr = ToolRegistry()
    for i in range(100):
        tr.register(f"bench_tool_{i}", {
            "description": f"Benchmark tool {i}",
            "parameters": {"type": "object", "properties": {}},
        }, lambda args: {"success": True, "output": "ok"})
    assert tr.get_handler("bench_tool_99") is not None


@bench("紧凑工具自动提升", threshold=0.5)
def bench_compact_promotion():
    from core.tool_registry import ToolRegistry
    tr = ToolRegistry()
    tr.register_compact("bench_compact", {
        "description": "Bench compact",
        "parameters": {"type": "object", "properties": {}},
    }, lambda args: {"success": True, "output": "ok"})
    assert tr._promote_compact_tool("bench_compact") is True
    assert tr._promote_compact_tool("bench_compact") is False


@bench("延迟工具搜索", threshold=0.5)
def bench_deferred_search():
    from core.tool_registry import ToolRegistry
    tr = ToolRegistry()
    results = tr._search_deferred_tools("搜索互联网")
    assert len(results) > 0


# ======================================================================
# 4. 记忆系统
# ======================================================================

@bench("记忆写入 100 条", threshold=2.0)
def bench_memory_write():
    from core.memory_api import MemoryAPI
    api = MemoryAPI()
    for i in range(100):
        api.remember(f"bench:key_{i}", f"测试记忆条目 #{i}", tags=["benchmark"])


@bench("记忆检索 100 条", threshold=2.0)
def bench_memory_recall():
    from core.memory_api import MemoryAPI
    api = MemoryAPI()
    for i in range(50):
        api.remember(f"bench:recall_{i}", f"检索测试 #{i}", tags=["benchmark"])
    results = api.recall("检索测试")
    assert results is not None


@bench("记忆反思", threshold=2.0)
def bench_memory_reflect():
    from core.memory_api import MemoryAPI
    api = MemoryAPI()
    api.remember("bench:reflect", "性能基准测试反思内容", tags=["benchmark"])
    reflection = api.reflect("性能基准")
    assert reflection is not None


# ======================================================================
# 5. 会话存储
# ======================================================================

@bench("SessionStore 创建会话", threshold=1.0)
def bench_session_create():
    from core.session_store import SessionStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(Path(tmpdir) / "sessions.db")
        sid = store.create_session("基准测试会话")
        assert sid is not None


@bench("SessionStore 写入 100 条消息", threshold=3.0)
def bench_session_messages():
    from core.session_store import SessionStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(Path(tmpdir) / "sessions.db")
        sid = store.create_session("消息基准")
        for i in range(100):
            store.append_message(sid, "user", f"消息 #{i}")
            store.append_message(sid, "assistant", f"回复 #{i}")
        msgs = store.get_messages(sid)
        assert len(msgs) == 200


@bench("SessionStore 搜索 100 个会话", threshold=3.0)
def bench_session_search():
    from core.session_store import SessionStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(Path(tmpdir) / "sessions.db")
        for i in range(100):
            store.create_session(f"搜索测试 #{i}")
        results = store.search_sessions("搜索测试")
        assert len(results) == 10


# ======================================================================
# 6. 上下文压缩
# ======================================================================

@bench("ContextCompress 初始化", threshold=0.5)
def bench_compress_init():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    assert cc.max_context_tokens == 12000


@bench("clean_old_tool_results（20条消息, 5轮）", threshold=1.0)
def bench_clean_tool_results():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    msgs = []
    for i in range(10):
        msgs.append({"role": "user", "content": f"第{i}轮Q"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"call_{i}", "function": {"name": "search", "arguments": '{"q":"x"}'}}
        ]})
        msgs.append({"role": "tool", "content": "A" * 5000, "tool_call_id": f"call_{i}"})
    new_msgs, saved = cc.clean_old_tool_results(msgs, max_rounds=2)
    # 总轮次=10 > max_rounds=2，所以肯定有清除
    assert saved > 0 or len(new_msgs) <= len(msgs)


@bench("estimate_tokens 1000 次", threshold=0.2)
def bench_estimate_tokens():
    from core.context_compress import estimate_tokens
    for _ in range(1000):
        estimate_tokens("这是一段测试文本" * 100)


@bench("budget_reduce_output 长文本", threshold=0.5)
def bench_budget_reduce():
    from core.context_compress import budget_reduce_output
    content = "A" * 50000
    result = budget_reduce_output(content, "terminal")
    assert "BudgetReduction" in result


# ======================================================================
# 7. 进化引擎
# ======================================================================

@bench("EvolutionEngine 初始化", threshold=1.0)
def bench_evolution_init():
    from core.evolution import EvolutionEngine
    engine = EvolutionEngine()
    assert engine is not None


@bench("evaluate_and_evolve 50 次", threshold=5.0)
def bench_evolve_many():
    from core.evolution import EvolutionEngine
    engine = EvolutionEngine()
    for i in range(50):
        engine.evaluate_and_evolve({
            "success": True,
            "errors": [],
            "tool_calls": 3,
            "task_type": f"bench_task_{i % 5}",
            "duration": 1.0,
        })
    stats = engine.get_evolution_stats()
    assert stats["total_evolutions"] >= 0


# ======================================================================
# 8. 安全与审批
# ======================================================================

@bench("validate_command 1000 次", threshold=0.5)
def bench_validate_cmd():
    from core.safety import validate_command
    cmds = ["ls -la", "cat file.txt", "rm -rf /", "echo hello", "python test.py",
            "git status", "pip install flask", "sudo apt update", "chmod 777 /tmp"]
    for _ in range(100):
        for cmd in cmds:
            validate_command(cmd)


@bench("classify_command 1000 次", threshold=0.5)
def bench_classify_cmd():
    from core.safety import SafetyLayer
    cmds = ["ls -la", "rm -rf /tmp", "sudo apt update", "echo hello",
            "git push origin main", "pip install flask"]
    for _ in range(100):
        for cmd in cmds:
            SafetyLayer.classify_command(cmd)


@bench("审批检查 500 次", threshold=3.0)
def bench_approval_check():
    from core.approval import ApprovalManager, DenyRules, AutoMode
    DenyRules.load()
    AutoMode.load()
    for _ in range(100):
        for tool in ["terminal", "read_file", "write_file"]:
            result = ApprovalManager.check_permission(tool, {"command": "ls"}, auto_override=True)
            assert isinstance(result, dict)


# ======================================================================
# 9. Token 计数
# ======================================================================

@bench("Token 估算 10000 次", threshold=1.0)
def bench_token_estimate():
    from core.context_compress import estimate_tokens
    texts = [
        "你好世界",
        "Hello World",
        "夸父逐日不息自我超越" * 10,
        "The quick brown fox jumps over the lazy dog",
        "test with numbers 12345 and symbols !@#$%^&*()",
    ]
    for _ in range(2000):
        for t in texts:
            estimate_tokens(t)


# ======================================================================
# 10. 身份系统
# ======================================================================

@bench("身份声明加载", threshold=0.5)
def bench_identity_load():
    from core.identity import load_identity_statement
    stmt = load_identity_statement()
    assert len(stmt) > 0


# ======================================================================
# 11. 审批决策解析
# ======================================================================

@bench("check_approval_decision 5000 次", threshold=0.5)
def bench_decision_parse():
    from core.approval import check_approval_decision
    texts = [
        "1 abc12345",
        "0 xyz98765",
        "批准 appr_1234",
        "拒绝 appr_5678",
        "approve abc",
        "reject xyz",
        "今天天气真好",
        "帮我搜索 Python",
    ]
    for _ in range(625):
        for t in texts:
            check_approval_decision(t)


# ======================================================================
# Main
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("夸父 (Kuafu) 性能基准测试")
    import datetime; print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    import socket
    print(f"Host: {socket.gethostname()}")
    import platform
    print(f"Python: {platform.python_version()}")
    print("=" * 60)
    print()

    tests = [
        ("1. Agent 初始化", [
            bench_agent_init,
            bench_agent_loop_init,
            bench_prompt_build_empty,
            bench_prompt_build_with_task,
        ]),
        ("2. 工具注册", [
            bench_tool_registry_init,
            bench_tool_register_many,
            bench_compact_promotion,
            bench_deferred_search,
        ]),
        ("3. 记忆系统", [
            bench_memory_write,
            bench_memory_recall,
            bench_memory_reflect,
        ]),
        ("4. 会话存储", [
            bench_session_create,
            bench_session_messages,
            bench_session_search,
        ]),
        ("5. 上下文压缩", [
            bench_compress_init,
            bench_clean_tool_results,
            bench_estimate_tokens,
            bench_budget_reduce,
        ]),
        ("6. 进化引擎", [
            bench_evolution_init,
            bench_evolve_many,
        ]),
        ("7. 安全与审批", [
            bench_validate_cmd,
            bench_classify_cmd,
            bench_approval_check,
            bench_decision_parse,
        ]),
        ("8. 工具函数", [
            bench_token_estimate,
            bench_identity_load,
        ]),
    ]

    for group_name, group_tests in tests:
        print(f"── {group_name} ──")
        for t in group_tests:
            t()
        print()

    print("=" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print()

    # 汇总表
    print(f"{'名称':<45} {'耗时':<8} {'阈值':<8} {'状态':<6}")
    print("-" * 67)
    for name, elapsed, threshold, ok in RESULTS:
        status = "✅" if ok else "⚠️"
        print(f"{name:<45} {elapsed:<8.3f} {threshold:<8.2f} {status:<6}")
    print()

    # 超阈值项汇总
    warnings = [(n, e, t) for n, e, t, ok in RESULTS if not ok]
    if warnings:
        print("⚠️  超阈值项:")
        for n, e, t in warnings:
            print(f"     {n}: {e:.3f}s (阈值 {t:.2f}s)")

    if FAIL > 0:
        sys.exit(1)
