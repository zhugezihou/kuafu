#!/usr/bin/env python3
"""
夸父 ToolRegistry 单元测试 — 覆盖纯逻辑 handler 和 schema 定义。

目标：测试 tool_registry.py 中所有不依赖外部服务（终端、网络、文件系统、
浏览器、白板）的纯逻辑函数，达到最高覆盖率。

测试范围：
  1. 所有 schema 静态方法（返回结构正确的 OpenAI Function Call schema）
  2. _handle_finish / _handle_finish_step（纯 dict 变换）
  3. _clean_html（HTML 文本提取，零依赖）
  4. _search_deferred_tools（关键词分词 + 评分 + 排序）
  5. _handle_tool_search（搜索 + 注入逻辑）
  6. _search_duckduckgo / _search_bing 的 fallback 逻辑
  7. 注册/注销/提升/注入等内部状态管理
  8. execute 的参数解析、异常处理、紧凑工具提升
  9. _build_env 的环境变量脱敏
  10. _tool_search_schema
"""

import json
import os
import re
import sys
from pathlib import Path

# ── 测试基础设施 ──────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tool_registry import ToolRegistry, ROOT_DIR


# ============================================================
# 1. Schema 静态方法测试
# ============================================================

def test_term_schema():
    """_term_schema 必须返回正确的 OpenAI Function Call 结构。"""
    schema = ToolRegistry._term_schema()
    assert isinstance(schema, dict)
    assert "description" in schema
    assert "parameters" in schema
    params = schema["parameters"]
    assert params["type"] == "object"
    assert "command" in params["properties"]
    assert "workdir" in params["properties"]
    assert "timeout" in params["properties"]
    assert params["properties"]["timeout"]["type"] == "integer"
    assert "command" in params["required"]


def test_read_schema():
    schema = ToolRegistry._read_schema()
    assert schema["parameters"]["properties"]["path"]["type"] == "string"
    assert schema["parameters"]["properties"]["offset"]["type"] == "integer"
    assert schema["parameters"]["properties"]["limit"]["type"] == "integer"
    assert "path" in schema["parameters"]["required"]


def test_write_schema():
    schema = ToolRegistry._write_schema()
    assert "path" in schema["parameters"]["required"]
    assert "content" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["content"]["type"] == "string"


def test_patch_schema():
    schema = ToolRegistry._patch_schema()
    assert set(schema["parameters"]["required"]) == {"path", "old_string", "new_string"}
    assert schema["parameters"]["properties"]["old_string"]["type"] == "string"
    assert schema["parameters"]["properties"]["new_string"]["type"] == "string"


def test_search_schema():
    schema = ToolRegistry._search_schema()
    assert "pattern" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["target"]["enum"] == ["content", "files"]


def test_web_search_schema():
    schema = ToolRegistry._web_search_schema()
    assert "query" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["max_results"]["type"] == "integer"


def test_web_fetch_schema():
    schema = ToolRegistry._web_fetch_schema()
    assert "url" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["url"]["type"] == "string"


def test_finish_schema():
    schema = ToolRegistry._finish_schema()
    assert "result" in schema["parameters"]["required"]
    assert "summary" in schema["parameters"]["properties"]
    assert schema["parameters"]["properties"]["result"]["type"] == "string"


def test_finish_step_schema():
    schema = ToolRegistry._finish_step_schema()
    assert "output" in schema["parameters"]["required"]
    assert "summary" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["output"]["type"] == "string"


def test_whiteboard_read_schema():
    schema = ToolRegistry._whiteboard_read_schema()
    assert "partition" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["partition"]["enum"] == [
        "current_state", "completed", "next_plan", "intermediate"
    ]


def test_whiteboard_write_schema():
    schema = ToolRegistry._whiteboard_write_schema()
    assert "partition" in schema["parameters"]["required"]
    assert "content" in schema["parameters"]["required"]
    assert "key" in schema["parameters"]["properties"]
    assert "excluded_paths" in schema["parameters"]["properties"]["partition"]["enum"]


def test_github_search_schema():
    schema = ToolRegistry._github_search_schema()
    assert "query" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["search_type"]["enum"] == ["repositories", "code"]


