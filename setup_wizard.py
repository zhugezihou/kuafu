#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸父 (Kuafu) — 交互式配置向导

首次安装后运行，引导用户配置：
1. 选择 LLM 后端（云端 DeepSeek / 本地 Qwen）
2. 输入 API Key
3. 测试连接
4. 保存 .env
5. 显示下一步指引

用法:
    python setup_wizard.py
"""
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
    # 回退到纯文本


def c(text, color=None):
    """简易颜色回退"""
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
    """打印步骤标题"""
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


# ─── 配置向导 ────────────────────────────────────────────────────────────────
def ask_backend() -> str:
    """选择 LLM 后端"""
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
    """获取 API Key"""
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
                if line.startswith("KUAFFU_API_KEY="):
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


def ask_hindsight() -> tuple:
    """Hindsight 记忆系统（可选）"""
    print_step(3, "记忆系统（可选）")

    print_info("Hindsight 是夸父的语义记忆系统，支持向量搜索和实体图谱")
    print_info("不使用 Hindsight: 使用本地 JSON 文件存储（关键词匹配）")

    if RICH_AVAILABLE:
        use_hindsight = Confirm.ask("\n  启用 Hindsight 云端记忆?", default=False)
    else:
        resp = input("\n  启用 Hindsight? (y/N): ").strip().lower()
        use_hindsight = resp == "y"

    if not use_hindsight:
        return "", ""

    if RICH_AVAILABLE:
        api_key = Prompt.ask("  Hindsight API Key", password=True)
        bank_id = Prompt.ask("  Bank ID", default="default")
    else:
        print("  Hindsight API Key: ", end="")
        api_key = input().strip()
        bank_id = input("  Bank ID (默认: default): ").strip() or "default"

    return api_key, bank_id


def test_connection(backend: str, api_key: str) -> bool:
    """测试 LLM 连接"""
    print_step(4, "测试连接")

    try:
        from core.llm import LLMClient

        if backend == "local":
            client = LLMClient(backend="local", timeout=15)
        else:
            if not api_key:
                print_err("API Key 不能为空")
                return False
            client = LLMClient(backend="cloud", api_key=api_key, timeout=15)

        print_info("发送测试请求...")
        result = client.chat([
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "回复 OK 即可"},
        ])

        if result and result.strip():
            print_ok(f"连接成功！回复: {result.strip()[:60]}")
            return True
        else:
            print_err("连接返回空响应")
            return False

    except Exception as e:
        print_err(f"连接失败: {str(e)}")
        return False


def save_config(backend: str, api_key: str, hindsight_key: str, hindsight_bank: str):
    """保存 .env 配置"""
    print_step(5, "保存配置")

    config_lines = []

    # 读取已有 .env 保留注释
    if DOT_ENV.exists():
        with open(DOT_ENV, encoding="utf-8") as f:
            config_lines = f.read().splitlines()

    # 替换或新增配置项
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

    # 核心配置
    if api_key:
        set_var("KUAFFU_API_KEY", api_key)
    set_var("KUAFFU_BACKEND", backend)

    if backend == "cloud":
        set_var("KUAFFU_BASE_URL", "https://api.deepseek.com")

    # Hindsight
    if hindsight_key:
        set_var("HINDSIGHT_API_KEY", hindsight_key)
    if hindsight_bank:
        set_var("HINDSIGHT_BANK_ID", hindsight_bank)

    with open(DOT_ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(config_lines) + "\n")

    print_ok(f"配置文件已保存: {DOT_ENV}")


def show_next_steps(backend: str):
    """显示下一步指引"""
    print_step(6, "下一步")

    steps = [
        "基本使用（交互模式）:  bash kuafu.sh",
        "命令式:  bash kuafu.sh '你的任务'",
        "Python API:  from kuafu import KuafuAgent; agent = KuafuAgent(); agent.run('任务')",
    ]

    if backend == "local":
        steps.insert(0, "确保 llama-server 已在运行:  ps aux | grep llama-server")
        steps.insert(0, "首次运行前请下载模型:  bash scripts/download_model.sh")

    for i, step in enumerate(steps, 1):
        print_info(f"{i}. {step}")

    print_info("文档: https://github.com/zhugezihou/kuafu")
    print_ok("配置完成！夸父已就绪，逐日不息！")


# ─── 本地模式专用 ────────────────────────────────────────────────────────────
def check_local_prerequisites():
    """检查本地模式前置条件"""
    print_step("进阶", "本地模式前置检查")

    # 检查 nvidia-smi
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
        print_info("参考文档: https://github.com/ggml-ai/llama.cpp")

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
    print_info("全程约 2 分钟，配置项可随时修改。")

    try:
        # 1. 选择后端
        backend = ask_backend()

        # 2. API Key
        api_key = ask_api_key(backend)

        # 3. Hindsight（可选）
        h_key, h_bank = ask_hindsight()

        # 4. 测试连接
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

        # 5. 保存
        save_config(backend, api_key, h_key, h_bank)

        # 6. 本地模式额外检查
        if backend == "local":
            check_local_prerequisites()

        # 7. 显示下一步
        show_next_steps(backend)

    except KeyboardInterrupt:
        print("\n")
        print_warn("配置已取消")
        sys.exit(1)


if __name__ == "__main__":
    main()
