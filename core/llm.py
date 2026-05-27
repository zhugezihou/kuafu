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
import threading
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

import logging
logger = logging.getLogger(__name__)

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
LOCAL_BASE_URL = os.environ.get("KUAFFU_LOCAL_BASE_URL", "http://localhost:8080")
LOCAL_MODEL = "Qwen3.5-9B-UD-Q4_K_XL.gguf"
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
        timeout: int = 120,
    ):
        # 后端选择：参数 > 环境变量 > 默认 cloud
        backend = (backend or KUAFFU_BACKEND).strip().lower()

        if backend == "local":
            self.backend = "local"
            self.api_key = api_key or "ignored"
            self.base_url = (base_url or LOCAL_BASE_URL).rstrip("/")
            self.model = model or LOCAL_MODEL
            self.max_tokens = max_tokens or LOCAL_MAX_TOKENS
            self.temperature = temperature
            self.timeout = timeout
        else:
            self.backend = "cloud"
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

    @staticmethod
    def _clean_surrogates(obj):
        """递归清理 dict/list 中所有字符串的 surrogate 字符。"""
        if isinstance(obj, str):
            return obj.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
        elif isinstance(obj, dict):
            return {k: LLMClient._clean_surrogates(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [LLMClient._clean_surrogates(item) for item in obj]
        return obj

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
                # 清理 surrogate 字符，避免 API 服务端 JSON 解析失败
                payload = self._clean_surrogates(payload)
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    self._api_url(),
                    data=data,
                    headers=self._build_headers(),
                    method="POST",
                )

                # ⏳ 长等待日志：单次请求超过 30 秒时输出进度
                _req_start = time.time()
                _logged = False

                def _long_wait_log():
                    nonlocal _logged
                    while True:
                        # 请求已完成，_req_start 被设为 None
                        if _req_start is None:
                            return
                        elapsed = time.time() - _req_start
                        if elapsed >= 30 and not _logged:
                            _logged = True
                            logger.info(f"⏳ LLM API 请求已等待 {elapsed:.0f} 秒（timeout={self.timeout}）")
                        time.sleep(10)

                _lw_thread = threading.Thread(target=_long_wait_log, daemon=True)
                _lw_thread.start()

                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        raw = resp.read()
                finally:
                    if _logged:
                        logger.info(f"⏳ LLM API 请求完成（耗时 {time.time() - _req_start:.1f} 秒）")
                    _req_start = None  # 标记监控线程退出

                result = json.loads(raw.decode("utf-8", errors="replace"))

                choice = result.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "") or message.get("reasoning_content", "")
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
                # 上下文超限：尝试截断旧消息再重试
                if e.code == 400 and "exceed" in body.lower() and attempt < max_retries - 1:
                    # 切掉最旧的一半非 system 消息
                    non_system = [(i, m) for i, m in enumerate(messages) if m.get("role") != "system"]
                    system_msgs = [m for m in messages if m.get("role") == "system"]
                    if len(non_system) > 4:
                        cut_count = len(non_system) // 3
                        cut_indices = {i for i, _ in non_system[:cut_count]}
                        messages = [m for idx, m in enumerate(messages) if idx not in cut_indices]
                        last_error = f"HTTP 400 (ctx): 截断 {cut_count} 条旧消息后重试"
                        time.sleep(0.5)
                        continue
                time.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                time.sleep(1 * (attempt + 1))

        # ── 降级重试：cloud 超时后自动切 local ──
        if self.backend == "cloud" and LOCAL_BASE_URL is not None:
            try:
                local_client = LLMClient(
                    backend="local",
                    api_key="ignored",
                    base_url=LOCAL_BASE_URL,
                    model=LOCAL_MODEL,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    timeout=self.timeout,
                )
                logger.info(f"🌧️  Cloud API 重试 {max_retries} 次均失败，降级到本地模型 ({LOCAL_MODEL})...")

                resp = local_client.chat(messages, tools=tools, max_retries=1)
                if resp["success"]:
                    logger.info("🌤️  本地模型降级成功")
                    return resp
                else:
                    logger.warning(f"☁️  本地模型也失败: {resp.get('error', '')[:60]}")
            except Exception as e:
                logger.warning(f"☁️  本地模型降级异常: {e}")

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

    def switch(self, config: dict) -> str:
        """运行时切换模型配置。

        Args:
            config: 包含 'backend', 'model', 'base_url', 'max_tokens', 'temperature' 的字典。
                    不提供的字段保持当前值。

        Returns:
            描述切换结果的字符串。
        """
        old_model = self.model
        old_backend = self.backend

        # 应用新配置
        if "backend" in config:
            self.backend = config["backend"].strip().lower()
        if "model" in config:
            self.model = config["model"]
        if "base_url" in config:
            self.base_url = config["base_url"].rstrip("/")
        if "max_tokens" in config:
            self.max_tokens = int(config["max_tokens"])
        if "temperature" in config:
            self.temperature = float(config["temperature"])
        if "api_key" in config:
            self.api_key = config["api_key"]

        # 同步环境变量（供子进程和 Reviewer 使用）
        os.environ["KUAFFU_BACKEND"] = self.backend

        changed = []
        if self.model != old_model:
            changed.append(f"模型: {old_model} → {self.model}")
        if self.backend != old_backend:
            changed.append(f"后端: {old_backend} → {self.backend}")

        return f"模型已切换: {'; '.join(changed) if changed else '无变化'}" if changed else "配置无变化"

    def count_tokens(self, text: str) -> int:
        """粗略估算 token 数（中文约 1.5 tokens/字，英文约 1 token/4 字符）。"""
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4)
