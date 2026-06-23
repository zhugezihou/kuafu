#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸父 (Kuafu) — 交互式配置向导

用法:
    python setup_wizard.py                    # 完整配置
    python setup_wizard.py reconfigure        # 重新配置（逐步选择）
    python setup_wizard.py reconfigure --step 3  # 从指定步开始
    python setup_wizard.py repair             # 检查修复
    python setup_wizard.py uninstall          # 卸载

特色:
    - 每一步都支持 上一步/下一步/跳过/重填
    - 后续可用 reconfigure 模式补配置
    - 非 rich 环境也正常工作
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DOT_ENV = ROOT_DIR / ".env"
CONFIG_CACHE = ROOT_DIR / ".setup_cache.json"

# ─── 颜色 / UI ────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

C = lambda t, c=None: t if RICH_AVAILABLE else (
    f"\033[92m{t}\033[0m" if c == "green" else
    f"\033[93m{t}\033[0m" if c == "yellow" else
    f"\033[96m{t}\033[0m" if c == "cyan" else
    f"\033[91m{t}\033[0m" if c == "red" else
    f"\033[1m{t}\033[0m" if c == "bold" else t
)


def print_step(num, text):
    if RICH_AVAILABLE:
        console.rule(f"[bold cyan]{'→ ' * (num > 0)}{num}. {text}")
    else:
        print(C(f"\n{'='*50}\n[{num}] {text}\n{'='*50}", "cyan"))


def print_info(text):
    (console.print(f"  [cyan]→[/cyan] {text}") if RICH_AVAILABLE else print(f"  → {text}"))

def print_ok(text):
    (console.print(f"  [green]✓[/green] {text}") if RICH_AVAILABLE else print(C(f"  ✓ {text}", "green")))

def print_warn(text):
    (console.print(f"  [yellow]⚠[/yellow] {text}") if RICH_AVAILABLE else print(C(f"  ⚠ {text}", "yellow")))

def print_err(text):
    (console.print(f"  [red]✗[/red] {text}") if RICH_AVAILABLE else print(C(f"  ✗ {text}", "red")))


# ─── 导航框架 ──────────────────────────────────────────────────────────────────
def ask_yesno(prompt_text: str, default: bool = True) -> bool:
    """统一的 yes/no 询问。"""
    if RICH_AVAILABLE:
        return Confirm.ask(f"\n  {prompt_text}", default=default)
    hint = "Y/n" if default else "y/N"
    resp = input(f"\n  {prompt_text} ({hint}): ").strip().lower()
    if default:
        return resp != "n"
    return resp == "y"


def wait_for_input():
    """等待用户按回车继续。"""
    if RICH_AVAILABLE:
        Prompt.ask("\n  [dim]按回车继续[/dim]", default="")
    else:
        input("\n  按回车继续: ")