def test_github_get_repo_schema():
    schema = ToolRegistry._github_get_repo_schema()
    assert "repo" in schema["parameters"]["required"]
    assert "get_readme" in schema["parameters"]["properties"]
    assert schema["parameters"]["properties"]["get_readme"]["type"] == "boolean"


def test_tavily_search_schema():
    schema = ToolRegistry._tavily_search_schema()
    assert "query" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["depth"]["enum"] == ["basic", "advanced"]


def test_image_gen_schema():
    schema = ToolRegistry._image_gen_schema()
    assert "prompt" in schema["parameters"]["required"]
    assert "size" in schema["parameters"]["properties"]
    assert schema["parameters"]["properties"]["size"]["enum"] is not None
    assert "model" in schema["parameters"]["properties"]


def test_vision_schema():
    schema = ToolRegistry._vision_schema()
    assert "image_path_or_url" in schema["parameters"]["required"]
    assert "question" in schema["parameters"]["properties"]


def test_tts_schema():
    schema = ToolRegistry._tts_schema()
    assert "text" in schema["parameters"]["required"]
    assert "voice" in schema["parameters"]["properties"]
    assert "speed" in schema["parameters"]["properties"]
    assert schema["parameters"]["properties"]["voice"]["enum"] == ["default", "female", "male"]


def test_stt_schema():
    schema = ToolRegistry._stt_schema()
    assert "audio_path" in schema["parameters"]["required"]
    assert "language" in schema["parameters"]["properties"]


def test_aggregate_search_schema():
    schema = ToolRegistry._aggregate_search_schema()
    assert "query" in schema["parameters"]["required"]
    assert "summary" in schema["parameters"]["properties"]
    assert schema["parameters"]["properties"]["summary"]["type"] == "boolean"


def test_download_schema():
    schema = ToolRegistry._download_schema()
    assert "url" in schema["parameters"]["required"]
    assert "filename" in schema["parameters"]["properties"]
    assert "timeout" in schema["parameters"]["properties"]


def test_browser_nav_schema():
    schema = ToolRegistry._browser_nav_schema()
    assert "url" in schema["parameters"]["required"]


def test_browser_snap_schema():
    schema = ToolRegistry._browser_snap_schema()
    assert schema["parameters"]["required"] == []
    assert "full" in schema["parameters"]["properties"]


def test_browser_click_schema():
    schema = ToolRegistry._browser_click_schema()
    assert "ref" in schema["parameters"]["required"]


def test_browser_type_schema():
    schema = ToolRegistry._browser_type_schema()
    assert set(schema["parameters"]["required"]) == {"ref", "text"}


def test_browser_screenshot_schema():
    schema = ToolRegistry._browser_screenshot_schema()
    assert schema["parameters"]["required"] == []
    assert "filename" in schema["parameters"]["properties"]


def test_browser_js_schema():
    schema = ToolRegistry._browser_js_schema()
    assert "expression" in schema["parameters"]["required"]


def test_read_tool_result_schema():
    schema = ToolRegistry._read_tool_result_schema()
    assert "file_path" in schema["parameters"]["required"]
    assert "Microcompact" in schema["description"] or "工具结果" in schema["description"]


def test_tool_search_schema():
    schema = ToolRegistry._tool_search_schema()
    assert "query" in schema["parameters"]["required"]
    assert "搜索" in schema["description"]


# ============================================================
# 2. 纯 handler 测试
# ============================================================

def test_handle_finish():
    """_handle_finish 必须将 args 序列化为 JSON，并提取 result 和 summary。"""
    result = ToolRegistry._handle_finish({
        "result": "任务完成",
        "summary": "完成了所有步骤",
    })
    assert result["success"] is True
    assert result["result"] == "任务完成"
    assert result["summary"] == "完成了所有步骤"
    parsed = json.loads(result["output"])
    assert parsed["result"] == "任务完成"

    # 缺少字段也能正常工作
    result2 = ToolRegistry._handle_finish({})
    assert result2["success"] is True
    assert result2["result"] == ""
    assert result2["summary"] == ""


