"""
tests/test_turn_context.py — TurnContext 不可变上下文测试
"""

import json
import time
import pytest
from core.turn_context import TurnContext


class TestCreate:

    def test_create_with_defaults(self):
        """默认创建"""
        ctx = TurnContext.create(task="测试任务")
        assert ctx.task == "测试任务"
        assert ctx.turn_id != ""
        assert ctx.created_at > 0
        assert ctx.turn_count == 0
        assert ctx.approval_enabled is True

    def test_create_with_overrides(self):
        """带覆盖参数创建"""
        ctx = TurnContext.create(
            task="coding",
            session_id="sess_123",
            turn_count=3,
            model="deepseek-chat",
            temperature=0.3,
        )
        assert ctx.task == "coding"
        assert ctx.session_id == "sess_123"
        assert ctx.turn_count == 3
        assert ctx.model == "deepseek-chat"
        assert ctx.temperature == 0.3


class TestImmutability:

    def test_fields_are_readonly(self):
        """字段不可变"""
        ctx = TurnContext.create(task="test")
        with pytest.raises(Exception):
            ctx.task = "changed"

    def test_with_task_returns_new(self):
        """with_* 返回新实例"""
        ctx1 = TurnContext.create(task="original")
        ctx2 = ctx1.with_task("changed")

        assert ctx1.task == "original"
        assert ctx2.task == "changed"
        assert ctx1 is not ctx2
        assert ctx1.turn_id == ctx2.turn_id  # 同一 turn 的不同版本

    def test_with_model_chain(self):
        """链式 with_ 调用"""
        ctx = TurnContext.create(task="test")
        ctx2 = ctx.with_model("gpt-4", temperature=0.1, max_tokens=8000)

        assert ctx.model == ""  # 原实例不变
        assert ctx2.model == "gpt-4"
        assert ctx2.temperature == 0.1
        assert ctx2.max_tokens == 8000
        assert ctx2.task == "test"  # 未覆盖的字段保持


class TestSerialization:

    def test_to_dict_roundtrip(self):
        """序列化/反序列化往返"""
        ctx = TurnContext.create(
            task="测试",
            session_id="s_abc",
            turn_count=5,
            reminders=["注意安全", "保持简洁"],
        )
        d = ctx.to_dict()
        assert d["_type"] == "TurnContext"
        assert d["task"] == "测试"
        assert d["session_id"] == "s_abc"
        assert d["reminders"] == ["注意安全", "保持简洁"]

        ctx2 = TurnContext.from_dict(d)
        assert ctx2.task == ctx.task
        assert ctx2.session_id == ctx.session_id
        assert ctx2.turn_count == ctx.turn_count
        assert ctx2.reminders == ctx.reminders
        assert ctx2.turn_id == ctx.turn_id

    def test_to_json(self):
        """JSON 序列化"""
        ctx = TurnContext.create(task="test")
        j = ctx.to_json()
        parsed = json.loads(j)
        assert parsed["task"] == "test"
        assert parsed["_type"] == "TurnContext"


class TestEdgeCases:

    def test_empty_task(self):
        """空任务可创建"""
        ctx = TurnContext.create()
        assert ctx.task == ""
        assert ctx.turn_id != ""

    def test_large_metadata(self):
        """元数据携带"""
        ctx = TurnContext.create(
            task="test",
            metadata={"custom": "value", "tags": ["a", "b"]}
        )
        assert ctx.metadata["custom"] == "value"
        assert ctx.metadata["tags"] == ["a", "b"]

    def test_unique_ids(self):
        """每次 create 生成不同 ID"""
        ctx1 = TurnContext.create()
        ctx2 = TurnContext.create()
        assert ctx1.turn_id != ctx2.turn_id


class TestCompression:

    def test_compression_default(self):
        """默认未压缩"""
        ctx = TurnContext.create(task="test")
        assert ctx.is_compressed is False
        assert ctx.compression_ratio == 0.0

    def test_with_compression(self):
        """更新压缩状态"""
        ctx = TurnContext.create(task="test")
        ctx2 = ctx.with_compression(0.65)
        assert ctx.is_compressed is False
        assert ctx2.is_compressed is True
        assert ctx2.compression_ratio == 0.65


class TestApproval:

    def test_with_approval(self):
        """更新审批策略"""
        ctx = TurnContext.create(task="test")
        ctx2 = ctx.with_approval(enabled=False)
        assert ctx.approval_enabled is True
        assert ctx2.approval_enabled is False

    def test_approval_mode(self):
        """审批模式"""
        ctx = TurnContext.create(task="test")
        ctx2 = ctx.with_approval(mode="interactive")
        assert ctx.approval_mode == "gateway"
        assert ctx2.approval_mode == "interactive"