# ─── 配置状态 ──────────────────────────────────────────────────────────────────
class Config:
    """保存所有配置项，每个步骤读写这个对象。"""
    def __init__(self):
        self.backend = ""
        self.provider_id = ""
        self.api_key = ""
        self.base_url = ""
        self.feishu = {}
        self.wechat = {}
        self.multimedia = {}
        self.tavily_api_key = ""
        self.github_token = ""
        self.gateway_port = "8765"
        self.disable_approval = False
        self.advanced = {}

    def to_env(self) -> dict:
        """转成 .env 格式的键值对。"""
        env = {}

        # LLM
        env["KUAFU_LLM_BACKEND"] = self.backend
        if self.backend == "local":
            env["KUAFU_PROVIDERS"] = "qwen"
            env["QWEN_BASE_URL"] = self.base_url or "http://localhost:8080"
        elif self.provider_id == "deepseek":
            if self.api_key:
                env["DEEPSEEK_API_KEY"] = self.api_key
                env["KUAFU_API_KEY"] = self.api_key
            env["DEEPSEEK_BASE_URL"] = self.base_url or "https://api.deepseek.com"
            env["KUAFU_PROVIDERS"] = "deepseek,qwen"
        elif self.provider_id == "openrouter":
            if self.api_key:
                env["OPENROUTER_API_KEY"] = self.api_key
            env["OPENROUTER_BASE_URL"] = self.base_url or "https://openrouter.ai/api/v1"
            env["KUAFU_PROVIDERS"] = "openrouter,qwen"
        elif self.provider_id == "claude":
            if self.api_key:
                env["ANTHROPIC_API_KEY"] = self.api_key
                env["CLAUDE_API_KEY"] = self.api_key
            env["CLAUDE_BASE_URL"] = self.base_url or "https://api.anthropic.com"
            env["KUAFU_PROVIDERS"] = "claude,qwen"
        elif self.provider_id == "custom":
            if self.api_key:
                env["CUSTOM_API_KEY"] = self.api_key
            if self.base_url:
                env["CUSTOM_BASE_URL"] = self.base_url
            env["KUAFU_PROVIDERS"] = "custom,qwen"

        # 通道
        for k, v in self.feishu.items():
            env[k] = v
        for k, v in self.wechat.items():
            env[k] = v

        # 多媒体
        for category, cfg in self.multimedia.items():
            if cfg.get("provider"):
                env[f"{category.upper()}_PROVIDER"] = cfg["provider"]
            if cfg.get("api_url"):
                env[f"{category.upper()}_API_URL"] = cfg["api_url"]
            if cfg.get("api_key"):
                env[f"{category.upper()}_API_KEY"] = cfg["api_key"]

        if self.tavily_api_key:
            env["TAVILY_API_KEY"] = self.tavily_api_key
        if self.github_token:
            env["GITHUB_TOKEN"] = self.github_token
        if self.gateway_port:
            env["KUAFU_GATEWAY_PORT"] = self.gateway_port
        if self.disable_approval:
            env["KUAFU_DISABLE_APPROVAL"] = "1"

        # 高级配置
        for k, v in self.advanced.items():
            env[k] = v

        return env

    def load_from_env(self):
        """从当前 .env 加载已有配置。"""
        if not DOT_ENV.exists():
            return
        env_vars = {}
        with open(DOT_ENV, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()

        if env_vars.get("KUAFU_LLM_BACKEND") == "local":
            self.backend = "local"
            self.provider_id = "qwen"
        else:
            self.backend = "cloud"
            providers = env_vars.get("KUAFU_PROVIDERS", "")
            if "deepseek" in providers:
                self.provider_id = "deepseek"
                self.api_key = env_vars.get("DEEPSEEK_API_KEY", env_vars.get("KUAFU_API_KEY", ""))
                self.base_url = env_vars.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            elif "openrouter" in providers:
                self.provider_id = "openrouter"
                self.api_key = env_vars.get("OPENROUTER_API_KEY", "")
                self.base_url = env_vars.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            elif "claude" in providers:
                self.provider_id = "claude"
                self.api_key = env_vars.get("ANTHROPIC_API_KEY", env_vars.get("CLAUDE_API_KEY", ""))
                self.base_url = env_vars.get("CLAUDE_BASE_URL", "https://api.anthropic.com")
            elif "custom" in providers:
                self.provider_id = "custom"
                self.api_key = env_vars.get("CUSTOM_API_KEY", "")
                self.base_url = env_vars.get("CUSTOM_BASE_URL", "")

        self.feishu = {}
        if env_vars.get("FEISHU_APP_ID"):
            self.feishu["FEISHU_APP_ID"] = env_vars["FEISHU_APP_ID"]
        if env_vars.get("FEISHU_APP_SECRET"):
            self.feishu["FEISHU_APP_SECRET"] = env_vars["FEISHU_APP_SECRET"]
        if env_vars.get("FEISHU_CHAT_ID"):
            self.feishu["FEISHU_CHAT_ID"] = env_vars["FEISHU_CHAT_ID"]
        for k in ["FEISHU_BOT_NAME", "FEISHU_REPLY_PRIVATE", "FEISHU_ENABLE_PREVIEW"]:
            if env_vars.get(k):
                self.feishu[k] = env_vars[k]

        self.tavily_api_key = env_vars.get("TAVILY_API_KEY", "")
        self.github_token = env_vars.get("GITHUB_TOKEN", "")
        self.gateway_port = env_vars.get("KUAFU_GATEWAY_PORT", "8765")
        self.disable_approval = env_vars.get("KUAFU_DISABLE_APPROVAL") == "1"

        self.advanced = {}
        for k in ["KUAFU_SKILL_MARKET_URL", "KUAFU_SKILL_REPOS", "KUAFU_DATA_DIR",
                   "KUAFU_GATEWAY_KEY", "WECHAT_ILINK_DATA_DIR", "KUAFU_INTERACTIVE",
                   "DEEPSEEK_MODEL", "QWEN_MODEL", "KUAFU_GATEWAY_HOST"]:
            if env_vars.get(k):
                self.advanced[k] = env_vars[k]


# ─── Banner ────────────────────────────────────────────────────────────────────
def show_banner():
    banner = r"""
    _                     __
   | |                   / _|
   | | __ ___   ____ _  | |_ _   _ _ __
   | |/ _` |\ \ / / _` | |  _| | | | `__|
   | | (_| |\ V / (_| | | | | |_| | |  | |
   |_|\__,_| \_/ \__,_| |_|  \__,_|_|  |_|

   逐日不息 · 自我超越

   夸父 (Kuafu) — 配置向导
"""
    if RICH_AVAILABLE:
        console.print(Panel(banner, style="cyan"))
    else:
        print(C(banner, "cyan"))


# ─── 步骤定义 ──────────────────────────────────────────────────────────────────
# 每一步是一个函数，返回 "next" | "prev" | "skip" | "redo"

THIRD_PARTY_PROVIDERS = [
    ("OpenRouter", "openrouter", False),
    ("Anthropic Claude（OpenAI 兼容）", "claude", False),
    ("自定义（OpenAI 兼容 API）", "custom", True),
]


def nav_menu(prompt_text: str = "操作") -> str:
    """显示导航菜单，返回指令。"""
    print()
    print_info("回车=继续  |  p=上一步  |  s=跳过  |  r=重填  |  q=退出")
    if RICH_AVAILABLE:
        cmd = Prompt.ask(f"  {prompt_text}", default="")
    else:
        cmd = input(f"  {prompt_text} [回车继续/p上一步/s跳过/r重填/q退出]: ").strip().lower()
    return cmd


def step_backend(cfg: Config) -> str:
    """Step 1: 选择 LLM 后端。"""
    print_step(1, "LLM 后端")

    provider_info = {
        "cloud": "云端模式（推荐）— 需要 API Key，免 GPU，速度快",
        "local": "本地模式 — 需要 NVIDIA GPU 8GB+，运行 Qwen 模型",
    }
    for k, v in provider_info.items():
        print_info(f"  {k}: {v}")
    print()

    # 选择后端类型
    default_backend = cfg.backend or "cloud"
    if RICH_AVAILABLE:
        choice = Prompt.ask("  选择后端", choices=["cloud", "local"], default=default_backend)
    else:
        choice = input(f"  选择后端 [cloud/local] (默认 {default_backend}): ").strip().lower() or default_backend
    cfg.backend = choice

    if choice == "cloud":
        print_info("")
        print_info("选择云端模型提供商：")
        print_info("  1. DeepSeek Chat（默认）— 性价比高，中文优秀")
        for i, (name, pid, _) in enumerate(THIRD_PARTY_PROVIDERS, 2):
            print_info(f"  {i}. {name}")
        print()

        default_provider = {"deepseek": "1", "openrouter": "2", "claude": "3", "custom": "4"}.get(cfg.provider_id, "1")
        if RICH_AVAILABLE:
            c = Prompt.ask("  选择", choices=[str(i) for i in range(1, 5)], default=default_provider)
        else:
            c = input("  选择 (1-4, 默认1): ").strip() or "1"
        idx = int(c) - 1
        if idx == 0:
            cfg.provider_id = "deepseek"
        else:
            cfg.provider_id = THIRD_PARTY_PROVIDERS[idx - 1][1]
        print_ok(f"已选择: {cfg.provider_id}")
    else:
        cfg.provider_id = "qwen"
        print_info("本地模式使用 llama-server + Qwen 模型")

    cmd = nav_menu("确认后端配置")
    if cmd == "s": return "skip"  # noqa: E701
    return cmd


def step_apikey(cfg: Config) -> str:
    """Step 2: 配置 API Key。"""
    print_step(2, "API Key")
    if cfg.backend == "local":
        print_info("本地模式不需要 API Key")
        wait_for_input()
        return "next"

    provider_info = {
        "deepseek": ("DeepSeek Chat", "https://api.deepseek.com", "https://platform.deepseek.com/", "DEEPSEEK_API_KEY"),
        "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys", "OPENROUTER_API_KEY"),
        "claude": ("Anthropic Claude", "https://api.anthropic.com", "https://console.anthropic.com/", "ANTHROPIC_API_KEY"),
        "custom": ("自定义", "", "", "CUSTOM_API_KEY"),
    }

    name, default_url, register_url, env_key = provider_info.get(cfg.provider_id, provider_info["deepseek"])

    print_info(f"夸父使用 {name} API")
    if register_url:
        print_info(f"注册: {register_url}")
    print()

    # 已有 Key？
    existing_key = cfg.api_key
    if existing_key and len(existing_key) > 3:
        masked = existing_key[:4] + "****" + existing_key[-4:]
        print_info(f"当前 Key: {masked}")
        if not ask_yesno("保留当前 Key?", default=True):
            existing_key = ""
    cfg.api_key = existing_key

    if not cfg.api_key:
        if RICH_AVAILABLE:
            cfg.api_key = Prompt.ask(f"  请输入 {name} API Key", password=True)
        else:
            print(f"  请输入 {name} API Key: ", end="")
            cfg.api_key = input().strip()

    # Base URL
    if cfg.provider_id == "custom":
        if RICH_AVAILABLE:
            cfg.base_url = Prompt.ask("  API Base URL", default=default_url or "（必填）")
        else:
            inp = input(f"  API Base URL [{default_url}]: ").strip()
            cfg.base_url = inp or default_url
    elif cfg.provider_id == "deepseek":
        cfg.base_url = default_url
    elif cfg.provider_id in ("openrouter", "claude"):
        cfg.base_url = default_url
    else:
        cfg.base_url = default_url

    if cfg.api_key:
        print_ok("API Key 已配置")
    else:
        print_warn("API Key 为空，后续可在 .env 中补填")

    cmd = nav_menu("确认 API Key")
    return cmd


