"""
夸父沙盒系统 — 不可变的核心层。

职责：
1. 文件操作路径白名单检查
2. 禁止对 core/ 目录的任何写入
3. 命令执行安全过滤
4. 高风险操作确认
"""

import os
import re
import shlex
from pathlib import Path
from typing import List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT_DIR / "core"

# ==== 路径保护 ====

PROTECTED_DIRS: List[Path] = [
    CORE_DIR,                       # core/ 不可写
    ROOT_DIR / "CORE_CHARTER.md",   # 宪章文件不可改
    ROOT_DIR / "IDENTITY.md",        # 身份文件不可改
]

# 允许的文件操作目录（白名单）
ALLOWED_WRITE_DIRS: List[Path] = [
    ROOT_DIR / "strategy",
    ROOT_DIR / "skills",
    ROOT_DIR / "memory",
    ROOT_DIR / "tests",
    ROOT_DIR / "logs",
]


def is_path_allowed_for_write(path: str) -> tuple[bool, str]:
    """检查路径是否允许写入。

    Returns:
        (True, "") 或 (False, "拒绝原因")
    """
    resolved = Path(path).resolve()

    # 检查是否在保护区内
    for protected in PROTECTED_DIRS:
        if protected.is_dir():
            if resolved == protected or str(resolved).startswith(str(protected) + "/"):
                return False, f"拒绝写入 core/ 保护区: {protected}"
        else:
            if resolved == protected:
                return False, f"拒绝修改核心文件: {protected}"

    # 检查是否在白名单目录内
    for allowed in ALLOWED_WRITE_DIRS:
        if str(resolved).startswith(str(allowed)):
            return True, ""

    return False, f"路径不在白名单中: {resolved}"


def register_allowed_dir(path: str) -> None:
    """动态注册新的可写目录。仅 evolution.py 可调用。"""
    p = Path(path).resolve()
    if p not in ALLOWED_WRITE_DIRS:
        ALLOWED_WRITE_DIRS.append(p)


# ==== 命令执行安全 ====

HIGH_RISK_COMMANDS = [
    "rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:",  # shell shock
    "wget.*|sh", "curl.*|sh",                        # pipe to shell
]

SENSITIVE_PATTERNS = [
    r"rm\s+(-rf?\s+)?/[^a-zA-Z]",                   # 删除根目录文件
    r"chmod\s+777\s+/",                               # 开放根目录权限
    r">\s*/dev/[hs]d",                                # 直接写磁盘
]


def validate_command(command: str) -> tuple[bool, str, str]:
    """验证命令安全性。

    Returns:
        (is_safe, risk_level, reason)
        risk_level: "safe" / "warning" / "dangerous"
    """
    # 高危命令
    for hc in HIGH_RISK_COMMANDS:
        if hc in command:
            return False, "dangerous", f"检测到高危命令模式: {hc}"

    # 敏感模式
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, command):
            return False, "warning", f"检测到敏感操作: {pattern}"

    return True, "safe", ""


def is_high_risk_write(path: str) -> tuple[bool, str]:
    """检查文件写入是否为高风险操作。"""
    p = Path(path).resolve()
    for parent in p.parents:
        # 检查 .git/ 目录
        if parent.name == ".git":
            return True, "写入 .git 目录"
        # 检查 /etc, /usr, /bin 等系统目录
        if str(parent) in ["/etc", "/usr", "/bin", "/sbin", "/boot"]:
            return True, f"写入系统目录: {parent}"
    return False, ""


# ==== 沙盒配置 ====

def get_sandbox_report() -> dict:
    """返回沙盒状态报告。"""
    return {
        "protected_dirs": [str(p) for p in PROTECTED_DIRS],
        "allowed_write_dirs": [str(p) for p in ALLOWED_WRITE_DIRS],
        "core_size": sum(
            f.stat().st_size for f in CORE_DIR.rglob("*") if f.is_file()
        ),
        "core_files": len(list(CORE_DIR.rglob("*"))),
    }
