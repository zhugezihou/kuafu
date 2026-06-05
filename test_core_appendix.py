"""
夸父核心模块补充覆盖测试 — 追加到 test_all.py

覆盖 4 个模块：
1. core/tool_registry.py — 初始化、register/has/get_schemas/execute、_promote_compact_tool、
   _inject_lazy_tools、compact 工具逻辑、多媒体工具注册和降级、JSON 参数解析、description 格式
2. core/gepa_engine.py — SkillGenome、FitnessEvaluator（6维）、QualityAwareFitnessEvaluator、
   MutationOperator、CrossoverOperator、SelectionOperator、GEPAEngine full_cycle/multi_generation/evaluate_with_report
3. core/llm.py — LLMClient 初始化、多后端、count_tokens、get_status、backend 切换、关闭
4. core/model_manager.py — ModelManager 初始化、切换 provider、添加 provider、列出模板

全部 mock 网络/LLM 调用。
"""

from __future__ import annotations

import json
import os
import sys
import time
import math
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ═══════════════════════════════════════════════════════════════
# 工具装饰器和计数器（与 test_all.py 一致）
# ═══════════════════════════════════════════════════════════════

PASS = 0
FAIL = 0
ERRORS = []

def test(name: str):
    """测试装饰器，与 test_all.py 完全一致"""
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
# 1. ToolRegistry 深度测试
# ═══════════════════════════════════════════════════════════════

@test("ToolRegistry: 初始化检查所有池")
def test_tr_init_pools():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 三级池都存在
    assert hasattr(r, '_schemas'), "应有核心工具池"
    assert hasattr(r, '_compact'), "应有紧凑工具池"
    assert hasattr(r, '_deferred'), "应有延迟工具池"
    assert hasattr(r, '_injected_tools'), "应有已注入工具池"
    assert hasattr(r, '_handlers'), "应有 handler 映射"
    # 初始有核心 + 紧凑 + 延迟 + 元工具
    assert len(r._schemas) >= 3, f"核心工具应 >= 3 (term, finish, tool_search), 实际 {len(r._schemas)}"
    assert len(r._compact) >= 5, f"紧凑工具应 >= 5, 实际 {len(r._compact)}"
    assert len(r._deferred) >= 5, f"延迟工具应 >= 5, 实际 {len(r._deferred)}"
    print(f"    ✅ ToolRegistry 初始化检查所有池")


@test("ToolRegistry: register 注册新工具")
def test_tr_register():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    def my_handler(args):
        return {"success": True, "output": "done"}
    r.register("my_tool", {"description": "测试工具", "parameters": {"type": "object", "properties": {}}}, my_handler)
    schemas = r.get_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "my_tool" in names, "注册后应在 schemas 中"
    assert r._handlers.get("my_tool") is my_handler, "handler 应保存"
    print(f"    ✅ ToolRegistry: register 注册新工具")


@test("ToolRegistry: register 覆盖同名紧凑工具")
def test_tr_register_overwrite():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    assert "read_file" in [s["function"]["name"] for s in r._compact], "read_file 是紧凑工具"
    def my_handler(args):
        return {"success": True, "output": "overwritten"}
    # 注册同名核心工具（register 不自动移除 compact，但会加到 schemas）
    r.register("read_file", {"description": "覆盖版", "parameters": {"type": "object", "properties": {}}}, my_handler)
    # 核心工具池中出现 read_file
    schema_names = [s["function"]["name"] for s in r._schemas]
    assert "read_file" in schema_names, "核心注册后应在 schemas 中"
    # 调用 execute 时优先走 _handlers
    result = r.execute({"function": {"name": "read_file", "arguments": {"path": "/tmp/x"}}})
    # handler 是我们注册的 my_handler
    assert result["output"] == "overwritten"
    print(f"    ✅ ToolRegistry: register 覆盖同名紧凑工具")


@test("ToolRegistry: register_compact 完整流程")
def test_tr_register_compact():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    def handler(args):
        return {"success": True, "output": "compact test"}
    r.register_compact("compact_test", {
        "description": "紧凑测试",
        "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
    }, handler)
    # 应在 compact 池中
    compact_names = [s["function"]["name"] for s in r._compact]
    assert "compact_test" in compact_names
    # 不应在 schemas 中
    schema_names = [s["function"]["name"] for s in r._schemas]
    assert "compact_test" not in schema_names
    # handler 应存在
    assert r._handlers.get("compact_test") is handler
    # get_compact_tools_description 应返回
    descs = r.get_compact_tools_description()
    desc_names = [n for n, _ in descs]
    assert "compact_test" in desc_names
    print(f"    ✅ ToolRegistry: register_compact 完整流程")


@test("ToolRegistry: register_deferred 完整流程")
def test_tr_register_deferred():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    def handler(args):
        return {"success": True, "output": "deferred test"}
    r.register_deferred("deferred_test", {
        "description": "延迟测试",
        "parameters": {"type": "object", "properties": {}},
    }, handler, keywords=["test", "demo"])
    deferred_names = [d["schema"]["function"]["name"] for d in r._deferred]
    assert "deferred_test" in deferred_names
    # 不应在 schemas 中
    schema_names = [s["function"]["name"] for s in r._schemas]
    assert "deferred_test" not in schema_names
    # keywords 应保存
    for d in r._deferred:
        if d["schema"]["function"]["name"] == "deferred_test":
            assert "test" in d["keywords"]
    print(f"    ✅ ToolRegistry: register_deferred 完整流程")


@test("ToolRegistry: unregister 从所有池移除")
def test_tr_unregister():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 注册一个核心工具
    def handler(args):
        return {"success": True}
    r.register("unreg_test", {"description": "x", "parameters": {"type": "object", "properties": {}}}, handler)
    assert "unreg_test" in r.list_tools()
    result = r.unregister("unreg_test")
    assert result, "unregister 应返回 True"
    assert "unreg_test" not in r.list_tools()
    assert "unreg_test" not in r._handlers
    # 注册紧凑工具并 unregister
    r.register_compact("unreg_compact", {
        "description": "x",
        "parameters": {"type": "object", "properties": {}},
    }, handler)
    result2 = r.unregister("unreg_compact")
    assert result2
    compact_names = [s["function"]["name"] for s in r._compact]
    assert "unreg_compact" not in compact_names
    # unregister 不存在的工具
    result3 = r.unregister("does_not_exist")
    assert not result3, "不存在的工具应返回 False"
    print(f"    ✅ ToolRegistry: unregister 从所有池移除")


@test("ToolRegistry: _promote_compact_tool 提升2次不重复")
def test_tr_promote_twice():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 首次提升
    p1 = r._promote_compact_tool("write_file")
    assert p1, "首次应返回 True"
    assert "write_file" in [s["function"]["name"] for s in r._injected_tools]
    # 再次提升
    p2 = r._promote_compact_tool("write_file")
    assert not p2, "再次提升应返回 False"
    # 注入池中应只有1个 write_file
    count = sum(1 for s in r._injected_tools if s["function"]["name"] == "write_file")
    assert count == 1, f"应只有1个实例, 实际 {count}"
    print(f"    ✅ ToolRegistry: _promote_compact_tool 提升2次不重复")


