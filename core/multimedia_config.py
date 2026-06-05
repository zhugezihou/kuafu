"""
core/multimedia_config.py — 夸父多媒体服务配置管理

为多媒体工具箱（image_gen / vision / TTS / STT）提供：
1. 多 provider 配置模板（类似 llm.py 的 PROVIDER_CONFIGS）
2. 环境变量加载（.env 兼容）
3. 连接测试
4. 运行时热切换

设计目标：
- 与 setup_wizard.py 集成，提供多媒体配置向导
- provider 可扩展（添加新服务只需在 PROVIDER_TEMPLATES 加条目）
- 零依赖（纯 Python 标准库 + urllib）
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kuafu.multimedia")

ROOT_DIR = Path(__file__).resolve().parent.parent

# ── Provider 模板 ────────────────────────────────────────────
# 每个多媒体服务的多 provider 配置。

IMAGE_GEN_PROVIDERS: dict[str, dict] = {
    "siliconflow": {
        "name": "SiliconFlow",
        "url": "https://api.siliconflow.cn/v1/images/generations",
        "key_env": ["SILICONFLOW_API_KEY", "IMAGE_GEN_API_KEY"],
        "model": "black-forest-labs/FLUX.1-dev",
        "desc": "SiliconFlow 开源图像生成 API。支持 FLUX/Stable Diffusion。需注册获取 API Key",
    },
    "openai": {
        "name": "OpenAI DALL-E",
        "url": "https://api.openai.com/v1/images/generations",
        "key_env": ["OPENAI_API_KEY"],
        "model": "dall-e-3",
        "desc": "OpenAI DALL-E 3 图像生成。需要 OpenAI API Key",
    },
    "comfyui": {
        "name": "ComfyUI (本地)",
        "url": "http://127.0.0.1:8188/prompt",
        "key_env": [],
        "model": "",
        "desc": "本地 ComfyUI。需在本地启动 ComfyUI 服务（默认端口 8188）",
    },
    "custom": {
        "name": "自定义 (Custom)",
        "url": "",  # 由 IMAGE_GEN_API_URL 指定
        "key_env": ["IMAGE_GEN_API_KEY"],
        "model": "",  # 由 IMAGE_GEN_MODEL 指定
        "desc": "自定义 API。设置 IMAGE_GEN_API_URL 和 IMAGE_GEN_API_KEY。若大模型同时支持图像生成/理解/语音，可统一指向同一端点",
    },
}

VISION_PROVIDERS: dict[str, dict] = {
    "openai": {
        "name": "OpenAI GPT-4V",
        "url": "https://api.openai.com/v1/chat/completions",
        "key_env": ["OPENAI_API_KEY"],
        "model": "gpt-4o",
        "desc": "OpenAI GPT-4o 多模态。支持图像理解、OCR、场景分析",
    },
    "siliconflow": {
        "name": "SiliconFlow Qwen-VL",
        "url": "https://api.siliconflow.cn/v1/chat/completions",
        "key_env": ["SILICONFLOW_API_KEY", "VISION_API_KEY"],
        "model": "Qwen/Qwen2-VL-72B-Instruct",
        "desc": "SiliconFlow 上的 Qwen-VL 多模态模型",
    },
    "qwen-local": {
        "name": "Qwen-VL (本地)",
        "url": "http://localhost:8080/v1/chat/completions",
        "key_env": [],
        "model": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        "desc": "本地 llama-server 的 Qwen 视觉模型。注意：Qwen3.5-9B 不支持图像输入",
    },
    "custom": {
        "name": "自定义 (Custom)",
        "url": "",
        "key_env": ["VISION_API_KEY"],
        "model": "",
        "desc": "自定义多模态 API。设置 VISION_API_URL 和 VISION_API_KEY, VISION_MODEL。可与 image_gen 共用同一端点",
    },
}

TTS_PROVIDERS: dict[str, dict] = {
    "openai": {
        "name": "OpenAI TTS",
        "url": "https://api.openai.com/v1/audio/speech",
        "key_env": ["OPENAI_API_KEY"],
        "model": "tts-1",
        "voice": "alloy",
        "desc": "OpenAI TTS 语音合成。支持多种音色。需要 OpenAI API Key",
    },
    "edge": {
        "name": "Edge TTS (本地)",
        "url": "",
        "key_env": [],
        "model": "",
        "voice": "zh-CN-XiaoxiaoNeural",
        "desc": "Microsoft Edge TTS。需要安装 edge-tts（pip install edge-tts）",
    },
    "espeak": {
        "name": "eSpeak (本地)",
        "url": "",
        "key_env": [],
        "model": "",
        "voice": "default",
        "desc": "本地 eSpeak TTS。需安装 espeak（apt install espeak）。中文支持有限",
    },
    "custom": {
        "name": "自定义 (Custom)",
        "url": "",
        "key_env": ["TTS_API_KEY"],
        "model": "",
        "voice": "",
        "desc": "自定义 TTS API。设置 TTS_API_URL 和 TTS_API_KEY, TTS_MODEL, TTS_VOICE",
    },
}

STT_PROVIDERS: dict[str, dict] = {
    "openai": {
        "name": "OpenAI Whisper",
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "key_env": ["OPENAI_API_KEY"],
        "model": "whisper-1",
        "desc": "OpenAI Whisper 语音识别。支持多语言，准确率高",
    },
    "siliconflow": {
        "name": "SiliconFlow Whisper",
        "url": "https://api.siliconflow.cn/v1/audio/transcriptions",
        "key_env": ["SILICONFLOW_API_KEY", "STT_API_KEY"],
        "model": "whisper-1",
        "desc": "SiliconFlow 上的 Whisper 语音识别",
    },
    "local": {
        "name": "本地 Whisper",
        "url": "http://localhost:8080/v1/audio/transcriptions",
        "key_env": [],
        "model": "whisper-1",
        "desc": "本地 llama-server 支持的 Whisper 转录",
    },
    "custom": {
        "name": "自定义 (Custom)",
        "url": "",
        "key_env": ["STT_API_KEY"],
        "model": "",
        "desc": "自定义 STT API。设置 STT_API_URL 和 STT_API_KEY",
    },
}

# ── 运行时配置 ─────────────────────────────────────────────


class MultimediaConfig:
    """多媒体服务运行时配置。

    从环境变量加载当前活跃的 provider，支持运行时切换。

    用法：
        cfg = MultimediaConfig()
        # image_gen config
        provider, config = cfg.get_image_gen_config()
        # 测试连接
        ok, msg = cfg.test_image_gen()
    """

    # 环境变量名 → 功能
    ENV_MAP = {
        "IMAGE_GEN_API_URL": "image_gen",
        "IMAGE_GEN_API_KEY": "image_gen",
        "IMAGE_GEN_PROVIDER": "image_gen",
        "VISION_API_URL": "vision",
        "VISION_API_KEY": "vision",
        "VISION_PROVIDER": "vision",
        "TTS_API_URL": "tts",
        "TTS_API_KEY": "tts",
        "TTS_PROVIDER": "tts",
        "STT_API_URL": "stt",
        "STT_API_KEY": "stt",
        "STT_PROVIDER": "stt",
        "SILICONFLOW_API_KEY": "shared",  # 可跨功能复用
    }

    # 默认 provider（当环境变量未配置时）
    DEFAULT_PROVIDERS = {
        "image_gen": "siliconflow",
        "vision": "openai",
        "tts": "openai",
        "stt": "openai",
    }

    def __init__(self):
        # 懒加载环境变量
        self._loaded = False
        self._config: dict[str, Any] = {}

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._config = {
            "image_gen": {
                "provider": os.environ.get("IMAGE_GEN_PROVIDER",
                                            os.environ.get("IMAGE_GEN_API_URL", "") and "custom" or ""),
                "api_url": os.environ.get("IMAGE_GEN_API_URL", ""),
                "api_key": os.environ.get("IMAGE_GEN_API_KEY",
                                           os.environ.get("SILICONFLOW_API_KEY", "")),
                "model": os.environ.get("IMAGE_GEN_MODEL", ""),
            },
            "vision": {
                "provider": os.environ.get("VISION_PROVIDER",
                                            os.environ.get("VISION_API_URL", "") and "custom" or ""),
                "api_url": os.environ.get("VISION_API_URL", ""),
                "api_key": os.environ.get("VISION_API_KEY",
                                           os.environ.get("SILICONFLOW_API_KEY", "")),
                "model": os.environ.get("VISION_MODEL", ""),
            },
            "tts": {
                "provider": os.environ.get("TTS_PROVIDER",
                                            os.environ.get("TTS_API_URL", "") and "custom" or ""),
                "api_url": os.environ.get("TTS_API_URL", ""),
                "api_key": os.environ.get("TTS_API_KEY",
                                           os.environ.get("OPENAI_API_KEY", "")),
                "model": os.environ.get("TTS_MODEL", ""),
                "voice": os.environ.get("TTS_VOICE", ""),
            },
            "stt": {
                "provider": os.environ.get("STT_PROVIDER",
                                            os.environ.get("STT_API_URL", "") and "custom" or ""),
                "api_url": os.environ.get("STT_API_URL", ""),
                "api_key": os.environ.get("STT_API_KEY",
                                           os.environ.get("OPENAI_API_KEY", "")),
            },
        }
        self._loaded = True

    # ── 获取配置 ──

    def get_image_gen_config(self) -> tuple[Optional[str], dict]:
        """获取图像生成配置。返回 (provider_name, config_dict)。"""
        self._ensure_loaded()
        cfg = self._config["image_gen"]
        return self._resolve("image_gen", IMAGE_GEN_PROVIDERS, cfg)

    def get_vision_config(self) -> tuple[Optional[str], dict]:
        """获取视觉分析配置。"""
        self._ensure_loaded()
        cfg = self._config["vision"]
        provider, resolved = self._resolve("vision", VISION_PROVIDERS, cfg)
        if cfg["model"]:
            resolved["model"] = cfg["model"]
        return provider, resolved

    def get_tts_config(self) -> tuple[Optional[str], dict]:
        """获取 TTS 配置。"""
        self._ensure_loaded()
        cfg = self._config["tts"]
        provider, resolved = self._resolve("tts", TTS_PROVIDERS, cfg)
        if cfg["model"]:
            resolved["model"] = cfg["model"]
        return provider, resolved

    def get_stt_config(self) -> tuple[Optional[str], dict]:
        """获取 STT 配置。"""
        self._ensure_loaded()
        cfg = self._config["stt"]
        return self._resolve("stt", STT_PROVIDERS, cfg)

    def _resolve(self, category: str, providers: dict, cfg: dict) -> tuple[Optional[str], dict]:
        """解析当前配置：provider 名 → 完整配置 dict。"""
        provider = cfg.get("provider", "")

        # 优先使用直接配置的 URL
        if cfg.get("api_url"):
            # 从环境变量读取模型/音色等自定义参数
            model = cfg.get("model") or os.environ.get(f"{category.upper()}_MODEL", "")
            voice = cfg.get("voice") or os.environ.get(f"{category.upper()}_VOICE", "")
            return "custom", {
                "url": cfg["api_url"],
                "api_key": cfg.get("api_key", ""),
                "model": model,
                "voice": voice,
            }

        # 用 provider 名从模板中查找
        if provider and provider in providers:
            p = providers[provider]
            # 从环境变量获取 API Key
            api_key = cfg.get("api_key", "")
            if not api_key:
                for env_name in p.get("key_env", []):
                    api_key = os.environ.get(env_name, "")
                    if api_key:
                        break
            # 自定义 provider 优先读取环境变量中的模型/音色
            model = cfg.get("model") or p.get("model", "")
            if provider == "custom":
                model = model or os.environ.get(f"{category.upper()}_MODEL", "")
                voice = os.environ.get(f"{category.upper()}_VOICE", p.get("voice", ""))
            else:
                voice = p.get("voice", "")
            # 如果 custom provider 没有 URL，尝试从环境变量读取
            url = p["url"]
            if provider == "custom" and not url:
                url = os.environ.get(f"{category.upper()}_API_URL", "")
            return provider, {
                "url": url,
                "api_key": api_key,
                "model": model,
                "voice": voice,
            }

        # 自动探测：遍历 provider 模板，找第一个可用的
        for name, p in providers.items():
            api_key = cfg.get("api_key", "")
            if not api_key:
                for env_name in p.get("key_env", []):
                    api_key = os.environ.get(env_name, "")
                    if api_key:
                        break
            url = p["url"]
            # custom 特殊处理：尝试从环境变量读取 URL
            if name == "custom" and not url:
                url = os.environ.get(f"{category.upper()}_API_URL", "")
            if url or api_key:
                return name, {
                    "url": url,
                    "api_key": api_key,
                    "model": cfg.get("model") or p.get("model", ""),
                    "voice": p.get("voice", ""),
                }

        return None, {}

    # ── 检查是否已配置 ──

    def is_configured(self, category: str) -> bool:
        """检查某个类别是否已配置。"""
        self._ensure_loaded()
        _, resolved = self._resolve(
            category,
            {"image_gen": IMAGE_GEN_PROVIDERS, "vision": VISION_PROVIDERS,
             "tts": TTS_PROVIDERS, "stt": STT_PROVIDERS}.get(category, {}),
            self._config.get(category, {}),
        )
        if resolved.get("api_key"):
            return True
        if resolved.get("url"):
            return True
        return False

    def get_status(self) -> dict[str, bool]:
        """获取所有功能的配置状态。"""
        return {
            "image_gen": self.is_configured("image_gen"),
            "vision": self.is_configured("vision"),
            "tts": self.is_configured("tts"),
            "stt": self.is_configured("stt"),
        }

    # ── 连接测试 ──

    def test_image_gen(self) -> tuple[bool, str]:
        """测试图像生成连接。"""
        provider, cfg = self.get_image_gen_config()
        if not cfg.get("url"):
            return False, "未配置 IMAGE_GEN_API_URL"
        return self._test_url(cfg["url"], cfg.get("api_key", ""))

    def test_vision(self) -> tuple[bool, str]:
        """测试视觉分析连接。"""
        provider, cfg = self.get_vision_config()
        if not cfg.get("url"):
            return False, "未配置 VISION_API_URL"
        return self._test_url(cfg["url"], cfg.get("api_key", ""))

    def test_tts(self) -> tuple[bool, str]:
        """测试 TTS 连接。"""
        provider, cfg = self.get_tts_config()
        if cfg.get("url"):
            return self._test_url(cfg["url"], cfg.get("api_key", ""))
        # 无 URL 的 provider（如本地 espeak）跳过网络测试
        if provider == "espeak":
            import subprocess
            try:
                r = subprocess.run(["espeak", "--version"], capture_output=True, timeout=5)
                return r.returncode == 0, "espeak 已安装" if r.returncode == 0 else "espeak 未安装"
            except FileNotFoundError:
                return False, "espeak 未安装"
        return False, "未配置 TTS_API_URL"

    def test_stt(self) -> tuple[bool, str]:
        """测试 STT 连接。"""
        provider, cfg = self.get_stt_config()
        if not cfg.get("url"):
            return False, "未配置 STT_API_URL"
        return self._test_url(cfg["url"], cfg.get("api_key", ""))

    def _test_url(self, url: str, api_key: str = "") -> tuple[bool, str]:
        """测试 URL 连通性。"""
        try:
            req = urllib.request.Request(url, method="GET")
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=5):
                return True, "连接成功"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "API Key 无效 (HTTP 401)"
            elif e.code == 404:
                return False, "端点不存在 (HTTP 404)"
            else:
                return True, f"服务器响应 ({e.code})"  # 能连通就算
        except urllib.error.URLError as e:
            return False, f"连接失败: {e.reason}"
        except Exception as e:
            return False, f"连接异常: {e}"

    # ── provider 列表（供 setup_wizard 展示） ──

    @staticmethod
    def list_image_gen_providers() -> dict[str, str]:
        return {k: v["desc"] for k, v in IMAGE_GEN_PROVIDERS.items()}

    @staticmethod
    def list_vision_providers() -> dict[str, str]:
        return {k: v["desc"] for k, v in VISION_PROVIDERS.items()}

    @staticmethod
    def list_tts_providers() -> dict[str, str]:
        return {k: v["desc"] for k, v in TTS_PROVIDERS.items()}

    @staticmethod
    def list_stt_providers() -> dict[str, str]:
        return {k: v["desc"] for k, v in STT_PROVIDERS.items()}
