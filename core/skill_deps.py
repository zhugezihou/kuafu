"""
夸父技能依赖管理器 (Skill Dependency Manager)

功能：
1. 解析技能 YAML 中的 dependencies 声明
2. 检测系统工具是否安装（which）
3. 检测 Python 包是否安装（import / pip list）
4. 自动安装缺失依赖
5. 验证安装结果

依赖声明格式（kfskill 规范）：

    dependencies:
      tools:              # 系统工具（通过 which 检测）
        - python3
        - curl
        - git
      packages:           # Python 包（通过 import 或 pip 检测）
        - requests>=2.25
        - beautifulsoup4
      env:                # 环境变量（可选）
        - OPENAI_API_KEY
      notes:              # 额外说明（仅提示，不自动检测）
        - 需要注册 API key

设计原则：
- 零新增依赖（仅标准库 + subprocess）
- 仅 pip 安装 Python 包（不涉及系统包管理器）
- 所有操作可降级（依赖缺失仅警告，不阻塞）
"""

import importlib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kuafu.skill_deps")

# ── 配置 ──────────────────────────────────────────────────────

# 哪些 Python 包可以用 import 检测
IMPORT_CHECK_BLACKLIST = {
    "os", "sys", "re", "json", "pathlib", "collections",
    "math", "time", "datetime", "random", "itertools",
    "functools", "typing",
}

# 已知包名 → import 名的映射（当两者不一致时）
PACKAGE_IMPORT_MAP = {
    "beautifulsoup4": "bs4",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
}

# 默认 pip 安装超时（秒）
PIP_TIMEOUT = 60


# ── 依赖检查 ──────────────────────────────────────────────────

class DependencyCheckResult:
    """依赖检查结果。"""

    def __init__(self):
        self.ok = True
        self.missing_tools: list[str] = []
        self.missing_packages: list[str] = []
        self.missing_env: list[str] = []
        self.warnings: list[str] = []

    def __bool__(self):
        return self.ok

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "missing_tools": self.missing_tools,
            "missing_packages": self.missing_packages,
            "missing_env": self.missing_env,
            "warnings": self.warnings,
        }

    def summary(self) -> str:
        if self.ok:
            return "✅ 所有依赖已满足"
        parts = []
        if self.missing_tools:
            parts.append(f"缺失系统工具: {', '.join(self.missing_tools)}")
        if self.missing_packages:
            parts.append(f"缺失 Python 包: {', '.join(self.missing_packages)}")
        if self.missing_env:
            parts.append(f"缺失环境变量: {', '.join(self.missing_env)}")
        return "⚠️  " + "; ".join(parts)


def check_dependencies(data: dict, strict: bool = False) -> DependencyCheckResult:
    """检查技能声明的依赖是否满足。

    Args:
        data: 技能 YAML 数据（包含可选的 dependencies 字段）
        strict: 严格模式（缺失即报错，不自动降级）

    Returns:
        DependencyCheckResult
    """
    result = DependencyCheckResult()
    deps = data.get("dependencies", {})

    if not deps:
        return result

    # 1. 检查系统工具
    for tool in deps.get("tools", []):
        if not shutil.which(tool):
            result.missing_tools.append(tool)
            result.ok = False

    # 2. 检查 Python 包
    for pkg in deps.get("packages", []):
        pkg_name, version_spec = _parse_package_spec(pkg)
        if _check_package(pkg_name, version_spec):
            continue
        result.missing_packages.append(pkg)
        result.ok = False

    # 3. 检查环境变量
    for env_var in deps.get("env", []):
        if not os.environ.get(env_var):
            result.missing_env.append(env_var)
            result.ok = False

    return result