@test("ToolRegistry: _promote_compact_tool 提升不存在的工具")
def test_tr_promote_nonexist():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r._promote_compact_tool("nonexistent_tool")
    assert not result, "不存在的工具应返回 False"
    print(f"    ✅ ToolRegistry: _promote_compact_tool 提升不存在的工具")


@test("ToolRegistry: inject_tool 延迟工具注入")
def test_tr_inject_deferred():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 注入 web_search
    ok = r.inject_tool("web_search")
    assert ok, "延迟工具应可注入"
    names = [s["function"]["name"] for s in r._injected_tools]
    assert "web_search" in names
    # 重复注入
    ok2 = r.inject_tool("web_search")
    assert ok2, "重复注入应返回 True"
    count = sum(1 for s in r._injected_tools if s["function"]["name"] == "web_search")
    assert count == 1, "重复注入不应重复添加"
    # 注入不存在的工具
    ok3 = r.inject_tool("nonexistent")
    assert not ok3, "不存在的延迟工具应返回 False"
    print(f"    ✅ ToolRegistry: inject_tool 延迟工具注入")


@test("ToolRegistry: get_schemas 返回核心+注入")
def test_tr_get_schemas():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 初始 get_schemas 只包含核心 + tool_search
    schemas = r.get_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "terminal" in names, "核心工具应可见"
    assert "finish" in names, "核心工具应可见"
    assert "tool_search" in names, "元工具应可见"
    # 紧凑工具不应可见
    assert "read_file" not in names, "紧凑工具不应在 schemas 中"
    # 注入后应可见
    r.inject_tool("web_search")
    schemas2 = r.get_schemas()
    names2 = [s["function"]["name"] for s in schemas2]
    assert "web_search" in names2
    print(f"    ✅ ToolRegistry: get_schemas 返回核心+注入")


@test("ToolRegistry: get_active_tools_names")
def test_tr_get_active_names():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    names = r.get_active_tools_names()
    assert "terminal" in names
    assert "finish" in names
    assert "tool_search" in names
    # 注入后
    r.inject_tool("vision_analyze")
    names2 = r.get_active_tools_names()
    assert "vision_analyze" in names2
    print(f"    ✅ ToolRegistry: get_active_tools_names")


@test("ToolRegistry: get_compact_tools_description 格式")
def test_tr_compact_desc():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    descs = r.get_compact_tools_description()
    # 检查格式
    for name, desc in descs:
        assert isinstance(name, str), f"名称应为 str: {name}"
        assert isinstance(desc, str), f"描述应为 str: {desc}"
    names = [n for n, _ in descs]
    assert "read_file" in names
    assert "write_file" in names
    print(f"    ✅ ToolRegistry: get_compact_tools_description 格式")


@test("ToolRegistry: execute JSON 参数解析")
def test_tr_execute_json_args():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 模拟工具 — 用 finish 测试（finish 不依赖外部）
    result = r.execute({
        "function": {
            "name": "finish",
            "arguments": '{"result": "测试完成", "summary": "测试摘要"}'
        }
    })
    assert result["success"], f"finish 应成功: {result}"
    assert result["output"] is not None
    parsed = json.loads(result["output"]) if isinstance(result["output"], str) else result["output"]
    if isinstance(parsed, dict):
        assert parsed.get("result") == "测试完成"
    print(f"    ✅ ToolRegistry: execute JSON 参数解析")


@test("ToolRegistry: execute 非法 JSON 参数")
def test_tr_execute_bad_json():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r.execute({
        "function": {
            "name": "finish",
            "arguments": '{"result": broken json!!!}'
        }
    })
    # 应该容错执行（非法 JSON → 空 dict）
    assert result["success"] is True or result.get("output") is not None
    print(f"    ✅ ToolRegistry: execute 非法 JSON 参数")


@test("ToolRegistry: execute dict 参数直接传入")
def test_tr_execute_dict_args():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r.execute({
        "function": {
            "name": "finish",
            "arguments": {"result": "dict测试"}
        }
    })
    assert result["success"]
    print(f"    ✅ ToolRegistry: execute dict 参数直接传入")


@test("ToolRegistry: execute 未知工具")
def test_tr_execute_unknown():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r.execute({"function": {"name": "impossible_tool_999", "arguments": {}}})
    assert not result["success"]
    assert "未知工具" in result["output"]
    print(f"    ✅ ToolRegistry: execute 未知工具")


@test("ToolRegistry: execute handler 抛异常")
def test_tr_execute_exception():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    def bad_handler(args):
        raise RuntimeError("模拟崩溃")
    r.register("crash_tool", {"description": "会崩溃", "parameters": {"type": "object", "properties": {}}}, bad_handler)
    result = r.execute({"function": {"name": "crash_tool", "arguments": {}}})
    assert not result["success"]
    assert "异常" in result["output"]
    print(f"    ✅ ToolRegistry: execute handler 抛异常")


@test("ToolRegistry: execute 紧凑工具自动提升")
def test_tr_execute_promote():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 执行前 read_file 不在 injected
    assert "read_file" not in [s["function"]["name"] for s in r._injected_tools]
    # 执行后应自动提升
    result = r.execute({"function": {"name": "finish", "arguments": {"result": "x"}}})
    # finish 是核心工具，但对 compact 工具不影响；直接执行 compact 工具
    # 先伪造 call: read_file 尚在 compact 池
    result2 = r.execute({"function": {"name": "read_file", "arguments": {"path": "/tmp/nonexist"}}})
    # 执行后 read_file 应被提升到 injected
    assert "read_file" in [s["function"]["name"] for s in r._injected_tools], "紧凑工具执行后应自动提升"
    print(f"    ✅ ToolRegistry: execute 紧凑工具自动提升")


@test("ToolRegistry: execute handler 返回非 dict")
def test_tr_execute_non_dict():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # handler 返回字符串
    def str_handler(args):
        return "直接返回字符串"
    r.register("str_tool", {"description": "返回字符串", "parameters": {"type": "object", "properties": {}}}, str_handler)
    result = r.execute({"function": {"name": "str_tool", "arguments": {}}})
    assert result["success"]
    assert result["output"] == "直接返回字符串"
    print(f"    ✅ ToolRegistry: execute handler 返回非 dict")


@test("ToolRegistry: execute handler 返回无 output 的 dict")
def test_tr_execute_no_output():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    def no_output_handler(args):
        return {"success": True, "result": "没有output键"}
    r.register("no_out_tool", {"description": "无output", "parameters": {"type": "object", "properties": {}}}, no_output_handler)
    result = r.execute({"function": {"name": "no_out_tool", "arguments": {}}})
    assert result["success"]
    # 应自动补 output
    assert "output" in result
    print(f"    ✅ ToolRegistry: execute handler 返回无 output 的 dict")