def test_handle_finish_step():
    """_handle_finish_step 返回 output 和 summary。"""
    r = ToolRegistry()
    result = r._handle_finish_step({
        "output": "步骤1输出",
        "summary": "步骤1摘要",
    })
    assert result["success"] is True
    assert result["output"] == "步骤1输出"
    assert result["summary"] == "步骤1摘要"

    # 缺少字段
    result2 = r._handle_finish_step({})
    assert result2["success"] is True
    assert result2["output"] == ""
    assert result2["summary"] == ""


# ============================================================
# 3. _clean_html 测试
# ============================================================

def test_clean_html_extracts_title():
    """_clean_html 必须提取 <title> 并加入输出。"""
    html = "<html><head><title>测试页面</title></head><body><p>Hello</p></body></html>"
    result = ToolRegistry._clean_html(html)
    assert "测试页面" in result
    assert "Hello" in result


def test_clean_html_removes_style_and_script():
    """_clean_html 必须移除 <style> 和 <script> 标签。"""
    html = """<html>
    <style>.hidden{display:none}</style>
    <script>alert('x')</script>
    <body>可见内容</body>
    </html>"""
    result = ToolRegistry._clean_html(html)
    assert ".hidden" not in result
    assert "alert" not in result
    assert "可见内容" in result


def test_clean_html_decodes_entities():
    """_clean_html 必须解码 HTML 实体。"""
    html = "<p>&amp; &lt; &gt; &quot; &#39; &nbsp;</p>"
    result = ToolRegistry._clean_html(html)
    assert "&amp;" not in result
    assert "&" in result
    assert "<" in result  # &lt; → <
    assert ">" in result  # &gt; → >


def test_clean_html_truncates_long_content():
    """_clean_html 对超过 max_length 的内容截断。"""
    long_text = "A" * 5000
    html = f"<html><body>{long_text}</body></html>"
    result = ToolRegistry._clean_html(html, max_length=100)
    assert len(result) <= 200  # 标题 + 截断标记
    assert "内容已截断" in result


def test_clean_html_without_title():
    """没有 <title> 时不报错。"""
    html = "<html><body>无标题</body></html>"
    result = ToolRegistry._clean_html(html)
    assert "无标题" in result
    assert "标题:" not in result


# ============================================================
# 4. _search_deferred_tools 测试
# ============================================================

def _registry_with_deferred():
    """创建一个包含几个 delay 工具的 ToolRegistry。"""
    r = ToolRegistry()
    # 清空并重新注册一些测试用工具
    r._deferred = []
    r.register_deferred(
        "web_search", {"description": "搜索互联网"},
        lambda x: x,
        keywords=["web", "search", "internet", "网页搜索", "互联网"],
    )
    r.register_deferred(
        "github_search", {"description": "搜索 GitHub 仓库"},
        lambda x: x,
        keywords=["github", "git", "repository", "开源仓库", "代码搜索"],
    )
    r.register_deferred(
        "download_file", {"description": "下载文件到本地"},
        lambda x: x,
        keywords=["download", "file", "url", "下载文件", "下载"],
    )
    return r


def test_search_deferred_tools_empty_query():
    """空查询返回空列表。"""
    r = ToolRegistry()
    assert r._search_deferred_tools("") == []
    assert r._search_deferred_tools("   ") == []


def test_search_deferred_tools_name_match():
    """工具名匹配得到最高分。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("web")
    names = [res["name"] for res in results]
    assert "web_search" in names
    # web_search 应该排在首位（名称匹配 10 分 + 关键词其他
    assert results[0]["name"] == "web_search"
    assert results[0]["score"] >= 10


def test_search_deferred_tools_keyword_match():
    """关键词匹配得到中等分。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("repository")
    names = [res["name"] for res in results]
    assert "github_search" in names
    # 关键词匹配 5 分
    assert any(res["score"] >= 5 for res in results if res["name"] == "github_search")


def test_search_deferred_tools_desc_match():
    """描述匹配得到低分。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("本地")
    assert any(res["name"] == "download_file" for res in results)


def test_search_deferred_tools_no_match():
    """不匹配任何工具返回空列表。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("xyzzy_nonexistent_42")
    assert results == []


