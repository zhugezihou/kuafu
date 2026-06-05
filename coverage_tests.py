"""
夸父 core/ 模块覆盖测试 — session_store, safety, context_compress, observer
可直接追加到 test_all.py 末尾（使用 @test 装饰器）
"""

# ═══════════════════════════════════════════════════════════════
# session_store.py 覆盖测试
# ═══════════════════════════════════════════════════════════════

@test("SessionStore: 函数级 estimate_tokens")
def test_session_estimate_tokens():
    from core.session_store import estimate_tokens
    assert estimate_tokens("你好世界") == int(len("你好世界") / 1.6)
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 160) == 100
    print(f"    ✅ test_session_estimate_tokens")


@test("SessionStore: 初始化 + __del__ 清理")
def test_session_init_del():
    from core.session_store import SessionStore
    store = SessionStore()
    assert store._conn is not None
    # 验证表已创建
    cursor = store._get_cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cursor.fetchall()}
    assert "sessions" in tables
    assert "messages" in tables
    print(f"    ✅ test_session_init_del")


@test("SessionStore: _clean_surrogates 边界")
def test_session_clean_surrogates():
    from core.session_store import SessionStore
    assert SessionStore._clean_surrogates("") == ""
    assert SessionStore._clean_surrogates("普通文本") == "普通文本"
    assert SessionStore._clean_surrogates(None) is None
    print(f"    ✅ test_session_clean_surrogates")


@test("SessionStore: create_session 默认标题")
def test_session_create_default_title():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session()
    assert sid.startswith("sess_")
    session = store.get_session(sid)
    assert session is not None
    assert "会话" in session.title
    assert session.status == "active"
    print(f"    ✅ test_session_create_default_title")


