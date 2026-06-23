"""
local_helper.py — 本地大模型辅助层。

LocalHelper 封装对本地 llama-server 的 HTTP 调用，
为夸父提供轻量级辅助推理（记忆分类/结果压缩/摘要）。

设计原则：
  1. 本地模型不可用时完全静默，不抛异常
  2. 超时短（2-5s），不阻塞主流程
  3. 所有方法返回 Optional[str]，None = 不可用/超时/失败
  4. 调用方只需 if helper.available() 或 try/except 即可
"""
#
# Copyright (c) 2026 zhugezihou
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import json
import logging
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger("kuafu.local_helper")

LOCAL_BASE_URL = "http://localhost:8080"
LOCAL_TIMEOUT = 5       # 健康检查超时（短）
INFERENCE_TIMEOUT = 8   # 推理超时（稍长，给本地模型时间）
MAX_INPUT_CHARS = 2000


class LocalHelper:
    """本地大模型辅助层。缺失时完全静默，不抛异常。"""

    def __init__(self, base_url: str = LOCAL_BASE_URL,
                 timeout: int = LOCAL_TIMEOUT):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._available = self._check_health()

    # ── 公开属性 ────────────────────────────────────────────────

    def available(self) -> bool:
        """本地模型是否可用（在线）。"""
        return self._available

    # ── 辅助能力 ────────────────────────────────────────────────

    def classify(self, content: str) -> Optional[str]:
        """
        记忆分类：判断事实类型（world / experience / opinion）。

        与 MemoryManager._llm_classify 同语义但用本地模型。
        不可用/超时返回 None，由调用方降级。
        """
        if not self._available or not content:
            return None

        prompt = (
            "判断以下内容的类型，只输出一个词：\n"
            "  world - 客观事实陈述\n"
            "  experience - Agent 的个人经历或操作\n"
            "  opinion - 主观判断、偏好、建议、信念\n\n"
            f"内容: {content[:300]}\n\n"
            "输出:"
        )
        return self._quick_chat(prompt, max_tokens=16, temperature=0.0)

    def summarize(self, text: str, max_chars: int = 300) -> Optional[str]:
        """
        结果压缩：将大文本压缩为摘要。

        适用于 microcompact、budget_reduce 等场景。
        不可用/超时返回 None，调用方直接用截断兜底。
        """
        if not self._available or not text or len(text) < 500:
            return None

        # 对过长的输入做截断（本地模型的 context 有限）
        input_text = text[:MAX_INPUT_CHARS]
        prompt = (
            f"将以下内容压缩为{max_chars}字以内的中文摘要，"
            "保留关键信息和数据：\n\n"
            f"{input_text}"
        )
        return self._quick_chat(prompt, max_tokens=256, temperature=0.2)

    def extract_facts(self, texts: list[str]) -> Optional[list[str]]:
        """
        从对话文本中提取用户事实。

        适用于 agent_loop._extract_conversation_memories 的快速路径。
        返回事实列表，失败返回 None。
        """
        if not self._available or not texts:
            return None

        joined = "\n---\n".join(texts[-6:])
        prompt = (
            "从以下用户消息中提取可复用的**用户事实**"
            "（偏好、项目信息、决策、重要上下文）。\n"
            "不要提取：技术经验、错误日志、命令用法。\n"
            "每条用陈述句，一行一条，不要序号和标记。\n\n"
            f"用户消息：\n{joined}"
        )
        result = self._quick_chat(prompt, max_tokens=256, temperature=0.1)
        if result:
            lines = [l.strip() for l in result.split("\n") if l.strip() and len(l.strip()) > 5]
            return lines[:10] if lines else None
        return None

    def refine(self, text: str, style: str = "summary") -> Optional[str]:
        """
        精炼文本（子Agent结果提炼/偏好快速学习）。

        Args:
            text: 需要精炼的文本
            style: "summary"（摘要）、"preference"（偏好提取）、"key_points"（要点提取）

        Returns:
            精炼后的文本，失败回退到原文本
        """
        if not self._available or not text:
            return None

        prompts = {
            "summary": f"用2-3句中文提炼以下内容的核心信息：\n\n{text[:MAX_INPUT_CHARS]}",
            "preference": (
                "从以下内容中提取用户明确表达的偏好和要求，"
                "用 key=value 格式输出：\n\n"
                f"{text[:MAX_INPUT_CHARS]}"
            ),
            "key_points": (
                "提取以下内容的要点，每条一行：\n\n"
                f"{text[:MAX_INPUT_CHARS]}"
            ),
        }
        prompt = prompts.get(style, prompts["summary"])
        return self._quick_chat(prompt, max_tokens=256, temperature=0.2)

    # ── 内部方法 ────────────────────────────────────────────────

    def _check_health(self) -> bool:
        """检测本地模型是否就绪。失败标记不可用。"""
        try:
            req = urllib.request.Request(
                f"{self._base_url}/v1/models",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status == 200:
                    logger.info("[LocalHelper] 本地模型就绪 ✓")
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        logger.info("[LocalHelper] 本地模型不可用，使用云端兜底")
        return False

    def _quick_chat(self, prompt: str, max_tokens: int = 256,
                    temperature: float = 0.1) -> Optional[str]:
        """单次轻量 LLM 调用。超时或失败返回 None。"""
        if not self._available:
            return None
        try:
            payload = json.dumps({
                "model": "",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self._base_url}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=INFERENCE_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip() if content else None
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                KeyError, IndexError) as e:
            logger.debug(f"[LocalHelper] 推理调用失败: {e}")
            # 连续失败标记不可用（由调用方决定是否重置）
            return None

    def reset(self):
        """手动重新检测本地模型是否在线。"""
        self._available = self._check_health()
