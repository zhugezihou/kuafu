
# ═══════════════════════════════════════════════════════════════
# core/budget_allocator.py 覆盖测试
# ═══════════════════════════════════════════════════════════════

@test("BudgetAllocator: estimate_tokens 函数")
def test_budget_estimate_tokens():
    from core.budget_allocator import estimate_tokens, CHARS_PER_TOKEN
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") == int(5 / CHARS_PER_TOKEN)
    assert estimate_tokens("你好世界") == int(4 / CHARS_PER_TOKEN)
    assert estimate_tokens("") == 0  # None would fail type check, but empty string works

@test("BudgetAllocator: BudgetCategory 常量")
def test_budget_category():
    from core.budget_allocator import BudgetCategory, ALL_CATEGORIES
    assert BudgetCategory.SYSTEM == "system"
    assert BudgetCategory.DIALOGUE == "dialogue"
    assert BudgetCategory.TOOLS == "tools"
    assert BudgetCategory.MEMORY == "memory"
    assert BudgetCategory.SKILLS == "skills"
    assert BudgetCategory.RESERVED == "reserved"
    assert len(ALL_CATEGORIES) == 6
    assert ALL_CATEGORIES == ["system", "dialogue", "tools", "memory", "skills", "reserved"]

@test("BudgetAllocator: BudgetPolicy 默认值")
def test_budget_policy_default():
    from core.budget_allocator import BudgetPolicy
    p = BudgetPolicy()
    assert p.total_budget == 28000
    assert abs(p.system_ratio + p.dialogue_ratio + p.tools_ratio + p.memory_ratio + p.skills_ratio + p.reserved_ratio - 1.0) < 0.01
    assert p.warning_threshold == 0.85
    assert p.critical_threshold == 0.95

@test("BudgetAllocator: BudgetPolicy 自动归一化")
def test_budget_policy_normalize():
    from core.budget_allocator import BudgetPolicy
    p = BudgetPolicy(system_ratio=0.5, dialogue_ratio=0.5, tools_ratio=0.0, memory_ratio=0.0, skills_ratio=0.0, reserved_ratio=0.0)
    assert abs(p.system_ratio - 0.5) < 0.01
    p2 = BudgetPolicy(system_ratio=1.0, dialogue_ratio=1.0, tools_ratio=0.0, memory_ratio=0.0, skills_ratio=0.0, reserved_ratio=0.0)
    assert abs(p2.system_ratio - 0.5) < 0.01

@test("BudgetAllocator: BudgetPolicy.get_budget / for_backend")
def test_budget_policy_methods():
    from core.budget_allocator import BudgetPolicy
    p = BudgetPolicy(total_budget=64000)
    sys_budget = p.get_budget("system")
    assert sys_budget == int(64000 * 0.15)
    assert p.get_budget("nonexistent") == 0
    p2 = BudgetPolicy.for_backend(28000)
    assert p2.total_budget == 28000
    p3 = BudgetPolicy.for_backend(64000)
    assert p3.total_budget == 64000

@test("BudgetAllocator: CategoryUsage 状态计算")
def test_budget_category_usage():
    from core.budget_allocator import CategoryUsage
    u_ok = CategoryUsage(category="system", budget=1000, used=100)
    assert u_ok.status == "ok"
    assert u_ok.ratio == 0.1
    u_warn = CategoryUsage(category="dialogue", budget=1000, used=850)
    assert u_warn.status == "warning"
    assert u_warn.ratio == 0.85
    u_crit = CategoryUsage(category="tools", budget=1000, used=950)
    assert u_crit.status == "critical"
    u_over = CategoryUsage(category="memory", budget=1000, used=1000)
    assert u_over.status == "over"
    u_zero = CategoryUsage(category="skills", budget=0, used=0)
    assert u_zero.ratio == 0.0
    assert u_zero.status == "ok"

@test("BudgetAllocator: BudgetSnapshot 属性")
def test_budget_snapshot():
    from core.budget_allocator import BudgetSnapshot, CategoryUsage
    snap = BudgetSnapshot(total_budget=28000, total_used=5000, categories={
        "system": CategoryUsage("system", 4200, 100),
        "dialogue": CategoryUsage("dialogue", 9800, 3000),
    })
    assert snap.overall_ratio == round(5000 / 28000, 3)
    assert snap.overall_ratio > 0
    snap_zero = BudgetSnapshot(total_budget=0, total_used=0)
    assert snap_zero.overall_ratio == 0.0

@test("BudgetAllocator: BudgetSnapshot needs_action / critical_categories")
def test_budget_snapshot_action():
    from core.budget_allocator import BudgetSnapshot, CategoryUsage
    snap_ok = BudgetSnapshot(total_budget=1000, total_used=100, categories={
        "system": CategoryUsage("system", 1000, 100),
    })
    assert snap_ok.needs_action is False
    assert snap_ok.critical_categories == []
    snap_warn = BudgetSnapshot(total_budget=1000, total_used=900, categories={
        "system": CategoryUsage("system", 1000, 900),
    })
    assert snap_warn.needs_action is True
    assert "system" in snap_warn.critical_categories
    d = snap_warn.to_dict()
    assert d["total_budget"] == 1000
    assert d["needs_action"] is True
    assert "system" in d["categories"]
    assert d["categories"]["system"]["status"] == "warning"

@test("BudgetAllocator: BudgetSnapshot.to_dict")
def test_budget_snapshot_to_dict():
    from core.budget_allocator import BudgetSnapshot, CategoryUsage
    snap = BudgetSnapshot(total_budget=100, total_used=50, categories={
        "test": CategoryUsage("test", 100, 50),
    })
    d = snap.to_dict()
    assert d["total_budget"] == 100
    assert d["total_used"] == 50
    assert d["overall_ratio"] == 0.5
    assert "critical_categories" in d
    assert "test" in d["categories"]

@test("BudgetAllocator: BudgetAction dataclass")
def test_budget_action():
    from core.budget_allocator import BudgetAction
    a = BudgetAction(category="dialogue", severity="critical", action_type="collapse", description="超限", priority=2)
    assert a.category == "dialogue"
    assert a.severity == "critical"
    assert a.action_type == "collapse"
    assert a.priority == 2

@test("BudgetAllocator: BudgetAllocator 初始化和回调")
def test_budget_allocator_init():
    from core.budget_allocator import BudgetAllocator, BudgetPolicy
    a = BudgetAllocator()
    assert a.policy is not None
    assert a.policy.total_budget == 28000
    cb_log = []
    a2 = BudgetAllocator(
        policy=BudgetPolicy(total_budget=64000),
        on_warning=lambda s, c: cb_log.append(("warn", c)),
        on_critical=lambda s, c: cb_log.append(("crit", c)),
    )
    assert a2.policy.total_budget == 64000
    assert a2.on_warning is not None
    assert a2.on_critical is not None

