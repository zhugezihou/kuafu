"""
夸父 Model Manager v2 — N 后端模型配置管理。

基于 LLMClient v2 的多后端架构：
- providers 列表定义优先级
- 每个 provider 独立配置（base_url / api_key / model）
- 运行时 switch 热切换
- 持久化到 memory/model_config.json
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "memory" / "model_config.json"

# 预定义 Provider 模板（与 llm.py 的 PROVIDER_CONFIGS 同步）
PROVIDER_TEMPLATES = {
    "deepseek": {
        "name": "DeepSeek Chat",
        "url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": ["KUAFFU_API_KEY", "DEEPSEEK_API_KEY"],
        "desc": "DeepSeek 官方 API",
    },
    "openai": {
        "name": "OpenAI",
        "url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": ["OPENAI_API_KEY"],
        "desc": "OpenAI GPT 系列",
    },
    "claude": {
        "name": "Anthropic Claude",
        "url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-20250514",
        "key_env": ["ANTHROPIC_API_KEY"],
        "desc": "Anthropic Claude Sonnet 4",
    },
    "qwen": {
        "name": "Qwen (本地)",
        "url": "http://localhost:8080",
        "model": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        "key_env": [],
        "desc": "本地 llama-server (Qwen3.5-9B)",
    },
    "openrouter": {
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen3.5-9b",
        "key_env": ["OPENROUTER_API_KEY"],
        "desc": "OpenRouter 聚合 API",
    },
}

ALIASES = {
    "ds": "deepseek", "deepseek": "deepseek",
    "openai": "openai", "gpt": "openai", "gpt4o": "openai",
    "claude": "claude", "sonnet": "claude",
    "qwen": "qwen", "local": "qwen",
    "openrouter": "openrouter",
}


def _resolve_api_key(env_names: list[str]) -> str:
    for name in env_names:
        val = os.environ.get(name, "")
        if val and val != "***":
            return val
    return ""


class ModelManager:
    """模型配置管理器 v2 — N 后端。"""

    def __init__(self, profile_id: str = "default"):
        self.profile_id = profile_id
        # providers 列表
        providers_str = os.environ.get("KUAFFU_PROVIDERS", "deepseek")
        self._providers = [p.strip() for p in providers_str.split(",") if p.strip()]
        self._configs: dict[str, dict] = {}  # provider -> config
        self._load()
        # 填充缺失
        for pid in self._providers:
            if pid not in self._configs:
                self._configs[pid] = self._default_config(pid)

    @staticmethod
    def _default_config(provider_id: str) -> dict:
        tmpl = PROVIDER_TEMPLATES.get(provider_id, PROVIDER_TEMPLATES["deepseek"])
        return {
            "provider": provider_id,
            "name": tmpl["name"],
            "base_url": os.environ.get(f"{provider_id.upper()}_BASE_URL", tmpl["url"]),
            "model": os.environ.get(f"{provider_id.upper()}_MODEL", tmpl["model"]),
            "max_tokens": int(os.environ.get(f"{provider_id.upper()}_MAX_TOKENS", "4096")),
            "api_key": _resolve_api_key(tmpl.get("key_env", [])),
            "description": tmpl.get("desc", ""),
        }

    def _load(self):
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                profile = data.get(self.profile_id, {})
                if "providers" in profile:
                    self._providers = profile["providers"]
                if "configs" in profile:
                    self._configs = profile["configs"]
            except Exception:
                pass

    def _save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        data[self.profile_id] = {"providers": self._providers, "configs": self._configs}
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 属性 ──────────────────────────────────────────────────────

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    @property
    def active_provider(self) -> str:
        """返回第一个可用的 provider（快速检测连通性）。"""
        for pid in self._providers:
            cfg = self._configs.get(pid, self._default_config(pid))
            # 本地后端（qwen/custom）需要 base_url 连通才认为可用
            if pid in ("qwen", "custom"):
                url = cfg.get("base_url", "").rstrip("/")
                if url and self._ping(url):
                    return pid
                continue  # 跳过不可用的本地后端
            # 云端后端只要有 api_key 就算可用
            if cfg.get("api_key"):
                return pid
        # 全不可用时降级到 deepseek（即使没 key，给个机会出 401 而非死等）
        return "deepseek"

    @staticmethod
    def _ping(base_url: str, timeout: int = 2) -> bool:
        """快速检测后端是否可用。"""
        import urllib.request
        try:
            req = urllib.request.Request(f"{base_url}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            return False

    def get_active_config(self) -> dict:
        pid = self.active_provider
        return self._configs.get(pid, self._default_config(pid))

    # ── 切换 ──────────────────────────────────────────────────────

    def switch(self, target: str) -> dict:
        """切换模型。

        Args:
            target: provider ID / 别名 / --backend --model 参数

        Returns:
            {"success": bool, "message": str, "configs": dict}
        """
        lower = target.strip().lower()

        # 别名解析
        if lower in ALIASES:
            lower = ALIASES[lower]

        # 按 provider 切换（设为第一优先级）
        if lower in PROVIDER_TEMPLATES:
            if lower in self._providers:
                self._providers.remove(lower)
            self._providers.insert(0, lower)
            if lower not in self._configs:
                self._configs[lower] = self._default_config(lower)
            self._save()

            cfg = self._configs[lower]
            return {
                "success": True,
                "message": f"已切换到 {cfg.get('name', lower)} ({cfg.get('model', '')})",
                "configs": self._configs,
            }

        # --backend/--model 自定义参数
        if lower.startswith("--"):
            return self._apply_custom(target)

        return {"success": False, "message": f"未知 provider: {target}", "configs": self._configs}

    def _apply_custom(self, args: str) -> dict:
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
                    i += 1
            else:
                i += 1

        if "provider" in params:
            pid = params["provider"]
            cfg = self._configs.get(pid, self._default_config(pid))
            for k, v in params.items():
                if k in ("base_url", "model", "api_key", "max_tokens", "temperature"):
                    cfg[k] = v
            self._configs[pid] = cfg
            if pid in self._providers:
                self._providers.remove(pid)
            self._providers.insert(0, pid)
            self._save()
            return {"success": True, "message": f"已配置 {pid}", "configs": self._configs}

        pid = self._providers[0]
        cfg = self._configs.get(pid, self._default_config(pid))
        for k in ("base_url", "model", "api_key", "max_tokens", "temperature"):
            if k in params:
                cfg[k] = params[k]
        self._configs[pid] = cfg
        self._save()
        return {"success": True, "message": f"已更新 {pid}", "configs": self._configs}

    # ── Provider 管理 ─────────────────────────────────────────────

    def add_provider(self, provider_id: str, position: int = -1) -> dict:
        """添加一个 provider 到列表。"""
        if provider_id not in PROVIDER_TEMPLATES:
            return {"success": False, "message": f"未知 provider: {provider_id}"}
        if provider_id not in self._configs:
            self._configs[provider_id] = self._default_config(provider_id)
        if provider_id not in self._providers:
            if position >= 0 and position < len(self._providers):
                self._providers.insert(position, provider_id)
            else:
                self._providers.append(provider_id)
        self._save()
        return {"success": True, "message": f"已添加 {provider_id}"}

    def remove_provider(self, provider_id: str) -> dict:
        if provider_id in self._providers:
            self._providers.remove(provider_id)
            self._save()
            return {"success": True, "message": f"已移除 {provider_id}"}
        return {"success": False, "message": f"未找到 {provider_id}"}

    def list_providers(self) -> list[dict]:
        result = []
        for pid in self._providers:
            cfg = self._configs.get(pid, self._default_config(pid))
            result.append({
                "id": pid,
                "name": cfg.get("name", pid),
                "model": cfg.get("model", ""),
                "active": pid == self.active_provider,
            })
        return result

    def list_templates(self) -> list[dict]:
        result = []
        for tid, tmpl in PROVIDER_TEMPLATES.items():
            active = tid in self._providers
            result.append({
                "id": tid,
                "name": tmpl["name"],
                "model": tmpl["model"],
                "active": active,
            })
        return result

    def as_dict(self) -> dict:
        return {
            "providers": self._providers,
            "active": self.active_provider,
            "configs": self._configs,
        }

    def apply(self, config: dict):
        if "providers" in config:
            self._providers = config["providers"]
        if "configs" in config:
            self._configs.update(config["configs"])
        self._save()
