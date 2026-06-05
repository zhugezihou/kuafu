"""
core/memory/two_phase_extract.py — 两阶段记忆提取系统

源自 Codex CLI Memories 系统：
  Phase 1: 低成本模型（fast_llm）从对话中提取知识点
  Phase 2: 高精度模型（deep_llm）合并、去重、格式化

与现有 MemoryManager 的关系：
  - MemoryManager.store() 是单次即时存储
  - TwoPhaseExtractor 是批量后处理：在任务完成后对对话进行回顾性提取
  - 两者互补：即时存储管当下，两阶段提取管深度反思

用法：
    extractor = TwoPhaseExtractor(llm_client)
    extractor.extract_from_conversation(messages, task)
    # 结果自动写入 MemoryManager
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kuafu.two_phase_memory")


class TwoPhaseExtractor:
    """两阶段记忆提取器。

    在任务完成后的后台线程中运行，不阻塞主流程。
    """

    # Phase 1 使用的系统 prompt（低成本模型）
    PHASE1_PROMPT = """从以下对话中提取有价值的知识点。

要求：
1. 只提取**可复用的知识**（事实、偏好、模式、教训、决策理由）
2. 不要提取对话中的任务细节（做了什么、用了什么命令）
3. 每条知识点是一个简短的陈述句
4. 标注类型：[fact] / [preference] / [lesson] / [pattern] / [decision]

输出格式：每行一条，不要序号。

示例输出：
[fact] 用户使用 WSL2 作为开发环境
[preference] 用户偏好中文沟通
[lesson] 修改代码后需要重启 gateway 才能生效
[pattern] 用户倾向于先研究再动手
"""

    # Phase 2 使用的系统 prompt（高精度模型）
    PHASE2_PROMPT = """你是记忆精炼专家。以下是 Phase 1 从对话中提取的候选知识点。

你的任务：
1. **去重**：合并意思相同但表述不同的知识点
2. **精炼**：用更精确的语言重写每条知识
3. **冲突检测**：如果两条知识互相矛盾，标记为 [conflict]
4. **置信度评估**：为每条知识标注置信度 [c=0.x]

输入格式：每行一条候选知识点

输出格式：每行一条精炼后的知识点

示例：
[c=0.95] 用户使用 WSL2 作为开发环境
[c=0.90] 用户偏好中文而非英文沟通
[c=0.85] 修改夸父核心代码后必须重启 gateway
"""

    def __init__(self, llm_client=None, memory_manager=None):
        """初始化。

        Args:
            llm_client: 夸父的 LLMClient 实例
            memory_manager: MemoryManager 实例（用于存储结果）
        """
        self.llm = llm_client
        self.memory = memory_manager

    def extract_from_conversation(self, messages: list[dict],
                                   task: str = "") -> list[str]:
        """对对话进行两阶段提取。

        Args:
            messages: 完整的对话消息列表（包含 tool 结果）
            task: 原始任务描述

        Returns:
            提取的知识点列表
        """
        if not self.llm:
            logger.warning("LLM 未配置，跳过记忆提取")
            return []

        # 准备对话摘要（避免 token 过多）
        conversation_text = self._prepare_conversation(messages)

        # Phase 1: 快速提取
        candidates = self._phase1_extract(conversation_text, task)
        if not candidates:
            return []

        logger.info(f"🧠 Phase 1: 提取 {len(candidates)} 条候选知识")

        # Phase 2: 精炼合并
        refined = self._phase2_refine(candidates)
        if not refined:
            return candidates  # Phase 2 失败则回退到 Phase 1 结果

        logger.info(f"🧠 Phase 2: 精炼为 {len(refined)} 条知识")

        # 存储到 MemoryManager
        if self.memory:
            for item in refined:
                self.memory.store(
                    content=item,
                    context=task,
                    source="two_phase_extract",
                )

        return refined

    def _prepare_conversation(self, messages: list[dict],
                               max_chars: int = 8000) -> str:
        """准备对话文本用于提取。

        只保留 user 和 assistant 消息，跳过 tool 结果以节省 token。
        从后往前取最重要的部分。
        """
        lines = []
        char_count = 0

        for msg in reversed(messages):
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role in ("user", "assistant") and isinstance(content, str):
                text = content[:500]  # 截断单条消息
                if char_count + len(text) > max_chars:
                    break
                lines.append(f"[{role}] {text}")
                char_count += len(text)

        lines.reverse()
        return "\n".join(lines)

    def _phase1_extract(self, conversation: str, task: str) -> list[str]:
        """Phase 1：低成本模型快速提取。"""
        try:
            prompt = f"对话上下文：{task}\n\n{conversation}\n\n---\n{self.PHASE1_PROMPT}"
            response = self.llm.chat([
                {"role": "system", "content": "你是记忆提取专家。提取简短、准确的知识点。"},
                {"role": "user", "content": prompt},
            ], tools=None)

            if response.get("success"):
                return self._parse_extracted(response["content"])
            return []
        except Exception as e:
            logger.warning(f"Phase 1 提取失败: {e}")
            return []

    def _phase2_refine(self, candidates: list[str]) -> list[str]:
        """Phase 2：高精度模型精炼合并。"""
        if len(candidates) <= 1:
            return candidates  # 一条不需要精炼

        try:
            input_text = "\n".join(candidates)
            response = self.llm.chat([
                {"role": "system", "content": self.PHASE2_PROMPT},
                {"role": "user", "content": input_text},
            ], tools=None)

            if response.get("success"):
                return self._parse_refined(response["content"])
            return []
        except Exception as e:
            logger.warning(f"Phase 2 精炼失败: {e}")
            return []

    def _parse_extracted(self, text: str) -> list[str]:
        """解析 Phase 1 输出。"""
        lines = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith(("#", "-", "*", "```")) and len(line) > 5:
                # 去掉可能的序号前缀
                clean = line
                for prefix in ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "0."]:
                    if clean.startswith(prefix):
                        clean = clean[len(prefix):].strip()
                        break
                lines.append(clean)
        return lines

    def _parse_refined(self, text: str) -> list[str]:
        """解析 Phase 2 输出。"""
        lines = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith(("```", "#", "- [x]", "- [ ]")):
                lines.append(line)
        return lines
