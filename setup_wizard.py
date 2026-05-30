#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸父 (Kuafu) — 交互式配置向导

首次安装后运行，引导用户配置：
1. 选择 LLM 后端
2. 输入 API Key
3. 配置飞书 WebSocket 直连通道（可选）
4. 配置微信 iLink 通道（腾讯官方，零配置）
5. 测试连接
6. 保存 .env
7. 显示下一步指引

用法:
    python setup_wizard.py
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DOT_ENV = ROOT_DIR / ".env"
DOT_ENV_EXAMPLE = ROOT_DIR / ".env.example"

# ─── 颜色 ────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.markdown import Markdown
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def c(text, color=None):
    if RICH_AVAILABLE:
        return text
    if color == "green":
        return f"\033[92m{text}\033[0m"
    elif color == "yellow":
        return f"\033[93m{text}\033[0m"
    elif color == "cyan":
        return f"\033[96m{text}\033[0m"
    elif color == "red":
        return f"\033[91m{text}\033[0m"
    elif color == "bold":
        return f"\033[1m{text}\033[0m"
    return text


def print_step(num, text):
    msg = f"\n{'='*50}\n[{num}] {text}\n{'='*50}"
    if RICH_AVAILABLE:
        console.rule(f"[bold cyan]{num}. {text}")
    else:
        print(c(msg, "cyan"))


def print_info(text):
    if RICH_AVAILABLE:
        console.print(f"  [cyan]→[/cyan] {text}")
    else:
        print(f"  → {text}")


def print_ok(text):
    if RICH_AVAILABLE:
        console.print(f"  [green]✓[/green] {text}")
    else:
        print(c(f"  ✓ {text}", "green"))


def print_warn(text):
    if RICH_AVAILABLE:
        console.print(f"  [yellow]⚠[/yellow] {text}")
    else:
        print(c(f"  ⚠ {text}", "yellow"))


def print_err(text):
    if RICH_AVAILABLE:
        console.print(f"  [red]✗[/red] {text}")
    else:
        print(c(f"  ✗ {text}", "red"))


# ─── Banner ───────────────────────────────────────────────────────────────────
def show_banner():
    banner = """
    _                     __
   | |                   / _|
   | | __ ___   ____ _  | |_ _   _ _ __
   | |/ _` |\\ \\ / / _` | |  _| | | | `__|
   | | (_| |\\ V / (_| | | | | |_| | |  | |
   |_|\\__,_| \\_/ \\__,_| |_|  \\__,_|_|  |_|

   逐日不息 · 自我超越

  夸父 (Kuafu) — 配置向导
"""
    if RICH_AVAILABLE:
        console.print(Panel(banner, style="cyan"))
    else:
        print(c(banner, "cyan"))


# ─── LLM 配置 ────────────────────────────────────────────────────────────────
def ask_backend() -> str:
    print_step(1, "选择 LLM 后端")
    print_info("夸父支持两种运行模式：")
    print_info("  cloud  — 云端模式（DeepSeek API），需要 API Key，免 GPU")
    print_info("  local  — 本地模式（Qwen3.5-9B），需要 NVIDIA GPU 8GB+")

    if RICH_AVAILABLE:
        backend = Prompt.ask(
            "\n  选择后端",
            choices=["cloud", "local"],
            default="cloud",
        )
    else:
        print("\n  输入后端类型 [cloud/local] (默认: cloud): ", end="")
        backend = input().strip().lower() or "cloud"

    if backend == "local":
        print_info("本地模式需要安装 llama.cpp 并下载模型文件")
        print_info("请参考: https://github.com/zhugezihou/kuafu?tab=readme-ov-file#本地部署")

    return backend


def ask_api_key(backend: str) -> str:
    print_step(2, "配置 API Key")
    if backend == "local":
        print_info("本地模式下 API Key 不需要（llama-server 不验证 token）")
        return ""

    print_info("夸父使用 DeepSeek Chat API（兼容 OpenAI 格式）")
    print_info("注册: https://platform.deepseek.com/")

    existing = ""
    if DOT_ENV.exists():
        with open(DOT_ENV, encoding="utf-8") as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("KUAFFU_API_KEY="):
                    existing = line.split("=", 1)[1].strip()
                    break

    if existing and existing != "***" and len(existing) > 3:
        masked = existing[:4] + "****" + existing[-4:]
        if RICH_AVAILABLE:
            use_existing = Confirm.ask(f"\n  检测到已有 Key ({masked})，继续使用?", default=True)
        else:
            print(f"\n  检测到已有 Key: {masked}")
            resp = input("  继续使用? (Y/n): ").strip().lower()
            use_existing = resp != "n"
        if use_existing:
            return existing

    if RICH_AVAILABLE:
        api_key = Prompt.ask("\n  请输入 DeepSeek API Key", password=True)
    else:
        print("\n  请输入 DeepSeek API Key: ", end="")
        api_key = input().strip()

    return api_key