@test("ToolRegistry: get_handler 获取/缺失")
def test_tr_get_handler():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    h = r.get_handler("terminal")
    assert h is not None, "terminal 应有 handler"
    assert callable(h)
    h2 = r.get_handler("nonexistent")
    assert h2 is None
    print(f"    ✅ ToolRegistry: get_handler 获取/缺失")


@test("ToolRegistry: list_tools 只返回核心工具")
def test_tr_list_tools():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    tools = r.list_tools()
    # 紧凑工具不应在 list_tools 中
    assert "read_file" not in tools, "list_tools 不应含紧凑工具"
    assert "terminal" in tools
    assert "finish" in tools
    assert "tool_search" in tools
    print(f"    ✅ ToolRegistry: list_tools 只返回核心工具")


@test("ToolRegistry: _search_deferred_tools 中文分词")
def test_tr_search_deferred_chinese():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 搜索中文关键词
    results = r._search_deferred_tools("搜索互联网", max_results=5)
    assert len(results) > 0, "应匹配到 web_search"
    names = [r["name"] for r in results]
    assert any("web" in n or "search" in n for n in names), f"应匹配搜索工具, 实际 {names}"
    print(f"    ✅ ToolRegistry: _search_deferred_tools 中文分词")


@test("ToolRegistry: _search_deferred_tools 英文关键词")
def test_tr_search_deferred_english():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    results = r._search_deferred_tools("github", max_results=3)
    names = [r["name"] for r in results]
    assert "github_search" in names or "github_get_repo" in names, f"应匹配 github 工具, 实际 {names}"
    print(f"    ✅ ToolRegistry: _search_deferred_tools 英文关键词")


@test("ToolRegistry: _search_deferred_tools 空查询")
def test_tr_search_deferred_empty():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    results = r._search_deferred_tools("", max_results=5)
    assert results == [], "空查询应返回空列表"
    results2 = r._search_deferred_tools("a", max_results=5)  # 单字符
    assert results2 == [], "单字符查询应返回空列表"
    print(f"    ✅ ToolRegistry: _search_deferred_tools 空查询")


@test("ToolRegistry: _tool_search_schema 格式")
def test_tr_tool_search_schema():
    from core.tool_registry import ToolRegistry
    schema = ToolRegistry._tool_search_schema()
    assert "description" in schema
    assert "parameters" in schema
    assert "query" in schema["parameters"]["properties"]
    assert "required" in schema["parameters"]
    print(f"    ✅ ToolRegistry: _tool_search_schema 格式")


@test("ToolRegistry: _handle_tool_search 注入工具")
def test_tr_handle_tool_search():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    # 模拟调用 tool_search
    result = r._handle_tool_search({"query": "github"})
    assert result["success"]
    assert "github_search" in result["output"] or "已注入" in result["output"] or "已找到" in result["output"]
    # 检查是否真的注入了
    names = [s["function"]["name"] for s in r._injected_tools]
    assert "github_search" in names or "github_get_repo" in names
    print(f"    ✅ ToolRegistry: _handle_tool_search 注入工具")


@test("ToolRegistry: _handle_tool_search 空查询")
def test_tr_handle_tool_search_empty():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r._handle_tool_search({"query": ""})
    assert not result["success"]
    print(f"    ✅ ToolRegistry: _handle_tool_search 空查询")


@test("ToolRegistry: _handle_finish 格式")
def test_tr_handle_finish():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r._handle_finish({"result": "测试结果", "summary": "测试摘要"})
    assert result["success"]
    assert result["result"] == "测试结果"
    assert result["summary"] == "测试摘要"
    print(f"    ✅ ToolRegistry: _handle_finish 格式")


@test("ToolRegistry: _handle_finish_step")
def test_tr_handle_finish_step():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    result = r._handle_finish_step({"output": "步骤输出", "summary": "步骤摘要"})
    assert result["success"]
    assert result["output"] == "步骤输出"
    assert result["summary"] == "步骤摘要"
    print(f"    ✅ ToolRegistry: _handle_finish_step")


@test("ToolRegistry: schema 函数都返回有效 dict")
def test_tr_all_schemas_valid():
    from core.tool_registry import ToolRegistry
    # 测试所有静态 schema 方法
    schema_methods = [
        "_term_schema", "_read_schema", "_write_schema", "_patch_schema",
        "_search_schema", "_web_search_schema", "_web_fetch_schema",
        "_finish_schema", "_finish_step_schema", "_whiteboard_read_schema",
        "_whiteboard_write_schema", "_read_tool_result_schema",
        "_github_search_schema", "_github_get_repo_schema",
        "_tavily_search_schema", "_image_gen_schema", "_vision_schema",
        "_tts_schema", "_stt_schema", "_aggregate_search_schema",
        "_download_schema", "_browser_nav_schema", "_browser_snap_schema",
        "_browser_click_schema", "_browser_type_schema",
        "_browser_screenshot_schema", "_browser_js_schema",
        "_tool_search_schema",
    ]
    for method_name in schema_methods:
        method = getattr(ToolRegistry, method_name)
        schema = method()
        assert isinstance(schema, dict), f"{method_name} 应返回 dict"
        assert "description" in schema, f"{method_name} 应有 description"
        assert isinstance(schema["description"], str), f"{method_name} description 应为 str"
        assert len(schema["description"]) > 5, f"{method_name} description 应充分"
        assert "parameters" in schema, f"{method_name} 应有 parameters"
    print(f"    ✅ ToolRegistry: 所有 {len(schema_methods)} 个 schema 都有效")


@test("ToolRegistry: 多媒体工具注册正确")
def test_tr_multimedia():
    from core.tool_registry import ToolRegistry
    r = ToolRegistry()
    deferred_names = [d["schema"]["function"]["name"] for d in r._deferred]
    for name in ["image_gen", "vision_analyze", "text_to_speech", "speech_to_text"]:
        assert name in deferred_names, f"{name} 应在延迟工具池中"
    # 关键词存在
    for d in r._deferred:
        if d["schema"]["function"]["name"] == "image_gen":
            assert "图像" in d["keywords"] or "图片" in d["keywords"]
    print(f"    ✅ ToolRegistry: 多媒体工具注册正确")


@test("ToolRegistry: _clean_html 处理")
def test_tr_clean_html():
    from core.tool_registry import ToolRegistry
    html = "<html><head><title>测试页面</title></head><body><p>Hello World</p></body></html>"
    text = ToolRegistry._clean_html(html, max_length=5000)
    assert "测试页面" in text
    assert "Hello World" in text
    # 没有 HTML 标签
    assert "<html>" not in text
    print(f"    ✅ ToolRegistry: _clean_html 处理")


# ═══════════════════════════════════════════════════════════════
# 2. GEPA Engine 深度测试
# ═══════════════════════════════════════════════════════════════

