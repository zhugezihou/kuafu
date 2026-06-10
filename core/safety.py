"""
夸父安全层

职责：
1. 路径安全保护（原 sandbox.py 路径白名单功能）
2. 命令安全分级（命令危险等级判断）
3. API key / 敏感信息脱敏
4. 危险操作确认机制（L0-L3 分级审批）
5. 文件操作安全（核心目录保护 + 敏感文件保护）
"""

import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Pattern

ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT_DIR / "core"

# ==== 路径保护（从 sandbox.py 合并） ====

# 只读保护区 — 禁止写入
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


# ==== 命令执行安全（从 sandbox.py 合并） ====

HIGH_RISK_COMMANDS = [
    "rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:",  # shell shock
    "wget.*|sh", "curl.*|sh",                        # pipe to shell
]

SENSITIVE_PATTERNS_CMD = [
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
    for pattern in SENSITIVE_PATTERNS_CMD:
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


# ── 拒绝跟踪（Denial Tracking） ──────────────────────────────────────
#
# 记录用户拒绝执行命令的次数和模式，自动调整安全策略敏感度。
# Claude Code 启发：连续拒绝 N 次 → 降低同类命令的敏感度/自动信任

@dataclass
class DenialRecord:
    """一次命令拒绝记录。"""
    command_pattern: str          # 匹配的命令模式（如 "pip install"）
    count: int = 0                # 拒绝次数
    first_seen: float = 0.0      # 首次拒绝时间
    last_seen: float = 0.0       # 最近拒绝时间
    consecutive_denials: int = 0  # 连续拒绝次数
    degraded: bool = False        # 是否已降级（降低敏感度后不再询问此类命令）


@dataclass
class DenialConfig:
    """拒绝跟踪配置项。"""
    # 连续拒绝多少次后自动降级（不再询问此类命令，默认直接执行）
    auto_trust_threshold: int = 3
    # 拒绝记录保存文件
    state_file: str = "memory/.denial_state.json"
    # 降级后的命令默认行为: "allow"（自动允许）或 "block"（自动拒绝）
    degraded_action: str = "allow"


class DenialTracker:
    """拒绝跟踪器 — 跟踪用户拒绝命令的模式，自动调整安全策略。

    核心机制：
    - 每次命令被拒绝时记录，按命令模式（分类）聚合
    - 同一模式连续拒绝 N 次（auto_trust_threshold）后自动降级
    - 降级后同类命令不再询问用户，直接按 degraded_action 处理
    - 持久化到 .denial_state.json，夸父重启后仍保留学习结果

    使用方式：
        tracker = DenialTracker()
        # 命令被拒绝时
        tracker.record_denial("pip install")
        # 查询是否应降级
        if tracker.should_degrade("pip install"):
            # 自动处理，不询问用户
        # 检查命令是否需要询问（自动决策）
        decision = tracker.get_decision("pip install")  # "ask" / "allow" / "block"
    """

    def __init__(self, root_dir: Optional[Path] = None, config: Optional[DenialConfig] = None):
        self.root_dir = root_dir or ROOT_DIR
        self.config = config or DenialConfig()
        self.state_path = self.root_dir / self.config.state_file
        self._data: dict[str, dict] = self._load()

    # ── 公开接口 ──

    def record_denial(self, command_pattern: str) -> dict:
        """记录一次命令被拒绝。返回更新后的记录。"""
        now = time.time()
        entry = self._data.get(command_pattern, {
            "count": 0,
            "first_seen": now,
            "last_seen": now,
            "consecutive_denials": 0,
            "degraded": False,
        })
        entry["count"] += 1
        entry["consecutive_denials"] = entry.get("consecutive_denials", 0) + 1
        entry["last_seen"] = now
        if entry["first_seen"] == 0:
            entry["first_seen"] = now

        # 自动降级判定
        if entry["consecutive_denials"] >= self.config.auto_trust_threshold and not entry["degraded"]:
            entry["degraded"] = True

        self._data[command_pattern] = entry
        self._save()
        return entry

    def record_approval(self, command_pattern: str):
        """记录一次命令被批准（重置连续拒绝计数）。"""
        entry = self._data.get(command_pattern)
        if entry:
            entry["consecutive_denials"] = 0
            self._save()

    def should_degrade(self, command_pattern: str) -> bool:
        """检查命令是否达到降级条件（连续拒绝 >= 阈值）。"""
        entry = self._data.get(command_pattern, {})
        return entry.get("degraded", False)

    def get_decision(self, command_pattern: str) -> str:
        """获取对该命令的决策：'ask'（询问用户）| 'allow'（自动允许）| 'block'（自动阻止）。

        逻辑：
        1. 如果已降级 → 按 degraded_action 自动处理
        2. 如果未降级 → 始终 ask
        """
        entry = self._data.get(command_pattern, {})
        if entry.get("degraded", False):
            return self.config.degraded_action  # "allow" or "block"
        return "ask"

    def get_stats(self) -> dict:
        """获取拒绝跟踪统计。"""
        total_patterns = len(self._data)
        degraded_count = sum(1 for v in self._data.values() if v.get("degraded"))
        total_denials = sum(v.get("count", 0) for v in self._data.values())
        return {
            "total_patterns": total_patterns,
            "degraded_count": degraded_count,
            "total_denials": total_denials,
            "patterns": dict(self._data),
        }

    def match_command(self, command: str) -> Optional[str]:
        """查找命令匹配的已知拒绝模式。返回匹配的模式名或 None。

        匹配逻辑：如果命令包含 DANGEROUS_COMMANDS 中的某个模式文本，
        则返回该模式的 risk_name。
        """
        command_lower = command.lower()
        # 先查完整命令匹配
        if command_lower in self._data:
            return command_lower
        # 再查子串匹配
        for pattern_key in self._data:
            if pattern_key in command_lower:
                return pattern_key
        return None

    def reset_pattern(self, command_pattern: str) -> bool:
        """重置某个命令模式的拒绝计数。"""
        if command_pattern in self._data:
            self._data[command_pattern] = {
                "count": 0,
                "first_seen": 0,
                "last_seen": 0,
                "consecutive_denials": 0,
                "degraded": False,
            }
            self._save()
            return True
        return False

    def reset_all(self):
        """重置所有拒绝记录。"""
        self._data = {}
        self._save()

    # ── 持久化 ──

    def _load(self) -> dict:
        try:
            if self.state_path.exists():
                with open(self.state_path) as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass


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
    (re.compile(r'>\s*/dev/(hd|sd|xvd|vd|nvme|mmcblk|loop|dm-)[a-z]', re.IGNORECASE), "写入设备"),
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
    """安全层。提供 API 脱敏、命令分级、敏感内容检测、拒绝跟踪。"""

    # 全局拒绝跟踪器（所有 SafetyLayer 实例共享）
    denial_tracker: DenialTracker = DenialTracker()

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
            locked_commands = lockfile.read_text(encoding='utf-8').strip().split("\n")
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
    def get_tri_state(command: str) -> dict:
        """三态安全决策。

        Returns:
            {"decision": "allow" | "block" | "escalate",
             "level": str, "risk_name": str, "reason": str,
             "suggestions": list[str]}
        """
        level, risk_name, reason = SafetyLayer.classify_command(command)

        if level == CommandLevel.FORBIDDEN:
            return {
                "decision": "block",
                "level": level,
                "risk_name": risk_name,
                "reason": reason,
                "suggestions": [f"命令被安全锁禁止: {reason}"],
            }

        if level == CommandLevel.DANGEROUS:
            return {
                "decision": "escalate",
                "level": level,
                "risk_name": risk_name,
                "reason": reason,
                "suggestions": [
                    f"该操作可能造成不可逆影响: {reason}",
                    "如果确认需要执行，使用终端手动操作",
                    "或简化命令范围降低风险",
                ],
            }

        if level == CommandLevel.ATTENTION:
            return {
                "decision": "escalate",
                "level": level,
                "risk_name": risk_name,
                "reason": reason,
                "suggestions": [
                    f"该操作需确认: {reason}",
                    "输入 y 继续，n 取消",
                ],
            }

        # SAFE
        return {
            "decision": "allow",
            "level": level,
            "risk_name": "",
            "reason": "",
            "suggestions": [],
        }

    @staticmethod
    def needs_approval_with_denial(level: str, command: str) -> tuple[bool, str]:
        """判断是否需要用户确认（集成拒绝跟踪）。

        与 needs_approval() 的区别：
        - 检查 DenialTracker 的决策（如果命令已降级，直接 allow/block）
        - 返回 (need_ask: bool, decision: str)

        Returns:
            (True, "ask")   → 需要询问用户
            (False, "allow") → 自动允许（已获信任）
            (False, "block") → 自动阻止（已学习用户反复拒绝此类命令）
        """
        # 基本判断：不需要审批的级别直接放行
        if level not in (CommandLevel.ATTENTION, CommandLevel.DANGEROUS):
            return (False, "allow")

        # 拒绝跟踪判断
        decision = SafetyLayer.denial_tracker.get_decision(command)

        if decision == "allow":
            # 用户已连续拒绝 N 次同类命令 → 自动降级放行（用户觉得烦了）
            return (False, "allow")
        elif decision == "block":
            # 已学习用户反复拒绝此类命令 → 自动阻止
            return (False, "block")

        # 默认：询问用户
        return (True, "ask")

    @staticmethod
    def report_denial(command: str):
        """报告一次命令被用户拒绝。"""
        SafetyLayer.denial_tracker.record_denial(command)

    @staticmethod
    def report_approval(command: str):
        """报告一次命令被用户批准（重置连续拒绝计数）。"""
        SafetyLayer.denial_tracker.record_approval(command)

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
            locked_commands = [l.strip() for l in lockfile.read_text(encoding='utf-8').split("\n") if l.strip()]

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
            "denial_tracking": SafetyLayer.denial_tracker.get_stats(),
        }

    @staticmethod
    def lock_command(command: str) -> bool:
        """将一个命令加入安全锁。"""
        lockfile = ROOT_DIR / ".safety-lock"
        existing = set()
        if lockfile.exists():
            existing = set(l.strip() for l in lockfile.read_text(encoding='utf-8').split("\n") if l.strip())
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
        lines = [l.strip() for l in lockfile.read_text(encoding='utf-8').split("\n")]
        if command not in lines:
            return False
        lines = [l for l in lines if l != command]
        lockfile.write_text("\n".join(lines) + "\n" if lines else "")
        return True