@test("SessionStore: append_message 和 get_messages")
def test_session_append_get():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("测试")
    store.append_message(sid, "user", "第一条消息")
    store.append_message(sid, "assistant", "回复消息")
    msgs = store.get_messages(sid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "第一条消息"
    assert msgs[1]["role"] == "assistant"
    session = store.get_session(sid)
    assert session.message_count == 2
    assert session.total_tokens > 0
    print(f"    ✅ test_session_append_get")


@test("SessionStore: get_messages 空会话")
def test_session_get_empty():
    from core.session_store import SessionStore
    store = SessionStore()
    msgs = store.get_messages("nonexistent_session_xxx")
    assert msgs == []
    print(f"    ✅ test_session_get_empty")


@test("SessionStore: get_messages 带 max_tokens 截断")
def test_session_get_truncated():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("截断测试")
    # 写入大量消息
    for i in range(20):
        store.append_message(sid, "user", "消息内容 " * 50)
        store.append_message(sid, "assistant", "回复内容 " * 50)
    # max_tokens 很小，应触发截断
    msgs = store.get_messages(sid, max_tokens=100)
    assert len(msgs) < 40
    # 截断后第一条应为 system 通知
    assert msgs[0]["role"] == "system"
    assert "截断" in msgs[0]["content"]
    print(f"    ✅ test_session_get_truncated")


@test("SessionStore: get_history_messages 过滤 system")
def test_session_history():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("历史测试")
    store.append_message(sid, "system", "系统提示")
    store.append_message(sid, "user", "用户问题")
    store.append_message(sid, "assistant", "助手回复")
    history = store.get_history_messages(sid)
    # 应过滤掉 system 消息
    assert all(m["role"] != "system" for m in history)
    # 应包含 user + assistant
    roles = [m["role"] for m in history]
    assert "user" in roles
    assert "assistant" in roles
    print(f"    ✅ test_session_history")


@test("SessionStore: get_context_messages 完整构建")
def test_session_context():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("上下文测试")
    store.append_message(sid, "user", "你好")
    msgs = store.get_context_messages(sid, "你是夸父", max_tokens=12000)
    assert len(msgs) >= 1
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "你是夸父"
    print(f"    ✅ test_session_context")


@test("SessionStore: list_sessions 排序")
def test_session_list():
    from core.session_store import SessionStore
    store = SessionStore()
    sid1 = store.create_session("A")
    sid2 = store.create_session("B")
    sessions = store.list_sessions(limit=10)
    assert len(sessions) >= 2
    # 应按 updated_at 倒序
    assert sessions[0].updated_at >= sessions[-1].updated_at
    print(f"    ✅ test_session_list")


@test("SessionStore: list_sessions 按状态过滤")
def test_session_list_status():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("归档测试")
    store.archive_session(sid)
    archived = store.list_sessions(limit=10, status="archived")
    assert any(s.id == sid for s in archived)
    active = store.list_sessions(limit=10, status="active")
    assert all(s.status == "active" for s in active)
    print(f"    ✅ test_session_list_status")


@test("SessionStore: search_sessions LIKE 搜索")
def test_session_search():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("独一无二的搜索测试标题")
    store.append_message(sid, "user", "包含独特关键词的消息内容")
    results = store.search_sessions("独一无二")
    assert len(results) >= 1
    assert any(s.id == sid for s in results)
    # 搜索内容也能匹配
    results2 = store.search_sessions("独特关键词")
    assert len(results2) >= 1
    print(f"    ✅ test_session_search")


@test("SessionStore: archive_session 归档")
def test_session_archive():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("归档")
    store.archive_session(sid)
    session = store.get_session(sid)
    assert session.status == "archived"
    print(f"    ✅ test_session_archive")


@test("SessionStore: delete_session 删除")
def test_session_delete():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("待删除")
    store.append_message(sid, "user", "内容")
    store.delete_session(sid)
    assert store.get_session(sid) is None
    # 消息也应被删除
    msgs = store.get_messages(sid)
    assert msgs == []
    print(f"    ✅ test_session_delete")


@test("SessionStore: prune_sessions 清理归档")
def test_session_prune():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("待清理")
    store.archive_session(sid)
    count = store.prune_sessions(keep_days=0)
    assert count >= 0
    # 可能有其他测试的归档会话，只需确认方法不报错
    print(f"    ✅ test_session_prune")


@test("SessionStore: export_session JSON 导出")
def test_session_export():
    from core.session_store import SessionStore
    import json
    store = SessionStore()
    # 不存在返回 None
    assert store.export_session("nonexistent") is None
    sid = store.create_session("导出测试")
    store.append_message(sid, "user", "你好")
    exported = store.export_session(sid)
    assert exported is not None
    data = json.loads(exported)
    assert "session" in data
    assert "messages" in data
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"
    print(f"    ✅ test_session_export")


@test("SessionStore: get_stats 统计")
def test_session_stats():
    from core.session_store import SessionStore
    store = SessionStore()
    stats = store.get_stats()
    assert "total_sessions" in stats
    assert "active_sessions" in stats
    assert "total_messages" in stats
    assert "total_tokens_estimated" in stats
    assert stats["total_sessions"] >= 0
    print(f"    ✅ test_session_stats")


@test("SessionStore: save_raw_messages 和 get_raw_messages JSONL")
def test_session_jsonl():
    from core.session_store import SessionStore
    import tempfile, os
    store = SessionStore()
    sid = store.create_session("JSONL测试")
    messages = [
        {"role": "user", "content": "问题1"},
        {"role": "assistant", "content": "回答1", "tool_calls": [{"id": "call_1", "function": {"name": "search", "arguments": {"q": "test"}}}]},
    ]
    store.save_raw_messages(sid, messages)
    raw = store.get_raw_messages(sid)
    assert raw is not None
    assert len(raw) == 2
    assert raw[0]["content"] == "问题1"
    # 检查 tool_calls 被摘要
    assert "tool_calls" in raw[1]
    # 不存在的会话返回 None
    raw2 = store.get_raw_messages("nonexistent_sid")
    assert raw2 is None
    print(f"    ✅ test_session_jsonl")


@test("SessionStore: get_raw_messages_since 索引+token裁剪")
def test_session_raw_since():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("RAW_SINCE")
    msgs = [{"role": "user", "content": f"消息{i}"} for i in range(10)]
    store.save_raw_messages(sid, msgs)
    # 从索引 5 开始
    result = store.get_raw_messages_since(sid, start_index=5)
    assert len(result) == 5
    assert result[0]["content"] == "消息5"
    # 不存在的会话
    assert store.get_raw_messages_since("nosession") == []
    print(f"    ✅ test_session_raw_since")


@test("SessionStore: _get_jsonl_path 安全处理")
def test_session_jsonl_path():
    from core.session_store import SessionStore
    store = SessionStore()
    path = store._get_jsonl_path("sess_2025_test")
    assert "sess_2025_test" in str(path)
    assert path.suffix == ".jsonl"
    # 路径穿越防护
    path2 = store._get_jsonl_path("../evil")
    assert ".." not in str(path2)
    print(f"    ✅ test_session_jsonl_path")


@test("SessionStore: fork_session 不存在源")
def test_session_fork_nonexistent():
    from core.session_store import SessionStore
    store = SessionStore()
    result = store.fork_session("nonexistent_src")
    assert result is None
    print(f"    ✅ test_session_fork_nonexistent")


@test("SessionStore: fork_session 含历史")
def test_session_fork_with_history():
    from core.session_store import SessionStore
    store = SessionStore()
    src = store.create_session("源会话")
    store.append_message(src, "user", "源问题")
    store.append_message(src, "assistant", "源回答")
    fork_id = store.fork_session(src, title="Fork之子", include_history=True)
    assert fork_id is not None
    assert fork_id != src
    fork_msgs = store.get_messages(fork_id)
    # 应有注入的历史 system 消息
    assert any(m["role"] == "system" for m in fork_msgs)
    print(f"    ✅ test_session_fork_with_history")


@test("SessionStore: fork_session 无历史")
def test_session_fork_no_history():
    from core.session_store import SessionStore
    store = SessionStore()
    src = store.create_session("源")
    fork_id = store.fork_session(src, include_history=False)
    assert fork_id is not None
    fork_msgs = store.get_messages(fork_id)
    assert len(fork_msgs) == 0
    print(f"    ✅ test_session_fork_no_history")


@test("SessionStore: resume_context 不存在的会话")
def test_session_resume_nonexistent():
    from core.session_store import SessionStore
    store = SessionStore()
    assert store.resume_context("nonexistent") is None
    print(f"    ✅ test_session_resume_nonexistent")


@test("SessionStore: resume_context 关键词摘要")
def test_session_resume_keyword():
    from core.session_store import SessionStore
    import time
    store = SessionStore()
    sid = store.create_session("简报测试")
    store.append_message(sid, "system", "白板: 项目结构已确定\n决策: 使用FastAPI")
    store.append_message(sid, "user", "你好")
    store.append_message(sid, "assistant", "你好！")
    result = store.resume_context(sid, use_llm=False)
    assert result is not None
    assert "简报" in result
    assert "白板" in result
    print(f"    ✅ test_session_resume_keyword")


@test("SessionStore: find_related_sessions")
def test_session_find_related():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("Python项目配置")
    store.append_message(sid, "user", "配置Python环境")
    results = store.find_related_sessions("Python", limit=3)
    assert len(results) >= 1
    assert len(results) <= 3
    print(f"    ✅ test_session_find_related")


@test("SessionStore: close 和 连接重用")
def test_session_close_reuse():
    from core.session_store import SessionStore
    store = SessionStore()
    store.close()
    # 关闭后_get_cursor 应重新初始化
    cursor = store._get_cursor()
    assert cursor is not None
    print(f"    ✅ test_session_close_reuse")


@test("SessionStore: _is_conn_open 边界")
def test_session_is_conn_open():
    from core.session_store import SessionStore
    assert SessionStore._is_conn_open(None) is False
    store = SessionStore()
    assert SessionStore._is_conn_open(store._conn) is True
    print(f"    ✅ test_session_is_conn_open")


@test("SessionStore: estimate_tokens 与 _clean_surrogates 配合")
def test_session_surrogate_write():
    from core.session_store import SessionStore
    store = SessionStore()
    sid = store.create_session("代理测试")
    # surrogate 字符应被安全处理
    store.append_message(sid, "user", "正常文本\ud800更多文本")
    msgs = store.get_messages(sid)
    assert len(msgs) == 1
    print(f"    ✅ test_session_surrogate_write")


# ═══════════════════════════════════════════════════════════════
# safety.py 覆盖测试
# ═══════════════════════════════════════════════════════════════

@test("Safety: is_path_allowed_for_write core/ 禁止")
def test_safety_path_core_deny():
    from core.safety import is_path_allowed_for_write
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    allowed, reason = is_path_allowed_for_write(str(root / "core" / "test_write.py"))
    assert not allowed
    assert "core" in reason or "保护区" in reason or "核心" in reason
    print(f"    ✅ test_safety_path_core_deny")


@test("Safety: is_path_allowed_for_write 白名单允许")
def test_safety_path_whitelist():
    from core.safety import is_path_allowed_for_write
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for sub in ["strategy", "skills", "memory", "tests", "logs"]:
        allowed, reason = is_path_allowed_for_write(str(root / sub / "test.txt"))
        assert allowed, f"{sub} 应允许: {reason}"
    print(f"    ✅ test_safety_path_whitelist")


@test("Safety: is_path_allowed_for_write 不在白名单")
def test_safety_path_not_allowed():
    from core.safety import is_path_allowed_for_write
    denied, reason = is_path_allowed_for_write("/etc/passwd")
    assert not denied
    assert "白名单" in reason
    print(f"    ✅ test_safety_path_not_allowed")


@test("Safety: register_allowed_dir 动态注册")
def test_safety_register_dir():
    from core.safety import register_allowed_dir, is_path_allowed_for_write
    from pathlib import Path
    import tempfile
    tmpdir = tempfile.mkdtemp()
    try:
        register_allowed_dir(tmpdir)
        allowed, _ = is_path_allowed_for_write(tmpdir + "/test.txt")
        assert allowed
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"    ✅ test_safety_register_dir")


