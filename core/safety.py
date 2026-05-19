"""
夸父安全层升级 (Safety Layer)

职责：
1. 命令安全分级（从 sandbox.py 的 command_risk_level 提升为独立模块）
2. API key / 敏感信息脱敏（防止在日志/记忆/回复中泄露）
3. 危险操作确认机制（L0-L3 分级审批）
4. 文件操作安全（核心目录保护 + 敏感文件保护）

与 sandbox.py 的关系：
- sandbox.py: 底层安全检查（路径保护、高危命令拦截）
- safety.py: 上层安全策略（命令分级、API脱敏、操作审批）
"""

import json
import os
import re
from pathlib import Path
from typing import Optional, Pattern

ROOT_DIR = Path(__file__).resolve().parent.parent


# ── 敏感信息类型 ──────────────────────────────────────────────────

SENSITIVE_PATTERNS: list[tuple[str, Pattern]] = [
    ("API Key", re.compile(
        r'(?:api[_-]?key|apikey|secret|token|key)[=:]\s*["\']?([a-zA-Z0-9_\-\.]{8,})["\']?',
        re.IGNORECASE,
    )),
    ("DeepSeek Key", re.compile(
        r'(sk-[a-zA-Z0-9]{20,})',
    )),
    ("OpenAI Key", re.compile(
        r'(sk-[a-zA-Z0-9]{20,})',
    )),
    ("JWT Token", re.compile(
        r'eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}',
    )),
    ("Authorization Header", re.compile(
        r'Authorization:\s*Bearer\s+[a-zA-Z0-9_\-\.]{20,}',
        re.IGNORECASE,
    )),
    ("Private Key", re.compile(
        r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----',
    )),
    ("密码明文", re.compile(
        r'password[=:]\s*["\']?([^"\'&\s]{4,})["\']?',
        re.IGNORECASE,
    )),
]


# ── 命令危险分级 ──────────────────────────────────────────────────

class CommandLevel:
    SAFE = "safe"         # 无风险命令
    ATTENTION = "attention"  # 可能有影响，需确认
    DANGEROUS = "danger"  # 高风险，需严格确认
    FORBIDDEN = "forbid"  # 禁止执行


# 危险命令模式
DANGEROUS_COMMANDS = [
    (re.compile(r'\brm\s+(-rf?|--recursive)\b'), "递归删除文件"),
    (re.compile(r'\bsudo\b'), "sudo 提权"),
    (re.compile(r'\bmkfs\b'), "格式化磁盘"),
    (re.compile(r'\bdd\b'), "dd 覆盖"),
    (re.compile(r'\bchmod\s+777\b'), "777 权限"),
    (re.compile(r'\bchown\b'), "更改所有者"),
    (re.compile(r'\bpasswd\b'), "修改密码"),
    (re.compile(r'\busermod\b'), "修改用户"),
    (re.compile(r'>\s*/dev/', re.IGNORECASE), "写入设备"),
    (re.compile(r':\(\)\s*\{'), "fork bomb"),
    (re.compile(r'\bwget\s+|curl\s+-o|curl\s+--output'), "下载远程文件（需确认）"),
    (re.compile(r'\bgit\s+push\b'), "git 推送"),
    (re.compile(r'\bgit\s+reset\s+--hard\b'), "git 强制重置"),
    (re.compile(r'\bgit\s+checkout\s+-f\b'), "git 强制切换"),
    (re.compile(r'\bpip\s+install\b'), "安装 Python 包"),
    (re.compile(r'\bnpm\s+install\b'), "安装 npm 包"),
    (re.compile(r'\bshutdown\b|\breboot\b|\bpoweroff\b'), "关机/重启"),
]


