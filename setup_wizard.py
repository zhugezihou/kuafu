#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸父 (Kuafu) — 交互式配置向导

首次安装后运行，引导用户配置：
1. 选择 LLM 后端
2. 输入 API Key
3. 配置消息通道（飞书/微信）
4. 配置多媒体服务（可选）
5. 测试连接
6. 运行测试验证
7. 保存 .env
8. 显示下一步指引

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
    banner = r"""
    _                     __
   | |                   / _|
   | | __ ___   ____ _  | |_ _   _ _ __
   | |/ _` |\\ \ / / _` | |  _| | | | `__|
   | | (_| |\ V / (_| | | | | |_| | |  | |
   |_|\__,_| \_/ \__,_| |_|  \__,_|_|  |_|

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
    print_info("")
    print_info("也可同时配置两者，通过 KUAFU_PROVIDERS 设置降级顺序：")
    print_info("  KUAFU_PROVIDERS=deepseek,qwen  # DeepSeek 为主，本地为备")
    print_info("  云端不可用时自动降级到本地，不中断工作流")

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
        print_info("请参考项目 README 中的本地部署章节")

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
    print_step(3, "飞书通道（可选）")
    print_info("夸父支持通过飞书 Bot 收发消息")
    print_info("需要在飞书开放平台创建应用: https://open.feishu.cn/app")
    print_info("配置后 Gateway 启动时会自动建立 WebSocket 连接")
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

    chat_id = input("  默认发送群 Chat ID (oc_xxx，可选，回车跳过): ").strip()
    if chat_id:
        config["FEISHU_CHAT_ID"] = chat_id

    print_ok("飞书通道配置完成")
    return config


# ─── 微信 iLink 通道配置 ─────────────────────────────────────────────────────
def ask_wechat() -> dict:
    print_step(4, "个人微信通道（可选）")
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


# ─── 多媒体服务配置 ─────────────────────────────────────────────────────────
def ask_multimedia() -> dict:
    print_step(5, "多媒体服务（可选）")
    print_info("夸父支持图像生成、图像理解、语音合成、语音识别。")
    print_info("可以跳过此步，工具仍可用但无配置时会提示设置环境变量。")
    print()

    if RICH_AVAILABLE:
        enable = Confirm.ask("  配置多媒体服务?", default=False)
    else:
        enable = input("  配置多媒体服务? (y/N): ").strip().lower() == "y"

    if not enable:
        print_info("跳过多媒体服务配置")
        return {}

    try:
        from core.multimedia_config import MultimediaConfig
    except ImportError:
        print_warn("multimedia_config 模块不可用，跳过")
        return {}

    result = {}
    categories = [
        ("image_gen", "图像生成", "Image Generation", MultimediaConfig.list_image_gen_providers()),
        ("vision", "图像理解", "Vision Analysis", MultimediaConfig.list_vision_providers()),
        ("tts", "语音合成", "Text-to-Speech", MultimediaConfig.list_tts_providers()),
        ("stt", "语音识别", "Speech-to-Text", MultimediaConfig.list_stt_providers()),
    ]

    for category, cn_name, en_name, providers in categories:
        print()
        print_info(f"── {cn_name} ({en_name}) ──")

        if RICH_AVAILABLE:
            setup = Confirm.ask(f"  配置{cn_name}?", default=False)
        else:
            setup = input(f"  配置{cn_name}? (y/N): ").strip().lower() == "y"

        if not setup:
            continue

        cfg = {}
        provider_keys = list(providers.keys())
        if provider_keys:
            print_info("  可用的服务商:")
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
            cfg["provider"] = provider_keys[idx]
        else:
            cfg["provider"] = ""

        if RICH_AVAILABLE:
            api_url = Prompt.ask(f"  API URL (可选，留空用默认)", default="")
        else:
            api_url = input(f"  API URL (可选，留空用默认): ").strip()
        if api_url:
            cfg["api_url"] = api_url

        print_info(f"  API Key（如已在环境变量中设置可跳过）")
        if RICH_AVAILABLE:
            api_key = Prompt.ask(f"  API Key (可选)", default="")
        else:
            api_key = input(f"  API Key (可选): ").strip()
        if api_key:
            cfg["api_key"] = api_key

        result[category] = cfg

    if result:
        print_ok(f"已配置 {len(result)} 个多媒体服务")
    else:
        print_info("未配置任何多媒体服务")

    return result


# ─── 测试连接 ────────────────────────────────────────────────────────────────
def test_connection(backend: str, api_key: str) -> bool:
    print_step(6, "测试 LLM 连接")
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


# ─── 运行测试验证 ────────────────────────────────────────────────────────────
def run_tests() -> bool:
    print_step(7, "运行测试验证")
    print_info("夸父自带 1900+ 测试用例，运行确认代码完整性。")
    print()

    if RICH_AVAILABLE:
        run_now = Confirm.ask("  现在运行测试? (首次建议运行)", default=True)
    else:
        resp = input("  现在运行测试? (Y/n): ").strip().lower()
        run_now = resp != "n"

    if not run_now:
        print_info("跳过测试验证。可随时运行:")
        print_info("  python -m pytest tests/ -x --tb=short -q")
        return True

    import subprocess
    print_info("运行核心单元测试...")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_bulk.py",
             "-k", "TestEvolutionEngine or TestEvolutionState or TestIdentity or TestObserver",
             "--tb=short", "-q"],
            cwd=str(ROOT_DIR),
            capture_output=True, text=True, timeout=120,
        )
        print(result.stdout)
        if result.returncode == 0:
            print_ok("核心测试全部通过")
        else:
            print_err(f"测试失败 ({result.returncode})")
            print(result.stderr[:500])
            return False
    except subprocess.TimeoutExpired:
        print_warn("测试超时（120s），跳过测试验证")
    except FileNotFoundError:
        print_warn("pytest 未安装，跳过测试验证")
        print_info("安装: pip install pytest pytest-cov")

    return True


# ─── 保存配置 ────────────────────────────────────────────────────────────────
def save_config(backend: str, api_key: str,
                feishu: dict, wechat: dict, multimedia: dict = None):
    print_step(8, "保存配置")

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
        set_var("KUAFU_PROVIDERS", "deepseek")
    elif backend == "local":
        set_var("KUAFU_PROVIDERS", "qwen")
        set_var("QWEN_BASE_URL", "http://localhost:8080")

    # 通道配置
    for k, v in feishu.items():
        set_var(k, v)
    for k, v in wechat.items():
        set_var(k, v)

    # 多媒体配置
    if multimedia:
        for category, cfg in multimedia.items():
            provider = cfg.get("provider", "")
            api_url = cfg.get("api_url", "")
            api_key_val = cfg.get("api_key", "")
            if provider:
                set_var(f"{category.upper()}_PROVIDER", provider)
            if api_url:
                set_var(f"{category.upper()}_API_URL", api_url)
            if api_key_val:
                set_var(f"{category.upper()}_API_KEY", api_key_val)

    with open(DOT_ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(config_lines) + "\n")

    print_ok(f"配置文件已保存: {DOT_ENV}")


# ─── 本地模式前置检查 ────────────────────────────────────────────────────────
def check_local_prerequisites():
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


# ─── 下一步指引 ──────────────────────────────────────────────────────────────
def show_next_steps(backend: str, has_feishu: bool, has_wechat: bool):
    print_step(9, "下一步")

    steps = [
        "交互模式:  bash kuafu.sh",
        "命令式:    bash kuafu.sh '你的任务'",
    ]

    if has_feishu or has_wechat:
        steps.append("Gateway 启动:  bash kuafu.sh gateway start --port 8765")

    if backend == "local":
        steps.insert(0, "首次运行前请下载模型:  bash scripts/download_model.sh")

    for i, step in enumerate(steps, 1):
        print_info(f"{i}. {step}")

    print()
    print_info("查看完整文档:")
    print_info("  https://github.com/zhugezihou/kuafu")
    print_info("开发者文档:")
    print_info("  cat DEVELOPER.md | less")
    print()
    print_ok("配置完成！夸父已就绪，逐日不息！")


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

        # 5. 多媒体服务（可选）
        multimedia_config = ask_multimedia()

        # 6. 测试连接
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

        # 7. 运行测试验证
        tests_ok = run_tests()
        if not tests_ok:
            print_warn("测试未全部通过，请检查代码")

        # 8. 保存
        save_config(backend, api_key, feishu_config, wechat_config, multimedia_config)

        # 9. 本地模式额外检查
        if backend == "local":
            check_local_prerequisites()

        # 10. 显示下一步
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