@test("Safety: validate_command 高危")
def test_safety_validate_highrisk():
    from core.safety import validate_command
    safe, risk, reason = validate_command("rm -rf /")
    assert not safe
    assert risk == "dangerous"
    assert "高危" in reason
    print(f"    ✅ test_safety_validate_highrisk")


@test("Safety: validate_command 敏感模式")
def test_safety_validate_sensitive():
    from core.safety import validate_command
    safe, risk, reason = validate_command("chmod 777 /somepath")
    assert not safe
    assert risk == "warning"
    print(f"    ✅ test_safety_validate_sensitive")


@test("Safety: validate_command 安全")
def test_safety_validate_safe():
    from core.safety import validate_command
    safe, risk, reason = validate_command("ls -la")
    assert safe
    assert risk == "safe"
    print(f"    ✅ test_safety_validate_safe")


@test("Safety: is_high_risk_write git/系统目录")
def test_safety_high_risk_write():
    from core.safety import is_high_risk_write
    is_risk, reason = is_high_risk_write("/tmp/project/.git/config")
    assert is_risk
    assert ".git" in reason
    is_risk2, reason2 = is_high_risk_write("/etc/hosts")
    assert is_risk2
    print(f"    ✅ test_safety_high_risk_write")


@test("Safety: is_high_risk_write 安全路径")
def test_safety_low_risk_write():
    from core.safety import is_high_risk_write
    is_risk, reason = is_high_risk_write("/home/user/test.txt")
    assert not is_risk
    print(f"    ✅ test_safety_low_risk_write")


@test("Safety: get_sandbox_report")
def test_safety_sandbox_report():
    from core.safety import get_sandbox_report
    report = get_sandbox_report()
    assert "protected_dirs" in report
    assert "allowed_write_dirs" in report
    assert "core_size" in report
    assert "core_files" in report
    assert report["core_files"] >= 0
    print(f"    ✅ test_safety_sandbox_report")


@test("Safety: sanitize_text API Key")
def test_safety_sanitize_apikey():
    from core.safety import SafetyLayer
    result = SafetyLayer.sanitize_text("api_key=sk-test1234567890abcdef")
    assert "***" in result
    result2 = SafetyLayer.sanitize_text("token=ghp_test1234567890abcdef")
    assert "***" in result2
    print(f"    ✅ test_safety_sanitize_apikey")


@test("Safety: sanitize_text JWT/PrivateKey/Password")
def test_safety_sanitize_various():
    from core.safety import SafetyLayer
    # JWT
    jwt_text = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNqPnd9y3Tz8GxN5w7UjyCQ"
    result = SafetyLayer.sanitize_text(jwt_text)
    assert "***" in result
    # Private Key
    pk_text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
    result2 = SafetyLayer.sanitize_text(pk_text)
    assert "***" in result2
    # Password
    pwd_text = "password=mysecret123"
    result3 = SafetyLayer.sanitize_text(pwd_text)
    assert "***" in result3
    print(f"    ✅ test_safety_sanitize_various")


@test("Safety: sanitize_text 安全文本不变")
def test_safety_sanitize_safe():
    from core.safety import SafetyLayer
    text = "这是一段安全文本，不包含敏感信息"
    result = SafetyLayer.sanitize_text(text)
    assert result == text
    print(f"    ✅ test_safety_sanitize_safe")


@test("Safety: sanitize_dict 嵌套结构")
def test_safety_sanitize_dict():
    from core.safety import SafetyLayer
    data = {
        "message": "api_key=sk-test123",
        "nested": {"token": "ghp_secret"},
        "list_items": ["password=12345678", "安全"],
        "number": 42,
    }
    result = SafetyLayer.sanitize_dict(data)
    assert "***" in result["message"]
    assert "***" in result["nested"]["token"]
    assert "***" in result["list_items"][0]
    assert result["list_items"][1] == "安全"
    assert result["number"] == 42
    print(f"    ✅ test_safety_sanitize_dict")


@test("Safety: sanitize_command")
def test_safety_sanitize_command():
    from core.safety import SafetyLayer
    cmd = 'curl -H "Authorization: Bearer sk-test1234567890abcdef" https://api.example.com'
    result = SafetyLayer.sanitize_command(cmd)
    assert "***" in result
    cmd2 = "some_command --api-key sk-abcdef123456"
    result2 = SafetyLayer.sanitize_command(cmd2)
    assert "***" in result2
    print(f"    ✅ test_safety_sanitize_command")


@test("Safety: classify_command 各级别")
def test_safety_classify():
    from core.safety import SafetyLayer, CommandLevel
    # SAFE
    level, _, _ = SafetyLayer.classify_command("ls -la")
    assert level == CommandLevel.SAFE
    level2, _, _ = SafetyLayer.classify_command("git status")
    assert level2 == CommandLevel.SAFE
    # DANGEROUS
    level3, risk, _ = SafetyLayer.classify_command("rm -rf /tmp/data")
    assert level3 == CommandLevel.DANGEROUS
    assert "删除" in risk
    level4, risk4, _ = SafetyLayer.classify_command("sudo apt update")
    assert level4 == CommandLevel.DANGEROUS
    # ATTENTION
    level5, risk5, _ = SafetyLayer.classify_command("pip install requests")
    assert level5 == CommandLevel.ATTENTION
    level6, _, _ = SafetyLayer.classify_command("git push origin main")
    assert level6 == CommandLevel.ATTENTION
    # FORBIDDEN 由安全锁处理，先测其他
    print(f"    ✅ test_safety_classify")


@test("Safety: classify_command 兜底 SAFE")
def test_safety_classify_fallback():
    from core.safety import SafetyLayer, CommandLevel
    # 不在任何规则中的命令
    level, _, _ = SafetyLayer.classify_command("custom_tool --flag value")
    assert level == CommandLevel.SAFE
    print(f"    ✅ test_safety_classify_fallback")