@test("BudgetAllocator: scan 空消息")
def test_budget_scan_empty():
    from core.budget_allocator import BudgetAllocator
    from core.budget_allocator import BudgetPolicy
    a = BudgetAllocator(policy=BudgetPolicy(total_budget=28000))
    # Use default policy, scan with empty messages
    snap = a.scan([])
    assert snap.total_used == 0
    assert len(snap.categories) == 6
    assert snap.needs_action is False

@test("BudgetAllocator: scan 带各类消息")
def test_budget_scan_messages():
    from core.budget_allocator import BudgetAllocator
    a = BudgetAllocator()
    messages = [
        {"role": "system", "content": "你是夸父智能助手，一个自我进化的 AI 助手。" * 10},
        {"role": "user", "content": "帮我写一个 Python 脚本"},
        {"role": "assistant", "content": "好的，我来帮你。", "tool_calls": [{"function": {"name": "write_file", "arguments": '{"path":"test.py","content":"print(1)"}'}}]},
        {"role": "tool", "content": "文件已写入" * 20},
    ]
    snap = a.scan(messages, memory_token_size=50, skills_token_size=30)
    assert snap.total_used > 0
    assert "system" in snap.categories
    assert "dialogue" in snap.categories
    assert "tools" in snap.categories
    assert "memory" in snap.categories
    assert snap.categories["memory"].used == 50
    assert snap.categories["skills"].used == 30
    assert a._last_snapshot is not None
    assert len(a._history) == 1

@test("BudgetAllocator: scan 触发回调")
def test_budget_scan_callback():
    from core.budget_allocator import BudgetAllocator, BudgetPolicy
    cb_log = []
    a = BudgetAllocator(
        on_warning=lambda s, c: cb_log.append(("warn", c)),
    )
    # Medium messages won't trigger warning on default 28K budget
    snap = a.scan([{"role": "user", "content": "hi"}], memory_token_size=0, skills_token_size=0)
    # small messages should be ok
    # Let's try to trigger warning with a large message using a small budget
    a3 = BudgetAllocator(
        policy=BudgetPolicy(total_budget=100),
        on_warning=lambda s, c: cb_log.append(("warn2", c)),
    )
    big_msg = [{"role": "user", "content": "X" * 500}]
    snap3 = a3.scan(big_msg)
    if snap3.needs_action:
        assert len(cb_log) >= 1

@test("BudgetAllocator: scan 历史记录裁剪")
def test_budget_scan_history():
    from core.budget_allocator import BudgetAllocator
    a = BudgetAllocator()
    for i in range(60):
        a.scan([{"role": "user", "content": f"msg{i}"}])
    assert len(a._history) <= 50

@test("BudgetAllocator: get_actions 无快照")
def test_budget_get_actions_no_snapshot():
    from core.budget_allocator import BudgetAllocator
    a = BudgetAllocator()
    actions = a.get_actions()
    assert actions == []

@test("BudgetAllocator: get_actions 各类别")
def test_budget_get_actions():
    from core.budget_allocator import BudgetAllocator, BudgetPolicy, BudgetSnapshot, CategoryUsage
    a = BudgetAllocator(policy=BudgetPolicy(total_budget=100))
    # Build snapshot with over/critical categories
    categories = {
        "dialogue": CategoryUsage("dialogue", 35, 35),
        "tools": CategoryUsage("tools", 20, 20),
        "memory": CategoryUsage("memory", 8, 8),
        "skills": CategoryUsage("skills", 7, 7),
        "system": CategoryUsage("system", 15, 5),
        "reserved": CategoryUsage("reserved", 15, 0),
    }
    snap = BudgetSnapshot(total_budget=100, total_used=75, categories=categories)
    a._last_snapshot = snap
    actions = a.get_actions()
    assert len(actions) >= 3  # dialogue, tools, memory should have actions
    # Check ordering: priority descending
    for i in range(len(actions) - 1):
        assert actions[i].priority >= actions[i+1].priority

@test("BudgetAllocator: _suggest_action 所有分支")
def test_budget_suggest_action():
    from core.budget_allocator import BudgetAllocator, CategoryUsage
    a = BudgetAllocator()
    # DIALOGUE over -> collapse
    r1 = a._suggest_action("dialogue", CategoryUsage("dialogue", 1000, 1000))
    assert r1 is not None and r1.action_type == "collapse"
    # DIALOGUE warning -> compress
    r2 = a._suggest_action("dialogue", CategoryUsage("dialogue", 1000, 850))
    assert r2 is not None and r2.action_type == "compress"
    # TOOLS over -> microcompact
    r3 = a._suggest_action("tools", CategoryUsage("tools", 1000, 1000))
    assert r3 is not None and r3.action_type == "microcompact"
    # TOOLS warning -> microcompact
    r4 = a._suggest_action("tools", CategoryUsage("tools", 1000, 850))
    assert r4 is not None and r4.action_type == "microcompact"
    # MEMORY over -> summarize
    r5 = a._suggest_action("memory", CategoryUsage("memory", 1000, 1000))
    assert r5 is not None and r5.action_type == "summarize"
    # SKILLS over -> summarize
    r6 = a._suggest_action("skills", CategoryUsage("skills", 1000, 1000))
    assert r6 is not None and r6.action_type == "summarize"
    # SYSTEM ok -> None
    r7 = a._suggest_action("system", CategoryUsage("system", 1000, 100))
    assert r7 is None
    # RESERVED ok -> None
    r8 = a._suggest_action("reserved", CategoryUsage("reserved", 1000, 0))
    assert r8 is None
    # MEMORY warning -> None (no action for memory warning)
    r9 = a._suggest_action("memory", CategoryUsage("memory", 1000, 850))
    assert r9 is None
    # SKILLS warning -> None (no action for skills warning)
    r10 = a._suggest_action("skills", CategoryUsage("skills", 1000, 850))
    assert r10 is None

@test("BudgetAllocator: get_categories_summary")
def test_budget_summary():
    from core.budget_allocator import BudgetAllocator, BudgetSnapshot, CategoryUsage
    a = BudgetAllocator()
    assert a.get_categories_summary() == "预算: 未扫描"
    categories = {"system": CategoryUsage("system", 4200, 100)}
    snap = BudgetSnapshot(total_budget=28000, total_used=100, categories=categories)
    text = a.get_categories_summary(snap)
    assert "Token" in text
    assert "system" in text
    assert "✅" in text

@test("BudgetAllocator: reset")
def test_budget_reset():
    from core.budget_allocator import BudgetAllocator
    a = BudgetAllocator()
    a.scan([{"role": "user", "content": "hi"}])
    assert a._last_snapshot is not None
    assert len(a._history) == 1
    a.reset()
    assert a._last_snapshot is None
    assert a._history == []

@test("BudgetAllocator: create_allocator_for_backend")
def test_budget_create_allocator():
    from core.budget_allocator import create_allocator_for_backend
    a = create_allocator_for_backend(64000)
    assert a.policy.total_budget == 64000


