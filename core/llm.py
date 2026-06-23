"""

v2 相对 v1 的改进：
  1. 从双后端（cloud/local）扩展为 N 后端
  2. 按优先级列表降级（主 → 备1 → 备2）
  3. 各后端独立 API Key / Base URL / 模型名
  4. 运行时 switch() 热切换
  5. 保留原有兼容接口

后端配置（通过 .env 或 ModelManager）：
  KUAFU_PROVIDERS=deepseek,openai,qwen  # 降级顺序
  DEEPSEEK_API_KEY=sk-xxx
  DEEPSEEK_BASE_URL=https://api.deepseek.com
  DEEPSEEK_MODEL=deepseek-chat
  OPENAI_API_KEY=sk-xxx
  OPENAI_BASE_URL=https://api.openai.com/v1
  OPENAI_MODEL=gpt-4o-mini
  QWEN_BASE_URL=http://localhost:8080
  QWEN_MODEL=Qwen3.5-9B-UD-Q4_K_XL.gguf
"""

from __future__ import annotations
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
#


import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── 默认配置模板 ──────────────────────────────────────────────

DEFAULT_PROVIDERS = os.environ.get("KUAFU_PROVIDERS", "deepseek").split(",")

PROVIDER_CONFIGS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek Chat",
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key_env": ["KUAFU_API_KEY", "DEEPSEEK_API_KEY"],
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "openai": {
        "name": "OpenAI",
        "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": ["OPENAI_API_KEY"],
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "claude": {
        "name": "Claude (OpenAI-compatible)",
        "base_url": os.environ.get("CLAUDE_BASE_URL", "https://api.anthropic.com"),
        "api_key_env": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
        "model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "qwen": {
        "name": "本地 Qwen（llama-server）",
        "base_url": os.environ.get("QWEN_BASE_URL", "http://localhost:8080"),
        "api_key_env": [],
        "model": os.environ.get("QWEN_MODEL", "Qwen3.5-9B-DeepSeek-V4-Flash-IQ4_XS.gguf"),
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "api_key_env": ["OPENROUTER_API_KEY"],
        "model": os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.5-9b"),
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "custom": {
        "name": "自定义",
        "base_url": os.environ.get("CUSTOM_BASE_URL", ""),
        "api_key_env": ["CUSTOM_API_KEY"],
        "model": os.environ.get("CUSTOM_MODEL", ""),
        "max_tokens": 4096,
        "temperature": 0.7,
    },
}


def _resolve_api_key(env_names: list[str]) -> str:
    for name in env_names:
        val = os.environ.get(name, "")
        if val and val != "***":
            return val
    return ""


class LLMBackend:
    """单个 LLM 后端。"""

    def __init__(self, provider_id: str, config: dict | None = None):
        cfg = config or PROVIDER_CONFIGS.get(provider_id, PROVIDER_CONFIGS["deepseek"])
        self.provider_id = provider_id
        self.name = cfg.get("name", provider_id)
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg.get("model", "")
        self.max_tokens = int(cfg.get("max_tokens", 4096))
        self.temperature = float(cfg.get("temperature", 0.7))
        self.api_key = cfg.get("api_key") or _resolve_api_key(cfg.get("api_key_env", []))

    def is_available(self) -> bool:
        """检查后端是否可用（本地检查 URL 连通性，云端检查 API Key）。"""
        if not self.base_url:
            return False
        if self.api_key or not self._needs_api_key():
            return True
        # 尝试 ping（超时 2 秒）
        try:
            req = urllib.request.Request(f"{self.base_url}/v1/models", method="GET")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:
            return False

    def _needs_api_key(self) -> bool:
        return self.provider_id not in ("qwen", "custom") or bool(PROVIDER_CONFIGS.get(self.provider_id, {}).get("api_key_env"))

    def to_dict(self) -> dict:
        return {
            "provider": self.provider_id,
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "max_tokens": self.max_tokens,
        }

    def __repr__(self) -> str:
        return f"<LLMBackend {self.provider_id}:{self.model}>"


def _clean_surrogates(obj):
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
    elif isinstance(obj, dict):
        return {k: _clean_surrogates(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_surrogates(item) for item in obj]
    return obj


class LLMClient:
    """LLM 客户端 v2 — N 后端 + 自动降级。"""

    def __init__(self, providers: str | list[str] | None = None,
                 api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, max_tokens: int = 4096,
                 temperature: float = 0.7, timeout: int = 120):
        # 构建后端列表
        if providers is None:
            providers = os.environ.get("KUAFU_PROVIDERS", "deepseek").split(",")
        if isinstance(providers, str):
            providers = [p.strip() for p in providers.split(",") if p.strip()]

        self.backends: list[LLMBackend] = []
        for pid in providers:
            cfg = dict(PROVIDER_CONFIGS.get(pid, PROVIDER_CONFIGS["deepseek"]))
            if pid == providers[0]:
                # 主后端：接受参数覆盖
                if api_key:
                    cfg["api_key"] = api_key
                if base_url:
                    cfg["base_url"] = base_url
                if model:
                    cfg["model"] = model
            self.backends.append(LLMBackend(pid, cfg))

        # 快速检测：主后端如果是 qwen/custom，检测连通性，不可用则冷却
        # threaded ping 避免阻塞（最多等 1 秒）
        failures: dict[str, float] = {}
        main = self.backends[0] if self.backends else None
        if main and main.provider_id in ("qwen", "custom") and main.base_url:
            ping_result = [True]
            def _ping():
                try:
                    req = urllib.request.Request(f"{main.base_url}/v1/models", method="GET")
                    with urllib.request.urlopen(req, timeout=2):
                        pass
                except urllib.error.HTTPError:
                    pass  # 4xx = server alive
                except Exception:
                    ping_result[0] = False
            t = threading.Thread(target=_ping, daemon=True)
            t.start()
            t.join(timeout=0.5)  # 最多等 0.5 秒
            if not ping_result[0] or t.is_alive():
                failures[main.provider_id] = time.time() + 300

        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._failures = failures
        self._lock = threading.Lock()
        self._last_successful: str = ""

        # 按可用性选择初始后端
        first_available = self._select_backend()
        if not first_available:
            first_available = self.backends[0] if self.backends else None

        # 兼容旧接口
        if first_available:
            self.backend = first_available.provider_id
            self.api_key = first_available.api_key
            self.base_url = first_available.base_url
            self.model = first_available.model
        else:
            self.backend = "cloud"
            self.api_key = ""
            self.base_url = ""
            self.model = ""

    # ── 选择最佳后端 ───────────────────────────────────────────

    def _select_backend(self) -> LLMBackend | None:
        """按优先级选择可用后端（跳过冷却期内的）。"""
        now = time.time()
        with self._lock:
            # 上次成功的后端优先（如果在冷却期内不惩罚）
            if self._last_successful:
                for bk in self.backends:
                    if bk.provider_id == self._last_successful:
                        cooldown = self._failures.get(bk.provider_id, 0)
                        if now >= cooldown:
                            return bk

            # 按优先级
            for bk in self.backends:
                cooldown = self._failures.get(bk.provider_id, 0)
                if now >= cooldown:
                    return bk

            # 全部冷却中 → 选第一个（即使冷却）
            return self.backends[0] if self.backends else None

    def _record_failure(self, provider_id: str):
        """记录后端失败，30 秒冷却。"""
        with self._lock:
            self._failures[provider_id] = time.time() + 30

    def _record_success(self, provider_id: str):
        self._last_successful = provider_id

    # ── API 调用 ────────────────────────────────────────────────

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             stream: bool = False, max_retries: int = 2) -> dict:
        """调用 LLM，支持多后端自动降级。

        降级流程：
          主后端 → 失败 → 冷却 30s → 下一后端 → ... → 全部失败 → 返回错误
        """
        last_error = None

        # 逐后端尝试，远端（deepseek/openai）只试 1 次，本地可重试
        # 用 _select_backend 做后端切换，以自省方式判断是否远端
        for attempt in range(max_retries * len(self.backends)):
            backend = self._select_backend()
            if not backend:
                break

            try:
                result = self._call_backend(backend, messages, tools, stream)
                if result["success"]:
                    self._record_success(backend.provider_id)
                    return result

                # 后端级错误
                self._record_failure(backend.provider_id)
                last_error = result.get("error", "unknown")
                logger.warning(f"后端 {backend.provider_id} 失败: {last_error}")

                # 认证错误不重试其他后端（节省时间）
                if "401" in (last_error or "") or "403" in (last_error or ""):
                    break

            except Exception as e:
                self._record_failure(backend.provider_id)
                last_error = str(e)
                logger.warning(f"后端 {backend.provider_id} 异常: {e}")

            time.sleep(0.5)

        return {
            "success": False, "content": "",
            "tool_calls": None, "usage": None,
            "error": last_error or "所有后端均不可用",
        }

    def _call_backend(self, backend: LLMBackend, messages: list[dict],
                      tools: Optional[list[dict]] = None,
                      stream: bool = False) -> dict:
        """向单个后端发送请求。"""
        # 无工具调用场景允许更大输出（基金定投/架构介绍等纯文本）
        effective_max = max(self.max_tokens, 8192) if not tools else self.max_tokens
        payload = {
            "model": backend.model,
            "messages": _clean_surrogates(messages),
            "max_tokens": effective_max,
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools

        url = f"{backend.base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if backend.api_key:
            headers["Authorization"] = f"Bearer {backend.api_key}"

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return {"success": False, "content": "", "error": f"HTTP {e.code}: {body[:200]}"}
        except Exception as e:
            return {"success": False, "content": "", "error": str(e)}

        result = json.loads(raw.decode("utf-8", errors="replace"))
        choice = result.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "") or message.get("reasoning_content", "")
        tool_calls_raw = message.get("tool_calls")

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
                    "function": {"name": fn.get("name", ""), "arguments": args},
                })

        return {
            "success": True, "content": content or "",
            "tool_calls": tool_calls,
            "usage": result.get("usage"),
            "error": None,
        }

    def chat_stream(self, messages: list[dict],
                    tools: Optional[list[dict]] = None) -> dict:
        """流式调用（只使用当前主后端）。"""
        backend = self._select_backend()
        if not backend:
            return {"success": False, "content": "", "error": "无可用后端"}

        payload = {
            "model": backend.model,
            "messages": _clean_surrogates(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        url = f"{backend.base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if backend.api_key:
            headers["Authorization"] = f"Bearer {backend.api_key}"

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        content_chunks = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                dj = json.loads(data_str)
                                delta = dj.get("choices", [{}])[0].get("delta", {})
                                c = delta.get("content", "")
                                if c:
                                    content_chunks.append(c)
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            return {"success": False, "content": "", "error": str(e)}

        return {"success": True, "content": "".join(content_chunks), "tool_calls": None, "usage": None, "error": None}

    # ── 运行时切换 ──────────────────────────────────────────────

    def switch(self, config: dict | str) -> str:
        """运行时切换模型配置。

        Args:
            config: 字典（新配置）或字符串（providers 列表或后端名）
        """
        if isinstance(config, str):
            # 后端名或 providers 列表
            if config in PROVIDER_CONFIGS:
                config = {"provider": config}
            else:
                config = {"providers": [p.strip() for p in config.split(",") if p.strip()]}

        if "providers" in config:
            self.__init__(providers=config["providers"])
            return f"后端列表已切换: {config['providers']}"

        # 替换或更新主后端配置
        if "provider" in config:
            pid = config["provider"]
            cfg = dict(PROVIDER_CONFIGS.get(pid, PROVIDER_CONFIGS["deepseek"]))
            if "api_key" in config:
                cfg["api_key"] = config["api_key"]
            if "base_url" in config:
                cfg["base_url"] = config["base_url"]
            if "model" in config:
                cfg["model"] = config["model"]

            # 插入到 backends 列表第一优先级
            self.backends = [LLMBackend(pid, cfg)] + [
                b for b in self.backends if b.provider_id != pid
            ]

            self.api_key = self.backends[0].api_key
            self.base_url = self.backends[0].base_url
            self.model = self.backends[0].model
            self.backend = self.backends[0].provider_id

            return f"已切换到 {pid}: {self.backends[0].model}"

        return "配置无变化"

    # ── 状态 ─────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """返回所有后端状态。"""
        status = []
        for bk in self.backends:
            cooldown = self._failures.get(bk.provider_id, 0)
            status.append({
                "provider": bk.provider_id,
                "name": bk.name,
                "model": bk.model,
                "available": time.time() >= cooldown,
                "cooldown_remaining": max(0, cooldown - time.time()),
            })
        return {
            "active": self.backend,
            "last_successful": self._last_successful,
            "backends": status,
        }

    # ── Token 估算 ──────────────────────────────────────────────

    def get_context_window(self) -> int:
        """获取当前激活后端的上下文窗口大小。

        不同模型的上下文窗口：
        - DeepSeek V3/R1: 1M (1048576)
        - GPT-4o: 128K
        - Claude Sonnet 4: 200K
        - OpenRouter: 依赖具体路由的模型

        返回安全阈值（80% 窗口大小），留充足空间给输出。
        """
        # 根据模型名称推断
        model_lower = self.model.lower()
        if "deepseek" in model_lower:
            return 800000  # 1M 的 80%
        elif "claude" in model_lower and "sonnet" in model_lower:
            return 160000  # 200K 的 80%
        elif "claude" in model_lower:
            return 160000
        elif "qwen" in model_lower:
            return 25000  # 32K 的 80%（本地模型）
        elif "gpt-4" in model_lower:
            return 100000  # 128K 的 80%
        elif "gpt" in model_lower:
            return 80000
        # 默认安全值
        return 100000

    @staticmethod
    def count_tokens(text: str) -> int:
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4)