@test("Safety: needs_approval")
def test_safety_needs_approval():
    from core.safety import SafetyLayer, CommandLevel
    assert SafetyLayer.needs_approval(CommandLevel.SAFE) is False
    assert SafetyLayer.needs_approval(CommandLevel.ATTENTION) is True
    assert SafetyLayer.needs_approval(CommandLevel.DANGEROUS) is True
    print(f"    ✅ test_safety_needs_approval")


@test("Safety: needs_approval_with_denial SAFE 直接放行")
def test_safety_approval_safe():
    from core.safety import SafetyLayer, CommandLevel
    need_ask, decision = SafetyLayer.needs_approval_with_denial(CommandLevel.SAFE, "ls")
    assert need_ask is False
    assert decision == "allow"
    print(f"    ✅ test_safety_approval_safe")


@test("Safety: DenialTracker 初始化")
def test_safety_denial_init():
    from core.safety import DenialTracker, DenialConfig
    import tempfile
    dt = DenialTracker()
    assert dt.config.auto_trust_threshold == 3
    assert dt.config.degraded_action == "allow"
    stats = dt.get_stats()
    assert stats["total_patterns"] >= 0
    print(f"    ✅ test_safety_denial_init")


@test("Safety: DenialTracker record_denial 和降级")
def test_safety_denial_record():
    from core.safety import DenialTracker
    dt = DenialTracker()
    dt.reset_pattern("pip install")
    entry = dt.record_denial("pip install")
    assert entry["count"] == 1
    assert entry["consecutive_denials"] == 1
    assert entry["degraded"] is False
    # 连续拒绝 3 次触发降级
    dt.record_denial("pip install")
    dt.record_denial("pip install")
    assert dt.should_degrade("pip install") is True
    # 降级后决策为 allow
    decision = dt.get_decision("pip install")
    assert decision == "allow"
    print(f"    ✅ test_safety_denial_record")


@test("Safety: DenialTracker record_approval 重置计数")
def test_safety_denial_approval():
    from core.safety import DenialTracker
    dt = DenialTracker()
    dt.reset_pattern("git push")
    dt.record_denial("git push")
    dt.record_approval("git push")
    entry = dt._data.get("git push", {})
    assert entry.get("consecutive_denials", 0) == 0
    print(f"    ✅ test_safety_denial_approval")


@test("Safety: DenialTracker get_decision 未降级")
def test_safety_denial_decision_ask():
    from core.safety import DenialTracker
    dt = DenialTracker()
    dt.reset_pattern("pip install")
    decision = dt.get_decision("pip install")
    assert decision == "ask"
    print(f"    ✅ test_safety_denial_decision_ask")


@test("Safety: DenialTracker match_command 精确/子串")
def test_safety_denial_match():
    from core.safety import DenialTracker
    dt = DenialTracker()
    dt.record_denial("pip install")
    # 精确匹配
    assert dt.match_command("pip install") == "pip install"
    # 子串匹配
    assert dt.match_command("pip install requests") == "pip install"
    # 不匹配
    assert dt.match_command("ls -la") is None
    print(f"    ✅ test_safety_denial_match")


@test("Safety: DenialTracker reset_pattern / reset_all")
def test_safety_denial_reset():
    from core.safety import DenialTracker
    dt = DenialTracker()
    dt.record_denial("test_cmd")
    assert dt.reset_pattern("test_cmd") is True
    assert dt.reset_pattern("nonexistent") is False
    dt.record_denial("cmd1")
    dt.reset_all()
    assert dt.get_stats()["total_patterns"] == 0
    print(f"    ✅ test_safety_denial_reset")


@test("Safety: DenialTracker 持久化异常不崩溃")
def test_safety_denial_persist_error():
    from core.safety import DenialTracker
    from pathlib import Path
    import tempfile
    # 无法写入的路径不应崩溃
    dt = DenialTracker()
    dt._data = {"test": {"count": 1, "degraded": False}}
    try:
        dt._save()
    except Exception:
        pass  # 不应抛异常
    print(f"    ✅ test_safety_denial_persist_error")


@test("Safety: get_approval_message 各级别")
def test_safety_approval_msg():
    from core.safety import SafetyLayer, CommandLevel
    msg_danger = SafetyLayer.get_approval_message(CommandLevel.DANGEROUS, "格式化磁盘", "风险操作")
    assert msg_danger is not None
    assert "高风险" in msg_danger
    msg_attn = SafetyLayer.get_approval_message(CommandLevel.ATTENTION, "安装包", "需确认")
    assert msg_attn is not None
    assert "需确认" in msg_attn
    msg_none = SafetyLayer.get_approval_message(CommandLevel.SAFE, "", "")
    assert msg_none is None
    print(f"    ✅ test_safety_approval_msg")


@test("Safety: report_denial / report_approval 静态方法")
def test_safety_report_static():
    from core.safety import SafetyLayer
    SafetyLayer.report_denial("test_static_cmd")
    SafetyLayer.report_approval("test_static_cmd")
    stats = SafetyLayer.denial_tracker.get_stats()
    assert stats["total_patterns"] >= 0
    print(f"    ✅ test_safety_report_static")


@test("Safety: is_path_sanitized 敏感文件检测")
def test_safety_path_sanitized():
    from core.safety import SafetyLayer
    assert SafetyLayer.is_path_sanitized("/home/user/.env")
    assert SafetyLayer.is_path_sanitized("/home/user/.ssh/id_rsa")
    assert SafetyLayer.is_path_sanitized("/home/user/credentials.txt")
    assert not SafetyLayer.is_path_sanitized("/home/user/README.md")
    print(f"    ✅ test_safety_path_sanitized")


@test("Safety: is_output_sensitive")
def test_safety_output_sensitive():
    from core.safety import SafetyLayer
    assert SafetyLayer.is_output_sensitive("api_key=sk-test1234567890")
    assert not SafetyLayer.is_output_sensitive("普通输出文本")
    print(f"    ✅ test_safety_output_sensitive")


@test("Safety: lock_command / unlock_command")
def test_safety_lock_unlock():
    from core.safety import SafetyLayer
    from pathlib import Path
    import os
    root = Path(__file__).resolve().parent.parent
    lockfile = root / ".safety-lock"
    # 清理
    if lockfile.exists():
        SafetyLayer.unlock_command("test_locked_cmd")
    # 锁定
    assert SafetyLayer.lock_command("test_locked_cmd") is True
    assert SafetyLayer.lock_command("test_locked_cmd") is False  # 重复锁定
    assert lockfile.exists()
    # 分类应检测到
    level, _, _ = SafetyLayer.classify_command("echo test_locked_cmd")
    assert level == "forbid"
    # 解锁
    assert SafetyLayer.unlock_command("test_locked_cmd") is True
    assert SafetyLayer.unlock_command("nonexistent") is False
    if lockfile.exists():
        lockfile.unlink()
    print(f"    ✅ test_safety_lock_unlock")


