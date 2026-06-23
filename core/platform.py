"""
core/platform.py — 跨平台抽象层

夸父同时运行在 Linux（WSL / 服务器）、Windows Desktop、Android 三个平台。
此模块集中处理所有跨平台差异，其他模块通过 Platform 接口获取平台信息，
不直接判断 sys.platform 或 os.name。

用法：
    from core.platform import Platform
    if Platform.is_windows():
        cmd = "dir"
    else:
        cmd = "ls"
"""

import os
import sys
import shutil
from pathlib import Path


class Platform:
    """跨平台信息与工具。所有方法均为静态方法。"""

    _desktop_mode: bool = False  # 由 gateway.py 在 Desktop 模式下设置

    @classmethod
    def set_desktop_mode(cls, value: bool = True):
        """由 gateway.py 在检测到 KUAFU_DESKTOP=1 时调用"""
        cls._desktop_mode = value

    # ── 系统检测 ──

    @classmethod
    def is_windows(cls) -> bool:
        """是否 Windows 系统（含 WSL 返回 False）"""
        return sys.platform == "win32" or os.name == "nt"

    @classmethod
    def is_wsl(cls) -> bool:
        """是否 WSL（Windows Subsystem for Linux）"""
        if cls.is_windows():
            return False
        try:
            return "microsoft" in Path("/proc/version").read_text().lower()
        except Exception:
            return False

    @classmethod
    def is_linux(cls) -> bool:
        """是否原生 Linux（不含 WSL）"""
        return sys.platform == "linux" and not cls.is_wsl()

    @classmethod
    def is_desktop(cls) -> bool:
        """是否 Desktop 模式（由环境变量控制）"""
        return cls._desktop_mode or os.environ.get("KUAFU_DESKTOP") == "1"

    @classmethod
    def is_android(cls) -> bool:
        """是否 Android（Termux 环境）"""
        return "ANDROID_ROOT" in os.environ or "TERMUX_VERSION" in os.environ

    # ── 路径 ──

    @classmethod
    def home_dir(cls) -> Path:
        """用户主目录"""
        return Path.home()

    @classmethod
    def temp_dir(cls) -> Path:
        """系统临时目录"""
        import tempfile
        return Path(tempfile.gettempdir())

    @classmethod
    def downloads_dir(cls) -> Path:
        """下载目录"""
        if cls.is_windows():
            return cls.home_dir() / "Downloads"
        return cls.home_dir() / "Downloads"

    @classmethod
    def desktop_dir(cls) -> Path:
        """桌面目录"""
        if cls.is_windows():
            return cls.home_dir() / "Desktop"
        return cls.home_dir() / "Desktop"

    @classmethod
    def path_style(cls) -> str:
        """路径风格：'windows' 或 'posix'"""
        return "windows" if cls.is_windows() else "posix"

    @classmethod
    def normalize_path(cls, path: str) -> str:
        """将路径转为当前平台的正确格式

        - WSL /mnt/c/Users/xxx → C:\\Users\\xxx（Windows）
        - C:\\Users\\xxx → /mnt/c/Users/xxx（WSL/Linux 兼容）
        """
        p = Path(path)
        if cls.is_windows():
            # /mnt/c/Users/xxx → C:\\Users\\xxx
            parts = p.parts
            if len(parts) > 2 and parts[0] == "/" and parts[1].startswith("mnt"):
                drive = parts[2][0].upper()
                rest = str(Path(*parts[3:]))
                return f"{drive}:\\{rest}"
            return str(p)
        # Windows 路径 → WSL 路径
        if len(path) > 2 and path[1] == ":":
            drive = path[0].lower()
            rest = path[2:].replace("\\", "/")
            return f"/mnt/{drive}{rest}"
        return str(p)

    # ── Shell ──

    @classmethod
    def shell(cls) -> str:
        """当前平台的默认 shell"""
        if cls.is_windows():
            return "cmd"
        return "bash"

    @classmethod
    def shell_args(cls, command: str) -> list[str]:
        """将命令转为适合当前平台 shell 的参数列表"""
        if cls.is_windows():
            return ["cmd.exe", "/c", command]
        return ["bash", "-c", command]

    # ── 安全命令 ──

    @classmethod
    def safe_commands(cls) -> tuple[str, ...]:
        """当前平台的安全（只读）命令前缀"""
        base = (
            "ls ", "cat ", "curl ", "echo ", "pwd", "whoami", "date",
            "head ", "tail ", "wc ", "sort ", "grep ", "find ", "which ",
            "pip list", "pip show", "pip3 list", "pip3 show",
            "python3 --version", "python --version",
            "node --version", "npm --version", "npx --version",
            "git status", "git log", "git diff", "git branch",
            "free ", "df ", "du ", "ps ", "top ", "env", "printenv",
            "uname", "id", "hostname", "uptime",
        )
        if cls.is_windows():
            return base + (
                "dir ", "type ", "findstr ", "where ", "tasklist",
                "systeminfo", "ver", "ipconfig", "netstat",
            )
        return base

    @classmethod
    def danger_commands(cls) -> list[str]:
        """当前平台的危险命令子串（自动拒绝）"""
        base = [
            "rm -rf /", "dd if=", "> /dev/sda", "mkfs", "fdisk",
            "chmod 777 /", "kill -9", "pkill", "shutdown", "reboot",
            "init 0", "poweroff",
        ]
        if cls.is_windows():
            return base + [
                "format ", "diskpart", "del /f /s", "rd /s /q",
                "taskkill /f", "shutdown /s",
            ]
        return base

    # ── 命令翻译（Linux → Windows） ──

    _CMD_TRANSLATIONS: dict[str, str] = {
        "ls ": "dir ",
        "ls -la": "dir",
        "ls -l": "dir",
        "cat ": "type ",
        "grep ": "findstr ",
        "which ": "where ",
        "pwd": "cd",
        "whoami": "echo %USERNAME%",
        "uname": "ver",
        "ps aux": "tasklist",
        "ps ": "tasklist",
        "top": "tasklist",
        "free ": "systeminfo | findstr Memory",
        "df ": "wmic logicaldisk get size,freespace,caption",
        "du ": "dir /s",
        "head ": "cmd /c \"head\" " if False else "",  # Windows 无 head
        "tail ": "cmd /c \"tail\" " if False else "",
        "rm ": "del ",
        "rm -rf": "rd /s /q ",
        "mv ": "move ",
        "cp ": "copy ",
        "mkdir -p": "mkdir ",
        "chmod": "",  # 无等价命令
        "chown": "",
    }

    @classmethod
    def translate_command(cls, command: str) -> str:
        """将 Linux 命令翻译为 Windows 等价命令（仅 Desktop 模式）"""
        if not cls.is_windows():
            return command

        translated = command
        for linux_cmd, win_cmd in cls._CMD_TRANSLATIONS.items():
            if translated.strip().startswith(linux_cmd):
                if not win_cmd:
                    # 无等价命令，追加注释
                    return f"REM {linux_cmd.strip()} 在 Windows 上无等价命令"
                translated = win_cmd + translated[len(linux_cmd):]
                break
        return translated