def step_feishu(cfg: Config) -> str:
    """Step 3: 飞书通道。"""
    print_step(3, "飞书通道（可选）")
    print_info("让夸父通过飞书 Bot 收发消息。")
    print_info("需在飞书开放平台创建应用: https://open.feishu.cn/app")
    print()

    has_feishu = bool(cfg.feishu.get("FEISHU_APP_ID"))
    if has_feishu:
        print_info(f"当前已配置: App ID {cfg.feishu['FEISHU_APP_ID'][:15]}...")
        if not ask_yesno("重新配置飞书?", default=False):
            return "next"

    if not ask_yesno("配置飞书通道?", default=has_feishu):
        cfg.feishu = {}
        print_info("跳过飞书通道")
        return "next"

    feishu = {}

    # App ID
    existing_id = cfg.feishu.get("FEISHU_APP_ID", "")
    if existing_id:
        print_info(f"已有 App ID: {existing_id[:15]}...")
        if not ask_yesno("保留?", default=True):
            existing_id = ""
    if not existing_id:
        if RICH_AVAILABLE:
            existing_id = Prompt.ask("  飞书 App ID (cli_xxx)")
        else:
            existing_id = input("  飞书 App ID (cli_xxx): ").strip()
    feishu["FEISHU_APP_ID"] = existing_id

    # App Secret
    existing_secret = cfg.feishu.get("FEISHU_APP_SECRET", "")
    if existing_secret and len(existing_secret) > 3:
        print_info("已有 App Secret")
        if not ask_yesno("保留?", default=True):
            existing_secret = ""
    if not existing_secret:
        if RICH_AVAILABLE:
            existing_secret = Prompt.ask("  飞书 App Secret", password=True)
        else:
            existing_secret = input("  飞书 App Secret: ").strip()
    feishu["FEISHU_APP_SECRET"] = existing_secret

    # Chat ID（可选）
    existing_chat = cfg.feishu.get("FEISHU_CHAT_ID", "")
    if RICH_AVAILABLE:
        feishu["FEISHU_CHAT_ID"] = Prompt.ask("  默认群 Chat ID", default=existing_chat or "oc_xxx")
    else:
        inp = input(f"  默认群 Chat ID (oc_xxx) [{existing_chat}]: ").strip()
        feishu["FEISHU_CHAT_ID"] = inp or existing_chat or ""

    # Bot 名称
    existing_name = cfg.feishu.get("FEISHU_BOT_NAME", "夸父")
    if RICH_AVAILABLE:
        feishu["FEISHU_BOT_NAME"] = Prompt.ask("  Bot 显示名称", default=existing_name)
    else:
        inp = input(f"  Bot 显示名称 [{existing_name}]: ").strip()
        feishu["FEISHU_BOT_NAME"] = inp or existing_name

    # 高级选项
    if ask_yesno("配置飞书高级选项?（私聊回复、预览等）", default=False):
        feishu["FEISHU_REPLY_PRIVATE"] = "true" if ask_yesno("允许私聊回复?", default=True) else "false"
        feishu["FEISHU_ENABLE_PREVIEW"] = "true" if ask_yesno("启用消息预览?", default=True) else "false"

    cfg.feishu = feishu
    if feishu.get("FEISHU_APP_ID"):
        print_ok("飞书通道配置完成")

    cmd = nav_menu("确认飞书配置")
    return cmd


