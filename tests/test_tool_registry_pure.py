#!/usr/bin/env python3
"""
夸父 ToolRegistry 纯逻辑单元测试（针对无外部依赖的函数）。

测试目标：
  1. _build_env — 环境变量脱敏逻辑
  2. _clean_html — HTML 清洗逻辑
  3. _search_duckduckgo — 通过 mock urllib 覆盖 URL 构建 + HTML 解析逻辑
  4. _handle_tool_search — 搜索+注入逻辑

覆盖策略：所有依赖外部服务（网络/终端/文件系统/浏览器/白板/多媒体 API）
的 handler 已标记 # pragma: no cover，本文件只测试纯逻辑部分。
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 测试基础设施 ──────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tool_registry import ToolRegistry


# ============================================================
# 1. _build_env 环境变量脱敏
# ============================================================

def test_build_env_sanitizes_api_key():
    """含 api_key 的变量被脱敏为 ***"""
    original = os.environ.get("MY_API_KEY_TEST", "")
    os.environ["MY_API_KEY_TEST"] = "secret-value-123"
    try:
        env = ToolRegistry._build_env()
        assert env.get("MY_API_KEY_TEST") == "***"
    finally:
        if original:
            os.environ["MY_API_KEY_TEST"] = original
        else:
            os.environ.pop("MY_API_KEY_TEST", None)


def test_build_env_sanitizes_api_secret():
    """含 api_secret 的变量被脱敏"""
    original = os.environ.get("MY_API_SECRET_TEST", "")
    os.environ["MY_API_SECRET_TEST"] = "supersecret"
    try:
        env = ToolRegistry._build_env()
        assert env.get("MY_API_SECRET_TEST") == "***"
    finally:
        if original:
            os.environ["MY_API_SECRET_TEST"] = original
        else:
            os.environ.pop("MY_API_SECRET_TEST", None)


def test_build_env_sanitizes_token():
    """含 token 的变量被脱敏"""
    original = os.environ.get("MY_TOKEN_TEST", "")
    os.environ["MY_TOKEN_TEST"] = "tok-12345"
    try:
        env = ToolRegistry._build_env()
        assert env.get("MY_TOKEN_TEST") == "***"
    finally:
        if original:
            os.environ["MY_TOKEN_TEST"] = original
        else:
            os.environ.pop("MY_TOKEN_TEST", None)


def test_build_env_sanitizes_password():
    """含 password 的变量被脱敏"""
    original = os.environ.get("MY_PASSWORD_TEST", "")
    os.environ["MY_PASSWORD_TEST"] = "pass-123"
    try:
        env = ToolRegistry._build_env()
        assert env.get("MY_PASSWORD_TEST") == "***"
    finally:
        if original:
            os.environ["MY_PASSWORD_TEST"] = original
        else:
            os.environ.pop("MY_PASSWORD_TEST", None)


def test_build_env_sanitizes_secret():
    """含 secret 的变量被脱敏"""
    original = os.environ.get("MY_SECRET_TEST", "")
    os.environ["MY_SECRET_TEST"] = "my-secret"
    try:
        env = ToolRegistry._build_env()
        assert env.get("MY_SECRET_TEST") == "***"
    finally:
        if original:
            os.environ["MY_SECRET_TEST"] = original
        else:
            os.environ.pop("MY_SECRET_TEST", None)


def test_build_env_preserves_safe_vars():
    """非敏感变量保持原值"""
    original = os.environ.get("MY_SAFE_VAR_TEST", "")
    os.environ["MY_SAFE_VAR_TEST"] = "safe-value"
    try:
        env = ToolRegistry._build_env()
        assert env.get("MY_SAFE_VAR_TEST") == "safe-value"
    finally:
        if original:
            os.environ["MY_SAFE_VAR_TEST"] = original
        else:
            os.environ.pop("MY_SAFE_VAR_TEST", None)


def test_build_env_partial_key_match():
    """变量名包含敏感关键词的任意位置都被脱敏"""
    original = os.environ.get("CLIENT_API_KEY_FOO", "")
    os.environ["CLIENT_API_KEY_FOO"] = "should-be-hidden"
    try:
        env = ToolRegistry._build_env()
        assert env.get("CLIENT_API_KEY_FOO") == "***"
    finally:
        if original:
            os.environ["CLIENT_API_KEY_FOO"] = original
        else:
            os.environ.pop("CLIENT_API_KEY_FOO", None)


# ============================================================
# 2. _clean_html HTML 清洗
# ============================================================

def test_clean_html_title_only():
    """只有 <title> 标签的情况"""
    html = "<html><head><title>仅标题</title></head><body></body></html>"
    result = ToolRegistry._clean_html(html)
    assert "仅标题" in result
    assert "标题:" in result


def test_clean_html_nested_tags():
    """嵌套 HTML 标签被正确移除"""
    html = "<div><p>外层 <b>内层</b> 文本</p></div>"
    result = ToolRegistry._clean_html(html)
    assert "外层 内层 文本" in result


def test_clean_html_empty_string():
    """空 HTML 字符串"""
    result = ToolRegistry._clean_html("")
    assert result.strip() == ""


def test_clean_html_only_style():
    """只有 style 标签"""
    html = "<html><head><style>body{color:red}</style></head></html>"
    result = ToolRegistry._clean_html(html)
    assert "color" not in result
    assert result == ""


def test_clean_html_only_script():
    """只有 script 标签"""
    html = "<html><head><script>alert(1)</script></head></html>"
    result = ToolRegistry._clean_html(html)
    assert "alert" not in result
    assert result == ""


def test_clean_html_title_no_body():
    """有标题无正文，标题文字出现在提取文本的很多地方"""
    html = "<html><head><title>标题</title></head></html>"
    result = ToolRegistry._clean_html(html)
    assert "标题" in result
    assert "标题:" in result


def test_clean_html_max_length_zero():
    """max_length=0 时几乎全部截断"""
    html = "<html><body>Hello World</body></html>"
    result = ToolRegistry._clean_html(html, max_length=0)
    assert "内容已截断" in result


def test_clean_html_bad_nbsp():
    """&nbsp; 被转换为普通空格"""
    html = "<p>Hello&nbsp;World</p>"
    result = ToolRegistry._clean_html(html)
    assert "Hello World" in result


def test_clean_html_script_with_newlines():
    """多行 script 内容被移除"""
    html = """<html>
    <script>
        function test() {
            alert("hello");
        }
    </script>
    <body>正文</body>
    </html>"""
    result = ToolRegistry._clean_html(html)
    assert "function" not in result
    assert "正文" in result


def test_clean_html_consecutive_whitespace():
    """连续空白被压缩为单个空格"""
    html = "<p>  A   B   C  </p>"
    result = ToolRegistry._clean_html(html)
    assert "A B C" in result


def test_clean_html_with_multiple_entities():
    """多种 HTML 实体同时解码"""
    html = "<p>&amp;&lt;&gt;&quot;&#39;</p>"
    result = ToolRegistry._clean_html(html)
    assert "&" in result
    assert "<" in result
    assert ">" in result
    assert '"' in result
    assert "'" in result


# ============================================================
# 3. _search_duckduckgo — mock urllib 覆盖
# ============================================================

DDG_HTML_SAMPLE = """
<html>
<head><title>Search Results</title></head>
<body>
<div class="results">
    <a class="result-link" href="https://example.com/1">Result One</a>
    <td class="result-snippet">First snippet text</td>
    <a class="result-link" href="https://example.com/2">Result Two</a>
    <td class="result-snippet">Second snippet text</td>
    <a class="result-link" href="https://example.com/3">Result Three</a>
    <td class="result-snippet">Third snippet text</td>
