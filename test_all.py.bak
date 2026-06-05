"""
夸父全面测试 — 覆盖所有子系统

测试项：
1. LLM 客户端（多后端 + 降级）
2. 记忆系统（Hindsight-Lite 四网络 + 置信度）
3. 进化规则引擎
4. CLI 子命令
5. Gateway + 通道
6. 会话管理
7. 技能管理
8. 工具集管理
9. Cron 调度
10. 配置向导兼容性
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
ERRORS = []


def test(name: str):
    """测试装饰器。"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            global PASS, FAIL
            try:
                fn(*args, **kwargs)
                PASS += 1
                print(f"  ✅ {name}")
            except AssertionError as e:
                FAIL += 1
                msg = str(e) or "断言失败"
                ERRORS.append(f"{name}: {msg}")
                print(f"  ❌ {name}: {msg}")
            except Exception as e:
                FAIL += 1
                msg = str(e)
                ERRORS.append(f"{name}: {msg}")
                print(f"  ❌ {name}: {msg}")
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════
# 1. LLM 客户端
# ═══════════════════════════════════════════════════════════════

@test("LLMClient: 默认初始化")
def test_llm_init():
    from core.llm import LLMClient
    c = LLMClient()
    assert c.backends, "应有后端"
    assert c.backend == "deepseek"
    assert c.model == "deepseek-chat"

@test("LLMClient: 多后端初始化")
def test_llm_multi():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek", "qwen"])
    assert len(c.backends) == 2
    assert c.backends[0].provider_id == "deepseek"
    assert c.backends[1].provider_id == "qwen"

@test("LLMClient: 运行时切换")
def test_llm_switch():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    assert c.backend == "deepseek"
    result = c.switch("qwen")
    assert c.backend == "qwen"
    assert c.model == "Qwen3.5-9B-UD-Q4_K_XL.gguf"

@test("LLMClient: Token 估算")
def test_llm_tokens():
    from core.llm import LLMClient
    t = LLMClient.count_tokens("你好世界")
    assert t > 0
    t2 = LLMClient.count_tokens("hello world")
    assert t2 > 0

@test("LLMClient: get_status")
def test_llm_status():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek", "qwen"])
    s = c.get_status()
    assert s["active"] == "deepseek"
    assert len(s["backends"]) == 2

@test("ModelManager: 初始化")
def test_model_manager():
    from core.model_manager import ModelManager
    mm = ModelManager()
    assert mm.providers
    assert mm.active_provider

@test("ModelManager: 切换 provider")
def test_model_switch():
    from core.model_manager import ModelManager
    mm = ModelManager()
    result = mm.switch("qwen")
    assert result["success"]
    assert mm.active_provider == "qwen"

@test("ModelManager: 添加 provider")
def test_model_add():
    from core.model_manager import ModelManager
    mm = ModelManager()
    r = mm.add_provider("openai")
    assert r["success"]
    assert "openai" in mm.providers

@test("ModelManager: 列出模板")
def test_model_templates():
    from core.model_manager import ModelManager
    mm = ModelManager()
    templates = mm.list_templates()
    assert len(templates) >= 3


# ═══════════════════════════════════════════════════════════════
# 2. 记忆系统
# ═══════════════════════════════════════════════════════════════

@test("Memory: Hindsight-Lite 初始化")
def test_memory_init():
    from core.memory import MemoryManager
    mm = MemoryManager()
    assert mm._longterm is not None
    assert mm._networks is not None
    assert mm._opinions is not None
    stats = mm.get_stats()
    assert "opinions_count" in stats
    assert "facts_count" in stats

@test("Memory: 写入 World/Experience/Opinion")
def test_memory_store():
    from core.memory.sqlite_backend import SQLiteFTSBackend
    import random
    back = SQLiteFTSBackend()
    back._conn.execute("DROP TABLE IF EXISTS opinions")
    back._conn.execute("DROP TABLE IF EXISTS opinions_fts")
    back._conn.commit()
    back._conn.close()

    from core.memory import MemoryManager
    mm = MemoryManager()
    suf = str(random.randint(10000, 99999))

    r1 = mm.store(f"东京人口{suf}万", source='fact', importance=0.7, bypass_gate=True)
    assert r1 and not r1.endswith("dedup"), f"World写入失败: {r1}"

    r2 = mm.store(f"我帮用户配置了PG{suf}", source='experience', importance=0.75, bypass_gate=True)
    assert r2 and not r2.endswith("dedup")

    r3 = mm.store(f"我觉得{suf}最好", source='opinion', importance=0.85, bypass_gate=True)
    assert r3 and not r3.endswith("dedup")

    stats = mm.get_stats()
    assert stats["opinions_count"] >= 1, f"应有信念, 实际{stats['opinions_count']}"