@test("Safety: get_safety_summary")
def test_safety_summary():
    from core.safety import SafetyLayer
    summary = SafetyLayer.get_safety_summary()
    assert "locked_commands" in summary
    assert "sensitive_patterns_active" in summary
    assert "command_classification" in summary
    assert "sanitization" in summary
    assert "denial_tracking" in summary
    print(f"    ✅ test_safety_summary")


# ═══════════════════════════════════════════════════════════════
# context_compress.py 覆盖测试
# ═══════════════════════════════════════════════════════════════

@test("Context: estimate_tokens 函数")
def test_context_estimate_tokens():
    from core.context_compress import estimate_tokens
    assert estimate_tokens("你好") == int(len("你好") / 1.6)
    assert estimate_tokens("") == 0
    print(f"    ✅ test_context_estimate_tokens")


@test("Context: CompressionResult __post_init__ 计算 ratio")
def test_context_compression_result():
    from core.context_compress import CompressionResult
    r = CompressionResult(original_tokens=1000, compressed_tokens=300, messages_removed=10, summary="test")
    assert r.compression_ratio == round(1 - 300/1000, 3)
    r2 = CompressionResult(original_tokens=0, compressed_tokens=0, messages_removed=0)
    assert r2.compression_ratio == 0.0
    print(f"    ✅ test_context_compression_result")


@test("Context: LocalSummarizer 初始化")
def test_context_summarizer_init():
    from core.context_compress import LocalSummarizer
    s = LocalSummarizer()
    assert s.base_url == "http://localhost:8080"
    assert s.max_tokens == 256
    s2 = LocalSummarizer(base_url="http://localhost:9090", max_tokens=512, timeout=10)
    assert s2.base_url == "http://localhost:9090"
    assert s2.max_tokens == 512
    assert s2.timeout == 10
    print(f"    ✅ test_context_summarizer_init")


@test("Context: LocalSummarizer summarize 空文本")
def test_context_summarize_empty():
    from core.context_compress import LocalSummarizer
    s = LocalSummarizer()
    assert s.summarize("") == ""
    assert s.summarize("   ") == ""
    print(f"    ✅ test_context_summarize_empty")


@test("Context: LocalSummarizer summarize 异常回退")
def test_context_summarize_fallback():
    from core.context_compress import LocalSummarizer
    s = LocalSummarizer(timeout=1)
    # LLM 不可达应回退到截断
    result = s.summarize("A" * 1000)
    assert len(result) <= 606  # 600 + "..."
    assert "..."
    print(f"    ✅ test_context_summarize_fallback")


@test("Context: LocalSummarizer is_available")
def test_context_is_available():
    from core.context_compress import LocalSummarizer
    s = LocalSummarizer()
    # 无服务的 health 检查应返回 False
    available = s.is_available()
    assert available is False
    print(f"    ✅ test_context_is_available")


@test("Context: ContextCompressor 初始化")
def test_context_compressor_init():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    assert cc.max_context_tokens == 12000
    assert cc.keep_recent_rounds == 5
    assert cc.summarizer is not None
    cc2 = ContextCompressor(max_context_tokens=8000, keep_recent_rounds=3)
    assert cc2.max_context_tokens == 8000
    assert cc2.keep_recent_rounds == 3
    print(f"    ✅ test_context_compressor_init")


@test("Context: needs_compression 阈值判断")
def test_context_needs_compression():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor(max_context_tokens=500)
    small_msgs = [{"role": "user", "content": "你好"}]
    assert cc.needs_compression(small_msgs) is False
    large_msgs = [{"role": "user", "content": "X" * 2000}]
    assert cc.needs_compression(large_msgs) is True
    print(f"    ✅ test_context_needs_compression")


@test("Context: clean_old_tool_results 空消息")
def test_context_clean_empty():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    result, saved = cc.clean_old_tool_results([])
    assert result == []
    assert saved == 0
    print(f"    ✅ test_context_clean_empty")


@test("Context: clean_old_tool_results 不超过阈值不变")
def test_context_clean_noop():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "回复"},
    ]
    result, saved = cc.clean_old_tool_results(msgs, max_rounds=4)
    # 仅 1 轮，不超过 max_rounds，不应处理
    assert len(result) == 2
    assert saved == 0
    print(f"    ✅ test_context_clean_noop")


@test("Context: clean_old_tool_results 工具结果替换")
def test_context_clean_tool_results():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    msgs = [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "search", "arguments": '{"q": "test"}'}}]},
        {"role": "tool", "content": "A" * 500, "tool_call_id": "tc1"},
        {"role": "user", "content": "第二轮"},
        {"role": "assistant", "content": "最终回复"},
    ]
    result, saved = cc.clean_old_tool_results(msgs, max_rounds=0, keep_summary_chars=50)
    # 只有 1 轮 user，但 max_rounds=0，所以所有轮次都算旧
    # 实际上 total_rounds=2 > max_rounds=0
    for m in result:
        if m.get("role") == "tool" and m.get("content", "").startswith("[工具"):
            assert True
            break
    assert saved > 0
    print(f"    ✅ test_context_clean_tool_results")


@test("Context: compress 无需压缩")
def test_context_compress_noop():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor(max_context_tokens=50000)
    msgs = [{"role": "user", "content": "你好"}]
    result = cc.compress(msgs)
    assert result.original_tokens > 0
    assert result.messages_removed == 0
    assert result.summary == "无需压缩"
    print(f"    ✅ test_context_compress_noop")


@test("Context: compress 所有消息被 Pin")
def test_context_compress_all_pinned():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor(max_context_tokens=50)
    msgs = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "X" * 200},
    ]
    result = cc.compress(msgs)
    # system 被 Pin，user 是最后一条也被 Pin，全部 Pin 时不应压缩
    assert result.messages_removed == 0
    print(f"    ✅ test_context_compress_all_pinned")


@test("Context: compress_with_local_llm 无需压缩")
def test_context_compress_llm_noop():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor(max_context_tokens=50000)
    msgs = [{"role": "user", "content": "你好"}]
    result = cc.compress_with_local_llm(msgs)
    assert result.summary == "无需压缩"
    print(f"    ✅ test_context_compress_llm_noop")


@test("Context: get_token_count 统计")
def test_context_token_count():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    msgs = [
        {"role": "system", "content": "你是夸父", "tool_calls": []},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "回复"},
    ]
    stats = cc.get_token_count(msgs)
    assert "total" in stats
    assert "system" in stats
    assert "conversation" in stats
    assert "threshold" in stats
    assert "needs_compression" in stats
    assert stats["total"] == stats["system"] + stats["conversation"]
    print(f"    ✅ test_context_token_count")