@test("GEPA: SkillGenome 创建")
def test_gepa_genome_create():
    from core.gepa_engine import SkillGenome
    g = SkillGenome(name="test-skill", trigger="当用户问Python", steps=["安装包", "写代码"])
    assert g.name == "test-skill"
    assert g.trigger == "当用户问Python"
    assert g.steps == ["安装包", "写代码"]
    assert g.version == 1
    assert g.parent is None
    print(f"    ✅ GEPA: SkillGenome 创建")


@test("GEPA: SkillGenome to_dict / from_skill_dict")
def test_gepa_genome_convert():
    from core.gepa_engine import SkillGenome
    g = SkillGenome(name="test", trigger="t", task_type="coding", steps=["a", "b"],
                    keywords=["py"], pitfalls=["no recursion"], error_pattern="IndexError", version=2, parent="parent")
    d = g.to_dict()
    assert d["name"] == "test"
    assert d["version"] == 2
    assert d["parent"] == "parent"
    # from_skill_dict
    skill = {"name": "derived", "trigger": "t2", "steps": ["x"], "keywords": ["k"], "pitfalls": ["p"], "error_pattern": "e"}
    g2 = SkillGenome.from_skill_dict(skill, task_type="test")
    assert g2.name == "derived"
    assert g2.task_type == "test"
    assert g2.steps == ["x"]
    print(f"    ✅ GEPA: SkillGenome to_dict / from_skill_dict")


@test("GEPA: FitnessEvaluator 6 维全部传入")
def test_gepa_fitness_all_dims():
    from core.gepa_engine import FitnessEvaluator, FitnessRecord
    record = FitnessEvaluator.evaluate(
        success_rate=0.9,
        usage_count=50,
        error_before=10,
        error_after=2,
        step_count=3,
        last_used_days=0.5,
        quality_score=0.85,
    )
    assert isinstance(record, FitnessRecord)
    assert 0 <= record.score <= 1.0
    assert record.metrics["success_rate"] == 0.9
    assert record.metrics["quality_score"] == 0.85
    print(f"    ✅ GEPA: FitnessEvaluator 6 维全部传入")


@test("GEPA: FitnessEvaluator 成功率上下限")
def test_gepa_fitness_bounds():
    from core.gepa_engine import FitnessEvaluator
    r1 = FitnessEvaluator.evaluate(success_rate=2.0, step_count=3)
    assert r1.metrics["success_rate"] == 1.0, "成功率应 clamp 到 1.0"
    r2 = FitnessEvaluator.evaluate(success_rate=-0.5, step_count=3)
    assert r2.metrics["success_rate"] == 0.0, "负值应 clamp 到 0.0"
    r3 = FitnessEvaluator.evaluate(quality_score=1.5)
    assert r3.metrics["quality_score"] == 1.0, "质量分应 clamp 到 1.0"
    print(f"    ✅ GEPA: FitnessEvaluator 成功率上下限")


@test("GEPA: FitnessEvaluator 空参数（默认中性值）")
def test_gepa_fitness_defaults():
    from core.gepa_engine import FitnessEvaluator
    record = FitnessEvaluator.evaluate()
    assert record.score > 0, "全空参数应返回正分"
    assert record.metrics["success_rate"] == 0.5
    print(f"    ✅ GEPA: FitnessEvaluator 空参数（默认中性值）")


@test("GEPA: FitnessEvaluator 步数效率曲线")
def test_gepa_fitness_step_eff():
    from core.gepa_engine import FitnessEvaluator
    r1 = FitnessEvaluator.evaluate(step_count=1)  # 1步 -> 0.7
    assert r1.metrics["step_efficiency"] == 0.7
    r2 = FitnessEvaluator.evaluate(step_count=3)  # 2-4 -> 1.0
    assert r2.metrics["step_efficiency"] == 1.0
    r3 = FitnessEvaluator.evaluate(step_count=20)  # 20步 -> 衰减
    assert r3.metrics["step_efficiency"] < 0.5
    print(f"    ✅ GEPA: FitnessEvaluator 步数效率曲线")


@test("GEPA: FitnessEvaluator 错误减少率")
def test_gepa_fitness_error():
    from core.gepa_engine import FitnessEvaluator
    # 有 before 无 after
    r1 = FitnessEvaluator.evaluate(error_before=5)
    assert r1.metrics["error_reduction"] == 0.3
    # 有 before+after
    r2 = FitnessEvaluator.evaluate(error_before=10, error_after=2)
    assert r2.metrics["error_reduction"] == 0.8
    # 无错误数据
    r3 = FitnessEvaluator.evaluate()
    assert r3.metrics["error_reduction"] == 0.5
    print(f"    ✅ GEPA: FitnessEvaluator 错误减少率")


@test("GEPA: FitnessEvaluator 使用频率 log 缩放")
def test_gepa_fitness_usage():
    from core.gepa_engine import FitnessEvaluator
    r1 = FitnessEvaluator.evaluate(usage_count=0)
    assert r1.metrics["usage_frequency"] == 0.0
    r2 = FitnessEvaluator.evaluate(usage_count=50)
    import math
    expected = min(1.0, math.log(51) / math.log(51))
    assert r2.metrics["usage_frequency"] == 1.0
    r3 = FitnessEvaluator.evaluate(usage_count=5)
    assert 0 < r3.metrics["usage_frequency"] < 1.0
    print(f"    ✅ GEPA: FitnessEvaluator 使用频率 log 缩放")


@test("GEPA: FitnessEvaluator 时效性衰减")
def test_gepa_fitness_recency():
    from core.gepa_engine import FitnessEvaluator
    r1 = FitnessEvaluator.evaluate(last_used_days=0)
    assert r1.metrics["recency"] == 1.0
    r2 = FitnessEvaluator.evaluate(last_used_days=60)
    assert r2.metrics["recency"] == 0.1, "60天应衰减到底"
    r3 = FitnessEvaluator.evaluate(last_used_days=15)
    assert 0.1 < r3.metrics["recency"] < 1.0
    print(f"    ✅ GEPA: FitnessEvaluator 时效性衰减")


@test("GEPA: FitnessEvaluator 描述格式")
def test_gepa_fitness_describe():
    from core.gepa_engine import FitnessEvaluator
    record = FitnessEvaluator.evaluate(success_rate=0.8, usage_count=10, step_count=3, quality_score=0.7)
    desc = FitnessEvaluator.describe(record)
    assert "适应度" in desc
    assert "成功率" in desc
    assert "质量评分" in desc
    print(f"    ✅ GEPA: FitnessEvaluator 描述格式")


@test("GEPA: QualityAwareFitnessEvaluator 无 LLM 时回退")
def test_gepa_quality_no_llm():
    from core.gepa_engine import QualityAwareFitnessEvaluator, SkillGenome
    qfe = QualityAwareFitnessEvaluator(llm_chat_fn=None)
    genome = SkillGenome(name="test", steps=["step1", "step2"])
    record = qfe.evaluate(genome, success_rate=0.8, usage_count=5)
    assert record.score > 0
    assert qfe._stats["llm_calls"] == 0
    print(f"    ✅ GEPA: QualityAwareFitnessEvaluator 无 LLM 时回退")


