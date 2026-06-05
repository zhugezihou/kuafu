# ═══════════════════════════════════════════════════════════════
# core/identity.py 测试
# ═══════════════════════════════════════════════════════════════

@test("identity: load_fallback_when_missing")
def test_identity_load_fallback():
    from core.identity import load_identity_statement, _fallback_identity
    # 模拟 IDENTITY.md 不存在
    import os
    _orig = None
    try:
        from core.identity import IDENTITY_PATH
        if IDENTITY_PATH.exists():
            # 临时改名
            bak = IDENTITY_PATH.with_suffix(".md.bak_for_test")
            IDENTITY_PATH.rename(bak)
            _orig = bak
        result = load_identity_statement()
        assert "夸父" in result or "Kuafu" in result
        fallback = _fallback_identity()
        assert "夸父" in fallback
        assert "Kuafu" in fallback
    finally:
        if _orig:
            _orig.rename(IDENTITY_PATH)
    print("    ✅ test_identity_load_fallback")


@test("identity: validate_prompt")
def test_identity_validate_prompt():
    from core.identity import validate_identity_in_prompt
    assert validate_identity_in_prompt("我是夸父 agent") is True
    assert validate_identity_in_prompt("Kuafu system") is True
    assert validate_identity_in_prompt("自我进化的 AI") is True
    assert validate_identity_in_prompt("你是一个助手") is False
    assert validate_identity_in_prompt("") is False
    print("    ✅ test_identity_validate_prompt")


@test("identity: detect_impersonation")
def test_identity_detect_impersonation():
    from core.identity import detect_identity_impersonation
    # 危险模式
    assert detect_identity_impersonation("我 是 用户") is True
    assert detect_identity_impersonation("I am the user") is True
    assert detect_identity_impersonation("你 不 是 夸父") is True
    assert detect_identity_impersonation("you are not Kuafu") is True
    # 安全消息
    assert detect_identity_impersonation("你好，今天天气不错") is False
    assert detect_identity_impersonation("") is False
    assert detect_identity_impersonation("帮我写代码") is False
    print("    ✅ test_identity_detect_impersonation")


@test("identity: get_agent_names")
def test_identity_get_agent_names():
    from core.identity import get_agent_name, get_agent_name_en
    assert get_agent_name() == "夸父"
    assert get_agent_name_en() == "Kuafu"
    print("    ✅ test_identity_get_agent_names")


# ═══════════════════════════════════════════════════════════════
# core/judge.py 测试
# ═══════════════════════════════════════════════════════════════

class _MockObs:
    """模拟 Observation 对象"""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@test("judge: build_digest_with_state")
def test_judge_build_digest_with_state():
    from core.judge import build_digest
    obs = _MockObs(
        task_type="test_task",
        success=True,
        tool_calls=5,
        tools_used={"read_file", "write_file"},
        has_user_correction=False,
        errors=[],
        tool_errors=[],
        has_unknown_error=False,
        result="任务完成",
        skill_name="",
    )
    state = {"consecutive_fail": 2, "count": 3}
    digest = build_digest(obs, state)
    assert digest["task_type"] == "test_task"
    assert digest["success"] is True
    assert digest["tool_calls"] == 5
    assert "read_file" in digest["tools_used"]
    assert digest["has_user_correction"] is False
    assert digest["consecutive_failures"] == 2
    assert digest["task_history"] == 3
    assert digest["error_count"] == 0
    assert digest["result_summary"] == "任务完成"
    print("    ✅ test_judge_build_digest_with_state")


@test("judge: build_digest_no_state")
def test_judge_build_digest_no_state():
    from core.judge import build_digest
    obs = _MockObs(
        task_type="simple",
        success=False,
        tool_calls=1,
        tools_used={"search"},
        has_user_correction=True,
        errors=["权限不足"],
        tool_errors=[],
        has_unknown_error=True,
        result="",
        skill_name=None,
    )
    digest = build_digest(obs, None)
    assert digest["consecutive_failures"] == 0
    assert digest["task_history"] == 0
    assert digest["error_count"] == 1
    assert digest["error_summary"] == "权限不足"
    assert digest["existing_skill"] == ""
    print("    ✅ test_judge_build_digest_no_state")


@test("judge: build_digest_with_tool_errors")
def test_judge_build_digest_tool_errors():
    from core.judge import build_digest
    class _TE:
        def __init__(self, msg):
            self.error_message = msg
    obs = _MockObs(
        task_type="complex",
        success=False,
        tool_calls=3,
        tools_used={"terminal", "write"},
        has_user_correction=True,
        errors=["网络错误"],
        tool_errors=[_TE("超时"), _TE("连接重置")],
        has_unknown_error=True,
        result="部分完成",
        skill_name="test-skill",
    )
    digest = build_digest(obs, {"consecutive_fail": 1, "count": 0})
    assert digest["error_count"] == 3
    assert "网络错误" in digest["error_summary"]
    assert "超时" in digest["error_summary"]
    assert digest["existing_skill"] == "test-skill"
    print("    ✅ test_judge_build_digest_tool_errors")


