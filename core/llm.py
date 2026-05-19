"""
夸父 LLM 客户端 — 封装 DeepSeek API 调用。

职责：
1. 组装对话消息（system + user + history）
2. 调用 DeepSeek Chat API（非流式 + 流式）
3. 解析响应，提取文本和 tool_calls
4. 错误重试和降级

依赖：
- 环境变量 DEEPSEEK_API_KEY
- 支持 OpenAI-compatible API
"""

import json
import os
import time
import re
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

# 加载 .env — 只查项目根目录
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DEEPSEEK_API_KEY = os.environ.get("KUAFFU_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("KUAFFU_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
KUAFFU_BACKEND = os.environ.get("KUAFFU_BACKEND", "cloud").strip().lower()
DEFAULT_MODEL = "deepseek-chat"

# 本地模型配置
LOCAL_BASE_URL = "http://localhost:8080"
LOCAL_MODEL = "Qwen3.5-9B-Q4_K_M.gguf"
LOCAL_MAX_TOKENS = 4096


class LLMClient:
    """LLM 客户端，封装 DeepSeek Chat API。"""

    def __init__(
        self,
        backend: str = "",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 60,
    ):
        # 后端选择：参数 > 环境变量 > 默认 cloud
        backend = (backend or KUAFFU_BACKEND).strip().lower()

        if backend == "local":
            self.api_key = api_key or "ignored"
            self.base_url = (base_url or LOCAL_BASE_URL).rstrip("/")
            self.model = model or LOCAL_MODEL
            self.max_tokens = max_tokens or LOCAL_MAX_TOKENS
            self.temperature = temperature
            self.timeout = timeout
        else:
            self.api_key = api_key or DEEPSEEK_API_KEY
            self.base_url = (base_url or DEEPSEEK_BASE_URL).rstrip("/")
            self.model = model or DEFAULT_MODEL
            self.max_tokens = max_tokens
            self.temperature = temperature
            self.timeout = timeout
            if not self.api_key:
                raise ValueError(
                    "API Key 未设置。请在项目 .env 中设置 KUAFFU_API_KEY，或传入 api_key。"
                )

    def _api_url(self) -> str:
        """构建 Chat Completions API URL。"""
        return f"{self.base_url}/v1/chat/completions"

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_payload(
        self,
        messages: list[dict],
        stream: bool = False,
        tools: Optional[list[dict]] = None,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: bool = False,
        max_retries: int = 3,
    ) -> dict:
        """调用 LLM Chat API。

        Returns:
            {
                "success": bool,
                "content": str,
                "tool_calls": list[dict] or None,
                "usage": dict or None,
                "error": str or None,
            }
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                payload = self._build_payload(messages, stream=False, tools=tools)
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self._api_url(),
                    data=data,
                    headers=self._build_headers(),
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                choice = result.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "")
                tool_calls_raw = message.get("tool_calls")

                # 格式化 tool_calls
                tool_calls = None
                if tool_calls_raw:
                    tool_calls = []
                    for tc in tool_calls_raw:
                        fn = tc.get("function", {})
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            args = {"raw": fn.get("arguments", "")}
                        tool_calls.append({
                            "id": tc.get("id", ""),
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": fn.get("name", ""),
                                "arguments": args,
                            },
                        })

                usage = result.get("usage")
                return {
                    "success": True,
                    "content": content or "",
                    "tool_calls": tool_calls,
                    "usage": usage,
                    "error": None,
                }

            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {e.code}: {body[:200]}"
                if e.code in (401, 403):
                    break  # 认证错误不重试
                time.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                time.sleep(1 * (attempt + 1))

        return {
            "success": False,
            "content": "",
            "tool_calls": None,
            "usage": None,
            "error": last_error,
        }

    def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """流式调用 LLM Chat API。

        返回与 chat() 相同的结构，但 content 为非流式完整文本。
        """
        last_error = None
        content_chunks = []
        try:
            payload = self._build_payload(messages, stream=True, tools=tools)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._api_url(),
                data=data,
                headers=self._build_headers(),
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", errors="replace")
                    # 解析 SSE 事件
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data_json = json.loads(data_str)
                                delta = (
                                    data_json.get("choices", [{}])[0]
                                    .get("delta", {})
                                )
                                content = delta.get("content", "")
                                if content:
                                    content_chunks.append(content)
                            except json.JSONDecodeError:
                                pass

            content = "".join(content_chunks)
            return {
                "success": True,
                "content": content,
                "tool_calls": None,
                "usage": None,
                "error": None,
            }

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {e.code}: {body[:200]}"
        except Exception as e:
            last_error = str(e)

        return {
            "success": False,
            "content": "",
            "tool_calls": None,
            "usage": None,
            "error": last_error,
        }

    def count_tokens(self, text: str) -> int:
        """粗略估算 token 数（中文约 1.5 tokens/字，英文约 1 token/4 字符）。"""
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4)