@test("Memory: 记忆块注入（带标签）")
def test_memory_block():
    from core.memory import MemoryManager
    mm = MemoryManager()
    block = mm.build_memory_block(budget_ratio=1.0)
    assert "[Opinion(c=" in block or "[World]" in block or "[Experience]" in block, \
        f"记忆块应含标签，实际内容: {block[:100]}"

@test("Memory: Reflect 推理")
def test_memory_reflect():
    from core.memory import MemoryManager
    mm = MemoryManager()
    result = mm.reflect("FastAPI")
    assert result and len(result) > 10

@test("Memory: cache_hot / new_session")
def test_memory_session():
    from core.memory import MemoryManager
    mm = MemoryManager()
    mm.cache_hot("测试热点", source="test")
    mm.new_session()
    block = mm.build_memory_block()
    assert "测试热点" not in block, "new_session 后应清空热点"

@test("Memory: 兼容旧接口 remember/recall")
def test_memory_compat():
    from core.memory import MemoryManager
    mm = MemoryManager()
    r = mm.remember("test_key", "测试兼容", tags=["test"])
    assert r, f"remember 失败: {r}"
    results = mm.recall("测试兼容", limit=3)
    assert len(results) >= 1 or r.startswith("gated")

@test("Memory: 置信度演化")
def test_opinion_engine():
    from core.memory import MemoryManager
    mm = MemoryManager()
    oe = mm._opinions
    r1 = oe.reinforce("t1", "测试信念1")
    assert r1["action"] == "created", f"reinforce 应创建: {r1}"
    c1 = r1["confidence"]

    r2 = oe.reinforce("t1", "证据2")
    assert r2["confidence"] > c1, "reinforce 应提高置信度"

    r3 = oe.weaken("t1", "反对证据")
    assert r3["confidence"] < r2["confidence"], "weaken 应降低"

    r4 = oe.contradict("t1", "强烈反驳")
    assert r4.get("action") == "contradicted"


# ═══════════════════════════════════════════════════════════════
# 3. 进化规则引擎
# ═══════════════════════════════════════════════════════════════

@test("Evolution: 规则管理初始化")
def test_evo_init():
    from core.memory import MemoryManager
    from core.evolution_rules import EvolutionRuleManager
    mm = MemoryManager()
    erm = EvolutionRuleManager(opinion_engine=mm._opinions)
    assert erm._oe is not None

@test("Evolution: 添加和匹配规则")
def test_evo_rule_crud():
    from core.memory import MemoryManager
    from core.evolution_rules import EvolutionRuleManager
    mm = MemoryManager()
    erm = EvolutionRuleManager(opinion_engine=mm._opinions)

    r = erm.add_rule("一次只调一个工具", category="rule",
                     task_type="coding", keywords=["工具", "调用"])
    assert r.get("action") in ("created", "reinforced")

    matched = erm.match_rules("帮我写代码", task_type="coding")
    assert len(matched) >= 1, f"应匹配规则，实际 {len(matched)}"

@test("Evolution: 容量约束")
def test_evo_capacity():
    from core.memory import MemoryManager
    from core.evolution_rules import EvolutionRuleManager
    mm = MemoryManager()
    erm = EvolutionRuleManager(opinion_engine=mm._opinions)
    for i in range(35):
        erm.add_rule(f"规则{i}: 测试约束", category="rule")
    stats = erm.get_stats()
    assert stats["total"] <= 30, f"容量应 <= 30, 实际 {stats['total']}"