def step_wechat(cfg: Config) -> str:
    """Step 4: 微信通道。"""
    print_step(4, "微信通道（可选）")
    print_info("夸父通过腾讯官方 iLink 协议连接个人微信。")
    print_info("扫码即可登录，无需手动填写 Token。")
    print()

    if ask_yesno("启用微信通道?", default=bool(cfg.wechat.get("WECHAT_ILINK_DATA_DIR"))):
        # 可选：数据目录
        existing_dir = cfg.wechat.get("WECHAT_ILINK_DATA_DIR", "")
        if RICH_AVAILABLE:
            data_dir = Prompt.ask("  iLink 数据目录（可选，留空用默认）", default=existing_dir)
        else:
            inp = input(f"  iLink 数据目录（可选）[{existing_dir}]: ").strip()
            data_dir = inp or existing_dir
        if data_dir:
            cfg.wechat["WECHAT_ILINK_DATA_DIR"] = data_dir
        print_ok("微信通道已启用（Gateway 启动时扫码登录）")
    else:
        cfg.wechat = {}
        print_info("跳过微信通道")

    cmd = nav_menu("确认微信配置")
    return cmd


def step_multimedia(cfg: Config) -> str:
    """Step 5: 多媒体服务。"""
    print_step(5, "多媒体服务（可选）")
    print_info("图像生成、图像理解、语音合成、语音识别。")
    print_info("可以跳过，工具仍可用但会提示设置环境变量。")
    print()

    if not ask_yesno("配置多媒体服务?", default=False):
        return "next"

    try:
        from core.multimedia_config import MultimediaConfig
    except ImportError:
        print_warn("multimedia_config 模块不可用")
        return "next"

    result = cfg.multimedia or {}
    categories = [
        ("image_gen", "图像生成", MultimediaConfig.list_image_gen_providers()),
        ("vision", "图像理解", MultimediaConfig.list_vision_providers()),
        ("tts", "语音合成", MultimediaConfig.list_tts_providers()),
        ("stt", "语音识别", MultimediaConfig.list_stt_providers()),
    ]

    for category, cn_name, providers in categories:
        print()
        print_info(f"── {cn_name} ──")
        if not ask_yesno(f"配置{cn_name}?", default=False):
            continue

        cfg_item = result.get(category, {})
        provider_keys = list(providers.keys())
        if provider_keys:
            print_info("  服务商:")
            for i, (k, desc) in enumerate(providers.items(), 1):
                print_info(f"    {i}. {desc}")
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                choice = IntPrompt.ask("  选择", default=1)
            else:
                try:
                    choice = int(input(f"  选择 (1-{len(provider_keys)}, 默认1): ").strip() or "1")
                except ValueError:
                    choice = 1
            idx = max(0, min(choice - 1, len(provider_keys) - 1))
            cfg_item["provider"] = provider_keys[idx]
        else:
            cfg_item["provider"] = ""

        if RICH_AVAILABLE:
            cfg_item["api_url"] = Prompt.ask("  API URL（可选）", default=cfg_item.get("api_url", ""))
            cfg_item["api_key"] = Prompt.ask("  API Key（可选）", default=cfg_item.get("api_key", ""))
        else:
            inp = input("  API URL（可选）: ").strip()
            if inp: cfg_item["api_url"] = inp
            inp = input("  API Key（可选）: ").strip()
            if inp: cfg_item["api_key"] = inp

        result[category] = cfg_item

    cfg.multimedia = result
    if result:
        print_ok(f"已配置 {len(result)} 个多媒体服务")

    cmd = nav_menu("确认多媒体配置")
    return cmd