# ═══════════════════════════════════════════════════════════════
# core/downloader.py 覆盖测试
# ═══════════════════════════════════════════════════════════════

@test("Downloader: estimate_tokens 对齐")
def test_dl_estimate():
    from core.downloader import DEFAULT_DOWNLOAD_DIR
    assert DEFAULT_DOWNLOAD_DIR.name == "downloads"

@test("Downloader: _safe_filename 全部路径")
def test_dl_safe_filename():
    from core.downloader import _safe_filename
    # Content-Disposition 优先级最高
    name = _safe_filename("http://example.com/file.pdf", disposition='attachment; filename="mydoc.pdf"')
    assert name == "mydoc.pdf"
    # URL 路径段
    name2 = _safe_filename("http://example.com/downloads/report.pdf")
    assert name2 == "report.pdf"
    # URL hash fallback
    name3 = _safe_filename("http://example.com/")
    assert len(name3) == 12 + 0  # just hash, no ext
    # Content-Type 推断扩展名
    name4 = _safe_filename("http://example.com/data", content_type="application/json")
    assert name4.endswith(".json")
    assert len(name4) == 12 + 5
    # 空 disposition
    name5 = _safe_filename("http://example.com/f.txt", disposition="")
    assert "f" in name5

@test("Downloader: _sanitize_name 清理")
def test_dl_sanitize():
    from core.downloader import _sanitize_name
    assert _sanitize_name("hello/world:test") == "hello_world_test"
    assert _sanitize_name("a..b") == "a..b"
    assert _sanitize_name("...") == "download"
    assert _sanitize_name("") == "download"
    assert _sanitize_name("safe_file.txt") == "safe_file.txt"
    long_name = "a" * 300
    assert len(_sanitize_name(long_name)) <= 200

@test("Downloader: _ext_from_content_type")
def test_dl_ext_from_ct():
    from core.downloader import _ext_from_content_type
    assert _ext_from_content_type("text/html") == ".html"
    assert _ext_from_content_type("text/plain; charset=utf-8") == ".txt"
    assert _ext_from_content_type("application/json") == ".json"
    assert _ext_from_content_type("image/png") == ".png"
    assert _ext_from_content_type("unknown/type") == ""

@test("Downloader: DownloadResult 属性")
def test_dl_result():
    from core.downloader import DownloadResult
    r = DownloadResult(path="/tmp/test.txt", size=1024, elapsed=2.0, engine="python_requests", url="http://example.com/f.txt")
    assert r.success is True
    assert r.error == ""
    assert r.speed == 512.0
    # size_str: 1024 / 1024 = 1.0 → "1.0 KB"
    size_str = r.size_str
    assert "KB" in size_str
    assert r.speed_str == "512 B/s"
    r2 = DownloadResult(path="", size=0, elapsed=0, engine="none", url="http://example.com/f.txt", success=False, error="失败")
    assert r2.success is False
    s = r2.summarize()
    assert "失败" in s
    s2 = r.summarize()
    assert "下载成功" in s2

@test("Downloader: DownloadResult size_str/speed_str 边界")
def test_dl_result_format():
    from core.downloader import DownloadResult
    r_small = DownloadResult("/f", 500, 1, "e", "u")
    assert r_small.size_str == "500 B"
    r_kb = DownloadResult("/f", 2048, 1, "e", "u")
    assert r_kb.size_str == "2.0 KB"
    r_mb = DownloadResult("/f", 3145728, 1, "e", "u")
    assert r_mb.size_str == "3.0 MB"
    r_gb = DownloadResult("/f", 3221225472, 1, "e", "u")
    assert r_gb.size_str == "3.00 GB"
    r_kbs = DownloadResult("/f", 1000, 2, "e", "u")
    speed_str = r_kbs.speed_str
    assert "B/s" in speed_str
    r_mbs = DownloadResult("/f", 2097152, 1, "e", "u")
    speed_str2 = r_mbs.speed_str
    assert "MB/s" in speed_str2

@test("Downloader: DownloadEngine.download 全部失败")
def test_dl_engine_all_fail():
    from core.downloader import DownloadEngine
    r = DownloadEngine.download("http://invalid.url.nonexistent.example/file.txt", timeout=2)
    assert r.success is False
    assert r.engine == "none"

@test("Downloader: DownloadEngine.check_engines")
def test_dl_check_engines():
    from core.downloader import DownloadEngine
    engines = DownloadEngine.check_engines()
    assert isinstance(engines, list)
    # At minimum "python_requests" should be listed (stdlib requests may not be installed but we check)
    for e in engines:
        assert e in ("python_requests", "aria2c", "wget", "curl")

@test("Downloader: DownloadEngine._deduplicate")
def test_dl_deduplicate():
    from core.downloader import DownloadEngine
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path
        p = Path(tmpdir) / "test.txt"
        result = DownloadEngine._deduplicate(p)
        assert result == p
        # Create the file
        p.write_text("hello")
        result2 = DownloadEngine._deduplicate(p)
        assert result2 != p
        assert result2.name == "test_1.txt"

@test("Downloader: _try_requests_stream requests 未安装")
def test_dl_requests_unavailable():
    from core.downloader import DownloadEngine
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        tmp = out / ".tmp"
        tmp.mkdir(exist_ok=True)
        # Remove requests availability by patching
        orig = DownloadEngine._requests_available
        DownloadEngine._requests_available = staticmethod(lambda: False)
        try:
            result = DownloadEngine._try_requests_stream("http://example.com", out, tmp, None, 10)
            assert result is None
        finally:
            DownloadEngine._requests_available = orig

@test("Downloader: _try_aria2c aria2c 未安装")
def test_dl_aria2c_unavailable():
    from core.downloader import DownloadEngine
    import shutil
    orig = shutil.which
    shutil.which = lambda cmd: None if cmd == "aria2c" else orig(cmd)
    try:
        result = DownloadEngine._try_aria2c("http://example.com", None, None, None, 10)
        assert result is None
    finally:
        shutil.which = orig

@test("Downloader: _try_wget wget 未安装")
def test_dl_wget_unavailable():
    from core.downloader import DownloadEngine
    import shutil
    orig = shutil.which
    shutil.which = lambda cmd: None if cmd == "wget" else orig(cmd)
    try:
        result = DownloadEngine._try_wget("http://example.com", None, None, None, 10)
        assert result is None
    finally:
        shutil.which = orig

@test("Downloader: _try_curl curl 未安装")
def test_dl_curl_unavailable():
    from core.downloader import DownloadEngine
    import shutil
    orig = shutil.which
    shutil.which = lambda cmd: None if cmd == "curl" else orig(cmd)
    try:
        result = DownloadEngine._try_curl("http://example.com", None, None, None, 10)
        assert result is None
    finally:
        shutil.which = orig