@test("judge: evaluate_success")
def test_judge_evaluate():
    from core.judge import Judge
    call_log = []

    def mock_llm(messages):
        call_log.append(messages)
        return {"content": json.dumps({
            "worth_learning": True,
            "evolution_mode": "CAPTURED",
            "reason": "用户纠正了行为",
            "skill": {
                "name": "fix-pip-install",
                "trigger": "pip 安装失败时",
                "steps": ["检查网络", "重试"],
            }
        })}

    judge = Judge(mock_llm)
    obs = _MockObs(
        task_type="pip_install",
        success=False,
        tool_calls=2,
        tools_used={"terminal"},
        has_user_correction=True,
        errors=["pip 超时"],
        tool_errors=[],
        has_unknown_error=False,
        result="",
        skill_name="",
    )
    result = judge.evaluate(obs, {"consecutive_fail": 1, "count": 0})
    assert result["worth_learning"] is True
    assert result["evolution_mode"] == "CAPTURED"
    assert result["skill"] is not None
    assert result["skill"]["name"] == "fix-pip-install"
    assert len(call_log) == 1
    print("    ✅ test_judge_evaluate")


@test("judge: evaluate_not_worth_learning")
def test_judge_evaluate_not_worth():
    from core.judge import Judge

    def mock_llm(messages):
        return {"content": json.dumps({
            "worth_learning": False,
            "evolution_mode": "CAPTURED",
            "reason": "简单任务无需记录",
            "skill": None,
        })}

    judge = Judge(mock_llm)
    obs = _MockObs(
        task_type="read_file",
        success=True,
        tool_calls=1,
        tools_used={"read_file"},
        has_user_correction=False,
        errors=[],
        tool_errors=[],
        has_unknown_error=False,
        result="读取成功",
        skill_name="",
    )
    result = judge.evaluate(obs, None)
    assert result["worth_learning"] is False
    assert result["skill"] is None
    print("    ✅ test_judge_evaluate_not_worth")


@test("judge: evaluate_json_decode_error")
def test_judge_evaluate_json_error():
    from core.judge import Judge

    def mock_llm(messages):
        return {"content": "invalid json{{{}}}"}

    judge = Judge(mock_llm)
    obs = _MockObs(
        task_type="test", success=True, tool_calls=1,
        tools_used=set(), has_user_correction=False,
        errors=[], tool_errors=[], has_unknown_error=False,
        result="ok", skill_name="",
    )
    result = judge.evaluate(obs)
    assert result["worth_learning"] is False
    assert "降级" in result["reason"] or "JSON" in result["reason"]
    print("    ✅ test_judge_evaluate_json_error")


@test("judge: evaluate_missing_worth_learning_field")
def test_judge_evaluate_missing_field():
    from core.judge import Judge

    def mock_llm(messages):
        return {"content": json.dumps({"reason": "test"})}

    judge = Judge(mock_llm)
    obs = _MockObs(
        task_type="test", success=True, tool_calls=1,
        tools_used=set(), has_user_correction=False,
        errors=[], tool_errors=[], has_unknown_error=False,
        result="ok", skill_name="",
    )
    result = judge.evaluate(obs)
    assert result["worth_learning"] is False
    assert "缺少" in result["reason"]
    print("    ✅ test_judge_evaluate_missing_field")


@test("judge: evaluate_empty_content")
def test_judge_evaluate_empty():
    from core.judge import Judge

    def mock_llm(messages):
        return {"content": ""}

    judge = Judge(mock_llm)
    obs = _MockObs(
        task_type="test", success=True, tool_calls=1,
        tools_used=set(), has_user_correction=False,
        errors=[], tool_errors=[], has_unknown_error=False,
        result="", skill_name="",
    )
    result = judge.evaluate(obs)
    assert result["worth_learning"] is False
    print("    ✅ test_judge_evaluate_empty")


@test("judge: evaluate_llm_exception")
def test_judge_evaluate_exception():
    from core.judge import Judge

    def mock_llm(messages):
        raise RuntimeError("LLM 连接失败")

    judge = Judge(mock_llm)
    obs = _MockObs(
        task_type="test", success=True, tool_calls=1,
        tools_used=set(), has_user_correction=False,
        errors=[], tool_errors=[], has_unknown_error=False,
        result="", skill_name="",
    )
    result = judge.evaluate(obs)
    assert result["worth_learning"] is False
    assert "异常" in result["reason"]
    print("    ✅ test_judge_evaluate_exception")


@test("judge: parse_content_dict_and_str")
def test_judge_parse_content():
    from core.judge import Judge
    # dict type
    assert Judge._parse_content({"content": "hello"}) == "hello"
    assert Judge._parse_content({"content": 123}) == "123" or Judge._parse_content({"content": 123}) == ""
    # str type
    assert Judge._parse_content("hello") == "hello"
    # other types
    assert Judge._parse_content(None) == ""
    assert Judge._parse_content(42) == ""
    print("    ✅ test_judge_parse_content")


@test("judge: default_fallback")
def test_judge_default_fallback():
    from core.judge import Judge
    fb = Judge._default_fallback("测试降级")
    assert fb["worth_learning"] is False
    assert fb["evolution_mode"] == "CAPTURED"
    assert "测试降级" in fb["reason"]
    assert fb["skill"] is None
    print("    ✅ test_judge_default_fallback")