def install_dependencies(data: dict, auto_confirm: bool = False,
                         pip_timeout: int = PIP_TIMEOUT,
                         upgrade: bool = False) -> dict:
    """安装技能声明的依赖。

    Args:
        data: 技能 YAML 数据
        auto_confirm: 是否自动确认 pip install（无需用户确认）
        pip_timeout: pip 安装超时秒数
        upgrade: 是否升级已安装的包

    Returns:
        {"installed": [str], "failed": [(str, str)], "skipped": [str], "warnings": [str]}
    """
    deps = data.get("dependencies", {})
    if not deps:
        return {"installed": [], "failed": [], "skipped": [], "warnings": []}

    result: dict[str, list] = {
        "installed": [],
        "failed": [],
        "skipped": [],
        "warnings": [],
    }

    # 1. 处理 Python 包
    for pkg in deps.get("packages", []):
        pkg_name, version_spec = _parse_package_spec(pkg)

        # 已经是 stdlib 的跳过
        if _is_stdlib(pkg_name):
            result["skipped"].append(f"{pkg} (标准库)")
            continue

        # 已安装且满足版本
        if _check_package(pkg_name, version_spec):
            result["skipped"].append(f"{pkg} (已安装)")
            continue

        # 安装
        pip_result = _pip_install(pkg, auto_confirm=auto_confirm,
                                   timeout=pip_timeout, upgrade=upgrade)
        if pip_result["success"]:
            result["installed"].append(pkg)
        else:
            result["failed"].append((pkg, pip_result.get("error", "未知错误")))

    # 2. 环境变量和系统工具仅提示，不自动安装
    missing_tools = deps.get("tools", [])
    missing_tools_actual = [t for t in missing_tools if not shutil.which(t)]
    if missing_tools_actual:
        result["warnings"].append(
            f"系统工具缺失（请手动安装）: {', '.join(missing_tools_actual)}"
        )

    missing_env = deps.get("env", [])
    missing_env_actual = [e for e in missing_env if not os.environ.get(e)]
    if missing_env_actual:
        result["warnings"].append(
            f"环境变量未设置: {', '.join(missing_env_actual)}"
        )

    return result


def verify_installation(data: dict) -> dict:
    """验证技能声明的依赖是否全部可用。"""
    check = check_dependencies(data)
    return {
        "ready": check.ok,
        **check.to_dict(),
    }


def suggest_command(data: dict) -> str:
    """生成依赖安装命令建议。"""
    deps = data.get("dependencies", {})
    if not deps:
        return ""

    parts = []
    packages = deps.get("packages", [])
    if packages:
        parts.append(f"pip install {' '.join(packages)}")

    tools = deps.get("tools", [])
    missing_tools = [t for t in tools if not shutil.which(t)]
    if missing_tools:
        parts.append(f"# 请安装系统工具: {' '.join(missing_tools)}")

    env_vars = deps.get("env", [])
    missing_env = [e for e in env_vars if not os.environ.get(e)]
    if missing_env:
        parts.append(f"# 请设置环境变量: {' '.join(missing_env)}")

    return "\n".join(parts) if parts else ""


# ── 内部工具函数 ──────────────────────────────────────────────

def _parse_package_spec(spec: str) -> tuple[str, str]:
    """解析包版本声明。

    "requests>=2.25" → ("requests", ">=2.25")
    "beautifulsoup4" → ("beautifulsoup4", "")
    """
    m = re.match(r"^([\w\-\.]+)\s*([><=!]+\s*[\w\.\*]+)?", spec.strip())
    if m:
        return m.group(1), (m.group(2) or "").strip()
    return spec.strip(), ""


def _check_package(pkg_name: str, version_spec: str = "") -> bool:
    """检查 Python 包是否已安装。

    先用 import 检测（快速），兜底用 pip list（慢）。
    """
    # 标准库不需要检查
    if _is_stdlib(pkg_name):
        return True

    # 1. import 检测
    import_name = PACKAGE_IMPORT_MAP.get(pkg_name, pkg_name)
    try:
        mod = importlib.import_module(import_name)
    except ImportError:
        # 尝试 pip list 兜底
        return _check_by_pip_list(pkg_name)
    except Exception:
        return _check_by_pip_list(pkg_name)

    # 2. 如果指定了版本，检查版本
    if version_spec:
        try:
            installed_version = getattr(mod, "__version__", "")
            if not installed_version:
                # 尝试 from importlib.metadata
                try:
                    from importlib.metadata import version as _meta_version
                    installed_version = _meta_version(pkg_name)
                except Exception:
                    pass
            if installed_version:
                return _check_version(installed_version, version_spec)
        except Exception:
            pass

    return True