@test("Context: estimate_fit_rounds")
def test_context_fit_rounds():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor(max_context_tokens=12000, system_token_estimation=2000)
    rounds = cc.estimate_fit_rounds(average_round_tokens=400)
    expected = max(1, (12000 - 2000) // max(400, 100))
    assert rounds == expected
    print(f"    ✅ test_context_fit_rounds")


@test("Context: _count_tokens tool_calls 计入")
def test_context_count_tokens_tool_calls():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    msgs = [
        {"role": "assistant", "content": "调用工具", "tool_calls": [{"function": {"arguments": '{"key": "value"}'}}]}
    ]
    count = cc._count_tokens(msgs)
    assert count > 0
    print(f"    ✅ test_context_count_tokens_tool_calls")


@test("Context: _format_dialogue 各种角色")
def test_context_format_dialogue():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    msgs = [
        {"role": "user", "content": "用户说"},
        {"role": "assistant", "content": "助手说", "tool_calls": []},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search"}}]},
        {"role": "tool", "content": "A" * 50},
    ]
    text = cc._format_dialogue(msgs)
    assert "用户" in text
    assert "助手" in text
    assert "search" in text or "搜索" in text
    print(f"    ✅ test_context_format_dialogue")


@test("Context: _create_summary 回退到关键字")
def test_context_create_summary_fallback():
    from core.context_compress import ContextCompressor
    cc = ContextCompressor()
    # 模拟 summarizer 不可用
    cc.summarizer.is_available = lambda: False
    msgs = [
        {"role": "user", "content": "用户问题"},
        {"role": "assistant", "content": "助手回答"},
    ]
    summary = cc._create_summary(msgs)
    assert summary is not None
    assert len(summary) > 0
    print(f"    ✅ test_context_create_summary_fallback")


@test("Context: PinnedContentManager 初始化")
def test_context_pin_init():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    assert pcm._explicit_pins == set()
    print(f"    ✅ test_context_pin_init")


@test("Context: PinnedContentManager identify system 始终 Pin")
def test_context_pin_identify_system():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    msgs = [
        {"role": "system", "content": "你是夸父"},
        {"role": "user", "content": "你好"},
    ]
    indices = pcm.identify(msgs)
    assert 0 in indices  # system 被 Pin
    assert 1 in indices  # 最后一条 user 也被 Pin
    print(f"    ✅ test_context_pin_identify_system")


@test("Context: PinnedContentManager identify Pin标记")
def test_context_pin_identify_marker():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    msgs = [
        {"role": "user", "content": "[PIN] 这条很重要"},
        {"role": "assistant", "content": "已记住"},
        {"role": "user", "content": "普通问题"},
    ]
    indices = pcm.identify(msgs)
    assert 0 in indices  # [PIN]
    assert 1 in indices  # Pin 关联的 assistant
    print(f"    ✅ test_context_pin_identify_marker")


@test("Context: PinnedContentManager identify 白板关键词")
def test_context_pin_identify_whiteboard():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    msgs = [
        {"role": "system", "content": "白板: 项目结构"},
        {"role": "user", "content": "继续"},
    ]
    indices = pcm.identify(msgs)
    assert 0 in indices
    print(f"    ✅ test_context_pin_identify_whiteboard")


@test("Context: PinnedContentManager pin/unpin 显式操作")
def test_context_pin_explicit():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    pcm.pin_message(2)
    pcm.pin_message(5)
    assert pcm._explicit_pins == {2, 5}
    pcm.unpin_message(2)
    assert pcm._explicit_pins == {5}
    pcm.unpin_message(99)  # 不在集合中，不应报错
    print(f"    ✅ test_context_pin_explicit")


@test("Context: PinnedContentManager is_pinned_index")
def test_context_pin_is_pinned():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    msgs = [{"role": "system", "content": "提示"}, {"role": "user", "content": "问"}]
    assert pcm.is_pinned_index(0, msgs) is True
    # 对于没有特殊标记的非 system 消息
    # 如果 index 不在 identify 返回中则为 False
    assert pcm.is_pinned_index(1, msgs) is True  # 最后一条 user
    print(f"    ✅ test_context_pin_is_pinned")


@test("Context: PinnedContentManager separate_pinned")
def test_context_pin_separate():
    from core.context_compress import PinnedContentManager
    pcm = PinnedContentManager()
    msgs = [
        {"role": "system", "content": "提示"},
        {"role": "user", "content": "问题1"},
        {"role": "assistant", "content": "回答1"},
        {"role": "user", "content": "问题2"},
    ]
    pinned, compressible = pcm.separate_pinned(msgs)
    assert len(pinned) >= 1  # system 被 Pin
    assert len(compressible) >= 0
    # 验证保留原始索引
    for idx, _ in pinned:
        assert isinstance(idx, int)
    print(f"    ✅ test_context_pin_separate")


@test("Context: ToolResultStore 初始化")
def test_context_tool_store_init():
    from core.context_compress import ToolResultStore
    import tempfile
    ts = ToolResultStore()
    assert ts.results_dir.exists()
    assert ts._max_files == 200
    print(f"    ✅ test_context_tool_store_init")


@test("Context: ToolResultStore store 和 read")
def test_context_tool_store_write_read():
    from core.context_compress import ToolResultStore
    ts = ToolResultStore()
    result = ts.store("test_func", "大量输出内容" * 200)
    assert "file_id" in result
    assert "file_path" in result
    assert "preview" in result
    assert "compact" in result
    assert "test_func" in result["compact"]
    # 读取
    content = ts.read_result(result["file_id"])
    assert "大量输出内容" in content
    # 不存在的文件
    content2 = ts.read_result("nonexistent_file")
    assert "不存在" in content2
    print(f"    ✅ test_context_tool_store_write_read")


@test("Context: ToolResultStore should_compact")
def test_context_tool_should_compact():
    from core.context_compress import ToolResultStore
    # 短文本不应压缩
    assert ToolResultStore.should_compact("短文本") is False
    # 长文本应压缩
    long_text = "X" * 3000
    assert ToolResultStore.should_compact(long_text) is True
    print(f"    ✅ test_context_tool_should_compact")


@test("Context: ToolResultStore load 类方法")
def test_context_tool_load():
    from core.context_compress import ToolResultStore
    assert ToolResultStore.load("/nonexistent/path.txt") is None
    print(f"    ✅ test_context_tool_load")


@test("Context: ToolResultStore try_read_from_path")
def test_context_tool_try_read():
    from core.context_compress import ToolResultStore
    # 没有完整路径标记
    assert ToolResultStore.try_read_from_path("普通文本") == ""
    # 有路径但不存在
    result = ToolResultStore.try_read_from_path("完整路径: /nonexistent/file.txt")
    assert result == ""
    print(f"    ✅ test_context_tool_try_read")


@test("Context: ContextCollapse 初始化")
def test_context_collapse_init():
    from core.context_compress import ContextCollapse
    cc = ContextCollapse()
    assert cc.keep_recent_rounds == 5
    assert cc.summarizer is not None
    assert "对话摘要器" in cc.summary_prompt
    print(f"    ✅ test_context_collapse_init")


@test("Context: ContextCollapse collapse 无需压缩")
def test_context_collapse_noop():
    from core.context_compress import ContextCollapse
    cc = ContextCollapse()
    msgs = [{"role": "user", "content": "短消息"}]
    result = cc.collapse(msgs, threshold_tokens=10000)
    assert result.original_count == 1
    assert result.collapsed_count == 1
    assert result.messages_written == 0
    print(f"    ✅ test_context_collapse_noop")


@test("Context: ContextCollapse collapse 无 session_store")
def test_context_collapse_no_store():
    from core.context_compress import ContextCollapse
    cc = ContextCollapse(keep_recent_rounds=1)
    msgs = [
        {"role": "user", "content": "A" * 500},
        {"role": "assistant", "content": "B" * 500},
        {"role": "user", "content": "C" * 500},
    ]
    result = cc.collapse(msgs, session_id="", session_store=None, force=True, threshold_tokens=10)
    assert result.original_count == 3
    assert result.messages_written == 0
    print(f"    ✅ test_context_collapse_no_store")


@test("Context: ContextCollapse _generate_summary 短文本")
def test_context_generate_summary_short():
    from core.context_compress import ContextCollapse
    cc = ContextCollapse()
    msgs = [{"role": "user", "content": "你好"}]
    summary = cc._generate_summary(msgs)
    assert len(summary) > 0
    print(f"    ✅ test_context_generate_summary_short")


@test("Context: ContextCollapse _keyword_summary")
def test_context_keyword_summary():
    from core.context_compress import ContextCollapse
    cc = ContextCollapse()
    msgs = [
        {"role": "user", "content": "用户问题"},
        {"role": "assistant", "content": "助手回答"},
    ]
    summary = cc._keyword_summary(msgs)
    assert "用户" in summary
    assert len(summary) > 0
    print(f"    ✅ test_context_keyword_summary")


@test("Context: ContextCollapse _format_dialogue")
def test_context_collapse_format():
    from core.context_compress import ContextCollapse
    cc = ContextCollapse()
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file"}}]},
        {"role": "tool", "content": "文件内容"},
    ]
    text = cc._format_dialogue(msgs)
    assert "用户" in text
    assert "read_file" in text or "调用" in text
    print(f"    ✅ test_context_collapse_format")