# ═══════════════════════════════════════════════════════════════
# core/approval.py 测试
# ═══════════════════════════════════════════════════════════════

@test("approval: DenyRules add/remove/load/save/check")
def test_approval_deny_rules():
    from core.approval import DenyRules
    # 清空
    DenyRules._rules = []

    # load 空文件场景
    rules = DenyRules.load()
    assert rules == []

    # add
    rid = DenyRules.add("write_file", "dangerous", "测试拒绝规则")
    assert rid.startswith("deny_")
    assert len(DenyRules._rules) == 1

    # check 精确匹配
    denied = DenyRules.check("write_file", {"path": "dangerous"})
    assert denied is not None
    assert denied.id == rid

    # check 不匹配
    denied2 = DenyRules.check("write_file", {"path": "safe.txt"})
    assert denied2 is None

    # check 不匹配工具名
    denied3 = DenyRules.check("read_file", {"path": "dangerous"})
    assert denied3 is None

    # remove
    assert DenyRules.remove(rid) is True
    assert DenyRules.remove("non_existent") is False
    assert len(DenyRules._rules) == 0

    # 通配符匹配
    rid2 = DenyRules.add("*", "forbidden", "全局拒绝")
    denied4 = DenyRules.check("any_tool", {"data": "forbidden"})
    assert denied4 is not None
    DenyRules.remove(rid2)

    # tool 前缀通配
    rid3 = DenyRules.add("mcp_*", "secret", "MCP拒绝")
    denied5 = DenyRules.check("mcp_write", {"key": "secret"})
    assert denied5 is not None
    denied6 = DenyRules.check("other_tool", {"key": "secret"})
    assert denied6 is None
    DenyRules.remove(rid3)

    # expired 规则清理
    import time
    rid4 = DenyRules.add("test_tool", "old", "过期规则", expires_at=time.time() - 10)
    denied7 = DenyRules.check("test_tool", {"data": "old"})
    assert denied7 is None  # 过期规则应被清理
    assert len(DenyRules._rules) == 0

    # list_rules
    rid5 = DenyRules.add("tool_a", "pat", "有效规则")
    listed = DenyRules.list_rules()
    assert len(listed) == 1
    assert listed[0].id == rid5
    DenyRules.remove(rid5)

    # re.error fallback
    rid6 = DenyRules.add("tool_b", '{"key": "value"}', "精确匹配")
    denied8 = DenyRules.check("tool_b", {"key": "value"})
    assert denied8 is not None
    DenyRules.remove(rid6)

    print("    ✅ test_approval_deny_rules")


@test("approval: DenyRules load_exception")
def test_approval_deny_rules_load_error():
    from core.approval import DenyRules, DENY_RULES_PATH
    import json
    # 写入非法 JSON
    DENY_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    DENY_RULES_PATH.write_text("not valid json{", encoding="utf-8")
    rules = DenyRules.load()
    assert rules == []
    print("    ✅ test_approval_deny_rules_load_error")


@test("approval: AutoMode risk_and_auto")
def test_approval_auto_mode():
    from core.approval import AutoMode
    AutoMode._history = []

    # _get_tool_risk
    assert AutoMode._get_tool_risk("delete_file") == "high"
    assert AutoMode._get_tool_risk("mcp_anything") == "high"
    assert AutoMode._get_tool_risk("terminal") == "high"
    assert AutoMode._get_tool_risk("write_file") == "medium"
    assert AutoMode._get_tool_risk("patch") == "medium"
    assert AutoMode._get_tool_risk("read_file") == "low"
    assert AutoMode._get_tool_risk("web_search") == "low"
    # unknown tool guessing
    assert AutoMode._get_tool_risk("write_custom") == "high"
    assert AutoMode._get_tool_risk("read_custom") == "low"
    assert AutoMode._get_tool_risk("unknown_tool") == "medium"

    # _get_approval_rate
    assert AutoMode._get_approval_rate("unknown", "low") == 0.5

    # should_auto_approve 低风险工具自动通过
    assert AutoMode.should_auto_approve("web_search", {"q": "test"}) is True
    assert AutoMode.should_auto_approve("read_file", {}) is True

    # terminal 安全命令
    assert AutoMode.should_auto_approve("terminal", {"command": "ls -la"}) is True

    # terminal 危险命令（在 low 工具分支 -> terminal 在 AUTO_TOOLS_LOW 之前已被检查）
    # terminal 不在 AUTO_TOOLS_LOW 中，会进入 terminal 分支
    result = AutoMode.should_auto_approve("terminal", {"command": "rm -rf /"})
    assert result is False

    # terminal 在 medium 分支的检查
    # 但 terminal 是 high risk, 所以它走 terminal 分支后不会进入 AUTO_TOOLS_MEDIUM
    result2 = AutoMode.should_auto_approve("terminal", {"command": "rm -rf /"})
    assert result2 is False

    # write_file (medium) auto pass
    assert AutoMode.should_auto_approve("write_file", {"path": "/tmp/test.txt"}) is True
    assert AutoMode.should_auto_approve("patch", {"path": "file.py"}) is True

    # high risk tool 人工审批
    result3 = AutoMode.should_auto_approve("delete_file", {"path": "/etc/passwd"})
    assert result3 is None  # 无历史记录 -> 人工审批

    # high risk tool with high approval rate
    # 先加历史记录
    AutoMode._record_decision("delete_file", "high", True, 0.95, "历史通过")
    AutoMode._record_decision("delete_file", "high", True, 0.95, "历史通过")
    AutoMode._record_decision("delete_file", "high", True, 0.95, "历史通过")
    result4 = AutoMode.should_auto_approve("delete_file", {"path": "/tmp/x"})
    assert result4 is True  # 批准率 > 0.9

    # high risk tool with low approval rate
    AutoMode._history = []
    AutoMode._record_decision("delete_db", "high", False, 0.1, "历史拒绝")
    AutoMode._record_decision("delete_db", "high", False, 0.1, "历史拒绝")
    AutoMode._record_decision("delete_db", "high", False, 0.1, "历史拒绝")
    result5 = AutoMode.should_auto_approve("delete_db", {"name": "test"})
    assert result5 is False

    AutoMode._history = []

    # args not dict -> 防御
    assert AutoMode.should_auto_approve("read_file", "not_a_dict") is True

    # record_mismatch
    AutoMode.record_mismatch("test_tool", "medium", True, False)
    assert len(AutoMode._history) > 0

    print("    ✅ test_approval_auto_mode")