def step_tavily(cfg: Config) -> str:
    """Step 6: Tavily 搜索。"""
    print_step(6, "Tavily 搜索（可选）")
    print_info("专业的 AI 搜索引擎，配置后专家系统可用实时搜索。")
    print_info("注册: https://tavily.com/")
    print()

    if not ask_yesno("配置 Tavily Search?", default=bool(cfg.tavily_api_key)):
        print_info("跳过，搜索工具会回退到内置搜索引擎")
        return "next"

    existing = cfg.tavily_api_key
    if existing and len(existing) > 3:
        masked = existing[:4] + "****" + existing[-4:]
        print_info(f"当前 Key: {masked}")
        if ask_yesno("保留?", default=True):
            print_ok("Tavily 已保留")
            return "next"

    if RICH_AVAILABLE:
        cfg.tavily_api_key = Prompt.ask("  请输入 Tavily API Key", password=True)
    else:
        print("  请输入 Tavily API Key: ", end="")
        cfg.tavily_api_key = input().strip()

    if cfg.tavily_api_key:
        print_ok("Tavily 配置完成")
    else:
        print_info("跳过 Tavily")

    cmd = nav_menu("确认 Tavily")
    return cmd


def step_github(cfg: Config) -> str:
    """Step 7: GitHub Token。"""
    print_step(7, "GitHub 集成（可选）")
    print_info("Token 让夸父可以搜索代码、创建 Issue、管理仓库。")
    print_info("注册: https://github.com/settings/tokens（需 repo + read:org 权限）")
    print()

    if not ask_yesno("配置 GitHub Token?", default=bool(cfg.github_token)):
        print_info("跳过，代码搜索工具不可用")
        return "next"

    existing = cfg.github_token
    if existing and len(existing) > 3:
        masked = existing[:4] + "****" + existing[-4:]
        print_info(f"当前 Token: {masked}")
        if ask_yesno("保留?", default=True):
            print_ok("GitHub 已保留")
            return "next"

    if RICH_AVAILABLE:
        cfg.github_token = Prompt.ask("  请输入 GitHub Personal Access Token", password=True)
    else:
        print("  请输入 GitHub Personal Access Token: ", end="")
        cfg.github_token = input().strip()

    if cfg.github_token:
        print_ok("GitHub 配置完成")
    else:
        print_info("跳过 GitHub")

    cmd = nav_menu("确认 GitHub")
    return cmd