def _check_by_pip_list(pkg_name: str) -> bool:
    """通过 pip list 检查包是否安装。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=columns"],
            capture_output=True, text=True, timeout=15,
        )
        # 匹配包名（忽略大小写）
        for line in result.stdout.split("\n")[2:]:  # 跳过表头
            if line.strip():
                parts = line.split()
                if parts and parts[0].lower() == pkg_name.lower():
                    return True
        return False
    except Exception:
        return False


def _pip_install(pkg: str, auto_confirm: bool = True,
                 timeout: int = 60, upgrade: bool = False) -> dict:
    """安装 Python 包。"""
    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.append(pkg)

    if not auto_confirm:
        return {"success": False, "error": "需要用户确认安装"}

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return {"success": True}
        else:
            error = proc.stderr.strip() or proc.stdout.strip()[:200]
            return {"success": False, "error": error}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"安装超时 ({timeout}s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _is_stdlib(pkg_name: str) -> bool:
    """判断是否为 Python 标准库模块。"""
    return pkg_name in IMPORT_CHECK_BLACKLIST or pkg_name in sys.builtin_module_names


def _check_version(installed: str, spec: str) -> bool:
    """简单的版本满足检查。

    支持: >=1.0, <=2.0, ==1.5, >1.0, <2.0
    """
    spec = spec.strip()
    if not spec:
        return True

    # 解析 spec
    m = re.match(r"^([><=!]+)\s*([\d\.\*]+)", spec)
    if not m:
        return True  # 无法解析的 spec，默认通过

    op, ver = m.group(1), m.group(2)
    ver = ver.rstrip("*.")

    try:
        installed_parts = [int(x) for x in installed.split(".")]
        spec_parts = [int(x) for x in ver.split(".")]

        # 补齐长度
        while len(installed_parts) < len(spec_parts):
            installed_parts.append(0)
        while len(spec_parts) < len(installed_parts):
            spec_parts.append(0)

        cmp = (installed_parts > spec_parts) - (installed_parts < spec_parts)

        if op == ">=":
            return cmp >= 0
        elif op == "<=":
            return cmp <= 0
        elif op == "==":
            return cmp == 0
        elif op == ">":
            return cmp > 0
        elif op == "<":
            return cmp < 0
        elif op == "!=":
            return cmp != 0
        return True
    except (ValueError, IndexError):
        return True


# ── 集成函数 ──────────────────────────────────────────────────

def get_deps_from_skill(name: str) -> dict:
    """从技能名称获取依赖信息。

    Returns:
        {"dependencies": {...}, "exists": bool, "file_path": str}
    """
    from core.skill_manager import SkillManager
    mgr = SkillManager()

    # 查本地 + 市场安装
    found = None
    for skill in mgr.list_local() + mgr.list_installed_market():
        if skill.name == name:
            found = skill
            break

    if not found:
        # 可能在仓库中
        from core.skill_repo import RepoManager
        rm = RepoManager()
        results = rm.search(name)
        for r in results:
            if r["name"] == name:
                return {
                    "exists": True,
                    "source": "remote",
                    "dependencies": {},
                    "repo": r.get("repo", ""),
                    "url": r.get("url", ""),
                }
        return {"exists": False, "dependencies": {}}

    # 读取 YAML
    try:
        import yaml
        fpath = found.file_path
        if not Path(fpath).is_absolute():
            from core.skill_manager import ROOT_DIR
            fpath = str(ROOT_DIR / found.file_path)
        data = yaml.safe_load(Path(fpath).read_text(encoding="utf-8")) or {}
        return {
            "exists": True,
            "source": found.source,
            "dependencies": data.get("dependencies", {}),
            "file_path": fpath,
            "name": data.get("name", name),
        }
    except Exception as e:
        return {"exists": True, "dependencies": {}, "error": str(e)}