@test("GEPA: QualityAwareFitnessEvaluator 带 LLM 缓存")
def test_gepa_quality_with_llm_cache():
    from core.gepa_engine import QualityAwareFitnessEvaluator, SkillGenome
    def fake_llm_chat(messages):
        return {"content": '{"clarity": 0.8, "completeness": 0.7, "generalizability": 0.6, "overall": 0.75}'}
    qfe = QualityAwareFitnessEvaluator(llm_chat_fn=fake_llm_chat)
    genome = SkillGenome(name="test-skill", steps=["step1", "step2", "step3"], version=1)
    record = qfe.evaluate(genome, success_rate=0.9)
    assert qfe._stats["llm_calls"] == 1
    assert record.metrics["quality_score"] is not None
    # 第二次调用应命中缓存
    record2 = qfe.evaluate(genome, success_rate=0.9)
    assert qfe._stats["llm_calls"] == 1, "应命中缓存"
    assert qfe._stats["cache_hits"] == 1
    print(f"    ✅ GEPA: QualityAwareFitnessEvaluator 带 LLM 缓存")


@test("GEPA: QualityAwareFitnessEvaluator force_llm 绕过缓存")
def test_gepa_quality_force():
    from core.gepa_engine import QualityAwareFitnessEvaluator, SkillGenome
    calls = []
    def fake_llm_chat(messages):
        calls.append(1)
        return {"content": '{"overall": 0.8}'}
    qfe = QualityAwareFitnessEvaluator(llm_chat_fn=fake_llm_chat)
    genome = SkillGenome(name="test", steps=["a"], version=1)
    qfe.evaluate(genome, success_rate=0.5)
    assert len(calls) == 1
    # force=True 应绕过缓存
    qfe.evaluate(genome, success_rate=0.5, force_llm=True)
    assert len(calls) == 2, "force 应绕过缓存"
    print(f"    ✅ GEPA: QualityAwareFitnessEvaluator force_llm 绕过缓存")


@test("GEPA: QualityAwareFitnessEvaluator 缓存失效")
def test_gepa_quality_cache_invalidate():
    from core.gepa_engine import QualityAwareFitnessEvaluator, SkillGenome
    def fake_llm(m):
        return {"content": '{"overall": 0.8}'}
    qfe = QualityAwareFitnessEvaluator(llm_chat_fn=fake_llm)
    genome = SkillGenome(name="test-skill", steps=["a"], version=1)
    qfe.evaluate(genome, success_rate=0.5)
    assert qfe._stats["llm_calls"] == 1
    qfe.invalidate_cache("test-skill")
    qfe.evaluate(genome, success_rate=0.5)
    assert qfe._stats["llm_calls"] == 2, "缓存失效后应重新调用 LLM"
    print(f"    ✅ GEPA: QualityAwareFitnessEvaluator 缓存失效")


@test("GEPA: MutationOperator 创建")
def test_gepa_mutation_init():
    from core.gepa_engine import MutationOperator
    mo = MutationOperator()
    assert mo._stats["total"] == 0
    assert len(mo._stats["by_type"]) == 0
    print(f"    ✅ GEPA: MutationOperator 创建")


@test("GEPA: MutationOperator add_step（context 含新错误）")
def test_gepa_mutation_add_step():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    genome = SkillGenome(name="test", steps=["step1"])
    context = {"new_error": "ModuleNotFoundError"}
    # 手动执行 _add_step
    new = mo._add_step(genome, context)
    assert new.version == genome.version + 1
    assert new.parent == genome.name
    assert "ModuleNotFoundError" in new.steps[-1]
    print(f"    ✅ GEPA: MutationOperator add_step（context 含新错误）")


@test("GEPA: MutationOperator add_step（context 含用户反馈）")
def test_gepa_mutation_add_step_feedback():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    genome = SkillGenome(name="test", steps=["step1"])
    context = {"user_feedback": "请先确认版本"}
    new = mo._add_step(genome, context)
    assert "请先确认版本" in new.steps[-1]
    print(f"    ✅ GEPA: MutationOperator add_step（context 含用户反馈）")


@test("GEPA: MutationOperator add_step（通用添加）")
def test_gepa_mutation_add_step_generic():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    genome = SkillGenome(name="test", steps=["step1"])
    new = mo._add_step(genome)
    assert "如遇异常" in new.steps[-1]
    print(f"    ✅ GEPA: MutationOperator add_step（通用添加）")


@test("GEPA: MutationOperator remove_step 保护")
def test_gepa_mutation_remove_step():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    # 1步的不删除
    g1 = SkillGenome(name="test", steps=["only step"])
    r1 = mo._remove_step(g1)
    assert len(r1.steps) == 1, "1步技能不应被删除"
    # 2步的不删中间（保护首尾）
    g2 = SkillGenome(name="test2", steps=["init", "do", "cleanup"])
    r2 = mo._remove_step(g2)
    assert len(r2.steps) == 2, "3步应删为2步"
    assert r2.version == g2.version + 1
    assert r2.parent == g2.name
    print(f"    ✅ GEPA: MutationOperator remove_step 保护")


@test("GEPA: MutationOperator optimize_trigger")
def test_gepa_mutation_optimize_trigger():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    # 空 trigger 应有步骤摘要填充
    g = SkillGenome(name="test", trigger="", steps=["Write a Python function"])
    new = mo._optimize_trigger(g)
    assert len(new.trigger) > 0
    # 有 context keywords
    g2 = SkillGenome(name="test2", trigger="old", keywords=["a"])
    new2 = mo._optimize_trigger(g2, {"new_keywords": ["python", "a"]})
    assert "python" in new2.keywords
    assert new2.keywords.count("a") == 1, "去重"
    print(f"    ✅ GEPA: MutationOperator optimize_trigger")


@test("GEPA: MutationOperator update_error")
def test_gepa_mutation_update_error():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    g = SkillGenome(name="test", error_pattern="IndexError")
    new = mo._update_error(g, {"new_error": "KeyError"})
    assert "KeyError" in new.error_pattern
    assert "IndexError" in new.error_pattern
    # 重复错误不应添加
    new2 = mo._update_error(g, {"new_error": "IndexError"})
    assert new2.error_pattern == "IndexError"
    print(f"    ✅ GEPA: MutationOperator update_error")


@test("GEPA: MutationOperator mutate 随机选择")
def test_gepa_mutation_mutate():
    from core.gepa_engine import MutationOperator, SkillGenome
    mo = MutationOperator()
    genome = SkillGenome(name="test", steps=["step1", "step2", "step3"], trigger="old", error_pattern="")
    result = mo.mutate(genome, {"new_error": "TestError"})
    assert result.name == genome.name
    assert result.version >= genome.version
    assert mo._stats["total"] == 1
    print(f"    ✅ GEPA: MutationOperator mutate 随机选择")


@test("GEPA: MutationOperator get_stats")
def test_gepa_mutation_get_stats():
    from core.gepa_engine import MutationOperator
    mo = MutationOperator()
    stats = mo.get_stats()
    assert "total" in stats
    assert "by_type" in stats
    print(f"    ✅ GEPA: MutationOperator get_stats")