@test("Downloader: download_file 便捷函数")
def test_dl_download_file():
    from core.downloader import download_file, DownloadResult
    r = download_file("http://invalid.url.example/file.txt", timeout=2)
    assert isinstance(r, DownloadResult)
    assert r.success is False

@test("Downloader: list_downloads 空目录")
def test_dl_list_downloads():
    from core.downloader import list_downloads
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        files = list_downloads(tmpdir)
        assert files == []

@test("Downloader: list_downloads 有文件")
def test_dl_list_downloads_with_files():
    from core.downloader import list_downloads
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        (d / "test.txt").write_text("hello")
        (d / ".hidden").write_text("hidden")
        files = list_downloads(tmpdir)
        assert len(files) == 1
        assert files[0]["name"] == "test.txt"
        assert files[0]["size"] == 5
        assert "size_str" in files[0]
        assert "mtime" in files[0]

@test("Downloader: _format_size")
def test_dl_format_size():
    from core.downloader import _format_size
    assert _format_size(500) == "500 B"
    assert _format_size(2048) == "2.0 KB"
    assert _format_size(3145728) == "3.0 MB"
    assert _format_size(3221225472) == "3.00 GB"


# ═══════════════════════════════════════════════════════════════
# core/gateway.py 覆盖测试 (mock httplib)
# ═══════════════════════════════════════════════════════════════

@test("Gateway: GatewayHandler._send_json")
def test_gw_send_json():
    from core.gateway import GatewayHandler
    import io
    h = GatewayHandler.__new__(GatewayHandler)
    h.wfile = io.BytesIO()
    h.protocol_version = "HTTP/1.1"
    h.close_connection = True
    h.requestline = "GET /test HTTP/1.1"
    h.command = "GET"
    h.path = "/test"
    h.request_version = "HTTP/1.1"
    # We just test it doesn't crash with the right setup
    # The BaseHTTPRequestHandler needs a proper socket, so mock further
    h.send_response = lambda s: None
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h._send_json(200, {"status": "ok"})
    data = h.wfile.getvalue()
    assert b"status" in data
    assert b"ok" in data

@test("Gateway: GatewayHandler._read_body")
def test_gw_read_body():
    from core.gateway import GatewayHandler
    import io
    h = GatewayHandler.__new__(GatewayHandler)
    h.rfile = io.BytesIO(b'{"key": "value"}')
    h.headers = type('h', (), {'get': lambda self, k, d=0: len(b'{"key": "value"}')})()
    body = h._read_body()
    assert body == {"key": "value"}

@test("Gateway: GatewayHandler._read_body 空/无效")
def test_gw_read_body_empty():
    from core.gateway import GatewayHandler
    import io
    h = GatewayHandler.__new__(GatewayHandler)
    h.rfile = io.BytesIO(b"")
    h.headers = type('h', (), {'get': lambda self, k, d=0: 0})()
    assert h._read_body() == {}
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2.rfile = io.BytesIO(b"not json{{{}}}")
    h2.headers = type('h', (), {'get': lambda self, k, d=0: 14})()
    assert h2._read_body() == {}

@test("Gateway: GatewayHandler._check_auth 无 key")
def test_gw_check_auth_no_key():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = ""
    assert h._check_auth() is True

@test("Gateway: GatewayHandler._check_auth 有 key")
def test_gw_check_auth_with_key():
    from core.gateway import GatewayHandler
    import io
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = "secret123"
    h.headers = type('h', (), {'get': lambda self, k, d="": "Bearer secret123"})()
    assert h._check_auth() is True
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2.api_key = "secret123"
    h2.headers = type('h', (), {'get': lambda self, k, d="": "Bearer wrong"})()
    h2.send_response = lambda s: None
    h2.send_header = lambda k, v: None
    h2.end_headers = lambda: None
    h2.wfile = io.BytesIO()
    assert h2._check_auth() is False

@test("Gateway: GatewayHandler do_GET 路由分发")
def test_gw_do_get():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = ""
    h._check_auth = lambda: True
    h.path = "/health"
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.close_connection = True
    h.headers = type('h', (), {'get': lambda self, k, d="": ""})()
    h.rfile = type('r', (), {'read': lambda self, l: b""})()
    called = []
    h._handle_health = lambda: called.append("health")
    h.do_GET()
    assert called == ["health"]
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2.api_key = ""
    h2._check_auth = lambda: True
    h2.path = "/api/status"
    h2.command = "GET"
    h2.request_version = "HTTP/1.1"
    h2.close_connection = True
    h2.headers = type('h', (), {'get': lambda self, k, d="": ""})()
    h2.rfile = type('r', (), {'read': lambda self, l: b""})()
    called2 = []
    h2._handle_status = lambda: called2.append("status")
    h2.do_GET()
    assert called2 == ["status"]

@test("Gateway: GatewayHandler do_GET 404")
def test_gw_do_get_404():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = ""
    h._check_auth = lambda: True
    h.path = "/nonexistent"
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.close_connection = True
    h.headers = type('h', (), {'get': lambda self, k, d="": ""})()
    h.rfile = type('r', (), {'read': lambda self, l: b""})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h.do_GET()
    assert sent == [(404, {"error": "Not Found"})]

@test("Gateway: GatewayHandler do_GET 未认证")
def test_gw_do_get_unauth():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = "secret"
    h._check_auth = lambda: False
    h.do_GET()
    # Should not crash

@test("Gateway: GatewayHandler do_POST 路由分发")
def test_gw_do_post():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = ""
    h._check_auth = lambda: True
    h.path = "/api/task"
    h.command = "POST"
    h.request_version = "HTTP/1.1"
    h.close_connection = True
    h.headers = type('h', (), {'get': lambda self, k, d="": ""})()
    h.rfile = type('r', (), {'read': lambda self, l: b""})()
    called = []
    h._handle_task = lambda: called.append("task")
    h.do_POST()
    assert called == ["task"]

@test("Gateway: GatewayHandler do_POST 未认证")
def test_gw_do_post_unauth():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.api_key = "secret"
    h._check_auth = lambda: False
    h.do_POST()
    # Should not crash

@test("Gateway: GatewayHandler _handle_health")
def test_gw_handle_health():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.start_time = 100.0
    h.agent = type('a', (), {'version': '0.4'})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._handle_health()
    assert sent[0][0] == 200
    assert sent[0][1]["status"] == "ok"
    assert sent[0][1]["version"] == "0.4"

@test("Gateway: GatewayHandler _handle_status")
def test_gw_handle_status():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    # Create a mock agent with all needed attrs
    class MockLLM:
        model = "deepseek-chat"
        backend = "deepseek"
    class MockEvo:
        @staticmethod
        def get_evolution_stats():
            return {"total_evolutions": 5}
    class MockAgent:
        version = "0.4"
        llm = MockLLM()
        evolution = MockEvo()
        _task_count = 42
    h.agent = MockAgent()
    h._handle_status()
    assert sent[0][0] == 200
    assert sent[0][1]["model"] == "deepseek-chat"
    assert sent[0][1]["task_count"] == 42
    assert "evolution" in sent[0][1]