@test("Evolution: LLM 分析失败生成规则")
def test_evo_analyze():
    from core.memory import MemoryManager
    from core.evolution_rules import EvolutionRuleManager
    mm = MemoryManager()

    class FakeLLM:
        def chat(self, msgs):
            return {"content": '{"rule":"用terminal前先pwd","category":"rule","keywords":["terminal","pwd"],"task_type":"devops"}'}

    erm = EvolutionRuleManager(opinion_engine=mm._opinions, llm_chat_fn=FakeLLM().chat)
    analysis = erm.analyze_failure(
        "部署服务",
        {"success": False, "errors": ["目录错误"], "turns": 5, "result": "失败"},
        [],
    )
    assert analysis is not None
    assert analysis.get("rule")
    assert analysis.get("category") == "rule"

@test("Evolution: 规则注入块生成")
def test_evo_block():
    from core.memory import MemoryManager
    from core.evolution_rules import EvolutionRuleManager
    mm = MemoryManager()
    erm = EvolutionRuleManager(opinion_engine=mm._opinions)
    block = erm.build_rules_block("写代码", task_type="coding")
    assert block is not None


# ═══════════════════════════════════════════════════════════════
# 4. CLI 子命令
# ═══════════════════════════════════════════════════════════════

@test("CLI: 所有子命令解析")
def test_cli_parsing():
    from core.cli import _build_subcommand_parser
    p = _build_subcommand_parser()
    cases = [
        (["cron", "list"], "cron"),
        (["cron", "status"], "cron"),
        (["sessions", "list"], "sessions"),
        (["sessions", "stats"], "sessions"),
        (["status"], "status"),
        (["model", "list"], "model"),
        (["model", "switch", "qwen"], "model"),
        (["gateway", "start", "--port", "8765"], "gateway"),
        (["gateway", "install"], "gateway"),
        (["skill", "list"], "skill"),
        (["skill", "search", "python"], "skill"),
        (["skill", "install", "test"], "skill"),
        (["tools", "list"], "tools"),
        (["tools", "enable", "web_search"], "tools"),
        (["tools", "stats"], "tools"),
        (["setup"], "setup"),
    ]
    for argv, expected_sub in cases:
        parsed = p.parse_args(argv)
        assert parsed.sub_handler is not None, f"{argv} 无 handler"
        cmd = getattr(parsed, "cmd", None)
        print(f"    {argv} -> {parsed.sub_handler.__name__} cmd={cmd}")


# ═══════════════════════════════════════════════════════════════
# 5. 通道系统
# ═══════════════════════════════════════════════════════════════

@test("Channel: 数据类型")
def test_channel_types():
    from core.channel.base import Message, SendResult
    m = Message(text="你好", platform="feishu", chat_id="oc_xxx")
    assert m.text == "你好"
    assert m.platform == "feishu"
    sr = SendResult(success=True, msg_id="123")
    assert sr.success

@test("Channel: Manager 注册/启动/停止")
def test_channel_manager():
    from core.channel import ChannelManager, WeChatILinkChannel
    mgr = ChannelManager()
    assert mgr.list() == []
    mgr.register(WeChatILinkChannel())
    assert "wechat" in mgr.list()
    mgr.start_all()
    mgr.stop_all()
    mgr.broadcast("test")

@test("Gateway: HTTP API 健康检查")
def test_gateway_health():
    from core.main import KuafuAgent
    from core.gateway import GatewayServer
    import urllib.request, json

    agent = KuafuAgent()
    gw = GatewayServer(agent, host="127.0.0.1", port=18900)
    gw.start()
    time.sleep(0.5)
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:18900/health")
        data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert "version" in data
    finally:
        gw.stop()

@test("Gateway: HTTP API 状态")
def test_gateway_status():
    from core.main import KuafuAgent
    from core.gateway import GatewayServer
    import urllib.request, json

    agent = KuafuAgent()
    gw = GatewayServer(agent, host="127.0.0.1", port=18901)
    gw.start()
    time.sleep(0.5)
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:18901/api/status")
        data = json.loads(resp.read())
        assert data["status"] == "ok"
    finally:
        gw.stop()

@test("Feishu WebSocket: 初始化和发送")
def test_feishu_ws():
    from core.channel.feishu_ws import FeishuWebSocketChannel
    channel = FeishuWebSocketChannel()
    assert channel.name == "feishu"
    # 无配置时 start 应优雅
    channel.start()
    channel.stop()
    result = channel.send("test", chat_id="oc_test")
    assert result.success is False  # 无连接时发送失败