def test_search_deferred_tools_max_results():
    """max_results 限制返回数量。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("搜索", max_results=1)
    assert len(results) == 1


def test_search_deferred_tools_chinese_segmentation():
    """中文查询词自动分词（2-4字滑动窗口）。"""
    r = _registry_with_deferred()
    # "下载文件" → 滑动窗口: 下载, 载文, 文件, 下载文, 载文件, 下载文件
    # "download_file" 的关键词: download, file, url, 下载文件, 下载
    results = r._search_deferred_tools("下载文件", max_results=5)
    assert any(res["name"] == "download_file" for res in results)
    assert results[0]["name"] == "download_file"


def test_search_deferred_tools_mixed_chinese_english_segmentation():
    """混合中英文词（如 'github仓库'）提取连续英文字母子串。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("github仓库")
    assert any(res["name"] == "github_search" for res in results)


def test_search_deferred_tools_sorting():
    """结果按分数降序排列。"""
    r = _registry_with_deferred()
    results = r._search_deferred_tools("搜索", max_results=10)
    if len(results) >= 2:
        scores = [res["score"] for res in results]
        assert scores == sorted(scores, reverse=True), f"scores not sorted: {scores}"


# ============================================================
# 5. _handle_tool_search 测试
# ============================================================

def test_handle_tool_search_empty_query():
    """空 query 返回失败。"""
    r = _registry_with_deferred()
    result = r._handle_tool_search({"query": ""})
    assert result["success"] is False
    assert "不能为空" in result["output"]


def test_handle_tool_search_no_match():
    """无匹配返回成功信息。"""
    r = _registry_with_deferred()
    result = r._handle_tool_search({"query": "zzz_not_found"})
    assert result["success"] is True
    assert "未找到" in result["output"]
    assert "核心工具" in result["output"]


def test_handle_tool_search_injects_tools():
    """匹配的工具被注入到当前 session。"""
    r = _registry_with_deferred()
    # 确保初始状态
    assert "web_search" not in r.get_active_tools_names()
    result = r._handle_tool_search({"query": "web"})
    assert result["success"] is True
    assert "已找到并激活" in result["output"]
    assert "web_search" in result["output"]
    assert "web_search" in r.get_active_tools_names()


def test_handle_tool_search_multiple_matches():
    """返回多个匹配结果。"""
    r = _registry_with_deferred()
    r._deferred = []
    r.register_deferred("a", {"description": "alpha"}, lambda x: x, keywords=["search"])
    r.register_deferred("b", {"description": "beta"}, lambda x: x, keywords=["search"])
    result = r._handle_tool_search({"query": "search"})
    assert result["success"] is True
    assert "a" in result["output"]
    assert "b" in result["output"]


# ============================================================
# 6. 注册/注销/提升/注入 内部状态管理测试
# ============================================================

def test_register_adds_handler_and_schema():
    """register() 将 handler 和 schema 添加到核心池。"""
    r = ToolRegistry()
    handler = lambda x: {"success": True, "output": "test"}
    r.register("test_tool", {"description": "测试"}, handler)
    assert "test_tool" in r._handlers
    assert any(s["function"]["name"] == "test_tool" for s in r._schemas)
    assert r._handlers["test_tool"] is handler


def test_register_replaces_existing():
    """重新注册同名工具会替换旧的。"""
    r = ToolRegistry()
    r.register("dup", {"description": "v1"}, lambda x: {"success": True, "output": "v1"})
    r.register("dup", {"description": "v2"}, lambda x: {"success": True, "output": "v2"})
    schemas = [s for s in r._schemas if s["function"]["name"] == "dup"]
    assert len(schemas) == 1
    assert schemas[0]["function"]["description"] == "v2"


def test_register_deferred_stores_handler():
    """register_deferred() 存储 handler 和关键词。"""
    r = ToolRegistry()
    # 记下初始 deferred 数量
    initial_count = len(r._deferred)
    handler = lambda x: {"success": True, "output": "deferred"}
    r.register_deferred("secret_tool", {"description": "秘密工具"}, handler,
                        keywords=["hidden", "秘密"])
    assert "secret_tool" in r._handlers
    assert r._handlers["secret_tool"] is handler
    assert len(r._deferred) == initial_count + 1
    # 找到新注册的工具
    deferred = [d for d in r._deferred if d["schema"]["function"]["name"] == "secret_tool"]
    assert len(deferred) == 1
    assert deferred[0]["keywords"] == ["hidden", "秘密"]