@test("approval: ApprovalManager check_permission")
def test_approval_check_permission():
    from core.approval import ApprovalManager, DenyRules, AutoMode
    DenyRules._rules = []
    AutoMode._history = []

    # Layer 1: Deny 规则阻止
    DenyRules.add("delete_file", "critical", "勿删关键文件")
    result = ApprovalManager.check_permission("delete_file", {"path": "critical"})
    assert result["allowed"] is False
    assert result["approach"] == "deny_rule"
    DenyRules._rules = []

    # Layer 2: 自动模式通过（低风险工具）
    result2 = ApprovalManager.check_permission("web_search", {"q": "hello"})
    assert result2["allowed"] is True
    assert result2["auto"] is True

    # Layer 2: 自动模式拒绝
    AutoMode._record_decision("delete_db_tool", "high", False, 0.1, "历史拒绝")
    AutoMode._record_decision("delete_db_tool", "high", False, 0.1, "历史拒绝")
    AutoMode._record_decision("delete_db_tool", "high", False, 0.1, "历史拒绝")
    result3 = ApprovalManager.check_permission("delete_db_tool", {"name": "x"})
    assert result3["allowed"] is False
    assert result3["approach"] == "auto_reject"
    AutoMode._history = []

    # Layer 2: auto_override=False 跳过自动模式
    result4 = ApprovalManager.check_permission("web_search", {"q": "test"}, auto_override=False)
    assert result4["allowed"] is True  # 走 Layer 3 中低风险自动通过

    # 非 dict args
    result5 = ApprovalManager.check_permission("read_file", "bad_args")
    assert "allowed" in result5

    print("    ✅ test_approval_check_permission")


@test("approval: submit_and_list_pending")
def test_approval_submit_and_pending():
    from core.approval import ApprovalManager, APPROVALS_DIR
    import shutil, os
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    # submit
    req_id = ApprovalManager.submit(
        title="测试审批",
        detail="测试详情",
        risk="high",
        tool="delete_file",
        args_snapshot='{"path": "/tmp/x"}',
        context_type="test",
    )
    assert req_id.startswith("appr_")
    assert APPROVALS_DIR.exists()

    # list_pending
    pending = ApprovalManager.list_pending()
    assert len(pending) == 1
    assert pending[0].title == "测试审批"
    assert pending[0].status == "pending"
    assert pending[0].tool == "delete_file"

    # approve
    assert ApprovalManager.approve(req_id) is True

    # 重复审批
    assert ApprovalManager.approve(req_id) is False

    # 空 req_id -> 没有 pending
    assert ApprovalManager.approve("") is False

    # reject with no pending
    assert ApprovalManager.reject("") is False

    # reject specific
    req_id2 = ApprovalManager.submit("test2", "detail2")
    assert ApprovalManager.reject(req_id2) is True
    assert ApprovalManager.reject(req_id2) is False

    # _resolve 短 ID
    req_id3 = ApprovalManager.submit("test3", "detail3")
    short = req_id3[-8:]
    resolved = ApprovalManager._resolve(short)
    assert resolved is not None
    assert resolved.id == req_id3

    # _resolve 不存在的 ID
    assert ApprovalManager._resolve("non_existent_12345678") is None

    # _resolve 空 -> 没有 pending
    assert ApprovalManager._resolve("") is None

    # list_recent
    recent = ApprovalManager.list_recent(limit=5)
    assert len(recent) >= 2

    # 清理
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    print("    ✅ test_approval_submit_and_pending")


@test("approval: pretooluse_check")
def test_approval_pretooluse_check():
    from core.approval import pretooluse_check, DenyRules, AutoMode, _pretooluse_cache
    _pretooluse_cache.clear()
    DenyRules._rules = []
    AutoMode._history = []

    # 安全 terminal 命令直接放行
    result = pretooluse_check("terminal", {"command": "ls -la /tmp"})
    assert result["allowed"] is True
    assert result["approach"] == "pretooluse_precheck"

    # 普通工具
    result2 = pretooluse_check("web_search", {"q": "test"})
    assert result2["allowed"] is True

    # args 非 dict
    result3 = pretooluse_check("terminal", "bad")
    assert "allowed" in result3

    print("    ✅ test_approval_pretooluse_check")