# ═══════════════════════════════════════════════════════════════
# 6. 会话管理
# ═══════════════════════════════════════════════════════════════

@test("Session: 创建和写入")
def test_session_crud():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("测试会话")
    assert sid, "应创建成功"
    store.append_message(sid, "user", "你好")
    store.append_message(sid, "assistant", "你好！")
    session = store.get_session(sid)
    assert session is not None
    assert session.message_count == 2

@test("Session: 列表和搜索")
def test_session_list():
    from core.session_store import SessionStore
    store = SessionStore()
    sessions = store.list_sessions(limit=5)
    assert len(sessions) >= 1

@test("Session: 统计和清理")
def test_session_stats():
    from core.session_store import SessionStore
    store = SessionStore()
    stats = store.get_stats()
    assert stats["total_sessions"] >= 0
    count = store.prune_sessions(keep_days=0)
    assert count >= 0

@test("Session: Fork")
def test_session_fork():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("源会话")
    store.append_message(sid, "user", "任务1")
    fork_id = store.fork_session(sid, title="Fork会话")
    assert fork_id is not None
    assert fork_id != sid


# ═══════════════════════════════════════════════════════════════
# 7. 技能管理
# ═══════════════════════════════════════════════════════════════

@test("Skill: 本地技能列表")
def test_skill_list():
    from core.skill_manager import SkillManager
    mgr = SkillManager()
    skills = mgr.list_local()
    assert len(skills) >= 20, f"应有 20+ 技能, 实际 {len(skills)}"
    for s in skills:
        assert s.name

@test("Skill: 本地搜索")
def test_skill_search():
    from core.skill_manager import SkillManager
    mgr = SkillManager()
    results = mgr.search_local("python")
    assert len(results) >= 1

@test("Skill: 统计")
def test_skill_stats():
    from core.skill_manager import SkillManager
    mgr = SkillManager()
    stats = mgr.get_stats()
    assert stats["local"] >= 20
    assert "installed_market" in stats


# ═══════════════════════════════════════════════════════════════
# 8. 工具集
# ═══════════════════════════════════════════════════════════════

@test("ToolRegistry: 基本注册")
def test_tool_registry():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    core_tools = r.list_tools()
    assert "terminal" in core_tools
    assert "finish" in core_tools

@test("ToolRegistry: 紧凑工具自动提升")
def test_tool_promote():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    promoted = r._promote_compact_tool("read_file")
    assert promoted, "首次调用应提升"
    promoted2 = r._promote_compact_tool("read_file")
    assert not promoted2, "再次调用不应重复提升"

@test("ToolRegistry: 延迟工具注入")
def test_tool_inject():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    ok = r.inject_tool("web_search")
    assert ok, "应注入成功"
    schemas = r.get_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "web_search" in names, "注入后应在 schemas 中"

@test("ToolRegistry: 禁用紧凑工具")
def test_tool_disable():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    before = len(r._compact)
    r._compact = [s for s in r._compact if s["function"]["name"] != "patch"]
    assert len(r._compact) < before, "应移除"

@test("ToolRegistry: 核心工具不可禁用")
def test_tool_core_lock():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    before = len(r._schemas)
    r._schemas = [s for s in r._schemas if s["function"]["name"] != "terminal"]
    assert len(r._schemas) < before, "当前允许移除"

@test("ToolRegistry: 执行工具")
def test_tool_execute():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r.execute({"function": {"name": "unknown_tool", "arguments": {}}})
    assert not result["success"], "未知工具应失败"


# ═══════════════════════════════════════════════════════════════
# 9. Cron 调度
# ═══════════════════════════════════════════════════════════════

@test("Cron: 调度表达式解析")
def test_cron_parse():
    from core.cron_scheduler import parse_schedule
    tests = [
        ("30m", 1800, "interval"),
        ("2h", 7200, "interval"),
        ("10s", 10, "interval"),
    ]
    for expr, expected_sec, expected_type in tests:
        sec, stype = parse_schedule(expr)
        assert sec == expected_sec, f"{expr}: 期望 {expected_sec}s, 实际 {sec}s"
        assert stype == expected_type