# ─── 飞书通道配置 ────────────────────────────────────────────────────────────
def ask_feishu() -> dict:
    """配置飞书 WebSocket 直连通道（可选）。"""
    print_step(3, "飞书 WebSocket 直连通道（可选）")
    print_info("夸父支持通过飞书 Bot 收发消息")
    print_info("需要在飞书开放平台创建应用: https://open.feishu.cn/app")
    print_info("配置后 Gateway 启动时会自动建立 WebSocket 连接，无需轮询")
    print()

    if RICH_AVAILABLE:
        enable = Confirm.ask("  是否配置飞书通道?", default=False)
    else:
        resp = input("  是否配置飞书通道? (y/N): ").strip().lower()
        enable = resp == "y"

    if not enable:
        print_info("跳过飞书通道，后续可在 .env 中手动配置")
        return {}

    config = {}

    # App ID
    existing_id = ""
    if DOT_ENV.exists():
        with open(DOT_ENV) as f:
            for line in f:
                if line.startswith("FEISHU_APP_ID="):
                    existing_id = line.split("=", 1)[1].strip()
                    break
    if existing_id:
        print_info(f"检测到已有 App ID: {existing_id[:15]}...")
        use = input("  使用已有? (Y/n): ").strip().lower() != "n"
        if use:
            config["FEISHU_APP_ID"] = existing_id
        else:
            config["FEISHU_APP_ID"] = input("  飞书 App ID (cli_xxx): ").strip()
    else:
        config["FEISHU_APP_ID"] = input("  飞书 App ID (cli_xxx): ").strip()

    # App Secret
    existing_secret = ""
    if DOT_ENV.exists():
        with open(DOT_ENV) as f:
            for line in f:
                if line.startswith("FEISHU_APP_SECRET="):
                    existing_secret = line.split("=", 1)[1].strip()
                    break
    if existing_secret and len(existing_secret) > 3:
        if input("  检测到已有 Secret，使用? (Y/n): ").strip().lower() != "n":
            config["FEISHU_APP_SECRET"] = existing_secret
        else:
            config["FEISHU_APP_SECRET"] = input("  飞书 App Secret: ").strip()
    else:
        config["FEISHU_APP_SECRET"] = input("  飞书 App Secret: ").strip()

    # 注意：WS 模式不需要 chat_id，但发送消息时需要指定目标
    chat_id = input("  默认发送群 Chat ID (oc_xxx，可选，回车跳过): ").strip()
    if chat_id:
        config["FEISHU_CHAT_ID"] = chat_id

    print_ok("飞书通道配置完成")
    return config


# ─── 微信 iLink 通道配置（腾讯官方） ──────────────────────────────────────
def ask_wechat() -> dict:
    """配置微信 iLink 通道（可选，零配置）。"""
    print_step(4, "个人微信 iLink 通道（腾讯官方）")
    print_info("夸父通过腾讯官方 iLink 协议连接个人微信")
    print_info("无需任何 Token 或 API Key，扫码即可登录")
    print_info("首次启动 Gateway 时自动打印二维码，微信扫码确认")
    print()

    if RICH_AVAILABLE:
        enable = Confirm.ask("  是否启用微信通道?", default=True)
    else:
        resp = input("  是否启用微信通道? (Y/n): ").strip().lower()
        enable = resp != "n"

    if not enable:
        print_info("跳过微信通道，后续可在 Gateway 启动时启用")
        return {}

    print_ok("微信通道将在 Gateway 启动时自动扫码登录")
    return {}  # iLink 不需要配置


# ─── 测试连接 ────────────────────────────────────────────────────────────────
def test_connection(backend: str, api_key: str) -> bool:
    print_step(5, "测试 LLM 连接")
    try:
        from core.llm import LLMClient
        if backend == "local":
            client = LLMClient(backend="local", timeout=300)
        else:
            if not api_key:
                print_err("API Key 不能为空")
                return False
            client = LLMClient(backend="cloud", api_key=api_key, timeout=300)

        print_info("发送测试请求...")
        result = client.chat([
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "回复 OK 即可"},
        ])

        if result and result.get("content", "").strip():
            print_ok(f"连接成功！回复: {result['content'].strip()[:60]}")
            return True
        else:
            print_err("连接返回空响应")
            return False
    except Exception as e:
        print_err(f"连接失败: {str(e)}")
        return False


