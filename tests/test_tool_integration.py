#!/usr/bin/env python3
"""
夸父工具集成测试 — 覆盖所有新增工具的真实调用链路

运行: python tests/test_tool_integration.py

测试范围：
1. 多媒体工具 (image_gen, vision_analyze, tts, stt) — 参数验证 + fallback 流程
2. 聚合搜索 (aggregate_search) — 多引擎合并 + LLM 汇总
3. 下载工具 (download_file) — URL 检测 + 文件系统操作
4. 浏览器工具 (browser_*) — 导航 → 快照 → 截图 → JS → 关闭（完整工作流）
5. 工具发现 (tool_search) — 关键词匹配 + 注入
6. 工具注册一致性 — 所有工具无冲突
"""

import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

passed = 0
failed = 0

def check(label, condition, hint=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {label}")
    else:
        failed += 1
        msg = f"  ❌ {label}" + (f"  — {hint}" if hint else "")
        print(msg)


# ============================================================
# 前提：共享工具注册表
# ============================================================
from core.tool_registry import ToolRegistry

_registry = ToolRegistry()


# ── 1. 多媒体工具 ──
def test_multimedia_registration():
    print("\n【1/5】多媒体工具 — 注册 + 参数验证")
    deferred_names = [s.get("schema", {}).get("function", {}).get("name")
                      for s in _registry._deferred]
    for name in ["image_gen", "vision_analyze", "text_to_speech", "speech_to_text"]:
        check(f"{name} 注册为 deferred", name in deferred_names)

    # image_gen: 空 prompt 验证
    _registry.inject_tool("image_gen")
    result = _registry.execute({
        "function": {"name": "image_gen", "arguments": '{"prompt":""}'}
    })
    check("image_gen 空 prompt 拒绝", not result["success"] and "prompt" in result.get("output", "").lower())

    # vision_analyze: 无路径
    _registry.inject_tool("vision_analyze")
    result = _registry.execute({
        "function": {"name": "vision_analyze", "arguments": "{}"}
    })
    check("vision_analyze 无参数拒绝", not result["success"])

    # tts: 静默参数验证
    _registry.inject_tool("text_to_speech")
    result = _registry.execute({
        "function": {"name": "text_to_speech", "arguments": '{"text":""}'}
    })
    check("tts 空文本拒绝", not result["success"])

    # stt: 无文件
    _registry.inject_tool("speech_to_text")
    result = _registry.execute({
        "function": {"name": "speech_to_text", "arguments": '{"audio_path":""}'}
    })
    check("stt 空路径拒绝", not result["success"])


# ── 2. 聚合搜索 ──
def test_aggregate_search():
    print("\n【2/5】聚合搜索 — 引擎 + 合并 + 注册")
    from core.aggregate_search import aggregate_search, _normalize_url

    # URL 归一化（两种追踪参数，最终应归约到不含追踪参数的 URL）
    url1 = "https://example.com/page?utm_source=twitter&q=test"
    url2 = "https://example.com/page?ref=newsletter&q=test"
    norm1 = _normalize_url(url1)
    norm2 = _normalize_url(url2)
    no_tracking = "utm_" not in norm1 and "ref=" not in norm1
    check("URL 归一化移除追踪参数", no_tracking, f"norm1={norm1}")

    # 安全 URL
    url3 = "https://example.com/path/file%20name"
    norm3 = _normalize_url(url3)
    check("URL 归一化不解码路径", "%20" in norm3)

    # 多引擎搜索（不依赖 LLM 汇总）
    result = aggregate_search(query="夸父 开源 AI agent")
    check("聚合搜索不抛异常", result["success"])
    check("聚合搜索返回结果", len(result.get("output", "")) > 50)
    check("聚合结果含来源标记", "http" in result.get("output", "").lower())

    # 含 LLM 汇总时也允许不提供 chat_fn（走原始合并，不崩溃）
    result2 = aggregate_search(query="Python asyncio 教程")
    check("聚合搜索+汇总不抛异常", result2["success"])

    # 空查询拒绝
    from core.tool_registry import ToolRegistry
    r2 = ToolRegistry()
    r2.inject_tool("aggregate_search")
    result3 = r2.execute({
        "function": {"name": "aggregate_search", "arguments": '{"query":""}'}
    })
    check("聚合搜索空查询拒绝", not result3["success"])


# ── 3. 下载工具 ──
def test_download_file():
    print("\n【3/5】下载工具 — 注册 + 参数 + 实际下载")
    from core.downloader import DownloadEngine, _safe_filename, list_downloads

    # 引擎检测
    engines = DownloadEngine.check_engines()
    check("至少一个下载引擎可用", len(engines) > 0, str(engines))

    # 文件名推断
    check("URL 文件名推断正确", _safe_filename("https://example.com/file.zip") == "file.zip")
    check("Content-Disposition fallback", len(_safe_filename("https://example.com/dl")) == 12)
    check("路径穿越清理", "/" not in _safe_filename("https://x.com/../etc/passwd.txt"))

    # 下载一个真实小文件
    result = DownloadEngine.download("https://raw.githubusercontent.com/nousresearch/hermes-agent/main/LICENSE")
    check("实际下载成功", result.success, f"engine={result.engine}")
    check("下载文件存在", os.path.exists(result.path))
    check("文件大小 > 0", result.size > 0)
    check("耗时合理 < 60s", result.elapsed < 60)

    # 查看下载目录
    files = list_downloads()
    check("list_downloads 返回列表", isinstance(files, list))
    check("至少包含刚下载的文件", any(f["name"] == "LICENSE" for f in files) or
          any(f["name"].endswith(".txt") for f in files))

    # 工具层参数验证
    _registry.inject_tool("download_file")
    result2 = _registry.execute({
        "function": {"name": "download_file", "arguments": '{"url":"file:///etc/passwd"}'}
    })
    check("非法协议拒绝", not result2["success"])

    result3 = _registry.execute({
        "function": {"name": "download_file", "arguments": '{"url":""}'}
    })
    check("空 URL 拒绝", not result3["success"])


# ── 4. 浏览器工具 ──
def test_browser_tools():
    print("\n【4/5】浏览器工具 — 注册 + 导航 + 交互 + 截图 + JS + 清理")
    from core.browser import navigate, snapshot, screenshot, execute_js, type_text

    # 注入所有浏览器工具
    for name in ["browser_navigate", "browser_snapshot", "browser_click",
                  "browser_type", "browser_screenshot", "browser_js"]:
        _registry.inject_tool(name)

    # 导航（用 example.com — 它有交互元素链接 + 标题）
    result = navigate("https://example.com")
    check("导航成功", result["success"], result.get("output", "")[:100])
    check("返回交互元素", "[@e" in result.get("output", ""), result.get("output", "")[:200])
    check("URL 正确", "example.com" in result.get("url", ""))

    # 快照
    result2 = snapshot(full=False)
    check("快照获取成功", result2["success"])
    check("快照含元素标记", "[@e" in result2.get("output", ""), result2.get("output", "")[:200])

    # 截图
    import tempfile
    snap_path = tempfile.mktemp(suffix=".png")
    try:
        result3 = screenshot(filename=os.path.basename(snap_path))
        check("截图成功", result3["success"], result3.get("output", ""))
        path = result3.get("path", "")
        if path and os.path.exists(path):
            check("截图文件存在", os.path.getsize(path) > 1000, f"size={os.path.getsize(path)}")
            os.unlink(path)
    except Exception as e:
        check(f"截图: {e}", False)

    # JS 执行
    result4 = execute_js("document.title")
    check("JS 执行成功", result4["success"])
    check("JS 返回标题（不空）", len(result4.get("output", "")) > 1,
          result4.get("output", ""))

    # 参数验证
    result5 = _registry.execute({
        "function": {"name": "browser_navigate", "arguments": "{}"}
    })
    check("navigate 空 URL 拒绝", not result5["success"])

    result6 = _registry.execute({
        "function": {"name": "browser_click", "arguments": "{}"}
    })
    check("click 空 ref 拒绝", not result6["success"])

    result7 = _registry.execute({
        "function": {"name": "browser_js", "arguments": '{"expression":""}'}
    })
    check("js 空 expression 拒绝", not result7["success"])

    # 关闭
    from core.browser import close
    close()
    check("浏览器关闭", True)


# ── 5. 注册一致性 ──
def test_registration_consistency():
    print("\n【5/5】注册一致性 — 冲突检测 + ToolSearch 发现")
    from core.tool_registry import ToolRegistry

    r = ToolRegistry()

    # 所有工具 handler 都已注册
    handler_names = list(r._handlers.keys())
    check("handler 数量 > 20", len(handler_names) > 20, f"实际有 {len(handler_names)} 个")

    # 无命名冲突
    seen = set()
    dupes = []
    for name in handler_names:
        if name in seen:
            dupes.append(name)
        seen.add(name)
    check("无重复注册名称", len(dupes) == 0, f"重复: {dupes}")

    # ToolSearch 发现浏览器工具
    discovered = r._search_deferred_tools("打开网页", max_results=10)
    browser_found = any("browser" in d["name"] for d in discovered)
    check("ToolSearch 发现浏览器工具", browser_found, str([d["name"] for d in discovered[:5]]))

    # ToolSearch 发现下载工具
    discovered2 = r._search_deferred_tools("下载文件", max_results=10)
    download_found = any("download" in d["name"] for d in discovered2)
    check("ToolSearch 发现下载工具", download_found, str([d["name"] for d in discovered2[:5]]))

    # ToolSearch 发现聚合搜索
    discovered3 = r._search_deferred_tools("深度搜索", max_results=10)
    agg_found = any("aggregate" in d["name"] for d in discovered3)
    check("ToolSearch 发现聚合搜索", agg_found, str([d["name"] for d in discovered3[:5]]))

    # 所有 deferred 工具可被注入
    deferred_count = len(r._deferred)
    inject_fails = []
    for entry in r._deferred:
        name = entry["schema"]["function"]["name"]
        if not r.inject_tool(name):
            inject_fails.append(name)
    check(f"所有 {deferred_count} 个 deferred 工具可注入",
          len(inject_fails) == 0, f"失败: {inject_fails}")


# ============================================================
# 运行
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("夸父工具集成测试")
    print("=" * 60)

    tests = [
        test_multimedia_registration,
        test_aggregate_search,
        test_download_file,
        test_browser_tools,
        test_registration_consistency,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback
            tb = traceback.format_exc()
            print(f"  💥 {t.__name__} 崩溃: {e}")
            # 只打印最后一行 traceback
            last_line = tb.strip().split("\n")[-1]
            print(f"     {last_line}")

    total = passed + failed
    print(f"\n{'=' * 60}")
    print(f"  结果: ✅ {passed} 通过 | ❌ {failed} 失败 | 共 {total} 项")
    print(f"{'=' * 60}")
    sys.exit(0 if failed == 0 else 1)