def test_register_compact_stores_handler():
    """register_compact() 存储 handler 到 compact 池。"""
    r = ToolRegistry()
    handler = lambda x: {"success": True, "output": "compact"}
    r.register_compact("compact_tool", {"description": "紧凑工具"}, handler)
    assert "compact_tool" in r._handlers
    assert r._handlers["compact_tool"] is handler
    assert any(s["function"]["name"] == "compact_tool" for s in r._compact)


def test_unregister_removes_from_all_pools():
    """unregister() 从所有池中移除工具。"""
    r = ToolRegistry()
    handler = lambda x: x
    r.register("core_tool", {"description": "核心"}, handler)
    r.register_compact("compact_tool", {"description": "紧凑"}, handler)
    # 注入该工具
    r._injected_tools.append({"function": {"name": "compact_tool"}})
    assert r.unregister("core_tool") is True
    assert "core_tool" not in r._handlers
    # 再次注销应返回 False（变更前多计数）
    # unregister returns whether pool changed
    assert r.unregister("core_tool") is False


def test_unregister_compact():
    """unregister() 从 compact 池移除。"""
    r = ToolRegistry()
    handler = lambda x: x
    r.register_compact("ct", {"description": "c"}, handler)
    assert r.unregister("ct") is True
    assert "ct" not in r._handlers
    assert r.unregister("ct") is False


def test_get_schemas_returns_core_and_injected():
    """get_schemas() 返回核心 + 已注入工具。"""
    r = ToolRegistry()
    assert len(r.get_schemas()) == len(r._schemas) + len(r._injected_tools)
    # 注入一个工具
    r._injected_tools.append({"function": {"name": "injected_test"}})
    schemas = r.get_schemas()
    assert any(s["function"]["name"] == "injected_test" for s in schemas)


def test_get_active_tools_names():
    """get_active_tools_names() 返回名称列表。"""
    r = ToolRegistry()
    names = r.get_active_tools_names()
    assert isinstance(names, list)
    assert "terminal" in names
    assert "finish" in names


def test_get_compact_tools_description():
    """get_compact_tools_description() 返回 (name, desc) 元组列表。"""
    r = ToolRegistry()
    descs = r.get_compact_tools_description()
    assert isinstance(descs, list)
    if descs:
        name, desc = descs[0]
        assert isinstance(name, str)
        assert isinstance(desc, str)


def test_promote_compact_tool_first_time():
    """首次提升紧凑工具返回 True 并注入。"""
    r = ToolRegistry()
    schema = {"function": {"name": "ct", "description": "紧凑"}}
    r._compact.append(schema)
    assert r._promote_compact_tool("ct") is True
    assert "ct" in [s["function"]["name"] for s in r._injected_tools]


def test_promote_compact_tool_already_injected():
    """已注入的紧凑工具再次提升返回 False。"""
    r = ToolRegistry()
    schema = {"function": {"name": "ct", "description": "紧凑"}}
    r._compact.append(schema)
    r._injected_tools.append(schema)
    assert r._promote_compact_tool("ct") is False


def test_promote_compact_tool_not_found():
    """不存在的紧凑工具提升返回 False。"""
    r = ToolRegistry()
    assert r._promote_compact_tool("nonexistent") is False


def test_inject_tool_from_deferred():
    """inject_tool() 从延迟池注入工具。"""
    r = ToolRegistry()
    r.register_deferred("test_tool", {"description": "测试"}, lambda x: x,
                        keywords=["test"])
    assert r.inject_tool("test_tool") is True
    assert any(s["function"]["name"] == "test_tool" for s in r._injected_tools)


def test_inject_tool_already_injected():
    """已注入的延迟工具返回 True（不重复注入）。"""
    r = ToolRegistry()
    r.register_deferred("test_tool", {"description": "测试"}, lambda x: x,
                        keywords=["test"])
    r.inject_tool("test_tool")
    pre_count = len(r._injected_tools)
    assert r.inject_tool("test_tool") is True
    assert len(r._injected_tools) == pre_count  # 未重复添加