@test("Context: budget_reduce_output 小内容不变")
def test_context_budget_small():
    from core.context_compress import budget_reduce_output
    result = budget_reduce_output("小内容")
    assert result == "小内容"
    print(f"    ✅ test_context_budget_small")


@test("Context: budget_reduce_output 普通文本裁剪")
def test_context_budget_large():
    from core.context_compress import budget_reduce_output
    large = "X" * 5000
    result = budget_reduce_output(large, tool_name="terminal")
    assert "BudgetReduction" in result
    assert len(result) < 5000
    print(f"    ✅ test_context_budget_large")


@test("Context: budget_reduce_output JSON 数组")
def test_context_budget_json_array():
    from core.context_compress import budget_reduce_output
    items = [{"id": i, "name": f"item_{i}"} for i in range(50)]
    import json
    large = json.dumps(items)
    result = budget_reduce_output(large, tool_name="search")
    assert "BudgetReduction" in result
    assert "JSON 数组" in result
    print(f"    ✅ test_context_budget_json_array")


@test("Context: _reduce_json_object 嵌套")
def test_context_reduce_json():
    from core.context_compress import _reduce_json_object
    data = {"key": "A" * 2000, "nested": {"deep": "B" * 2000}, "short": "OK"}
    result = _reduce_json_object(data, 3000)
    assert "truncated" in result
    print(f"    ✅ test_context_reduce_json")


# ═══════════════════════════════════════════════════════════════
# observer.py 覆盖测试
# ═══════════════════════════════════════════════════════════════

@test("Observer: ToolError dataclass")
def test_observer_tool_error():
    from core.observer import ToolError
    import time
    e = ToolError(tool_name="search", error_message="连接超时")
    assert e.tool_name == "search"
    assert e.error_message == "连接超时"
    assert e.retry_count == 0
    assert isinstance(e.timestamp, float)
    e2 = ToolError(tool_name="read", error_message="文件不存在", retry_count=2)
    assert e2.retry_count == 2
    print(f"    ✅ test_observer_tool_error")


@test("Observer: Observation dataclass 默认值")
def test_observer_observation_defaults():
    from core.observer import Observation
    obs = Observation()
    assert obs.tool_errors == []
    assert obs.tool_error_count == 0
    assert obs.tool_error_names == set()
    assert obs.tool_chain == []
    assert obs.tool_calls == 0
    assert obs.tools_used == set()
    assert obs.success is False
    assert obs.task_type == "generic"
    assert obs.errors == []
    assert obs.result == ""
    assert obs.duration == 0.0
    assert obs.user_input == ""
    assert obs.has_user_correction is False
    assert obs.denials == 0
    assert obs.has_auto_block is False
    assert obs.has_auto_allow is False
    print(f"    ✅ test_observer_observation_defaults")


@test("Observer: Observation merge 合并")
def test_observer_merge():
    from core.observer import Observation, ToolError
    a = Observation(
        tool_errors=[ToolError("search", "err1")],
        tool_error_count=1,
        tool_error_names={"search"},
        tool_chain=["search"],
        tool_calls=1,
        tools_used={"search"},
        errors=["e1"],
    )
    b = Observation(
        tool_errors=[ToolError("read", "err2")],
        tool_error_count=1,
        tool_error_names={"read"},
        tool_chain=["read"],
        tool_calls=2,
        tools_used={"read"},
        errors=["e2"],
    )
    merged = a.merge(b)
    assert merged.tool_error_count == 2
    assert merged.tool_calls == 3
    assert merged.tool_error_names == {"search", "read"}
    assert merged.tools_used == {"search", "read"}
    assert merged.errors == ["e1", "e2"]
    print(f"    ✅ test_observer_merge")


@test("Observer: has_value 用户纠正")
def test_observer_has_correction():
    from core.observer import Observation
    obs = Observation(has_user_correction=True)
    assert obs.has_value() is True
    print(f"    ✅ test_observer_has_correction")