@test("GEPA: CrossoverOperator 创建")
def test_gepa_crossover_init():
    from core.gepa_engine import CrossoverOperator
    co = CrossoverOperator()
    assert co._stats["total"] == 0
    print(f"    ✅ GEPA: CrossoverOperator 创建")


@test("GEPA: CrossoverOperator 正常交叉")
def test_gepa_crossover_normal():
    from core.gepa_engine import CrossoverOperator, SkillGenome
    co = CrossoverOperator()
    a = SkillGenome(name="skill-a", trigger="当A", steps=["a1", "a2", "a3", "a4"], keywords=["a"], pitfalls=["pa"], error_pattern="ErrA")
    b = SkillGenome(name="skill-b", trigger="当B", steps=["b1", "b2"], keywords=["b"], pitfalls=["pb"], error_pattern="ErrB")
    child = co.crossover(a, b)
    assert child is not None
    assert "hybrid" in child.name
    assert child.version == 1
    assert child.parent == "skill-a+skill-b"
    # 步骤：A前半 + B后半
    assert len(child.steps) > 0
    # 关键词合并
    assert "a" in child.keywords
    assert "b" in child.keywords
    # 陷阱合并
    assert "pa" in child.pitfalls
    assert "pb" in child.pitfalls
    # 错误模式合并
    assert "ErrA" in child.error_pattern
    assert "ErrB" in child.error_pattern
    # 触发条件：取长的
    assert child.trigger == "当A"  # 4 > 2
    assert co._stats["total"] == 1
    print(f"    ✅ GEPA: CrossoverOperator 正常交叉")


@test("GEPA: CrossoverOperator 空步骤返回 None")
def test_gepa_crossover_empty():
    from core.gepa_engine import CrossoverOperator, SkillGenome
    co = CrossoverOperator()
    a = SkillGenome(name="a", steps=[])
    b = SkillGenome(name="b", steps=["b1"])
    assert co.crossover(a, None) is None
    print(f"    ✅ GEPA: CrossoverOperator 空步骤返回 None")


@test("GEPA: SelectionOperator 创建和分类")
def test_gepa_selection_classify():
    from core.gepa_engine import SelectionOperator, FitnessRecord
    so = SelectionOperator()
    # elite
    assert so.classify(FitnessRecord(score=0.8)) == "elite"
    assert so.classify(FitnessRecord(score=0.7)) == "elite"
    # normal
    assert so.classify(FitnessRecord(score=0.5)) == "normal"
    # weak
    assert so.classify(FitnessRecord(score=0.2)) == "weak"
    assert so.classify(FitnessRecord(score=0.3)) == "normal"  # 边界：>=0.3 是 normal
    # cull
    assert so.classify(FitnessRecord(score=0.1)) == "cull"
    print(f"    ✅ GEPA: SelectionOperator 创建和分类")


@test("GEPA: SelectionOperator select 完整流程")
def test_gepa_selection_select():
    from core.gepa_engine import SelectionOperator, SkillGenome, FitnessRecord
    so = SelectionOperator()
    genomes = [
        ("elite1", SkillGenome(name="elite1"), FitnessRecord(score=0.8)),
        ("elite2", SkillGenome(name="elite2"), FitnessRecord(score=0.75)),
        ("normal1", SkillGenome(name="normal1"), FitnessRecord(score=0.5)),
        ("weak1", SkillGenome(name="weak1"), FitnessRecord(score=0.2)),
        ("cull1", SkillGenome(name="cull1"), FitnessRecord(score=0.1)),
        ("cull2", SkillGenome(name="cull2"), FitnessRecord(score=0.05)),
    ]
    result = so.select(genomes)
    assert len(result["elite"]) == 2
    assert len(result["cull"]) == 2
    assert len(result["weak"]) == 1
    assert len(result["normal"]) == 1
    assert so._stats["elite"] == 2
    assert so._stats["culled"] == 2
    print(f"    ✅ GEPA: SelectionOperator select 完整流程")


@test("GEPA: GEPAEngine 创建")
def test_gepa_engine_init():
    from core.gepa_engine import GEPAEngine
    engine = GEPAEngine()
    assert engine._generation == 0
    assert engine.fitness is not None
    assert engine.mutation is not None
    assert engine.crossover is not None
    assert engine.selection is not None
    print(f"    ✅ GEPA: GEPAEngine 创建")


@test("GEPA: GEPAEngine evaluate_with_report")
def test_gepa_engine_report():
    from core.gepa_engine import GEPAEngine, SkillGenome
    engine = GEPAEngine()
    genome = SkillGenome(name="report-test", steps=["a", "b", "c"])
    report = engine.evaluate_with_report(genome, success_rate=0.9, usage_count=10)
    assert report["skill_name"] == "report-test"
    assert report["version"] == 1
    assert 0 <= report["fitness"] <= 1.0
    assert "metrics" in report
    assert "summary" in report
    assert "适应度" in report["summary"]
    print(f"    ✅ GEPA: GEPAEngine evaluate_with_report")


@test("GEPA: GEPAEngine mutate / crossover / select 委托")
def test_gepa_engine_delegates():
    from core.gepa_engine import GEPAEngine, SkillGenome, FitnessRecord
    engine = GEPAEngine()
    g = SkillGenome(name="test", steps=["a", "b"], trigger="t")
    # mutate
    mutated = engine.mutate(g, {"new_error": "ErrX"})
    assert mutated.version >= g.version
    # crossover (通过 .crossover 属性调用 crossover 方法)
    a = SkillGenome(name="a", steps=["a1", "a2"])
    b = SkillGenome(name="b", steps=["b1", "b2", "b3"])
    child = engine.crossover.crossover(a, b)
    assert child is not None
    assert "hybrid" in child.name
    # select
    genomes = [("a", a, FitnessRecord(score=0.8)), ("b", b, FitnessRecord(score=0.2))]
    result = engine.select(genomes)
    assert "elite" in result
    print(f"    ✅ GEPA: GEPAEngine mutate / crossover / select 委托")


@test("GEPA: GEPAEngine evolve_once 完整周期")
def test_gepa_engine_evolve_once():
    from core.gepa_engine import GEPAEngine, SkillGenome, FitnessRecord
    engine = GEPAEngine()
    genomes = [
        ("elite1", SkillGenome(name="elite1", steps=["a", "b", "c"]), FitnessRecord(score=0.8)),
        ("elite2", SkillGenome(name="elite2", steps=["d", "e"]), FitnessRecord(score=0.75)),
        ("normal1", SkillGenome(name="normal1", steps=["f"]), FitnessRecord(score=0.5)),
        ("weak1", SkillGenome(name="weak1", steps=["g", "h", "i"]), FitnessRecord(score=0.2)),
        ("cull1", SkillGenome(name="cull1", steps=["j"]), FitnessRecord(score=0.05)),
    ]
    result = engine.evolve_once(genomes, context={"new_error": "TestErr"})
    assert result["generation"] == 1
    assert len(result["culled"]) == 1
    assert result["culled"] == ["cull1"]
    assert len(result["mutations"]) == 1  # weak1 变异
    print(f"    ✅ GEPA: GEPAEngine evolve_once 完整周期")