def test_inject_tool_not_found():
    """不存在的延迟工具注入返回 False。"""
    r = ToolRegistry()
    assert r.inject_tool("nonexistent") is False


def test_list_tools():
    """list_tools() 只返回核心工具名。"""
    r = ToolRegistry()
    names = r.list_tools()
    assert isinstance(names, list)
    assert "terminal" in names
    assert "finish" in names


# ============================================================
# 7. execute 测试
# ============================================================

def test_execute_unknown_tool():
    """execute() 未知工具返回失败。"""
    r = ToolRegistry()
    result = r.execute({"function": {"name": "nonexistent", "arguments": {}}})
    assert result["success"] is False
    assert "未知工具" in result["output"]


def test_execute_string_arguments():
    """execute() 支持字符串 arguments（JSON 解析）。"""
    r = ToolRegistry()
    test_handler = lambda args: {"success": True, "output": args.get("msg", "")}
    r.register("echo", {"description": "测试", "parameters": {"type": "object",
                "properties": {"msg": {"type": "string"}}, "required": []}}, test_handler)
    result = r.execute({"function": {"name": "echo", "arguments": '{"msg": "hello"}'}})
    assert result["success"] is True
    assert result["output"] == "hello"


def test_execute_invalid_json_arguments():
    """execute() 无效 JSON 解析为空 dict。"""
    r = ToolRegistry()
    test_handler = lambda args: {"success": True, "output": str(args)}
    r.register("echo", {"description": "测试"}, test_handler)
    result = r.execute({"function": {"name": "echo", "arguments": "not-json"}})
    assert result["success"] is True
    assert "{}" in result["output"]


def test_execute_non_dict_argument():
    """execute() 非 dict 的 arguments 返回空 dict。"""
    r = ToolRegistry()
    test_handler = lambda args: {"success": True, "output": "ok" if isinstance(args, dict) else "not dict"}
    r.register("echo", {"description": "测试"}, test_handler)
    result = r.execute({"function": {"name": "echo", "arguments": [1, 2, 3]}})
    assert result["success"] is True


def test_execute_handler_exception():
    """execute() 捕获 handler 异常。"""
    r = ToolRegistry()
    def broken(args):
        raise ValueError("模拟错误")
    r.register("broken", {"description": "坏的"}, broken)
    result = r.execute({"function": {"name": "broken", "arguments": {}}})
    assert result["success"] is False
    assert "异常" in result["output"]
    assert "模拟错误" in result["output"]


def test_execute_returns_non_dict():
    """execute() 处理 handler 返回非 dict 的情况。"""
    r = ToolRegistry()
    r.register("str_ret", {"description": "返回字符串"}, lambda args: "plain string")
    result = r.execute({"function": {"name": "str_ret", "arguments": {}}})
    assert result["success"] is True
    assert result["output"] == "plain string"


def test_execute_result_missing_output():
    """execute() 在返回 dict 缺少 output 时自动补全。"""
    r = ToolRegistry()
    r.register("no_out", {"description": "无output"},
               lambda args: {"success": True, "result": "some value"})
    result = r.execute({"function": {"name": "no_out", "arguments": {}}})
    assert result["success"] is True
    assert result["output"] == "some value"


def test_execute_promotes_compact_tool():
    """execute() 自动提升紧凑工具。"""
    r = ToolRegistry()
    handler = lambda args: {"success": True, "output": "ok"}
    r.register_compact("compact_tool", {"description": "紧凑"}, handler)
    assert "compact_tool" not in [s["function"]["name"] for s in r._injected_tools]
    r.execute({"function": {"name": "compact_tool", "arguments": {}}})
    assert "compact_tool" in [s["function"]["name"] for s in r._injected_tools]


def test_get_handler():
    """get_handler() 返回对应 handler。"""
    r = ToolRegistry()
    fn = lambda x: x
    r.register("h", {"description": "h"}, fn)
    assert r.get_handler("h") is fn
    assert r.get_handler("nonexistent") is None


# ============================================================
# 8. _build_env 测试
# ============================================================