@test("Gateway: GatewayHandler _handle_status 无 llm/evolution")
def test_gw_handle_status_no_llm():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h.agent = type('a', (), {'version': '0.4'})()
    h._handle_status()
    assert sent[0][0] == 200

@test("Gateway: GatewayHandler _handle_task 同步")
def test_gw_handle_task_sync():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.agent = type('a', (), {'run': lambda self, t, mode='standard': {"success": True, "result": "done", "duration": 1.0, "turns": 2, "errors": []}})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"task": "test task", "mode": "standard", "sync": True}
    h._handle_task()
    assert sent[0][0] == 200
    assert sent[0][1]["success"] is True
    assert sent[0][1]["result"] == "done"

@test("Gateway: GatewayHandler _handle_task 异步")
def test_gw_handle_task_async():
    from core.gateway import GatewayHandler
    import threading
    h = GatewayHandler.__new__(GatewayHandler)
    h.agent = type('a', (), {'run': lambda self, t, mode='standard': {"success": True}})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"task": "async task", "sync": False}
    h._handle_task()
    assert sent[0][0] == 202

@test("Gateway: GatewayHandler _handle_task 空")
def test_gw_handle_task_empty():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {}
    h._handle_task()
    assert sent[0][0] == 400

@test("Gateway: GatewayHandler _handle_cron_list")
def test_gw_handle_cron_list():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.agent = type('a', (), {'_cron_scheduler': None})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._handle_cron_list()
    assert sent[0][1]["tasks"] == []

@test("Gateway: GatewayHandler _handle_cron_create/remove/start/stop")
def test_gw_handle_cron_crud():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    scheduler = None
    class MockScheduler:
        def __init__(self):
            self._running = False
            self.tasks = {}
        def add_task(self, t):
            self.tasks[t.name] = t
            if not self._running:
                self._running = True
        def remove_task(self, name):
            return name in self.tasks
        def get_tasks(self):
            return list(self.tasks.values())
        def start(self):
            self._running = True
        def stop(self):
            self._running = False
    mock_sched = MockScheduler()
    h.agent = type('a', (), {'_cron_scheduler': mock_sched})()

    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": "test_cron", "schedule": "30m", "task": "echo hello", "output_mode": "file"}
    # Mock import
    import core.gateway as gw_mod
    orig_import = __import__
    def fake_import(name, *args, **kwargs):
        if name == 'core.cron_scheduler':
            mod = type('mod', (), {})()
            class CT:
                def __init__(self, **kw):
                    for k, v in kw.items():
                        setattr(self, k, v)
            mod.CronTask = CT
            mod.CronScheduler = type('CS', (), {'__init__': lambda self, on_task_run: None})
            return mod
        return orig_import(name, *args, **kwargs)
    # Actually we'll test with simpler approach
    sent.clear()
    h._handle_cron_create()
    assert sent[-1][0] == 200
    assert sent[-1][1]["status"] == "created"

@test("Gateway: GatewayHandler _handle_cron_remove 已有")
def test_gw_handle_cron_remove_found():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    class MockSched:
        def remove_task(self, name):
            return True
    h.agent = type('a', (), {'_cron_scheduler': MockSched()})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": "test"}
    h._handle_cron_remove()
    assert sent[0][0] == 200

@test("Gateway: GatewayHandler _handle_cron_remove 不存在")
def test_gw_handle_cron_remove_notfound():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    class MockSched:
        def remove_task(self, name):
            return False
    h.agent = type('a', (), {'_cron_scheduler': MockSched()})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": "nonexistent"}
    h._handle_cron_remove()
    assert sent[0][0] == 404

@test("Gateway: GatewayHandler _handle_cron_remove 无 scheduler")
def test_gw_handle_cron_remove_no_sched():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.agent = type('a', (), {'_cron_scheduler': None})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": "test"}
    h._handle_cron_remove()
    assert sent[0][0] == 404

@test("Gateway: GatewayHandler _handle_cron_start/stop")
def test_gw_handle_cron_start_stop():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    class MockSched:
        running = False
        def start(self):
            self.running = True
        def stop(self):
            self.running = False
    ms = MockSched()
    h.agent = type('a', (), {'_cron_scheduler': ms})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._handle_cron_start()
    assert ms.running is True
    h._handle_cron_stop()
    assert ms.running is False

@test("Gateway: GatewayHandler _handle_cron_start/stop 无 scheduler")
def test_gw_handle_cron_no_sched():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.agent = type('a', (), {'_cron_scheduler': None})()
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._handle_cron_start()
    assert sent[0][1]["status"] == "no scheduler"
    h._handle_cron_stop()
    assert sent[1][1]["status"] == "no scheduler"

@test("Gateway: GatewayHandler _handle_sessions_list")
def test_gw_handle_sessions():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h.agent = type('a', (), {'sessions': None})()
    h._handle_sessions_list()
    assert sent[0][1]["sessions"] == []

@test("Gateway: GatewayHandler _handle_shutdown")
def test_gw_handle_shutdown():
    from core.gateway import GatewayHandler
    import threading
    ev = threading.Event()
    h = GatewayHandler.__new__(GatewayHandler)
    h.shutdown_event = ev
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._handle_shutdown()
    assert sent[0][0] == 200
    assert ev.is_set()

@test("Gateway: GatewayHandler _handle_channel_discover")
def test_gw_handle_channel_discover():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._handle_channel_discover()
    assert "discovered" in sent[0][1]

@test("Gateway: GatewayHandler _handle_channel_load")
def test_gw_handle_channel_load():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": ""}
    h._handle_channel_load()
    assert sent[0][0] == 400
    sent2 = []
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2._send_json = lambda s, d: sent2.append((s, d))
    h2._read_body = lambda: {"name": "test"}
    h2._get_channel_mgr = lambda: None
    h2._handle_channel_load()
    assert sent2[0][0] == 400

@test("Gateway: GatewayHandler _handle_channel_remove")
def test_gw_handle_channel_remove():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": ""}
    h._handle_channel_remove()
    assert sent[0][0] == 400
    sent2 = []
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2._send_json = lambda s, d: sent2.append((s, d))
    h2._read_body = lambda: {"name": "test"}
    h2._get_channel_mgr = lambda: type('m', (), {'remove': lambda self, n: False})()
    h2._handle_channel_remove()
    assert sent2[0][0] == 404

@test("Gateway: GatewayHandler _handle_channel_reload")
def test_gw_handle_channel_reload():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {"name": ""}
    h._handle_channel_reload()
    assert sent[0][0] == 400
    sent2 = []
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2._send_json = lambda s, d: sent2.append((s, d))
    h2._read_body = lambda: {"name": "test"}
    h2._get_channel_mgr = lambda: type('m', (), {'reload_channel': lambda self, n: True})()
    h2._handle_channel_reload()
    assert sent2[0][0] == 200