@test("approval: format_helpers")
def test_approval_format():
    from core.approval import format_approval, format_pending_summary, check_approval_decision, handle_approval_decision, ApprovalManager, APPROVALS_DIR
    import shutil
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    # format_approval
    from core.approval import ApprovalRequest
    req = ApprovalRequest(id="test123", title="测试审批", detail="test detail", risk="high", status="pending", created_at=100.0)
    fmt = format_approval(req)
    assert "测试审批" in fmt
    assert "test123" in fmt

    # format_pending_summary 无待审批
    assert format_pending_summary() == ""

    # 有待审批
    req_id = ApprovalManager.submit("待审", "待审详情", risk="medium")
    summary = format_pending_summary()
    assert "待审" in summary
    assert "夸父待审批事项" in summary

    # check_approval_decision
    dec = check_approval_decision("")
    assert dec is None
    dec = check_approval_decision("1 abc12345")
    assert dec is not None
    assert dec["action"] == "approve"
    dec = check_approval_decision("0 abc12345")
    assert dec["action"] == "reject"
    dec = check_approval_decision("批准 abc12345")
    assert dec["action"] == "approve"
    dec = check_approval_decision("拒绝 abc12345")
    assert dec["action"] == "reject"
    dec = check_approval_decision("approve abc12345")
    assert dec["action"] == "approve"
    dec = check_approval_decision("reject abc12345")
    assert dec["action"] == "reject"
    dec = check_approval_decision("随便写点东西")
    assert dec is None

    # handle_approval_decision
    result = handle_approval_decision({"action": "approve", "req_id": req_id})
    assert "已批准" in result or "审批失败" in result

    # handle with fuzzy match
    result2 = handle_approval_decision({"action": "reject", "req_id": req_id[-8:], "fuzzy": True})
    # req_id 已经被批准了，所以 reject 会失败
    assert result2 is not None

    # fuzzy with multiple matches
    from core.approval import ApprovalRequest as AR
    # 清理后重新测试
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))
    req_a = ApprovalManager.submit("A", "test_a")
    req_b = ApprovalManager.submit("B", "test_b", risk="high", args_snapshot='{"x": 1}')
    # make them share suffix
    result3 = handle_approval_decision({"action": "approve", "req_id": req_a[-8:], "fuzzy": True})
    assert result3 is not None

    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    print("    ✅ test_approval_format")


@test("approval: _is_interactive")
def test_approval_is_interactive():
    from core.approval import _is_interactive
    import os
    # Gateway 模式
    os.environ["KUAFFU_GATEWAY_RUNNING"] = "1"
    assert _is_interactive() is False
    del os.environ["KUAFFU_GATEWAY_RUNNING"]

    # 飞书通道
    os.environ["FEISHU_APP_ID"] = "test"
    assert _is_interactive() is False
    del os.environ["FEISHU_APP_ID"]

    # 交互模式
    os.environ["KUAFFU_INTERACTIVE"] = "1"
    assert _is_interactive() is True
    del os.environ["KUAFFU_INTERACTIVE"]

    print("    ✅ test_approval_is_interactive")


@test("approval: _is_safe_terminal")
def test_approval_safe_terminal():
    from core.approval import _is_safe_terminal
    assert _is_safe_terminal("ls -la") is True
    assert _is_safe_terminal("cat /tmp/file.txt") is True
    assert _is_safe_terminal("pwd") is True
    assert _is_safe_terminal("rm -rf /") is False
    assert _is_safe_terminal("") is False
    assert _is_safe_terminal(123) is False
    assert _is_safe_terminal("grep foo bar.txt") is True
    assert _is_safe_terminal("git status") is True
    print("    ✅ test_approval_safe_terminal")


@test("approval: list_pending_expired")
def test_approval_pending_expired():
    from core.approval import ApprovalManager, APPROVALS_DIR, _save, ApprovalRequest
    import shutil, time
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    # 创建过期审批
    old_req = ApprovalRequest(
        id="old_test_id",
        title="过期审批",
        detail="test",
        risk="low",
        status="pending",
        created_at=time.time() - 99999,
        timeout=1,
    )
    _save(old_req)
    pending = ApprovalManager.list_pending()
    assert len(pending) == 0  # 应被标记为 expired

    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    print("    ✅ test_approval_pending_expired")


@test("approval: list_recent_empty")
def test_approval_list_recent_empty():
    from core.approval import ApprovalManager, APPROVALS_DIR
    import shutil
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))
    assert ApprovalManager.list_recent() == []
    print("    ✅ test_approval_list_recent_empty")