def step_advanced(cfg: Config) -> str:
    """Step 8: 高级配置。"""
    print_step(8, "高级配置（可选）")
    print_info("Gateway 端口、审批模式、技能市场等。")
    print("")

    advanced = cfg.advanced.copy()

    # Gateway 端口
    current_port = cfg.gateway_port
    if RICH_AVAILABLE:
        cfg.gateway_port = Prompt.ask("  Gateway 端口", default=current_port)
    else:
        inp = input(f"  Gateway 端口 [{current_port}]: ").strip()
        if inp: cfg.gateway_port = inp

    # 审批
    if ask_yesno("关闭命令审批?（不安全，仅开发环境推荐）", default=cfg.disable_approval):
        cfg.disable_approval = True
        print_warn("命令审批已关闭")
    else:
        cfg.disable_approval = False

    # 其他高级选项
    print()
    if ask_yesno("配置更多高级选项?（技能市场、数据目录、Gateway Key 等）", default=False):
        advanced_fields = [
            ("KUAFU_SKILL_MARKET_URL", "技能市场 URL", ""),
            ("KUAFU_SKILL_REPOS", "技能仓库（逗号分隔）", ""),
            ("KUAFU_DATA_DIR", "数据目录", ""),
            ("KUAFU_GATEWAY_KEY", "Gateway API Key（保护 Gateway）", ""),
            ("KUAFU_INTERACTIVE", "终端交互模式", ""),
            ("DEEPSEEK_MODEL", "DeepSeek 模型名覆盖", ""),
            ("QWEN_MODEL", "Qwen 模型文件覆盖", ""),
        ]
        for key, label, default_val in advanced_fields:
            existing_val = advanced.get(key, default_val)
            if RICH_AVAILABLE:
                val = Prompt.ask(f"  {label}", default=existing_val)
            else:
                inp = input(f"  {label} [{existing_val}]: ").strip()
                val = inp or existing_val
            if val:
                advanced[key] = val

    cfg.advanced = advanced
    print_ok("高级配置完成")

    cmd = nav_menu("确认高级配置")
    return cmd


# ─── 测试连接 ──────────────────────────────────────────────────────────────────
def step_test_connection(cfg: Config) -> str:
    """Step 9: 测试 LLM 连接。"""
    print_step(9, "测试 LLM 连接")

    if not ask_yesno("测试 LLM 连接?", default=True):
        return "next"

    try:
        from core.llm import LLMClient
        if cfg.backend == "local":
            client = LLMClient(providers=["qwen"], timeout=300)
        else:
            if not cfg.api_key:
                print_err("API Key 为空，无法测试")
                return "next"
            client = LLMClient(
                providers=[cfg.provider_id],
                api_key=cfg.api_key,
                base_url=cfg.base_url or None,
                timeout=300,
            )

        print_info("发送测试请求...")
        result = client.chat([
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "回复 OK 即可"},
        ])

        if result and result.get("content", "").strip():
            print_ok(f"连接成功！回复: {result['content'].strip()[:60]}")
        else:
            print_err("连接返回空响应")
    except Exception as e:
        print_err(f"连接失败: {str(e)}")
        if ask_yesno("重试?", default=False):
            return "redo"

    cmd = nav_menu("确认测试结果")
    return cmd


