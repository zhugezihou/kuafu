"""
tests/test_compact_hooks.py — Compact Hook 接口测试
"""

import pytest
from core.compact_hooks import (
    CompactContext, CompactResult,
    CompactHookManager, LoggingCompactHook,
)


class TestCompactContext:

    def test_create(self):
        ctx = CompactContext(original_tokens=1000, target_tokens=800,
                            messages_count=50)
        assert ctx.original_tokens == 1000
        assert ctx.target_tokens == 800
        assert ctx.messages_count == 50
        assert ctx.strategy == ""


class TestCompactResult:

    def test_create(self):
        r = CompactResult(messages_removed=10, summary="摘要",
                         compressed_tokens=600)
        assert r.messages_removed == 10
        assert r.summary == "摘要"
        assert r.compressed_tokens == 600


class TestCompactHookManager:

    def test_register_and_fire(self):
        mgr = CompactHookManager()
        events = []

        class TestHook:
            def pre_compact(self, ctx):
                events.append("pre")
            def post_compact(self, ctx, result):
                events.append("post")

        mgr.register(TestHook())
        mgr.fire_pre(CompactContext(100, 80, 10))
        mgr.fire_post(CompactContext(100, 80, 10),
                      CompactResult(messages_removed=5))

        assert "pre" in events
        assert "post" in events

    def test_unregister(self):
        mgr = CompactHookManager()
        hook = LoggingCompactHook()
        mgr.register(hook)
        assert mgr.unregister(hook) is True
        assert mgr.unregister(hook) is False

    def test_exception_does_not_stop_chain(self):
        mgr = CompactHookManager()

        class BrokenHook:
            def pre_compact(self, ctx):
                raise RuntimeError("broken")
            def post_compact(self, ctx, result):
                pass

        calls = []

        class GoodHook:
            def pre_compact(self, ctx):
                calls.append("good_pre")
            def post_compact(self, ctx, result):
                calls.append("good_post")

        mgr.register(BrokenHook())
        mgr.register(GoodHook())
        mgr.fire_pre(CompactContext(100, 80, 10))
        mgr.fire_post(CompactContext(100, 80, 10),
                      CompactResult(messages_removed=5))

        assert "good_pre" in calls
        assert "good_post" in calls


class TestLoggingCompactHook:

    def test_pre_post(self):
        hook = LoggingCompactHook()
        ctx = CompactContext(1000, 800, 50)
        result = CompactResult(messages_removed=10, summary="测试")
        # 确保不抛异常
        hook.pre_compact(ctx)
        hook.post_compact(ctx, result)