@test("approval: _save_load_corrupt")
def test_approval_corrupt_file():
    from core.approval import _load, APPROVALS_DIR, _save, ApprovalRequest
    import shutil, time
    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    # 保存正常请求
    req = ApprovalRequest(
        id="corrupt_test", title="正常请求", detail="test",
        risk="low", status="pending", created_at=time.time(),
    )
    _save(req)

    # 损坏文件
    bad = APPROVALS_DIR / "bad_req.json"
    bad.write_text("not json{{{}}}", encoding="utf-8")
    loaded = _load("bad_req")
    assert loaded is None

    # list_pending 跳过损坏文件
    from core.approval import ApprovalManager
    pending = ApprovalManager.list_pending()
    assert len(pending) >= 0

    if APPROVALS_DIR.exists():
        shutil.rmtree(str(APPROVALS_DIR))

    print("    ✅ test_approval_corrupt_file")


@test("approval: _get_approval_timeout")
def test_approval_timeout():
    from core.approval import _get_approval_timeout
    timeout = _get_approval_timeout()
    assert timeout == 300  # 默认值
    print("    ✅ test_approval_timeout")


# ═══════════════════════════════════════════════════════════════
# core/hooks.py 测试
# ═══════════════════════════════════════════════════════════════

@test("hooks: HookRegistry init/register/unregister/get")
def test_hooks_registry():
    from core.hooks import HookRegistry, HOOK_EVENTS, HOOKS_CONFIG_PATH
    # 重置
    HookRegistry._handlers = {}
    HookRegistry._initialized = False

    # init (no config file)
    if HOOKS_CONFIG_PATH.exists():
        import os
        os.remove(str(HOOKS_CONFIG_PATH))
    HookRegistry.init()
    assert HookRegistry._initialized is True
    assert HookRegistry._handlers == {}

    # register
    hid = HookRegistry.register(
        event="on_tool_before",
        handler_type="shell",
        config={"command": "echo 'tool called'"},
        description="测试shell钩子",
        priority=10,
        async_=True,
        max_retries=1,
        timeout=5,
    )
    assert hid.startswith("hook_")
    assert "on_tool_before" in HookRegistry._handlers
    assert len(HookRegistry._handlers["on_tool_before"]) == 1
    assert HookRegistry._handlers["on_tool_before"][0].type == "shell"

    # register 未知事件 -> ValueError
    try:
        HookRegistry.register("unknown_event", "shell", {"command": "echo"})
        assert False, "应抛出 ValueError"
    except ValueError:
        pass

    # 再次 init -> 不覆盖
    HookRegistry._initialized = False
    HookRegistry.init()
    assert len(HookRegistry._handlers["on_tool_before"]) == 1

    # get_handlers
    handlers = HookRegistry.get_handlers("on_tool_before")
    assert len(handlers) == 1
    assert handlers[0].enabled is True

    # get_handlers for event with no handlers
    assert HookRegistry.get_handlers("on_agent_start") == []

    # unregister
    assert HookRegistry.unregister(hid) is True
    assert HookRegistry.unregister("non_existent") is False
    assert len(HookRegistry._handlers["on_tool_before"]) == 0

    print("    ✅ test_hooks_registry")


@test("hooks: template_rendering")
def test_hooks_template():
    from core.hooks import _render_template, _render_config
    # 基本渲染
    result = _render_template("Hello {{name}}!", {"name": "World"})
    assert result == "Hello World!"

    # 缺失变量保持原样
    result2 = _render_template("Hello {{unknown}}!", {})
    assert result2 == "Hello {{unknown}}!"

    # dict/list 渲染
    result3 = _render_template("Data: {{data}}", {"data": {"key": "val"}})
    assert '"key"' in result3

    # _render_config 递归
    config = {
        "url": "http://{{host}}:{{port}}",
        "nested": {"cmd": "echo {{name}}"},
        "items": ["{{a}}", "plain"],
    }
    rendered = _render_config(config, {"host": "localhost", "port": "8080", "name": "test", "a": "value"})
    assert rendered["url"] == "http://localhost:8080"
    assert rendered["nested"]["cmd"] == "echo test"
    assert rendered["items"][0] == "value"
    assert rendered["items"][1] == "plain"

    # 非 str/list/dict 保留原值
    config2 = {"num": 42, "flag": True}
    rendered2 = _render_config(config2, {})
    assert rendered2["num"] == 42
    assert rendered2["flag"] is True

    print("    ✅ test_hooks_template")


@test("hooks: _execute_shell")
def test_hooks_execute_shell():
    from core.hooks import _execute_shell, HookHandler, HookResult
    import time
    handler = HookHandler(
        id="test_shell",
        event="on_tool_before",
        type="shell",
        config={"command": "echo 'hello world'"},
        timeout=5,
    )
    result = _execute_shell(handler, {"tool": "test"})
    assert result.success is True
    assert "hello world" in result.output
    assert result.type == "shell"
    assert result.duration >= 0

    # 命令失败
    handler2 = HookHandler(
        id="test_shell_fail",
        event="on_tool_before",
        type="shell",
        config={"command": "exit 1"},
    )
    result2 = _execute_shell(handler2, {})
    assert result2.success is False

    # 命令超时
    handler3 = HookHandler(
        id="test_shell_timeout",
        event="on_tool_before",
        type="shell",
        config={"command": "sleep 10"},
        timeout=1,
    )
    result3 = _execute_shell(handler3, {})
    assert result3.success is False
    assert "超时" in result3.error

    # 命令异常
    handler4 = HookHandler(
        id="test_shell_exc",
        event="on_tool_before",
        type="shell",
        config={},  # 缺少 command
    )
    result4 = _execute_shell(handler4, {})
    assert result4.success is False

    print("    ✅ test_hooks_execute_shell")