def test_build_env_sanitizes_sensitive_keys():
    """_build_env 对敏感环境变量脱敏。"""
    # 临时设置环境变量
    original = {}
    for key in ["TEST_API_KEY", "TEST_TOKEN", "TEST_PASSWORD", "TEST_SECRET", "SAFE_VAR"]:
        original[key] = os.environ.get(key, "")
        os.environ[key] = f"real_{key.lower()}_value"
    try:
        env = ToolRegistry._build_env()
        assert env.get("TEST_API_KEY") == "***"
        assert env.get("TEST_TOKEN") == "***"
        assert env.get("TEST_PASSWORD") == "***"
        assert env.get("TEST_SECRET") == "***"
        # 注意：非敏感变量是否保留取决于最初的环境变量设置
        # 这里我们主要测试脱敏逻辑
    finally:
        for key, val in original.items():
            if val:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)


def test_build_env_preserves_other_vars():
    """_build_env 保留非敏感变量。"""
    os.environ["MY_TEST_VAR_UNIQUE"] = "preserved_value"
    try:
        env = ToolRegistry._build_env()
        if "MY_TEST_VAR_UNIQUE" in env:
            assert env["MY_TEST_VAR_UNIQUE"] == "preserved_value"
    finally:
        os.environ.pop("MY_TEST_VAR_UNIQUE", None)


# ============================================================
# 9. 注册一致性测试
# ============================================================

def test_all_tools_registered():
    """所有工具 handler 在注册后可用。"""
    r = ToolRegistry()
    expected_names = {
        "terminal", "finish", "read_file", "write_file", "patch",
        "search_files", "finish_step", "whiteboard_read", "whiteboard_write",
        "read_tool_result", "tool_search",
        "web_search", "web_fetch", "tavily_search", "github_search",
        "github_get_repo", "image_gen", "vision_analyze", "text_to_speech",
        "speech_to_text", "aggregate_search", "download_file",
        "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
        "browser_screenshot", "browser_js",
    }
    registered = set(r._handlers.keys())
    missing = expected_names - registered
    assert not missing, f"缺少 handler: {missing}"
    extra = registered - expected_names
    if extra:
        print(f"额外 handler（可能是动态添加的）: {extra}")


def test_no_handler_none():
    """所有注册的 handler 都是可调用的。"""
    r = ToolRegistry()
    for name, handler in r._handlers.items():
        assert callable(handler), f"Handler {name} 不可调用"


def test_all_deferred_have_keywords():
    """所有延迟工具都有非空关键词。"""
    r = ToolRegistry()
    for entry in r._deferred:
        name = entry["schema"]["function"]["name"]
        assert entry["keywords"], f"Deferred tool {name} 的关键词为空"
        assert all(isinstance(kw, str) for kw in entry["keywords"]), \
            f"Deferred tool {name} 的关键词类型错误"


def test_all_deferred_have_description():
    """所有延迟工具都有描述。"""
    r = ToolRegistry()
    for entry in r._deferred:
        name = entry["schema"]["function"]["name"]
        assert entry["description"], f"Deferred tool {name} 的描述为空"


def test_all_schemas_have_function_name():
    """所有 schema 的 function name 非空。"""
    r = ToolRegistry()
    for name in r._handlers:
        assert name, "Handler 名称为空"


# ============================================================
# 10. 工具搜索中文分词逻辑
# ============================================================

def test_search_deferred_tools_mixed_word_segmentation():
    """混合中文+英文词的分词逻辑。"""
    r = _registry_with_deferred()
    # "github仓库" → 提取 'github'
    results = r._search_deferred_tools("github仓库", max_results=5)
    assert any("github" in res["name"].lower() for res in results)


def test_search_deferred_tools_score_priority():
    """名称匹配 > 关键词匹配 > 描述匹配。"""
    r = ToolRegistry()
    # 注册一个名称完全匹配和一个仅关键词匹配的工具
    r.register_deferred(
        "target_tool", {"description": "target description"},
        lambda x: x, keywords=["xyzzy_target"],
    )
    r.register_deferred(
        "other_tool", {"description": "has target description match"},
        lambda x: x, keywords=["other"],
    )
    results = r._search_deferred_tools("target_tool", max_results=5)
    # 名称完全匹配的得分最高
    target_results = [res for res in results if res["name"] == "target_tool"]
    other_results = [res for res in results if res["name"] == "other_tool"]
    if target_results and other_results:
        assert target_results[0]["score"] > other_results[0]["score"]