# ─── 保存 ──────────────────────────────────────────────────────────────────────
def save_config(cfg: Config):
    """将配置写入 .env。"""
    print_step(10, "保存配置")

    env = cfg.to_env()

    # 读取现有 .env（保留注释和无关行）
    existing_lines = []
    if DOT_ENV.exists():
        with open(DOT_ENV, encoding="utf-8") as f:
            existing_lines = f.read().splitlines()

    # 更新或追加
    def set_var(key, value):
        nonlocal existing_lines
        found = False
        for i, line in enumerate(existing_lines):
            if line.strip().startswith(key + "="):
                existing_lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            existing_lines.append(f"{key}={value}")

    for k, v in env.items():
        set_var(k, v)

    with open(DOT_ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(existing_lines) + "\n")

    print_ok(f"配置已保存: {DOT_ENV}")
    print_info(f"共写入 {len(env)} 个配置项")


# ─── 检查升级 ──────────────────────────────────────────────────────────────────
def check_upgrade():
    print_step(0, "版本检查")
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://pypi.org/pypi/kuafu-agent/json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            latest = data.get("info", {}).get("version", "")
        if latest:
            current = "1.1.1"
            if latest > current:
                print_warn(f"新版本: {latest} (当前: {current})")
                print_info("升级: pip install --upgrade kuafu-agent")
            else:
                print_ok(f"夸父 v{current} 已是最新")
    except Exception:
        print_info("无法检查更新（网络不可用）")