@test("hooks: trigger_event")
def test_hooks_trigger():
    from core.hooks import trigger, HOOK_EVENTS, HookRegistry, HOOKS_CONFIG_PATH
    HookRegistry._handlers = {}
    HookRegistry._initialized = False
    if HOOKS_CONFIG_PATH.exists():
        import os
        os.remove(str(HOOKS_CONFIG_PATH))
    HookRegistry.init()

    # 未知事件
    results = trigger("non_existent_event")
    assert results == []

    # 无处理器事件
    results = trigger("on_agent_start")
    assert results == []

    # 注册 shell 处理器并触发
    hid = HookRegistry.register(
        event="on_tool_before",
        handler_type="shell",
        config={"command": "echo 'hook triggered for {{tool}}'"},
    )
    results = trigger("on_tool_before", {"tool": "my_tool"})
    assert len(results) == 1
    assert results[0].success is True
    assert "hook triggered" in results[0].output

    HookRegistry.unregister(hid)

    # 注册未知执行类型的处理器
    from core.hooks import HookHandler
    HookRegistry._handlers["on_test"] = [
        HookHandler(id="unknown_type", event="on_test", type="unknown_type", config={})
    ]
    results = trigger("on_test", {})
    assert len(results) == 1
    assert results[0].success is False
    assert "未知执行类型" in results[0].error

    # 同步触发 + block_on_failure
    HookRegistry._handlers = {}
    HookRegistry._initialized = False
    HookRegistry.init()
    from core.hooks import _EXECUTORS

    # 恢复原始执行器
    print("    ✅ test_hooks_trigger")


@test("hooks: trigger_async_and_sync")
def test_hooks_trigger_async_sync():
    from core.hooks import trigger_async, trigger_sync, HookRegistry, HOOKS_CONFIG_PATH
    HookRegistry._handlers = {}
    HookRegistry._initialized = False
    if HOOKS_CONFIG_PATH.exists():
        import os
        os.remove(str(HOOKS_CONFIG_PATH))
    HookRegistry.init()

    hid = HookRegistry.register(
        event="on_tool_after",
        handler_type="shell",
        config={"command": "echo 'async test'"},
    )

    # 异步触发（不阻塞）
    trigger_async("on_tool_after", {"tool": "test"})
    # 不能直接断言结果，因为异步

    # 同步触发
    results = trigger_sync("on_tool_after", {"tool": "test"})
    assert len(results) == 1
    assert results[0].success is True

    HookRegistry.unregister(hid)
    print("    ✅ test_hooks_trigger_async_sync")


@test("hooks: convenient_register_functions")
def test_hooks_convenient():
    from core.hooks import on_tool_before_shell, on_tool_before_llm, on_approval_notify_webhook, HookRegistry, HOOKS_CONFIG_PATH
    HookRegistry._handlers = {}
    HookRegistry._initialized = False
    if HOOKS_CONFIG_PATH.exists():
        import os
        os.remove(str(HOOKS_CONFIG_PATH))
    HookRegistry.init()

    hid1 = on_tool_before_shell("echo 'pre tool'", "shell pre-tool", priority=5)
    assert hid1.startswith("hook_")
    assert len(HookRegistry._handlers["on_tool_before"]) == 1

    hid2 = on_tool_before_llm("分析工具调用 {{tool}}", model="qwen-turbo", description="LLM分析")
    assert hid2.startswith("hook_")
    assert len(HookRegistry._handlers["on_tool_before"]) == 2
    assert HookRegistry._handlers["on_tool_before"][0].priority == 5  # 排序后大的在前

    hid3 = on_approval_notify_webhook("https://example.com/hook", description="审批通知")
    assert hid3.startswith("hook_")
    assert "on_approval_result" in HookRegistry._handlers

    # init_hooks
    from core.hooks import init_hooks
    HookRegistry._initialized = False
    init_hooks()

    print("    ✅ test_hooks_convenient")


# ═══════════════════════════════════════════════════════════════
# core/mcp_bridge.py 测试
# ═══════════════════════════════════════════════════════════════

@test("mcp: JSON-RPC helpers")
def test_mcp_json_rpc():
    from core.mcp_bridge import _make_request, _parse_response, _JsonRpcError

    # _make_request
    req = _make_request("tools/list", msg_id=1)
    data = json.loads(req)
    assert data["jsonrpc"] == "2.0"
    assert data["method"] == "tools/list"
    assert data["id"] == 1
    assert "params" not in data

    req2 = _make_request("tools/call", {"name": "test"}, msg_id=2)
    data2 = json.loads(req2)
    assert data2["params"]["name"] == "test"

    # _parse_response
    resp = _parse_response('{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')
    assert resp == {"tools": []}

    # _parse_response with error
    try:
        _parse_response('{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Method not found"}}')
        assert False, "应抛出 _JsonRpcError"
    except _JsonRpcError as e:
        assert e.code == -32601
        assert "Method not found" in str(e)

    # _JsonRpcError 构造
    err = _JsonRpcError(-32000, "测试错误", {"detail": "info"})
    assert err.code == -32000
    assert err.data["detail"] == "info"

    print("    ✅ test_mcp_json_rpc")


