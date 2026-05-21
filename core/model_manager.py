"""
夸父 Model Manager — 运行时模型切换与持久化。

职责：
1. 预定义模型模板（cloud + local）
2. 运行时切换后端/模型/URL/参数
3. 持久化当前模型配置文件
4. 从 env/环境变量初始化
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "memory" / "model_config.json"

# ── 预定义模型模板 ──────────────────────────────────────────
MODEL_TEMPLATES = {
    "cloud:deepseek": {
        "name": "DeepSeek Chat (云端)",
        "backend": "cloud",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "KUAFFU_API_KEY",
        "max_tokens": 4096,
        "temperature": 0.7,
        "description": "DeepSeek 官方 API，默认云端模型",
    },
    "cloud:claude": {
        "name": "Claude (云端)",
        "backend": "cloud",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
        "max_tokens": 4096,
        "temperature": 0.7,
        "description": "Anthropic Claude Sonnet 4",
    },
    "cloud:openai": {
        "name": "OpenAI GPT-4o (云端)",
        "backend": "cloud",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
        "max_tokens": 4096,
        "temperature": 0.7,
        "description": "OpenAI GPT-4o",
    },
    "local:qwen": {
        "name": "Qwen3.5-9B (本地)",
        "backend": "local",
        "base_url": "http://172.21.224.1:8080",
        "model": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        "api_key_env": "",
        "max_tokens": 4096,
        "temperature": 0.7,
        "description": "本地 llama-server (Qwen3.5-9B, port 8080)",
    },
    "local:qwen_ctx8k": {
        "name": "Qwen3.5-9B 8K (本地)",
        "backend": "local",
        "base_url": "http://172.21.224.1:8080",
        "model": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        "api_key_env": "",
        "max_tokens": 8192,
        "temperature": 0.7,
        "description": "本地 llama-server (8K max_tokens)",
    },
}

# 简写别名
ALIASES = {
    "deepseek": "cloud:deepseek",
    "ds": "cloud:deepseek",
    "claude": "cloud:claude",
    "sonnet": "cloud:claude",
    "openai": "cloud:openai",
    "gpt4o": "cloud:openai",
    "gpt-4o": "cloud:openai",
    "qwen": "local:qwen",
    "local": "local:qwen",
    "qwen8k": "local:qwen_ctx8k",
}


class ModelManager:
    """模型配置管理器。

    管理当前使用的 LLM 模型配置，支持运行时切换和持久化。
    """

    def __init__(self, profile_id: str = "default"):
        self.profile_id = profile_id
        self._config = self._default_config()
        self._load()

    # ── 配置结构 ─────────────────────────────────────────────

    @staticmethod
    def _default_config() -> dict:
        """从环境变量推断默认配置。"""
        backend = os.environ.get("KUAFFU_BACKEND", "cloud").strip().lower()
        if backend == "local":
            return {
                "backend": "local",
                "base_url": os.environ.get("KUAFFU_BASE_URL", "http://localhost:8080"),
                "model": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
                "max_tokens": 4096,
                "temperature": 0.7,
                "profile": "local:qwen",
            }
        return {
            "backend": "cloud",
            "base_url": os.environ.get("KUAFFU_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("KUAFFU_MODEL", "deepseek-chat"),
            "max_tokens": 4096,
            "temperature": 0.7,
            "profile": "cloud:deepseek",
        }

    def _load(self):
        """从持久化文件加载配置。"""
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                profile = data.get(self.profile_id)
                if profile:
                    self._config.update(profile)
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self):
        """持久化当前配置到文件。"""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        data[self.profile_id] = dict(self._config)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 属性 ─────────────────────────────────────────────────

    @property
    def backend(self) -> str:
        return self._config["backend"]

    @property
    def model(self) -> str:
        return self._config["model"]

    @property
    def base_url(self) -> str:
        return self._config["base_url"]

    @property
    def max_tokens(self) -> int:
        return self._config["max_tokens"]

    @property
    def temperature(self) -> float:
        return self._config["temperature"]

    @property
    def profile(self) -> str:
        return self._config.get("profile", "")

    def as_dict(self) -> dict:
        return dict(self._config)

    def apply(self, config: dict):
        """直接应用外部配置到当前 ModelManager 并持久化。"""
        for key in ("backend", "model", "base_url", "max_tokens", "temperature"):
            if key in config:
                self._config[key] = config[key]
        self._config["profile"] = f"custom:{self._config['model']}"
        self._save()

    # ── 切换 ─────────────────────────────────────────────────

    def switch(self, target: str) -> dict:
        """切换模型配置。

        Args:
            target: 模板 ID（如 'cloud:deepseek'）、别名（如 'claude'）
                     或直接 'local' / 'cloud' 快速切后端

        Returns:
            {"success": bool, "config": dict, "message": str}
        """
        lower = target.strip().lower()

        # 1. 快速切换后端（保持当前模型）
        if lower == "local":
            return self._quick_switch_backend("local")
        if lower == "cloud":
            return self._quick_switch_backend("cloud")

        # 2. 别名查找
        if lower in ALIASES:
            target = ALIASES[lower]

        # 3. 模板查找
        if target in MODEL_TEMPLATES:
            tmpl = MODEL_TEMPLATES[target]
            self._apply_template(tmpl, profile_id=target)
            self._save()
            return {
                "success": True,
                "config": self.as_dict(),
                "message": f"✅ 已切换到 **{tmpl['name']}** (`{target}`)",
            }

        # 4. 自定义模型参数
        #    格式: "--backend local --model xxx --base-url http://..."
        if lower.startswith("--"):
            return self._parse_custom_args(target)

        # 5. 尝试作为自定义模型名（保持当前后端）
        self._config["model"] = target
        self._config["profile"] = f"custom:{target}"
        self._save()
        return {
            "success": True,
            "config": self.as_dict(),
            "message": f"✅ 模型已切换为 `{target}`（后端: {self.backend}）",
        }

    def _quick_switch_backend(self, backend: str) -> dict:
        """快速切换后端。"""
        old_backend = self._config["backend"]
        if old_backend == backend:
            return {
                "success": True,
                "config": self.as_dict(),
                "message": f"ℹ️ 已经是 {backend} 后端，无需切换",
            }

        if backend == "local":
            self._config["backend"] = "local"
            self._config["base_url"] = "http://172.21.224.1:8080"
            self._config["profile"] = "local:qwen"
            self._config["model"] = "Qwen3.5-9B-UD-Q4_K_XL.gguf"
        else:
            self._config["backend"] = "cloud"
            self._config["base_url"] = os.environ.get(
                "KUAFFU_BASE_URL", "https://api.deepseek.com"
            )
            self._config["profile"] = "cloud:deepseek"
            self._config["model"] = "deepseek-chat"

        self._save()
        return {
            "success": True,
            "config": self.as_dict(),
            "message": f"✅ 已切换到 **{backend} 后端**（模型: {self.model})",
        }

    def _apply_template(self, tmpl: dict, profile_id: str):
        """应用预定义模板配置。"""
        self._config["backend"] = tmpl["backend"]
        self._config["base_url"] = tmpl["base_url"]
        self._config["model"] = tmpl["model"]
        self._config["max_tokens"] = tmpl["max_tokens"]
        self._config["temperature"] = tmpl["temperature"]
        self._config["profile"] = profile_id

        # 尝试从环境变量读取 API key（如果有）
        if tmpl.get("api_key_env"):
            env_key = os.environ.get(tmpl["api_key_env"])
            if env_key:
                self._config["api_key"] = env_key

    def _parse_custom_args(self, args: str) -> dict:
        """解析自定义参数格式：
        --backend local --model xxx --base-url http://... --max-tokens 8192
        """
        import shlex

        try:
            tokens = shlex.split(args)
        except ValueError:
            tokens = args.split()

        params = {}
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t.startswith("--"):
                key = t.lstrip("-").replace("-", "_")
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                    params[key] = tokens[i + 1]
                    i += 2
                else:
                    params[key] = True
                    i += 1
            else:
                i += 1

        allowed = {"backend", "model", "base_url", "base_url", "max_tokens", "temperature"}
        applied = []
        for key, val in params.items():
            if key in allowed and val is not True:
                if key == "max_tokens":
                    self._config[key] = int(val)
                elif key == "temperature":
                    self._config[key] = float(val)
                else:
                    self._config[key] = val
                applied.append(f"{key}={val}")

        self._config["profile"] = "custom"
        self._save()

        if applied:
            return {
                "success": True,
                "config": self.as_dict(),
                "message": f"✅ 已应用自定义参数: {', '.join(applied)}",
            }
        return {
            "success": False,
            "config": self.as_dict(),
            "message": "❌ 未识别到有效参数。格式: --backend local --model xxx --base-url http://...",
        }

    # ── 列表 ─────────────────────────────────────────────────

    def list_templates(self) -> list[dict]:
        """列出所有可用模板。"""
        result = []
        for tid, tmpl in MODEL_TEMPLATES.items():
            result.append({
                "id": tid,
                "name": tmpl["name"],
                "description": tmpl.get("description", ""),
                "backend": tmpl["backend"],
                "model": tmpl["model"],
                "active": tid == self._config.get("profile"),
            })
        return result

    def list_aliases(self) -> dict:
        """列出所有简写别名。"""
        return dict(ALIASES)

    # ── 重置 ─────────────────────────────────────────────────

    def reset(self):
        """重置为默认配置（从环境变量推断）。"""
        self._config = self._default_config()
        self._save()
        return {
            "success": True,
            "config": self.as_dict(),
            "message": "✅ 已重置为默认模型配置",
        }