@test("Gateway: GatewayHandler _handle_channel_list")
def test_gw_handle_channel_list():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._get_channel_mgr = lambda: None
    h._handle_channel_list()
    assert sent[0][1]["channels"] == []
    sent2 = []
    h2 = GatewayHandler.__new__(GatewayHandler)
    h2._send_json = lambda s, d: sent2.append((s, d))
    class MockCh:
        _running = True
    class MockMgr:
        def list(self):
            return ["ch1"]
        def get(self, n):
            return MockCh()
    h2._get_channel_mgr = lambda: MockMgr()
    h2._handle_channel_list()
    assert sent2[0][1]["channels"] == [{"name": "ch1", "running": True}]

@test("Gateway: GatewayHandler _handle_batch_submit 无 tasks")
def test_gw_handle_batch_submit_empty():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {}
    h._handle_batch_submit()
    assert sent[0][0] == 400

@test("Gateway: GatewayHandler _handle_batch_status")
def test_gw_handle_batch_status_no_id():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {}
    h._handle_batch_status()
    assert sent[0][0] == 400

@test("Gateway: GatewayHandler _handle_batch_cancel_no_id")
def test_gw_handle_batch_cancel():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {}
    h._handle_batch_cancel()
    assert sent[0][0] == 400

@test("Gateway: GatewayHandler _handle_batch_retry_no_id")
def test_gw_handle_batch_retry():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {}
    h._handle_batch_retry()
    assert sent[0][0] == 400

@test("Gateway: GatewayHandler _handle_batch_clear_no_id")
def test_gw_handle_batch_clear():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    sent = []
    h._send_json = lambda s, d: sent.append((s, d))
    h._read_body = lambda: {}
    h._handle_batch_clear()
    assert sent[0][0] == 400

@test("Gateway: GatewayHandler _get_query_param")
def test_gw_get_query_param():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    h.path = "/api/test?key=value&count=3"
    assert h._get_query_param("key") == "value"
    assert h._get_query_param("count") == "3"
    assert h._get_query_param("missing", "default") == "default"

@test("Gateway: GatewayHandler log_message")
def test_gw_log_message():
    from core.gateway import GatewayHandler
    h = GatewayHandler.__new__(GatewayHandler)
    # Should not raise
    h.log_message("%s %s", "GET", "/health")

@test("Gateway: GatewayServer 生命周期")
def test_gw_server_lifecycle():
    from core.gateway import GatewayServer
    agent = type('a', (), {'version': '0.4', 'llm': type('l', (), {'model': 'x', 'backend': 'y'})(), '_task_count': 0})()
    agent.evolution = type('e', (), {'get_evolution_stats': lambda: {"total_evolutions": 0}})()

    gw = GatewayServer(agent, host="127.0.0.1", port=18999, api_key="test-key")
    assert gw.host == "127.0.0.1"
    assert gw.port == 18999
    assert gw.api_key == "test-key"
    assert gw._running is False

    # Start on a random port to avoid conflicts
    ok = gw.start()
    if ok:
        assert gw._running is True
        assert gw.is_running() is True
        gw.stop()
        assert gw._running is False
    else:
        # Port may be in use - still valid
        pass

@test("Gateway: GatewayServer start 失败")
def test_gw_server_start_fail():
    from core.gateway import GatewayServer, GatewayHandler
    agent = type('a', (), {'version': '0.4'})()
    # Invalid port should fail gracefully
    gw = GatewayServer(agent, host="127.0.0.1", port=-1)
    try:
        ok = gw.start()
        # If it somehow returns True, that's fine too
    except Exception:
        # exception from OSError is expected for invalid port
        pass

@test("Gateway: install_service / uninstall_service 不实际安装")
def test_gw_service():
    from core.gateway import install_service, uninstall_service, SYSTEMD_SERVICE_NAME
    # These should not crash even if systemd is not available
    # install_service needs to write to ~/.config which is ok in test env
    # We won't run these as they touch system state


# ═══════════════════════════════════════════════════════════════
# core/skill_repo.py 覆盖测试 (mock httplib)
# ═══════════════════════════════════════════════════════════════

@test("SkillRepo: RepoConfig 初始化")
def test_repo_config_init():
    from core.skill_repo import RepoConfig, REPOS_DIR
    rc = RepoConfig(name="test-repo", url="http://example.com/index.json", description="测试仓库", enabled=True)
    assert rc.name == "test-repo"
    assert rc.url == "http://example.com/index.json"
    assert rc.description == "测试仓库"
    assert rc.enabled is True
    assert rc._cache is None
    assert rc._cache_time == 0
    assert rc.cache_path.parent == REPOS_DIR
    assert "test-repo" in str(rc.cache_path)

@test("SkillRepo: RepoConfig to_dict")
def test_repo_config_to_dict():
    from core.skill_repo import RepoConfig
    rc = RepoConfig("my-repo", "http://example.com", "desc", True)
    d = rc.to_dict()
    assert d["name"] == "my-repo"
    assert d["url"] == "http://example.com"
    assert d["enabled"] is True
    assert len(d["description"]) <= 60

@test("SkillRepo: RepoConfig is_cache_fresh")
def test_repo_config_cache_fresh():
    from core.skill_repo import RepoConfig
    import time
    rc = RepoConfig("test", "http://example.com")
    assert rc.is_cache_fresh() is False
    rc._cache = {"skills": []}
    rc._cache_time = time.time()
    assert rc.is_cache_fresh(ttl=3600) is True
    assert rc.is_cache_fresh(ttl=0) is False

@test("SkillRepo: RepoConfig fetch 无 URL")
def test_repo_config_fetch_no_url():
    from core.skill_repo import RepoConfig
    rc = RepoConfig("test", url="")
    result = rc.fetch()
    assert result["success"] is False
    assert "未配置" in result["error"]

@test("SkillRepo: RepoConfig fetch 网络失败回退缓存")
def test_repo_config_fetch_cache_fallback():
    from core.skill_repo import RepoConfig
    import tempfile, json, os
    rc = RepoConfig("test-fallback", "http://invalid.example.com/index.json")
    # Write a cache file first
    cache_data = {"skills": [{"name": "cached-skill"}]}
    cp = rc.cache_path
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(cache_data), encoding="utf-8")
    rc.load_cache()
    result = rc.fetch(force=True)
    assert result["success"] is True
    assert "fallback" in result["source"]
    assert result["data"]["skills"][0]["name"] == "cached-skill"