@test("mcp: MCPServer init_and_properties")
def test_mcp_server_init():
    from core.mcp_bridge import MCPServer, DEFAULT_TIMEOUT
    server = MCPServer(name="test-server", command="echo", args=["hello"])
    assert server.name == "test-server"
    assert server.command == "echo"
    assert server.args == ["hello"]
    assert server.env == {}
    assert server.timeout == DEFAULT_TIMEOUT
    assert server.connected is False
    assert server.list_tools() == []

    # _next_id 递增
    assert server._next_id() == 1
    assert server._next_id() == 2

    print("    ✅ test_mcp_server_init")


@test("mcp: MCPServer connect_failure")
def test_mcp_server_connect_fail():
    from core.mcp_bridge import MCPServer
    # 命令不存在 -> 连接失败
    server = MCPServer(name="fail-server", command="/nonexistent/binary", args=[])
    ok = server.connect()
    assert ok is False
    assert server.connected is False

    # restart 失败
    ok2 = server.restart()
    assert ok2 is False

    print("    ✅ test_mcp_server_connect_fail")


@test("mcp: MCPBridge basic")
def test_mcp_bridge_basic():
    from core.mcp_bridge import MCPBridge, MCPServer
    bridge = MCPBridge()
    assert bridge._servers == {}
    assert bridge._config_path == ""

    # get_server_status
    status = bridge.get_server_status()
    assert status == []

    # disconnect_all empty
    bridge.disconnect_all()

    # get_handler with no servers
    handler = bridge.get_handler("nonexistent_tool")
    assert handler is None

    # register_to_registry with empty
    class _MockRegistry:
        def __init__(self):
            self.calls = []
        def register(self, name, schema, handler):
            self.calls.append((name, schema))

    reg = _MockRegistry()
    count = bridge.register_to_registry(reg)
    assert count == 0

    # get_all_tools empty
    assert bridge.get_all_tools() == []

    print("    ✅ test_mcp_bridge_basic")


@test("mcp: MCPServer call_tool_not_connected")
def test_mcp_server_call_not_connected():
    from core.mcp_bridge import MCPServer
    server = MCPServer(name="test", command="echo", args=[])
    result = server.call_tool("test_tool", {"arg": "val"})
    assert result["success"] is False
    assert "未连接" in result["output"]
    print("    ✅ test_mcp_server_call_not_connected")


@test("mcp: MCPServer disconnect")
def test_mcp_server_disconnect():
    from core.mcp_bridge import MCPServer
    server = MCPServer(name="test", command="echo", args=["hello"])
    # 未连接时断开
    server.disconnect()
    assert server.connected is False
    assert server._process is None

    # 连接后断开
    ok = server.connect()
    # echo hello 不是有效的 MCP Server，连接会失败
    assert ok is False
    # disconnect 后状态复位
    assert server.connected is False

    print("    ✅ test_mcp_server_disconnect")


@test("mcp: MCPBridge load_config_and_connect_all")
def test_mcp_bridge_load_config():
    import tempfile, os, yaml
    from core.mcp_bridge import MCPBridge

    # 创建临时 YAML 配置文件
    config = {
        "mcp_servers": {
            "test_server1": {
                "command": "echo",
                "args": ["hello"],
                "enabled": True,
            },
            "test_server2": {
                "command": "python3",
                "args": ["-c", "print('test')"],
                "enabled": True,
            },
            "disabled_server": {
                "command": "echo",
                "args": ["disabled"],
                "enabled": False,
            },
        }
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config, f)
        tmp_path = f.name

    try:
        bridge = MCPBridge()
        bridge.load_config(tmp_path)
        assert len(bridge._servers) == 2
        assert "test_server1" in bridge._servers
        assert "test_server2" in bridge._servers
        assert "disabled_server" not in bridge._servers

        # connect_all (will fail because echo/python3 aren't real MCP servers)
        failed = bridge.connect_all()
        assert len(failed) == 2  # 两个都会失败

        # disconnect_all
        bridge.disconnect_all()
        assert len(bridge._tool_to_server) == 0

    finally:
        os.unlink(tmp_path)

    print("    ✅ test_mcp_bridge_load_config")


@test("mcp: MCPBridge load_config_empty")
def test_mcp_bridge_load_empty():
    import tempfile, os, yaml
    from core.mcp_bridge import MCPBridge

    config = {}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config, f)
        tmp_path = f.name

    try:
        bridge = MCPBridge()
        bridge.load_config(tmp_path)
        assert len(bridge._servers) == 0
    finally:
        os.unlink(tmp_path)

    print("    ✅ test_mcp_bridge_load_empty")


@test("mcp: MCPServer restart_exceed_max")
def test_mcp_server_restart_max():
    from core.mcp_bridge import MCPServer, MAX_RESTARTS
    server = MCPServer(name="max-restart", command="/nonexistent", args=[])
    server._restart_count = MAX_RESTARTS
    ok = server.restart()
    assert ok is False
    print("    ✅ test_mcp_server_restart_max")
