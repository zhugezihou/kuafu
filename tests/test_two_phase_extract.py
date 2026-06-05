"""
tests/test_two_phase_extract.py — 两阶段记忆提取测试
"""

import pytest
from unittest.mock import MagicMock

from core.memory.two_phase_extract import TwoPhaseExtractor


class MockLLM:
    """模拟 LLM 客户端"""
    def __init__(self, response="[fact] 测试知识点"):
        self.response = response

    def chat(self, messages, tools=None):
        return {"success": True, "content": self.response}


class MockMemory:
    """模拟 MemoryManager"""
    def __init__(self):
        self.stored = []

    def store(self, content, context="", source=""):
        self.stored.append({"content": content, "context": context, "source": source})


class TestTwoPhaseExtractor:

    def test_extract_candidates(self):
        """Phase 1 提取候选知识"""
        llm = MockLLM(response="[fact] 用户使用 Python 3.11\n[preference] 偏好简洁代码")
        extractor = TwoPhaseExtractor(llm_client=llm)
        result = extractor._phase1_extract("对话内容", "编写 API")
        assert len(result) == 2
        assert "Python 3.11" in result[0]

    def test_phase2_refine(self):
        """Phase 2 精炼"""
        llm = MockLLM(response="[c=0.95] 用户使用 Python 3.11\n[c=0.90] 偏好简洁代码")
        extractor = TwoPhaseExtractor(llm_client=llm)
        result = extractor._phase2_refine(["Python 3.11", "偏好简洁"])
        assert len(result) == 2

    def test_parse_extracted(self):
        """解析 Phase 1 输出"""
        text = "[fact] 知识点A\n[preference] 知识点B\n"
        extractor = TwoPhaseExtractor()
        result = extractor._parse_extracted(text)
        assert len(result) == 2

    def test_parse_refined(self):
        """解析 Phase 2 输出"""
        text = "[c=0.95] 精炼后知识\n[c=0.80] 另一条\n"
        extractor = TwoPhaseExtractor()
        result = extractor._parse_refined(text)
        assert len(result) == 2

    def test_full_extract_pipeline(self):
        """完整两阶段流程"""
        llm = MockLLM(response="[fact] 用户使用 Python 3.11\n[preference] 偏好简洁代码")
        memory = MockMemory()
        extractor = TwoPhaseExtractor(llm_client=llm, memory_manager=memory)

        messages = [
            {"role": "user", "content": "帮我写一个Python脚本"},
            {"role": "assistant", "content": "好的，我写了一个简洁的脚本"},
        ]
        result = extractor.extract_from_conversation(messages, task="写脚本")

        assert len(result) > 0
        # 验证存储到 MemoryManager
        assert len(memory.stored) > 0

    def test_no_llm_skips(self):
        """无 LLM 时跳过"""
        extractor = TwoPhaseExtractor()
        result = extractor.extract_from_conversation([], task="")
        assert result == []

    def test_prepare_conversation(self):
        """准备对话文本"""
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮忙？"},
            {"role": "tool", "content": "{'result': 'ok'}"},
        ]
        extractor = TwoPhaseExtractor()
        text = extractor._prepare_conversation(messages)
        assert "[user] 你好" in text
        assert "[assistant] 你好！" in text
        assert "tool" not in text  # tool 消息被过滤