# ─── 保存配置 ────────────────────────────────────────────────────────────────
def save_config(backend: str, api_key: str,
                feishu: dict, wechat: dict):
    print_step(6, "保存配置")

    config_lines = []
    if DOT_ENV.exists():
        with open(DOT_ENV, encoding="utf-8") as f:
            config_lines = f.read().splitlines()

    def set_var(key, value):
        nonlocal config_lines
        found = False
        for i, line in enumerate(config_lines):
            if line.strip().startswith(key + "="):
                config_lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            config_lines.append(f"{key}={value}")

    # LLM 配置
    if api_key:
        set_var("DEEPSEEK_API_KEY", api_key)
        set_var("KUAFFU_API_KEY", api_key)
    set_var("KUAFFU_BACKEND", backend)
    if backend == "cloud":
        set_var("KUAFFU_BASE_URL", "https://api.deepseek.com")
        set_var("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    # 飞书通道配置
    for k, v in feishu.items():
        set_var(k, v)

    # 微信通道配置
    for k, v in wechat.items():
        set_var(k, v)

    with open(DOT_ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(config_lines) + "\n")

    print_ok(f"配置文件已保存: {DOT_ENV}")


# ─── 下一步指引 ──────────────────────────────────────────────────────────────
def show_next_steps(backend: str, has_feishu: bool, has_wechat: bool):
    print_step(7, "下一步")

    steps = [
        "交互模式:  bash kuafu.sh",
        "命令式:    bash kuafu.sh '你的任务'",
    ]

    if has_feishu or has_wechat:
        steps.append("Gateway 启动:  bash kuafu.sh gateway start --port 8765")
        steps.append("Gateway 自启:  bash kuafu.sh gateway install")

    if backend == "local":
        steps.insert(0, "首次运行前请下载模型:  bash scripts/download_model.sh")

    for i, step in enumerate(steps, 1):
        print_info(f"{i}. {step}")

    if has_feishu:
        print_ok("飞书通道已配置，Gateway 启动后自动连接")

    if has_wechat:
        print_ok("微信通道已配置，Gateway 启动后自动扫码登录（腾讯 iLink 官方协议）")

    print()
    print_info("文档: https://github.com/zhugezihou/kuafu")
    print_ok("配置完成！夸父已就绪，逐日不息！")


def check_local_prerequisites():
    """检查本地模式前置条件"""
    print_step("进阶", "本地模式前置检查")

    import shutil
    has_nvidia = shutil.which("nvidia-smi") is not None
    has_llama = shutil.which("llama-server") is not None or \
                (ROOT_DIR.parent / "llama.cpp" / "build2" / "bin" / "llama-server").exists()

    if not has_nvidia:
        print_warn("未检测到 nvidia-smi，本地推理需要 NVIDIA GPU 8GB+")

    if has_llama:
        print_ok("检测到 llama-server")
    else:
        print_info("llama-server 未找到，首次运行 kuafu.sh 会自动提示安装指引")

    model_dir = ROOT_DIR.parent / "models"
    if model_dir.exists():
        models = list(model_dir.glob("*.gguf"))
        if models:
            print_ok(f"检测到模型: {models[0].name}")
        else:
            print_info("模型目录存在，但未找到 .gguf 文件")

    return has_nvidia, has_llama


# ─── 主入口 ──────────────────────────────────────────────────────────────────
def main():
    show_banner()
    print_info("欢迎！让我帮你完成夸父的初始配置。")
    print_info("全程约 3 分钟，配置项可随时修改 .env 文件。")

    try:
        # 1-2. LLM 配置
        backend = ask_backend()
        api_key = ask_api_key(backend)

        # 3. 飞书通道（可选）
        feishu_config = ask_feishu()

        # 4. 微信通道（可选）
        wechat_config = ask_wechat()

        # 5. 测试连接
        test_ok = test_connection(backend, api_key)

        if not test_ok:
            print_warn("连接测试未通过，配置仍会保存")
            if RICH_AVAILABLE:
                proceed = Confirm.ask("\n  继续保存配置?", default=True)
            else:
                proceed = input("\n  继续保存配置? (Y/n): ").strip().lower() != "n"
            if not proceed:
                print_info("已取消配置保存")
                return

        # 6. 保存
        save_config(backend, api_key, feishu_config, wechat_config)

        # 7. 本地模式额外检查
        if backend == "local":
            check_local_prerequisites()

        # 8. 显示下一步
        show_next_steps(
            backend,
            has_feishu=bool(feishu_config),
            has_wechat=bool(wechat_config),
        )

    except KeyboardInterrupt:
        print("\n")
        print_warn("配置已取消")
        sys.exit(1)


if __name__ == "__main__":
    main()