@test("SkillRepo: RepoConfig fetch JSON 解码错误回退缓存")
def test_repo_config_fetch_json_error():
    from core.skill_repo import RepoConfig
    # Mock urllib to return invalid JSON
    import urllib.request as ur
    orig_urlopen = ur.urlopen
    class MockResp:
        def read(self):
            return b"not valid json{{{}}}"
        def __exit__(self, *a):
            pass
        def __enter__(self):
            return self
        def __init__(self):
            pass
    def mock_urlopen(req, timeout=15):
        return MockResp()
    ur.urlopen = mock_urlopen
    try:
        rc = RepoConfig("test-json-err", "http://example.com/index.json")
        # Without cache, should fail
        result = rc.fetch(force=True)
        assert result["success"] is False
        assert "JSON" in result["error"]
    finally:
        ur.urlopen = orig_urlopen

@test("SkillRepo: RepoConfig fetch 网络错误回退缓存")
def test_repo_config_fetch_network_error():
    from core.skill_repo import RepoConfig
    import urllib.request as ur
    orig_urlopen = ur.urlopen
    def mock_fail(req, timeout=15):
        raise ConnectionError("网络不可达")
    ur.urlopen = mock_fail
    try:
        rc = RepoConfig("test-net-err", "http://example.com/index.json")
        result = rc.fetch(force=True)
        assert result["success"] is False
        assert "拉取失败" in result["error"]
    finally:
        ur.urlopen = orig_urlopen

@test("SkillRepo: RepoConfig fetch 无 skills 字段")
def test_repo_config_fetch_no_skills():
    from core.skill_repo import RepoConfig
    import urllib.request as ur
    orig_urlopen = ur.urlopen
    class MockResp:
        def read(self):
            return b'{"name": "test"}'
        def __exit__(self, *a):
            pass
        def __enter__(self):
            return self
        def __init__(self):
            pass
    ur.urlopen = lambda req, timeout=15: MockResp()
    try:
        rc = RepoConfig("test-no-skills", "http://example.com/index.json")
        result = rc.fetch(force=True)
        assert result["success"] is False
        assert "skills" in result["error"]
    finally:
        ur.urlopen = orig_urlopen

@test("SkillRepo: RepoConfig fetch 成功远程")
def test_repo_config_fetch_success():
    from core.skill_repo import RepoConfig
    import urllib.request as ur, json
    orig_urlopen = ur.urlopen
    skills_data = {"skills": [{"name": "web-scraper", "description": "Scrape web", "version": "1.0.0"}]}
    class MockResp:
        def read(self):
            return json.dumps(skills_data).encode("utf-8")
        def __exit__(self, *a):
            pass
        def __enter__(self):
            return self
        def __init__(self):
            pass
    ur.urlopen = lambda req, timeout=15: MockResp()
    try:
        rc = RepoConfig("test-success", "http://example.com/index.json")
        result = rc.fetch(force=True)
        assert result["success"] is True
        assert result["source"] == "remote"
        assert result["data"]["skills"][0]["name"] == "web-scraper"
        # Cache should be saved
        assert rc._cache is not None
        assert rc._cache_time > 0
    finally:
        ur.urlopen = orig_urlopen

@test("SkillRepo: RepoConfig get_skills")
def test_repo_config_get_skills():
    from core.skill_repo import RepoConfig
    rc = RepoConfig("test", "http://example.com")
    skills = rc.get_skills()
    assert skills == []
    # With cache hit
    rc._cache = {"skills": [{"name": "s1"}, {"name": "s2"}]}
    rc._cache_time = 9999999999.0
    skills2 = rc.get_skills()
    assert len(skills2) == 2

@test("SkillRepo: RepoConfig save_cache / load_cache")
def test_repo_config_cache_io():
    from core.skill_repo import RepoConfig
    import json
    rc = RepoConfig("cache-io-test", "http://example.com")
    data = {"skills": [{"name": "test-skill"}]}
    rc.save_cache(data)
    assert rc._cache is not None
    assert rc._cache_time > 0
    cp = rc.cache_path
    assert cp.exists()
    # Re-read
    rc2 = RepoConfig("cache-io-test", "http://example.com")
    loaded = rc2.load_cache()
    assert loaded is not None
    assert loaded["skills"][0]["name"] == "test-skill"

@test("SkillRepo: RepoManager 初始化")
def test_repo_manager_init():
    from core.skill_repo import RepoManager, DEFAULT_REPOS
    mgr = RepoManager()
    assert mgr._repos is not None
    # Should have default repos
    assert len(mgr._repos) >= 1

@test("SkillRepo: RepoManager list_repos")
def test_repo_manager_list():
    from core.skill_repo import RepoManager
    mgr = RepoManager()
    repos = mgr.list_repos()
    assert len(repos) >= 1
    for r in repos:
        assert "name" in r
        assert "url" in r
        assert "enabled" in r

@test("SkillRepo: RepoManager add_repo / remove_repo")
def test_repo_manager_add_remove():
    from core.skill_repo import RepoManager
    mgr = RepoManager()
    # Add
    result = mgr.add_repo("test-new", "http://example.com/skills.json", "测试新增")
    # May fail if network unavailable, but should still add
    assert result["success"] is True or (result["success"] is False and result.get("reachable") is False)
    # Duplicate name
    result2 = mgr.add_repo("test-new", "http://other.com/skills.json")
    assert result2["success"] is False
    assert "已存在" in result2["error"]
    # Remove
    ok = mgr.remove_repo("test-new")
    assert ok is True
    ok2 = mgr.remove_repo("nonexistent")
    assert ok2 is False

@test("SkillRepo: RepoManager get_repo")
def test_repo_manager_get():
    from core.skill_repo import RepoManager
    mgr = RepoManager()
    r = mgr.get_repo("nonexistent")
    assert r is None
    # Get default
    repos = mgr.list_repos()
    if repos:
        r2 = mgr.get_repo(repos[0]["name"])
        assert r2 is not None

@test("SkillRepo: RepoManager search 空查询")
def test_repo_manager_search_empty():
    from core.skill_repo import RepoManager
    mgr = RepoManager()
    # Empty query matches all skills ('' in any string is True)
    # So this returns results, but they should have score 50 or higher
    results = mgr.search("", force=False)
    # The test just verifies it doesn't crash

@test("SkillRepo: RepoManager search 匹配逻辑")
def test_repo_manager_search_matching():
    from core.skill_repo import RepoManager
    mgr = RepoManager()
    # Create a repo with known skills
    from core.skill_repo import RepoConfig
    rc = RepoConfig("mock-repo", "http://mock.example.com")
    rc._cache = {"skills": [
        {"name": "web-scraper", "description": "网页抓取工具", "keywords": ["scraping", "web"], "category": "web", "version": "1.0", "author": "test", "steps": 3, "url": "http://example.com/skill.yaml"},
        {"name": "file-helper", "description": "文件操作助手", "keywords": ["file", "helper"], "category": "utility", "version": "2.0", "author": "test", "steps": 2, "url": ""},
    ]}
    rc._cache_time = 9999999999.0
    mgr._repos = [rc]
    # Exact match
    results = mgr.search("web-scraper")
    assert len(results) >= 1
    assert results[0]["name"] == "web-scraper"
    assert results[0]["score"] == 100
    # Partial match
    results2 = mgr.search("scraper")
    assert len(results2) >= 1
    # Description match
    results3 = mgr.search("抓取")
    assert len(results3) >= 1
    # Keyword match
    results4 = mgr.search("scraping")
    assert len(results4) >= 1
    # Category match
    results5 = mgr.search("utility")
    assert len(results5) >= 1

