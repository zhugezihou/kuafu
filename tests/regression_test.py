#!/usr/bin/env python3
"""夸父核心模块回归测试"""
import sys, os, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
sys.path.insert(0, ROOT)

tests_passed = 0
tests_total = 0

def test(name, fn):
    global tests_passed, tests_total
    tests_total += 1
    try:
        fn()
        print("  ✅ %s" % name)
        tests_passed += 1
    except AssertionError as e:
        print("  ❌ %s: %s" % (name, e))
    except Exception as e:
        print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))

# ── 1. 模块导入 ──
print("=== 1. 模块导入 ===")
for mod in ['core.tool_registry', 'core.session_store', 'core.context_compress', 'core.safety']:
    test("导入 " + mod, lambda m=mod: __import__(m))

# ── 2. ToolRegistry ──
print("\n=== 2. ToolRegistry ===")
from core.tool_registry import ToolRegistry
tr = ToolRegistry()
schemas = tr.get_schemas()

test("8个工具", lambda: len(schemas) == 8)
test("含terminal/read_file/write_file/finish", lambda: (
    all(n in [s["function"]["name"] for s in tr.get_schemas()]
        for n in ["terminal", "read_file", "write_file", "finish"])
))

test("终端执行", lambda: tr.execute({
    "function": {"name": "terminal", "arguments": {"command": "echo test", "timeout": 5}}
})["success"])

test("文件读取", lambda: tr.execute({
    "function": {"name": "read_file", "arguments": {"path": "core/__init__.py", "limit": 3}}
})["success"])

test("finish", lambda: tr.execute({
    "function": {"name": "finish", "arguments": {"result": "done", "summary": "ok"}}
})["success"])

test("未知工具返回失败", lambda: not tr.execute({
    "function": {"name": "nonexistent", "arguments": {}}
})["success"])

# ── 3. SessionStore ──
print("\n=== 3. SessionStore ===")
from core.session_store import SessionStore
dbp = tempfile.mktemp(suffix=".db")

def _session_test():
    ss = SessionStore(dbp)
    sid = ss.create_session("test")
    ss.append_message(sid, "user", "hi")
    ss.append_message(sid, "assistant", "hello")
    assert len(ss.get_messages(sid)) == 2
    s = ss.get_session(sid)
    assert s is not None
    stats = ss.get_stats()
    assert stats["total_sessions"] >= 1
    assert stats["total_messages"] == 2
    ss.close()
test("创建/读写会话", _session_test)

def _list_test():
    ss = SessionStore(dbp)
    s = ss.list_sessions()
    assert len(s) >= 1
    ss.close()
    os.unlink(dbp)
test("列出会话", _list_test)

# ── 4. ContextCompressor ──
print("\n=== 4. ContextCompressor ===")
from core.context_compress import ContextCompressor

cc = ContextCompressor(max_context_tokens=500)
test("小上下文不压缩", lambda: not cc.needs_compression([
    {"role": "system", "content": "x" * 100}
]))

cc_small = ContextCompressor(max_context_tokens=50)
msgs = [
    {"role": "system", "content": "你是夸父"},
    {"role": "user", "content": "你好" * 50},
    {"role": "assistant", "content": "回复" * 50},
]
test("压缩后tokens不增加", lambda: (
    cc_small.needs_compression(msgs) and
    cc_small.compress(msgs).compressed_tokens <= cc_small.compress(msgs).original_tokens
))

# ── 5. SafetyLayer ──
print("\n=== 5. SafetyLayer ===")
from core.safety import SafetyLayer, CommandLevel

test("API Key脱敏", lambda: "***" in SafetyLayer.sanitize_text("apikey=sk-abc123xyz456"))
test("密码脱敏", lambda: "***" in SafetyLayer.sanitize_text("password: mysecret123"))
test("Auth Header脱敏", lambda: "***" in SafetyLayer.sanitize_text(
    "Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
))

test("安全命令", lambda: SafetyLayer.classify_command("ls -la")[0] == CommandLevel.SAFE)
test("echo安全", lambda: SafetyLayer.classify_command("echo hello")[0] == CommandLevel.SAFE)
test("rm危险", lambda: SafetyLayer.classify_command("rm -rf /tmp")[0] == CommandLevel.DANGEROUS)
test("sudo危险", lambda: SafetyLayer.classify_command("sudo rm -rf /")[0] == CommandLevel.DANGEROUS)
test("pip需确认", lambda: SafetyLayer.classify_command("pip install requests")[0] == CommandLevel.ATTENTION)
test("git push需确认", lambda: SafetyLayer.classify_command("git push origin main")[0] == CommandLevel.ATTENTION)

# ── 汇总 ──
print("\n" + "=" * 40)
print("通过 %d/%d 项测试" % (tests_passed, tests_total))
if tests_passed == tests_total:
    print("🎉 全部通过！")
else:
    print("❌ %d 项失败" % (tests_total - tests_passed))
    sys.exit(1)