class SafetyLayer:
    """安全层。提供 API 脱敏、命令分级、敏感内容检测。"""

    # ── API 脱敏 ──────────────────────────────────────────────────

    @staticmethod
    def sanitize_text(text: str) -> str:
        """脱敏文本中的敏感信息。

        将所有 API key、token、密码等替换为 ***。
        """
        sanitized = text
        for name, pattern in SENSITIVE_PATTERNS:
            sanitized = pattern.sub(f"[{name}:***]", sanitized)
        return sanitized

    @staticmethod
    def sanitize_dict(data: dict) -> dict:
        """脱敏 dict 中的所有字符串值。"""
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[k] = SafetyLayer.sanitize_text(v)
            elif isinstance(v, dict):
                result[k] = SafetyLayer.sanitize_dict(v)
            elif isinstance(v, list):
                result[k] = [
                    SafetyLayer.sanitize_text(item) if isinstance(item, str)
                    else SafetyLayer.sanitize_dict(item) if isinstance(item, dict)
                    else item
                    for item in v
                ]
            else:
                result[k] = v
        return result

    @staticmethod
    def sanitize_command(command: str) -> str:
        """脱敏命令中的敏感信息。

        对于包含 API key 的命令（如 curl -H "Authorization: Bearer sk-xxx"），
        脱敏 key 部分。
        """
        # 脱敏 Authorization header
        command = re.sub(
            r'(Bearer\s+)[a-zA-Z0-9_\-\.]{20,}',
            r'\1[***]',
            command,
        )
        # 脱敏包含 key 的参数
        command = re.sub(
            r'(--api-key\s+|=sk-[a-zA-Z0-9]+)',
            r'\1[***]',
            command,
            flags=re.IGNORECASE,
        )
        return command

    # ── 命令危险分级 ──────────────────────────────────────────────

    @staticmethod
    def classify_command(command: str) -> tuple[str, str, str]:
        """对命令进行危险分级。

        Returns:
            (level: str, risk_name: str, reason: str)
            level 取值: CommandLevel.SAFE / ATTENTION / DANGEROUS / FORBIDDEN
        """
        # 检查锁定文件
        lockfile = ROOT_DIR / ".safety-lock"
        if lockfile.exists():
            locked_commands = lockfile.read_text().strip().split("\n")
            for lc in locked_commands:
                if lc and lc.strip() in command:
                    return (CommandLevel.FORBIDDEN, "锁定命令", f"命令「{lc.strip()}」被安全锁禁止")

        # 检查危险命令
        for pattern, risk_name in DANGEROUS_COMMANDS:
            if pattern.search(command):
                if risk_name in ("递归删除文件", "sudo 提权", "格式化磁盘", "dd 覆盖", "关机/重启"):
                    return (CommandLevel.DANGEROUS, risk_name, f"命令包含高风险操作：{risk_name}")
                else:
                    return (CommandLevel.ATTENTION, risk_name, f"命令包含需确认的操作：{risk_name}")

        # 简单的只读命令
        harmless_cmds = [
            r'^\s*(ls|cat|head|tail|echo|pwd|whoami|date|uptime|df|du|free|ps)\b',
            r'^\s*(git\s+(status|diff|log|show|branch))\b',
            r'^\s*python3?\s+(-[cV]|--version)',
        ]
        for pat in harmless_cmds:
            if re.match(pat, command):
                return (CommandLevel.SAFE, "", "")

        return (CommandLevel.SAFE, "", "")

    @staticmethod
    def needs_approval(level: str) -> bool:
        """判断是否需要用户确认。"""
        return level in (CommandLevel.ATTENTION, CommandLevel.DANGEROUS)

    @staticmethod
    def get_approval_message(level: str, risk_name: str, reason: str) -> Optional[str]:
        """获取审批提示信息。"""
        if level == CommandLevel.DANGEROUS:
            return (
                f"⚠️ **高风险操作** — {risk_name}\n\n"
                f"{reason}\n\n"
                "此操作可能对系统造成不可逆影响。确认继续？"
            )
        elif level == CommandLevel.ATTENTION:
            return (
                f"⚡ **需确认操作** — {risk_name}\n\n"
                f"{reason}\n\n"
                "确认继续？"
            )
        return None

    # ── 敏感文件保护 ──────────────────────────────────────────────

    @staticmethod
    def is_path_sanitized(path: str) -> bool:
        """检查路径是否应该被脱敏（如 .env 文件中的内容）。"""
        path = path.lower()
        sensitive_files = [
            ".env", "id_rsa", "id_ed25519", ".ssh/",
            "credentials", "secret", "token",
            ".netrc", ".npmrc", ".docker/config.json",
        ]
        return any(sf in path for sf in sensitive_files)

    @staticmethod
    def is_output_sensitive(content: str) -> bool:
        """检查输出内容是否包含敏感信息。"""
        for name, pattern in SENSITIVE_PATTERNS:
            if pattern.search(content):
                return True
        return False

    # ── 安全状态 ──────────────────────────────────────────────────

    @staticmethod
    def get_safety_summary() -> dict:
        """获取安全层概要状态。"""
        lockfile = ROOT_DIR / ".safety-lock"
        locked_commands = []
        if lockfile.exists():
            locked_commands = [l.strip() for l in lockfile.read_text().split("\n") if l.strip()]

        return {
            "locked_commands": locked_commands,
            "sensitive_patterns_active": len(SENSITIVE_PATTERNS),
            "command_classification": {
                "dangerous_patterns": len(DANGEROUS_COMMANDS),
                "levels": {
                    "safe": "低风险命令，自动执行",
                    "attention": "中风险命令，需确认",
                    "dangerous": "高风险命令，严格审批",
                    "forbidden": "安全锁禁止的命令",
                },
            },
            "sanitization": {
                "auto_sanitize_api_keys": True,
                "auto_sanitize_output": True,
            },
        }

    @staticmethod
    def lock_command(command: str) -> bool:
        """将一个命令加入安全锁。"""
        lockfile = ROOT_DIR / ".safety-lock"
        existing = set()
        if lockfile.exists():
            existing = set(l.strip() for l in lockfile.read_text().split("\n") if l.strip())
        if command in existing:
            return False
        existing.add(command)
        lockfile.write_text("\n".join(sorted(existing)) + "\n")
        return True

    @staticmethod
    def unlock_command(command: str) -> bool:
        """从安全锁移除一个命令。"""
        lockfile = ROOT_DIR / ".safety-lock"
        if not lockfile.exists():
            return False
        lines = [l.strip() for l in lockfile.read_text().split("\n")]
        if command not in lines:
            return False
        lines = [l for l in lines if l != command]
        lockfile.write_text("\n".join(lines) + "\n" if lines else "")
        return True