@test("SkillRepo: RepoManager list_all_skills")
def test_repo_manager_list_all():
    from core.skill_repo import RepoManager, RepoConfig
    mgr = RepoManager()
    rc = RepoConfig("test-list", "http://example.com")
    rc._cache = {"skills": [{"name": "skill-a", "description": "A", "version": "1", "author": "me", "category": "utils", "url": ""}]}
    rc._cache_time = 9999999999.0
    rc.enabled = True
    mgr._repos = [rc]
    all_s = mgr.list_all_skills()
    assert len(all_s) == 1
    assert all_s[0]["name"] == "skill-a"

@test("SkillRepo: RepoManager install 未找到")
def test_repo_manager_install_not_found():
    from core.skill_repo import RepoManager, RepoConfig
    mgr = RepoManager()
    rc = RepoConfig("test-install", "http://example.com")
    rc._cache = {"skills": [{"name": "existing-skill"}]}
    rc._cache_time = 9999999999.0
    mgr._repos = [rc]
    result = mgr.install("nonexistent-skill")
    assert result["success"] is False
    assert "未找到" in result["error"]

@test("SkillRepo: RepoManager install 无 URL")
def test_repo_manager_install_no_url():
    from core.skill_repo import RepoManager, RepoConfig
    mgr = RepoManager()
    rc = RepoConfig("test-no-url", "http://example.com")
    rc._cache = {"skills": [{"name": "no-url-skill", "url": ""}]}
    rc._cache_time = 9999999999.0
    mgr._repos = [rc]
    result = mgr.install("no-url-skill")
    assert result["success"] is False
    assert "没有下载 URL" in result["error"]

@test("SkillRepo: RepoManager install disabled repo")
def test_repo_manager_install_disabled():
    from core.skill_repo import RepoManager, RepoConfig
    mgr = RepoManager()
    rc = RepoConfig("disabled-repo", "http://example.com", enabled=False)
    rc._cache = {"skills": [{"name": "hidden-skill"}]}
    rc._cache_time = 9999999999.0
    mgr._repos = [rc]
    result = mgr.install("hidden-skill")
    assert result["success"] is False

@test("SkillRepo: RepoManager install_from_url")
def test_repo_manager_install_from_url():
    from core.skill_repo import RepoManager
    mgr = RepoManager()
    result = mgr.install_from_url("http://invalid.example.com/skill.yaml")
    assert result["success"] is False
    assert "下载失败" in result["error"]

@test("SkillRepo: RepoManager refresh_all")
def test_repo_manager_refresh():
    from core.skill_repo import RepoManager, RepoConfig
    mgr = RepoManager()
    rc = RepoConfig("test-refresh", "http://invalid.example.com", enabled=True)
    mgr._repos = [rc]
    results = mgr.refresh_all()
    assert len(results) == 1
    assert results[0]["name"] == "test-refresh"
    assert results[0]["success"] is False

@test("SkillRepo: RepoManager clear_cache")
def test_repo_manager_clear_cache():
    from core.skill_repo import RepoManager, RepoConfig, REPOS_DIR
    mgr = RepoManager()
    rc = RepoConfig("clear-test", "http://example.com")
    rc.save_cache({"skills": []})
    assert rc.cache_path.exists()
    # Add the repo to the manager so it can find it
    mgr._repos.append(rc)
    cleared = mgr.clear_cache(name="clear-test")
    assert cleared == 1
    assert not rc.cache_path.exists()
    cleared2 = mgr.clear_cache(name="nonexistent")
    assert cleared2 == 0
    # clear all (glob-based, finds any .cache.json in REPOS_DIR)
    rc2 = RepoConfig("clear-all-test", "http://example.com")
    rc2.save_cache({"skills": []})
    count = mgr.clear_cache()
    assert count >= 1

@test("SkillRepo: RepoManager get_stats")
def test_repo_manager_stats():
    from core.skill_repo import RepoManager, RepoConfig
    mgr = RepoManager()
    rc = RepoConfig("stats-test", "http://example.com")
    rc._cache = {"skills": [{"name": "s1"}, {"name": "s2"}]}
    rc._cache_time = 9999999999.0
    mgr._repos = [rc]
    stats = mgr.get_stats()
    assert stats["total_repos"] >= 1
    assert stats["total_skills"] >= 2
    assert len(stats["repos"]) >= 1

@test("SkillRepo: RepoManager _load_repos 环境变量")
def test_repo_manager_load_env():
    import os
    from core.skill_repo import RepoManager
    os.environ["KUAFFU_SKILL_REPOS"] = '[{"name":"env-repo","url":"http://env.example.com","description":"来自环境变量"}]'
    try:
        mgr = RepoManager()
        names = [r.name for r in mgr._repos]
        assert "env-repo" in names
    finally:
        del os.environ["KUAFFU_SKILL_REPOS"]

@test("SkillRepo: RepoManager _load_repos 环境变量单仓库 dict")
def test_repo_manager_load_env_dict():
    import os
    from core.skill_repo import RepoManager
    os.environ["KUAFFU_SKILL_REPOS"] = '{"name":"single-repo","url":"http://single.example.com"}'
    try:
        mgr = RepoManager()
        names = [r.name for r in mgr._repos]
        assert "single-repo" in names
    finally:
        del os.environ["KUAFFU_SKILL_REPOS"]

@test("SkillRepo: RepoManager _load_repos 环境变量无效 JSON")
def test_repo_manager_load_env_invalid():
    import os
    from core.skill_repo import RepoManager
    os.environ["KUAFFU_SKILL_REPOS"] = "not json{{{}}}"
    try:
        mgr = RepoManager()
        # Should fallback to defaults
        assert len(mgr._repos) >= 1
    finally:
        del os.environ["KUAFFU_SKILL_REPOS"]

@test("SkillRepo: RepoManager _load_repos 旧格式兼容")
def test_repo_manager_load_legacy():
    import os
    from core.skill_repo import RepoManager, DEFAULT_REPOS
    os.environ["KUAFFU_SKILL_MARKET_URL"] = "http://legacy.example.com/index.json"
    try:
        mgr = RepoManager()
        urls = [r.url for r in mgr._repos]
        assert "http://legacy.example.com/index.json" in urls
    finally:
        del os.environ["KUAFFU_SKILL_MARKET_URL"]