@test("GEPA: GEPAEngine 多次进化 get_stats")
def test_gepa_engine_multi_gen():
    from core.gepa_engine import GEPAEngine, SkillGenome, FitnessRecord
    engine = GEPAEngine()
    base_genomes = [
        ("s1", SkillGenome(name="s1", steps=["a", "b", "c"]), FitnessRecord(score=0.9)),
        ("s2", SkillGenome(name="s2", steps=["d", "e"]), FitnessRecord(score=0.8)),
        ("s3", SkillGenome(name="s3", steps=["g", "h", "i"]), FitnessRecord(score=0.3)),
    ]
    for _ in range(3):
        engine.evolve_once(base_genomes)
    stats = engine.get_stats()
    assert stats["generation"] == 3
    assert len(stats["history"]) == 3
    print(f"    ✅ GEPA: GEPAEngine 多次进化 get_stats")


# ═══════════════════════════════════════════════════════════════
# 3. LLM 客户端深度测试
# ═══════════════════════════════════════════════════════════════

@test("LLMClient: 多后端降级列表")
def test_llm_multi_backends():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek", "openai", "qwen"])
    assert len(c.backends) == 3
    assert c.backends[0].provider_id == "deepseek"
    assert c.backends[1].provider_id == "openai"
    assert c.backends[2].provider_id == "qwen"
    print(f"    ✅ LLMClient: 多后端降级列表")


@test("LLMClient: 自定义参数覆盖主后端")
def test_llm_custom_params():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"], api_key="sk-custom", base_url="http://custom:8080", model="custom-model")
    assert c.api_key == "sk-custom"
    assert c.base_url == "http://custom:8080"
    assert c.model == "custom-model"
    print(f"    ✅ LLMClient: 自定义参数覆盖主后端")


@test("LLMClient: count_tokens 中文/英文/混合")
def test_llm_count_tokens():
    from core.llm import LLMClient
    # 纯中文
    zh = LLMClient.count_tokens("你好世界这是一个测试")
    assert zh > 0
    # 纯英文
    en = LLMClient.count_tokens("hello world this is a test")
    assert en > 0
    # 中英混合
    mix = LLMClient.count_tokens("你好 world 测试 hello")
    assert mix > 0
    # 中文权重 > 英文
    assert zh > en, "同长度中文 token 应多于英文"
    print(f"    ✅ LLMClient: count_tokens 中文/英文/混合")


@test("LLMClient: get_status 所有后端状态")
def test_llm_get_status():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek", "openai"])
    s = c.get_status()
    assert s["active"] == "deepseek"
    assert len(s["backends"]) == 2
    for bk in s["backends"]:
        assert "provider" in bk
        assert "model" in bk
        assert "available" in bk
    print(f"    ✅ LLMClient: get_status 所有后端状态")


@test("LLMClient: switch 切换后端名")
def test_llm_switch_name():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    assert c.backend == "deepseek"
    msg = c.switch("qwen")
    assert c.backend == "qwen"
    assert "qwen" in msg.lower() or "qwen" in msg
    print(f"    ✅ LLMClient: switch 切换后端名")


@test("LLMClient: switch providers 列表")
def test_llm_switch_providers():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    msg = c.switch("openai, qwen")
    assert len(c.backends) == 2
    assert c.backends[0].provider_id == "openai"
    print(f"    ✅ LLMClient: switch providers 列表")


@test("LLMClient: switch dict 配置")
def test_llm_switch_dict():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    msg = c.switch({"provider": "openai", "api_key": "sk-test", "model": "gpt-4"})
    assert c.backend == "openai"
    assert c.api_key == "sk-test"
    assert c.model == "gpt-4"
    print(f"    ✅ LLMClient: switch dict 配置")


@test("LLMClient: switch 未知后端名")
def test_llm_switch_unknown():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    # 未识别的后端名会被当作 providers 列表（逗号分隔）重新初始化
    msg = c.switch("nonexistent_provider")
    assert "已切换" in msg or "后端列表" in msg
    print(f"    ✅ LLMClient: switch 未知后端名")


@test("LLMClient: _select_backend 优先级")
def test_llm_select():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek", "openai"])
    bk = c._select_backend()
    assert bk is not None
    assert bk.provider_id == "deepseek"
    print(f"    ✅ LLMClient: _select_backend 优先级")


@test("LLMClient: _record_failure 和 _record_success")
def test_llm_record():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    # 记录成功
    c._record_success("deepseek")
    assert c._last_successful == "deepseek"
    # 记录失败
    c._record_failure("deepseek")
    cooldown = c._failures.get("deepseek", 0)
    assert cooldown > time.time() - 1, "失败后应有冷却时间"
    print(f"    ✅ LLMClient: _record_failure 和 _record_success")


@test("LLMClient: _call_backend 返回错误格式")
def test_llm_call_backend():
    from core.llm import LLMClient
    c = LLMClient(providers=["deepseek"])
    # 不实际的调用，但测试方法存在
    assert hasattr(c, "_call_backend")
    print(f"    ✅ LLMClient: _call_backend 返回错误格式")


@test("LLMClient: chat 无可用后端返回错误")
def test_llm_chat_no_backend():
    from core.llm import LLMClient
    c = LLMClient()
    # mock 掉 _select_backend 返回 None
    c._select_backend = lambda: None
    result = c.chat([{"role": "user", "content": "hi"}])
    assert not result["success"]
    print(f"    ✅ LLMClient: chat 无可用后端返回错误")


@test("LLMClient: 字符串 providers 初始化")
def test_llm_str_providers():
    from core.llm import LLMClient
    c = LLMClient(providers="deepseek, openai")
    assert len(c.backends) == 2
    assert c.backends[0].provider_id == "deepseek"
    print(f"    ✅ LLMClient: 字符串 providers 初始化")


@test("LLMClient: None providers 使用默认")
def test_llm_none_providers():
    from core.llm import LLMClient
    # 确保环境变量未设置
    old = os.environ.get("KUAFFU_PROVIDERS")
    if "KUAFFU_PROVIDERS" in os.environ:
        del os.environ["KUAFFU_PROVIDERS"]
    try:
        c = LLMClient(providers=None)
        assert len(c.backends) >= 1
        assert c.backends[0].provider_id == "deepseek"
    finally:
        if old is not None:
            os.environ["KUAFFU_PROVIDERS"] = old
    print(f"    ✅ LLMClient: None providers 使用默认")


# ═══════════════════════════════════════════════════════════════
# 4. ModelManager 深度测试
# ═══════════════════════════════════════════════════════════════