# ─── 修复 ──────────────────────────────────────────────────────────────────────
def repair():
    print_step(0, "夸父修复工具")
    print_info("检查安装完整性...\n")

    issues = 0

    # .env
    if not DOT_ENV.exists():
        print_warn(".env 不存在，运行 python setup_wizard.py 创建")
        issues += 1
    else:
        print_ok(".env 存在")

    # 目录
    for d in ["core", "experts", "tests"]:
        p = ROOT_DIR / d
        if p.exists() and p.is_dir():
            print_ok(f"  {d}/ 目录存在")
        else:
            print_err(f"  {d}/ 缺失")
            issues += 1

    # 依赖
    for dep in ["pyyaml"]:
        try:
            __import__(dep.replace("-", "_"))
            print_ok(f"  {dep} 已安装")
        except ImportError:
            print_warn(f"  {dep} 未安装")
            import subprocess
            r = subprocess.run([sys.executable, "-m", "pip", "install", dep, "--break-system-packages"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                print_ok(f"  {dep} 已安装")
            else:
                print_err(f"  {dep} 安装失败")
                issues += 1

    # 专家配置
    expert_dir = ROOT_DIR / "experts"
    if expert_dir.exists():
        yamls = list(expert_dir.glob("*.yaml"))
        if yamls:
            print_ok(f"  {len(yamls)} 个专家配置")
        else:
            print_warn("  专家目录为空")
            issues += 1

    # 核心导入
    try:
        from core.llm import LLMClient  # noqa: F401
        from core.memory import MemoryManager  # noqa: F401
        from core.session_store import SessionStore  # noqa: F401
        print_ok("  核心模块导入正常")
    except Exception as e:
        print_err(f"  核心模块导入失败: {e}")
        issues += 1

    print()
    if issues == 0:
        print_ok("夸父完好！")
    else:
        print_warn(f"发现 {issues} 个问题")
        print_info("运行: python setup_wizard.py uninstall")

    return 0


# ─── 卸载 ──────────────────────────────────────────────────────────────────────
def uninstall():
    print_step(0, "夸父卸载工具")
    print_warn("将删除所有配置、记忆数据和缓存。")
    print_warn("代码目录不会删除（手动: rm -rf kuafu）\n")

    if not ask_yesno("确认卸载?", default=False):
        print_info("已取消")
        return 1

    import shutil

    targets = []
    if DOT_ENV.exists():
        targets.append(str(DOT_ENV))

    memory_dir = ROOT_DIR / "memory"
    if memory_dir.exists():
        for pattern in ["*.db", "*.db-wal", "*.db-shm", "*.json"]:
            targets.extend(str(p) for p in memory_dir.glob(pattern))

    cron_out = ROOT_DIR / "cron" / "output"
    if cron_out.exists():
        targets.extend(str(p) for p in cron_out.iterdir())

    tool_dir = ROOT_DIR / ".config" / "kuafu" / "tool_results"
    if tool_dir.exists():
        targets.extend(str(p) for p in tool_dir.rglob("*") if p.is_file())

    map_file = ROOT_DIR / "core" / "memory" / "session_map.json"
    if map_file.exists():
        targets.append(str(map_file))

    if not targets:
        print_info("没有文件需要删除")
        return 0

    print_info(f"将删除 {len(targets)} 个文件:")
    for t in targets[:10]:
        print_info(f"  {t}")
    if len(targets) > 10:
        print_info(f"  ...及其他 {len(targets) - 10} 个")

    if not ask_yesno("确认删除?", default=False):
        print_info("已取消")
        return 1

    for t in targets:
        p = Path(t)
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        except Exception as e:
            print_err(f"  删除失败: {t} ({e})")

    print_ok("夸父配置已清除")
    return 0


# ─── 下一步 ─────────────────────────────────────────────────────────────────────
def show_next_steps(cfg: Config):
    print_step(0, "下一步")
    steps = [
        "交互模式:  bash kuafu.sh",
        "命令式:    bash kuafu.sh '你的任务'",
    ]
    if cfg.feishu or cfg.wechat:
        steps.append(f"Gateway 启动:  bash kuafu.sh gateway start --port {cfg.gateway_port}")

    if cfg.backend == "local":
        steps.insert(0, "首次运行前下载模型:  bash scripts/download_model.sh")

    for i, step in enumerate(steps, 1):
        print_info(f"{i}. {step}")

    print()
    print_info("后续可随时重新配置:")
    print_info("  python setup_wizard.py reconfigure       # 逐步选择")
    print_info("  python setup_wizard.py reconfigure -s 3  # 从第3步开始")
    print()
    print_ok("配置完成！夸父已就绪，逐日不息！")


# ─── 主流程 ────────────────────────────────────────────────────────────────────
STEPS = [
    ("LLM 后端", step_backend),
    ("API Key", step_apikey),
    ("飞书通道", step_feishu),
    ("微信通道", step_wechat),
    ("多媒体服务", step_multimedia),
    ("Tavily 搜索", step_tavily),
    ("GitHub 集成", step_github),
    ("高级配置", step_advanced),
    ("测试连接", step_test_connection),
]

STEP_NAMES = [s[0] for s in STEPS]


def run_steps(cfg: Config, start_step: int = 0, interactive: bool = True):
    """运行步骤序列，支持上一步/下一步/跳过/重填。"""
    i = start_step
    while 0 <= i < len(STEPS):
        name, func = STEPS[i]
        if interactive:
            print(f"\n  [步骤 {i+1}/{len(STEPS)}] {name}")

        result = func(cfg)

        if result == "prev":
            i -= 1
        elif result == "next" or result == "" or result is None:
            i += 1
        elif result == "skip":
            print_info(f"跳过: {name}")
            # 清除对应配置
            if name == "飞书通道":
                cfg.feishu = {}
            elif name == "微信通道":
                cfg.wechat = {}
            elif name == "多媒体服务":
                cfg.multimedia = {}
            elif name == "Tavily 搜索":
                cfg.tavily_api_key = ""
            elif name == "GitHub 集成":
                cfg.github_token = ""
            elif name == "高级配置":
                cfg.advanced = {}
            i += 1
        elif result == "redo":
            # 不移动，重做当前步
            pass
        elif result == "q":
            return i  # 退出，返回当前位置

    return i


# ─── main ──────────────────────────────────────────────────────────────────────
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = set(a for a in sys.argv[1:] if a.startswith("-"))

    show_banner()

    mode = args[0] if args else "setup"
    start_step = 0

    if mode == "uninstall":
        return uninstall()
    elif mode == "repair":
        return repair()
    elif mode == "reconfigure":
        # 检查 -s/--step 参数
        for i, a in enumerate(sys.argv[1:]):
            if a in ("-s", "--step") and i + 2 < len(sys.argv):
                try:
                    start_step = int(sys.argv[i + 2]) - 1  # 用户1-indexed
                except ValueError:
                    pass

        cfg = Config()
        cfg.load_from_env()

        print_info("重新配置模式 — 选择要修改的步骤")
        print()

        if "--step" not in " ".join(sys.argv):
            # 交互选择要重新配置哪些步
            print_info("可用步骤:")
            for idx, name in enumerate(STEP_NAMES):
                print_info(f"  {idx + 1}. {name}")
            print()
            if RICH_AVAILABLE:
                try:
                    from rich.prompt import IntPrompt
                    start_step = IntPrompt.ask("  从哪步开始?", default=1) - 1
                except Exception:
                    start_step = 0
            else:
                try:
                    start_step = int(input("  从哪步开始? (1-9, 默认1): ").strip() or "1") - 1
                except ValueError:
                    start_step = 0
            start_step = max(0, min(start_step, len(STEPS) - 1))

    else:  # setup
        cfg = Config()
        cfg.load_from_env()

    final_step = run_steps(cfg, start_step)

    # 保存
    if mode != "uninstall" and mode != "repair":
        check_upgrade()
        print()
        save_config(cfg)
        print()
        show_next_steps(cfg)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n")
        print_warn("配置已取消")
        sys.exit(1)