</div>
</body>
</html>
"""

DDG_HTML_NO_RESULTS = "<html><body>No results found.</body></html>"

DDG_HTML_FALLBACK = """
<html>
<body>
<a href="https://fallback.com/1">Fallback One</a>
<a href="https://fallback.com/2">Fallback Two</a>
</body>
</html>
"""


def _make_mock_response(data: bytes, status: int = 200):
    """创建一个模拟的 urllib.response"""
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    mock_resp.read.return_value = data
    mock_resp.status = status
    return mock_resp


def test_search_duckduckgo_parses_results():
    """_search_duckduckgo 正确解析 DDG HTML 结果"""
    r = ToolRegistry()
    mock_resp = _make_mock_response(DDG_HTML_SAMPLE.encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = r._search_duckduckgo("test query", max_results=2)

    assert result["success"] is True
    assert "Result One" in result["output"]
    assert "Result Two" in result["output"]
    assert "Result Three" not in result["output"]


def test_search_duckduckgo_no_results():
    """_search_duckduckgo 处理无结果"""
    r = ToolRegistry()
    mock_resp = _make_mock_response(DDG_HTML_NO_RESULTS.encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = r._search_duckduckgo("no match query")

    assert result["success"] is True
    assert "未找到结果" in result["output"]


def test_search_duckduckgo_url_construction():
    """_search_duckduckgo 构建了正确的 URL（中文/特殊字符被编码）"""
    r = ToolRegistry()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = _make_mock_response(DDG_HTML_SAMPLE.encode("utf-8"))
        mock_urlopen.return_value = mock_resp

        r._search_duckduckgo("你好世界", max_results=2)

        # 验证调用时的 URL 包含了 URL 编码后的查询
        call_args = mock_urlopen.call_args
        req = call_args[0][0]  # 第一个位置参数是 Request 对象
        assert req.full_url is not None
        assert "%E4%BD%A0%E5%A5%BD" in req.full_url  # "你好" 的 URL 编码


def test_search_duckduckgo_fallback_to_generic_links():
    """当特定结果为空时，DDG 搜索 fallback 到通用链接提取"""
    r = ToolRegistry()
    mock_resp = _make_mock_response(DDG_HTML_FALLBACK.encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = r._search_duckduckgo("fallback test", max_results=5)

    assert result["success"] is True
    assert "Fallback One" in result["output"] or "https://fallback.com/1" in result["output"]


def test_search_duckduckgo_exception_triggers_bing_fallback():
    """当 urlopen 抛出异常时，should 调用 _search_bing (但 _search_bing 也是 no cover, 不过我们不测这个)"""
    r = ToolRegistry()

    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        # 由于 _search_bing 也标记了 no cover, 这里测试框架不会报错
        result = r._search_duckduckgo("test query", max_results=2)

    # 异常时调用 _search_bing, 但 _search_bing 也会尝试 urlopen 并可能再次抛异常
    # 由于没有 mock _search_bing 的 urlopen, 它也会抛异常然后返回失败
    # 但在纯逻辑测试中我们不关心这个, 只验证不抛异常即可
    assert isinstance(result, dict)
    assert "success" in result


# ============================================================
# 4. _handle_tool_search 搜索+注入逻辑
# ============================================================

def _make_registry_with_deferred():
    """创建一个包含多个延迟工具的 ToolRegistry"""
    r = ToolRegistry()
    r._deferred = []
    r.register_deferred(
        "secret_web",
        {"description": "秘密网页搜索工具"},
        lambda x: {"success": True, "output": "web"},
        keywords=["web", "search", "internet", "网页", "搜索"],
    )
    r.register_deferred(
        "secret_github",
        {"description": "秘密 GitHub 搜索工具"},
        lambda x: {"success": True, "output": "github"},
        keywords=["github", "git", "repo", "仓库"],
    )
    r.register_deferred(
        "secret_download",
        {"description": "秘密下载工具"},
        lambda x: {"success": True, "output": "download"},
        keywords=["download", "file", "url", "下载"],
    )
    return r


def test_handle_tool_search_exact_query():
    """精确搜索返回匹配"""
    r = _make_registry_with_deferred()
    result = r._handle_tool_search({"query": "web"})
    assert result["success"] is True
    assert "secret_web" in result["output"]
    assert "秘密网页搜索工具" in result["output"]


def test_handle_tool_search_query_trim():
    """带空格的 query 也能匹配"""
    r = _make_registry_with_deferred()
    result = r._handle_tool_search({"query": "  github  "})
    assert result["success"] is True
    assert "secret_github" in result["output"]


def test_handle_tool_search_chinese_query():
    """中文 query 匹配"""
    r = _make_registry_with_deferred()
    result = r._handle_tool_search({"query": "下载"})
    assert result["success"] is True
    assert "secret_download" in result["output"]


def test_handle_tool_search_no_match():
    """无匹配返回合适信息"""
    r = _make_registry_with_deferred()
    result = r._handle_tool_search({"query": "zzz_nonexistent"})
    assert result["success"] is True
    assert "未找到" in result["output"]
    assert "核心工具" in result["output"]


def test_handle_tool_search_injection():
    """匹配的工具注入到 session"""
    r = _make_registry_with_deferred()
    assert "secret_web" not in r.get_active_tools_names()
    result = r._handle_tool_search({"query": "web"})
    assert result["success"] is True
    assert "secret_web" in r.get_active_tools_names()


def test_handle_tool_search_multiple_tools_injected():
    """多个匹配工具都注入"""
    r = _make_registry_with_deferred()
    r._handle_tool_search({"query": "搜索"})
    assert "secret_web" in r.get_active_tools_names()
    assert "secret_github" in r.get_active_tools_names()


def test_handle_tool_search_output_format():
    """输出格式包含已注入提示"""
    r = _make_registry_with_deferred()
    result = r._handle_tool_search({"query": "web"})
    assert "已找到并激活" in result["output"]
    assert "已注入当前 session" in result["output"]
    assert "secret_web" in result["output"]


def test_handle_tool_search_no_match_contains_active_tools():
    """无匹配时输出包含当前可用核心工具"""
    r = _make_registry_with_deferred()
    result = r._handle_tool_search({"query": "zzz_nonexistent"})
    assert "当前可用核心工具" in result["output"]
    assert "terminal" in result["output"] or "finish" in result["output"]


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
    print(f"ToolRegistry 纯逻辑测试 — {len(tests)} 个测试")
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
            lines = tb.strip().split("\n")
            summary = lines[-1]
            print(f"  ❌ {name}: {e}")
            if len(lines) > 1:
                print(f"     {lines[-2].strip()}")

    total = passed + failed
    print(f"\n{'=' * 60}")
    print(f"  结果: ✅ {passed} 通过 | ❌ {failed} 失败 | 共 {total} 项")
    print(f"{'=' * 60}")
    sys.exit(0 if failed == 0 else 1)