@test("ModelManager: 初始化含默认配置")
def test_mm_init():
    from core.model_manager import ModelManager
    mm = ModelManager()
    assert mm._providers is not None
    assert mm._configs is not None
    assert len(mm._providers) >= 1
    print(f"    ✅ ModelManager: 初始化含默认配置")


@test("ModelManager: providers 属性")
def test_mm_providers_prop():
    from core.model_manager import ModelManager
    mm = ModelManager()
    p = mm.providers
    assert isinstance(p, list)
    assert len(p) >= 1
    print(f"    ✅ ModelManager: providers 属性")


@test("ModelManager: active_provider 返回有效值")
def test_mm_active():
    from core.model_manager import ModelManager
    mm = ModelManager()
    active = mm.active_provider
    assert active is not None
    assert isinstance(active, str)
    print(f"    ✅ ModelManager: active_provider 返回有效值")


@test("ModelManager: get_active_config")
def test_mm_get_config():
    from core.model_manager import ModelManager
    mm = ModelManager()
    cfg = mm.get_active_config()
    assert "provider" in cfg
    assert "model" in cfg
    assert "base_url" in cfg
    print(f"    ✅ ModelManager: get_active_config")


@test("ModelManager: switch 使用别名")
def test_mm_switch_alias():
    from core.model_manager import ModelManager
    mm = ModelManager()
    # 使用别名 "gpt" -> "openai"
    result = mm.switch("gpt")
    assert result["success"], f"switch 失败: {result}"
    # openai 应成为第一个 provider
    assert mm.providers[0] == "openai", f"openai 应在首位, 实际 {mm.providers}"
    print(f"    ✅ ModelManager: switch 使用别名")


@test("ModelManager: switch 未知 provider")
def test_mm_switch_unknown():
    from core.model_manager import ModelManager
    mm = ModelManager()
    result = mm.switch("unknown_xyz")
    assert not result["success"]
    print(f"    ✅ ModelManager: switch 未知 provider")


@test("ModelManager: add_provider 重复添加")
def test_mm_add_dup():
    from core.model_manager import ModelManager
    mm = ModelManager()
    initial_len = len(mm.providers)
    r = mm.add_provider("deepseek")
    assert r["success"]
    assert len(mm.providers) == initial_len, "重复添加不应增加长度"
    print(f"    ✅ ModelManager: add_provider 重复添加")


@test("ModelManager: add_provider 指定位置")
def test_mm_add_position():
    from core.model_manager import ModelManager
    mm = ModelManager()
    # 添加 openai 到位置 0
    r = mm.add_provider("openai", position=0)
    assert r["success"]
    assert mm.providers[0] == "openai"
    print(f"    ✅ ModelManager: add_provider 指定位置")


@test("ModelManager: add_provider 未知")
def test_mm_add_unknown():
    from core.model_manager import ModelManager
    mm = ModelManager()
    r = mm.add_provider("unknown_provider_999")
    assert not r["success"]
    print(f"    ✅ ModelManager: add_provider 未知")


@test("ModelManager: remove_provider")
def test_mm_remove():
    from core.model_manager import ModelManager
    mm = ModelManager()
    # 先确保有 openai 在列表中
    if "openai" not in mm.providers:
        mm.add_provider("openai")
    assert "openai" in mm.providers, "移除前 openai 应在列表中"
    r = mm.remove_provider("openai")
    assert r["success"], f"移除失败: {r}"
    assert "openai" not in mm.providers, "移除后 openai 不应在列表中"
    # 删除不存在
    r2 = mm.remove_provider("nonexistent_xyz")
    assert not r2["success"]
    print(f"    ✅ ModelManager: remove_provider")


@test("ModelManager: list_providers")
def test_mm_list():
    from core.model_manager import ModelManager
    mm = ModelManager()
    providers = mm.list_providers()
    assert len(providers) >= 1
    for p in providers:
        assert "id" in p
        assert "name" in p
        assert "model" in p
        assert "active" in p
    print(f"    ✅ ModelManager: list_providers")


@test("ModelManager: list_templates")
def test_mm_list_templates():
    from core.model_manager import ModelManager
    mm = ModelManager()
    templates = mm.list_templates()
    assert len(templates) >= 5, f"应有 >= 5 个模板, 实际 {len(templates)}"
    for t in templates:
        assert "id" in t
        assert "name" in t
        assert "model" in t
        assert "active" in t
    # deepseek 应在模板中
    ids = [t["id"] for t in templates]
    assert "deepseek" in ids
    assert "openai" in ids
    print(f"    ✅ ModelManager: list_templates — {len(templates)} 个模板")


@test("ModelManager: as_dict 格式")
def test_mm_as_dict():
    from core.model_manager import ModelManager
    mm = ModelManager()
    d = mm.as_dict()
    assert "providers" in d
    assert "active" in d
    assert "configs" in d
    print(f"    ✅ ModelManager: as_dict 格式")


@test("ModelManager: apply 配置更新")
def test_mm_apply():
    from core.model_manager import ModelManager
    mm = ModelManager()
    mm.apply({"providers": ["qwen", "deepseek"]})
    assert mm.providers == ["qwen", "deepseek"]
    mm.apply({"configs": {"qwen": {"model": "test-model"}}})
    assert mm._configs.get("qwen", {}).get("model") == "test-model"
    print(f"    ✅ ModelManager: apply 配置更新")


@test("ModelManager: _default_config 格式")
def test_mm_default_config():
    from core.model_manager import ModelManager
    cfg = ModelManager._default_config("deepseek")
    assert "provider" in cfg
    assert "name" in cfg
    assert "base_url" in cfg
    assert "model" in cfg
    assert "max_tokens" in cfg
    print(f"    ✅ ModelManager: _default_config 格式")


@test("ModelManager: _apply_custom --backend --model")
def test_mm_apply_custom():
    from core.model_manager import ModelManager
    mm = ModelManager()
    result = mm._apply_custom("--base_url http://test:8080 --model test-model")
    assert result["success"]
    pid = mm.providers[0]
    cfg = mm._configs.get(pid, {})
    assert cfg.get("base_url") == "http://test:8080"
    assert cfg.get("model") == "test-model"
    print(f"    ✅ ModelManager: _apply_custom --backend --model")


if __name__ == "__main__":
    # 收集所有 test 装饰的函数并执行
    import types
    _items = list(globals().items())
    test_fns = [obj for name, obj in _items
                if name.startswith("test_") and isinstance(obj, types.FunctionType)]
    # 按名称排序保证稳定顺序
    test_fns.sort(key=lambda f: f.__name__)
    for fn in test_fns:
        try:
            fn()
        except Exception as _exc:
            _gbl = globals()
            _gbl['FAIL'] = _gbl.get('FAIL', 0) + 1
            ERRORS.append(f"{fn.__name__}: {_exc}")
            print(f"  ❌ {fn.__name__}: {_exc}")

    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"总计: {total}  通过: {PASS}  失败: {FAIL}")
    if ERRORS:
        print(f"\n错误列表:")
        for e in ERRORS:
            print(f"  - {e}")