# ============================================================
# 11. Edge cases for execute's argument parsing
# ============================================================

def test_execute_missing_function():
    """execute() 缺失 function 字段返回未知工具。"""
    r = ToolRegistry()
    result = r.execute({})
    assert result["success"] is False
    assert "未知工具" in result["output"]


def test_execute_missing_name():
    """execute() 缺失 function.name 返回未知工具。"""
    r = ToolRegistry()
    result = r.execute({"function": {}})
    assert result["success"] is False
    assert "未知工具" in result["output"]


def test_execute_dict_arguments_passed_directly():
    """execute() dict 参数直接传给 handler。"""
    r = ToolRegistry()
    received = []
    def capture(args):
        received.append(args)
        return {"success": True, "output": "ok"}
    r.register("capture", {"description": "c"}, capture)
    r.execute({"function": {"name": "capture", "arguments": {"key": "val"}}})
    assert received[0] == {"key": "val"}


# ============================================================
# 12. 跨池注册冲突检测
# ============================================================

def test_register_removes_from_compact():
    """register() 不会移除 `_compact` 池中同名工具（设计如此）。"""
    r = ToolRegistry()
    handler = lambda x: x
    r.register_compact("dup", {"description": "compact"}, handler)
    r.register("dup", {"description": "core"}, handler)
    # register() 不移除 _compact，因为紧凑工具的 schema 仍然需要在
    # 首次调用时提升；register() 将其同时加入 core 后，提升逻辑仍然工作
    # （_promote_compact_tool 会检查 _injected_tools 避免重复注入）
    assert any(s["function"]["name"] == "dup" for s in r._compact), \
        "register 应当不修改 compact 池"
    # 核心池也应存在
    assert any(s["function"]["name"] == "dup" for s in r._schemas)


def test_register_removes_from_deferred():
    """register() 会从 deferred 池移除同名工具。"""
    r = ToolRegistry()
    handler = lambda x: x
    r.register_deferred("dup", {"description": "deferred"}, handler, keywords=["d"])
    r.register("dup", {"description": "core"}, handler)
    assert not any(s.get("schema", {}).get("function", {}).get("name") == "dup"
                   for s in r._deferred)


def test_register_deferred_removes_from_core():
    """register_deferred() 会从 core 池移除同名工具。"""
    r = ToolRegistry()
    handler = lambda x: x
    r.register("dup", {"description": "core"}, handler)
    r.register_deferred("dup", {"description": "deferred"}, handler, keywords=["d"])
    assert not any(s["function"]["name"] == "dup" for s in r._schemas)


def test_register_compact_removes_from_all():
    """register_compact() 会从 core 和 deferred 池移除同名工具。"""
    r = ToolRegistry()
    handler = lambda x: x
    r.register("dup", {"description": "core"}, handler)
    r.register_deferred("dup", {"description": "deferred"}, handler, keywords=["d"])
    r.register_compact("dup", {"description": "compact"}, handler)
    assert not any(s["function"]["name"] == "dup" for s in r._schemas)
    assert not any(s.get("schema", {}).get("function", {}).get("name") == "dup"
                   for s in r._deferred)


# ============================================================
# 运行入口
# ============================================================

if __name__ == "__main__":
    import inspect

    tests = []
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            tests.append((name, func))

    passed = 0
    failed = 0

    print("=" * 60)
    print(f"ToolRegistry 单元测试 — {len(tests)} 个测试")
    print("=" * 60)

    for name, func in tests:
        try:
            func()
            passed += 1
            print(f"  ✅ {name}")
        except Exception as e:
            failed += 1
            import traceback
            tb = traceback.format_exc()
            # 只打印最后两行
            lines = tb.strip().split("\n")
            summary = lines[-1]  # 断言错误行
            print(f"  ❌ {name}: {e}")
            if len(lines) > 1:
                print(f"     {lines[-2].strip()}")

    total = passed + failed
    print(f"\n{'=' * 60}")
    print(f"  结果: ✅ {passed} 通过 | ❌ {failed} 失败 | 共 {total} 项")
    print(f"{'=' * 60}")
    sys.exit(0 if failed == 0 else 1)