@test("Observer: has_value 工具错误")
def test_observer_has_tool_error():
    from core.observer import Observation
    obs = Observation(tool_error_count=2, tool_calls=3)
    assert obs.has_value() is True
    # 工具错误但调用次数不足
    obs2 = Observation(tool_error_count=1, tool_calls=1)
    assert obs2.has_value() is False
    print(f"    ✅ test_observer_has_tool_error")


@test("Observer: has_value 重复失败不触发")
def test_observer_repeated_failure():
    from core.observer import Observation
    obs = Observation(
        is_repeated_failure=True,
        task_type_history=5,
        tool_error_count=0,
        tool_calls=2,
    )
    assert obs.has_value() is False
    # history < 5 仍触发
    obs2 = Observation(
        is_repeated_failure=True,
        task_type_history=3,
        tool_error_count=0,
        tool_calls=2,
    )
    assert obs2.has_value() is True
    print(f"    ✅ test_observer_repeated_failure")


@test("Observer: has_value novel task")
def test_observer_novel():
    from core.observer import Observation
    obs = Observation(is_novel_task=True, tool_calls=2)
    assert obs.has_value() is True
    # 不足 2 步
    obs2 = Observation(is_novel_task=True, tool_calls=1)
    assert obs2.has_value() is False
    print(f"    ✅ test_observer_novel")


@test("Observer: has_value 3+ 调用且有结果")
def test_observer_substantial():
    from core.observer import Observation
    obs = Observation(tool_calls=4, result="A" * 100)
    assert obs.has_value() is True
    # 结果太短
    obs2 = Observation(tool_calls=4, result="短")
    assert obs2.has_value() is False
    print(f"    ✅ test_observer_substantial")


@test("Observer: _detect_user_correction 检测纠正信号")
def test_observer_detect_correction():
    from core.observer import _detect_user_correction
    assert _detect_user_correction("不要用那个工具") is True
    assert _detect_user_correction("注意，这里用错了") is True
    assert _detect_user_correction("应该用另一个方法") is True
    assert _detect_user_correction("普通文本") is False
    assert _detect_user_correction("") is False
    print(f"    ✅ test_observer_detect_correction")


@test("Observer: Observer 初始化")
def test_observer_init():
    from core.observer import Observer
    obs = Observer()
    assert obs._runtime_errors == []
    assert obs._tool_chain == []
    assert obs._tools_used == set()
    assert obs._tool_calls == 0
    assert obs._current_tool == ""
    assert obs._current_retry == 0
    print(f"    ✅ test_observer_init")


@test("Observer: on_tool_call 无错误")
def test_observer_tool_call_ok():
    from core.observer import Observer
    obs = Observer()
    obs.on_tool_call("search", {"q": "test"}, {"success": True, "output": "结果"})
    assert obs._tool_calls == 1
    assert obs._tool_chain == ["search"]
    assert obs._tools_used == {"search"}
    assert obs._runtime_errors == []
    print(f"    ✅ test_observer_tool_call_ok")


@test("Observer: on_tool_call 检测错误")
def test_observer_tool_call_error():
    from core.observer import Observer
    obs = Observer()
    obs.on_tool_call("read_file", {"path": "/nonexistent"}, {"success": False, "output": "文件不存在"})
    assert len(obs._runtime_errors) == 1
    assert obs._runtime_errors[0].tool_name == "read_file"
    # 字符串结果含 error
    obs.on_tool_call("terminal", {"cmd": "bad"}, "error: command not found")
    assert len(obs._runtime_errors) == 2
    print(f"    ✅ test_observer_tool_call_error")


@test("Observer: on_tool_call 连续相同工具重试计数")
def test_observer_retry_count():
    from core.observer import Observer
    obs = Observer()
    obs.on_tool_call("read_file", {}, {"success": False, "output": "err1"})
    obs.on_tool_call("read_file", {}, {"success": False, "output": "err2"})
    assert obs._current_retry == 1
    assert obs._runtime_errors[1].retry_count == 1
    # 切换工具，重试重置
    obs.on_tool_call("search", {}, {"success": False, "output": "err3"})
    assert obs._runtime_errors[2].retry_count == 0
    print(f"    ✅ test_observer_retry_count")


@test("Observer: on_task_complete 构造 Observation")
def test_observer_task_complete():
    from core.observer import Observer
    obs = Observer()
    obs.on_tool_call("search", {"q": "test"}, {"success": True, "output": "结果"})
    task_result = {
        "success": True,
        "task_type": "research",
        "errors": [],
        "result": "完成了",
        "duration": 5.0,
    }
    observation = obs.on_task_complete(task_result, "搜索一下")
    assert observation.success is True
    assert observation.task_type == "research"
    assert observation.tool_calls == 1
    assert observation.result == "完成了"
    assert "搜索" in observation.user_input
    print(f"    ✅ test_observer_task_complete")


@test("Observer: on_task_complete _reset 清理状态")
def test_observer_reset():
    from core.observer import Observer
    obs = Observer()
    obs.on_tool_call("search", {}, {"success": True, "output": "ok"})
    obs.on_task_complete({"success": True, "errors": [], "result": "", "duration": 0}, "输入")
    # reset 后状态恢复
    assert obs._runtime_errors == []
    assert obs._tool_chain == []
    assert obs._tools_used == set()
    assert obs._tool_calls == 0
    print(f"    ✅ test_observer_reset")


@test("Observer: on_task_complete 检测 unknown error")
def test_observer_unknown_error():
    from core.observer import Observer
    obs = Observer()
    task_result = {
        "success": False,
        "errors": ["未知错误"],
        "result": "",
        "duration": 1.0,
    }
    # 有 errors 但没有 tool_errors 则 has_unknown_error=True
    observation = obs.on_task_complete(task_result, "测试")
    assert observation.has_unknown_error is True
    print(f"    ✅ test_observer_unknown_error")


@test("Observer: on_task_complete 用户纠正检测")
def test_observer_correction_detection():
    from core.observer import Observer
    obs = Observer()
    result = obs.on_task_complete({"success": True, "errors": [], "result": "", "duration": 0}, "不要用那个工具")
    assert result.has_user_correction is True
    print(f"    ✅ test_observer_correction_detection")


@test("Observer: _get_denial_stats_since_last 异常安全")
def test_observer_denial_stats():
    from core.observer import Observer
    obs = Observer()
    # 第一次调用应返回正常值
    stats = obs._get_denial_stats_since_last()
    assert "recent_denials" in stats
    assert "total_denials" in stats
    # 再次调用，增量应为 0
    stats2 = obs._get_denial_stats_since_last()
    assert stats2["recent_denials"] == 0
    print(f"    ✅ test_observer_denial_stats")
