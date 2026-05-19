#!/usr/bin/env python3
"""
夸父启动器 (Kuafu Launcher) — 由夸父自己为自己开发的启动工具。

功能:
  1. 交互式菜单 — 选择模式
  2. 一键启动 — 快速进入交互模式
  3. 状态看板 — 查看进化状态、记忆数量、任务统计
  4. 日志查看 — 查看最近执行日志
  5. 任务快捷 — 预设常用任务快速执行
  6. 系统监控 — 查看资源使用情况

用法:
  python launcher.py           # 交互菜单
  python launcher.py --status  # 直接看状态
  python launcher.py --task "..."  # 直接执行任务
"""

import os
import sys
import json
import time
import shutil
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

# 颜色
class Color:
    RESET = "[0m"
    BOLD = "[1m"
    DIM = "[2m"
    RED = "[91m"
    GREEN = "[92m"
    YELLOW = "[93m"
    BLUE = "[94m"
    MAGENTA = "[95m"
    CYAN = "[96m"
    WHITE = "[97m"
    BGRED = "[41m"
    BGGREEN = "[42m"
    BGBLUE = "[44m"
    BGMAGENTA = "[45m"
    BGCYAN = "[46m"

    @staticmethod
    def ok(text): return f"{Color.GREEN}{text}{Color.RESET}"
    @staticmethod
    def err(text): return f"{Color.RED}{text}{Color.RESET}"
    @staticmethod
    def warn(text): return f"{Color.YELLOW}{text}{Color.RESET}"
    @staticmethod
    def info(text): return f"{Color.CYAN}{text}{Color.RESET}"
    @staticmethod
    def title(text): return f"{Color.BOLD}{Color.BGBLUE} {text} {Color.RESET}"
    @staticmethod
    def highlight(text): return f"{Color.BOLD}{Color.MAGENTA}{text}{Color.RESET}"

BANNER = f"""{Color.RED}    _                     __              {Color.RESET}
{Color.YELLOW}   | |                   / _|             {Color.RESET}
{Color.GREEN}   | | __ ___   ____ _  | |_ _   _ _ __   {Color.RESET}
{Color.CYAN}   | |/ _` \\ \\ / / _` | |  _| | | | '_ \\  {Color.RESET}
{Color.BLUE}   | | (_| |\\ V / (_| | | | | |_| | | | | {Color.RESET}
{Color.MAGENTA}   |_|\\__,_| \\_/ \\__,_| |_|  \\__,_|_| |_| {Color.RESET}
{Color.DIM}   逐日不息 · 自我超越{Color.RESET}"""

print(BANNER)
print('test ok')