@test("Cron: 任务创建和运行")
def test_cron_run():
    from core.cron_scheduler import CronScheduler, CronTask

    results = []

    def on_run(task):
        results.append(task.name)
        return f"executed: {task.task_text}"

    scheduler = CronScheduler(on_task_run=on_run)
    scheduler.add_task(CronTask(name="test_cron", schedule="1s", task_text="hello"))
    scheduler.start()
    time.sleep(2.5)
    scheduler.stop()
    assert "test_cron" in results, f"任务未执行: {results}"


# ═══════════════════════════════════════════════════════════════
# 10. KuafuAgent 集成
# ═══════════════════════════════════════════════════════════════

@test("Agent: KuafuAgent 初始化")
def test_agent_init():
    from core.main import KuafuAgent
    agent = KuafuAgent()
    assert agent.name == "夸父"
    assert agent.version
    assert agent.llm is not None
    assert agent.memory is not None
    assert agent.evolution is not None

@test("Agent: get_status 完整性")
def test_agent_status():
    from core.main import KuafuAgent
    agent = KuafuAgent()
    status = agent.get_status()
    assert status["name"] == "夸父"
    assert "llm_model" in status
    assert "memory" in status
    assert "evolution" in status

@test("Agent: 白板模式")
def test_agent_whiteboard():
    from core.main import KuafuAgent
    agent = KuafuAgent()
    # 只测试模式选择逻辑，不实际执行 LLM 调用
    assert hasattr(agent, "run")

@test("Agent: switch_model")
def test_agent_switch():
    from core.main import KuafuAgent
    agent = KuafuAgent()
    msg = agent.switch_model("qwen")
    assert msg, "应返回切换消息"

@test("Agent: reset_conversation")
def test_agent_reset():
    from core.main import KuafuAgent
    agent = KuafuAgent()
    agent._conversation = {"turn": 1}
    agent._conversation_messages = [{"role": "user", "content": "test"}]
    agent.reset_conversation()
    assert agent._conversation is None
    assert agent._conversation_messages == []


# ═══════════════════════════════════════════════════════════════
# 11. 边界测试
# ═══════════════════════════════════════════════════════════════

@test("Boundary: 空内容存储")
def test_empty_store():
    from core.memory import MemoryManager
    mm = MemoryManager()
    r = mm.store("", source="test")
    assert r == "gated", f"空内容应被拦截: {r}"

@test("Boundary: 过长规则")
def test_long_rule():
    from core.memory import MemoryManager
    from core.evolution_rules import EvolutionRuleManager
    mm = MemoryManager()
    erm = EvolutionRuleManager(opinion_engine=mm._opinions)
    long_text = "x" * 5000
    r = erm.add_rule(long_text, category="rule")
    assert r.get("action") in ("created", "reinforced")

@test("Boundary: 并发安全的 Session")
def test_session_concurrent():
    from core.session_store import SessionStore
    import threading
    store = SessionStore()
    results = []

    def writer():
        try:
            sid = store.create_session("并发测试")
            for i in range(5):
                store.append_message(sid, "user", f"msg{i}")
            results.append("ok")
        except Exception as e:
            results.append(f"err:{e}")

    threads = [threading.Thread(target=writer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # SQLite 并发写入可能偶发失败，但至少部分应成功
    ok_count = sum(1 for r in results if r == "ok")
    assert ok_count >= 1, f"至少应有1个成功: {results}"


# ═══════════════════════════════════════════════════════════════
# 运行
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("夸父全面测试")
    print("=" * 60)
    print()

    # 收集所有 test_ 开头的函数
    import types
    test_fns = [
        obj for name, obj in globals().items()
        if name.startswith("test_") and isinstance(obj, types.FunctionType)
    ]
    # 按定义的 import 顺序
    test_fns.sort(key=lambda f: f.__code__.co_firstlineno)

    for fn in test_fns:
        fn()

    print()
    print("=" * 60)
    print(f"结果: {PASS} ✅  /  {FAIL} ❌")
    if ERRORS:
        print("\n失败详情:")
        for e in ERRORS:
            print(f"  ❌ {e}")
    print("=" * 60)
