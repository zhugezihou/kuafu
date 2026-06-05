"""
夸父技能沙箱 (Skill Sandbox)

技能沙箱不是隔离的容器，而是在 LLM 执行技能步骤时注入安全规则，
限制文件操作范围、命令危险级别、网络请求权限，确保技能执行不越界。

三级沙箱策略：
  - permit: 允许（默认，轻度提示）
  - ask:    询问（需要用户确认敏感操作）
  - deny:   拒绝（禁止特定危险操作）

设计原则：
  - 零新增依赖（仅标准库）
  - 不创建独立的隔离环境（容器/虚拟机）
  - 通过安全规则注入 + 终端命令过滤实现防护
  - 与现有 safety.py 安全层集成
  - 所有操作可降级（沙箱有问题时不阻滞执行）
"""

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent

# ── 沙箱等级 ──────────────────────────────────────────────────

SANDBOX_PERMIT = "permit"   # 允许，有轻度提示
SANDBOX_ASK = "ask"         # 需要用户确认
SANDBOX_DENY = "deny"       # 禁止


# ── 沙箱配置 ──────────────────────────────────────────────────

@dataclass
class SandboxConfig:
    """单个技能的沙箱配置。"""

    # 沙箱策略级别
    terminal_level: str = SANDBOX_PERMIT   # 终端命令
    file_write_level: str = SANDBOX_ASK    # 文件写入
    network_level: str = SANDBOX_PERMIT    # 网络请求
    pip_install_level: str = SANDBOX_ASK   # pip 安装包
    git_push_level: str = SANDBOX_DENY     # git push

    # 允许写入的目录（超出范围的需要 ask/deny）
    allowed_write_dirs: list[str] = field(default_factory=lambda: [
        "strategy", "skills", "memory", "tests", "logs", "downloads",
    ])

    # 禁止读取的敏感文件
    forbidden_read_paths: list[str] = field(default_factory=lambda: [
        ".env", "*.pem", "*.key", "id_rsa*", "*.token",
    ])

    # 允许访问的域名白名单（空 = 全部允许）
    allowed_domains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "terminal": self.terminal_level,
            "file_write": self.file_write_level,
            "network": self.network_level,
            "pip_install": self.pip_install_level,
            "git_push": self.git_push_level,
        }


# ── 默认沙箱配置 ──────────────────────────────────────────────

# 技能分类 → 默认沙箱策略
CATEGORY_SANDBOX: dict[str, dict] = {
    "coding": {
        "terminal": SANDBOX_PERMIT,
        "file_write": SANDBOX_ASK,
        "pip_install": SANDBOX_ASK,
        "git_push": SANDBOX_DENY,
    },
    "web": {
        "terminal": SANDBOX_PERMIT,
        "network": SANDBOX_PERMIT,
        "file_write": SANDBOX_ASK,
    },
    "devops": {
        "terminal": SANDBOX_PERMIT,
        "git_push": SANDBOX_ASK,
        "pip_install": SANDBOX_ASK,
        "file_write": SANDBOX_ASK,
    },
    "research": {
        "terminal": SANDBOX_PERMIT,
        "file_write": SANDBOX_PERMIT,
        "network": SANDBOX_PERMIT,
    },
    "media": {
        "terminal": SANDBOX_PERMIT,
        "file_write": SANDBOX_PERMIT,
        "network": SANDBOX_PERMIT,
    },
    "writing": {
        "terminal": SANDBOX_PERMIT,
        "file_write": SANDBOX_PERMIT,
    },
    "data-science": {
        "terminal": SANDBOX_PERMIT,
        "pip_install": SANDBOX_ASK,
        "file_write": SANDBOX_ASK,
    },
    "general": {
        "terminal": SANDBOX_PERMIT,
        "file_write": SANDBOX_ASK,
        "pip_install": SANDBOX_ASK,
    },
}

# 默认配置
DEFAULT_SANDBOX_CONFIG = SandboxConfig()


# ── 安全规则生成 ──────────────────────────────────────────────

def build_sandbox_rules(skill_data: dict) -> dict:
    """根据技能数据生成沙箱规则。

    Args:
        skill_data: 技能 YAML 数据（包含 category、name、dependencies 等）

    Returns:
        {
            "sandbox_level": "permit" / "ask" / "deny",
            "prompt_rules": str,   # 注入到 system prompt 的安全规则文本
            "config": SandboxConfig,
            "warnings": [str],
        }
    """
    name = skill_data.get("name", "未知技能")
    category = skill_data.get("category", "general")
    deps = skill_data.get("dependencies", {})

    # 1. 按分类获取基础策略
    base_policy = CATEGORY_SANDBOX.get(category, CATEGORY_SANDBOX["general"])

    # 映射简写键 → SandboxConfig 字段名
    _KEY_MAP = {
        "terminal": "terminal_level",
        "file_write": "file_write_level",
        "network": "network_level",
        "pip_install": "pip_install_level",
        "git_push": "git_push_level",
    }
    mapped_policy = {}
    for k, v in base_policy.items():
        mapped_policy[_KEY_MAP.get(k, k)] = v

    config = SandboxConfig()
    for k, v in mapped_policy.items():
        if hasattr(config, k):
            setattr(config, k, v)

    # 2. 根据依赖自动调整策略
    raw_deps = deps or {}
    packages = raw_deps.get("packages", [])
    tools = raw_deps.get("tools", [])

    # 如果依赖中包含系统级工具，加强限制
    system_tools = {"sudo", "docker", "systemctl", "service", "fdisk", "mkfs"}
    if system_tools & set(tools):
        config.terminal_level = SANDBOX_ASK

    # 如果依赖不包含网络包，加强网络限制
    network_packages = {"requests", "httpx", "aiohttp", "urllib3", "curl", "wget"}
    if not (network_packages & set(packages)):
        config.network_level = SANDBOX_ASK

    # 3. 评估整体等级
    levels = [config.terminal_level, config.file_write_level,
              config.network_level, config.pip_install_level]
    if SANDBOX_DENY in levels:
        sandbox_level = SANDBOX_DENY
    elif SANDBOX_ASK in levels:
        sandbox_level = SANDBOX_ASK
    else:
        sandbox_level = SANDBOX_PERMIT

    # 4. 生成注入 prompt 的安全规则
    prompt_rules = _generate_prompt_rules(name, config, category)

    # 5. 警告
    warnings = _generate_warnings(name, config, category)

    return {
        "sandbox_level": sandbox_level,
        "prompt_rules": prompt_rules,
        "config": config.to_dict(),
        "warnings": warnings,
    }


def _generate_prompt_rules(name: str, config: SandboxConfig,
                            category: str) -> str:
    """生成注入 system prompt 的安全规则文本。"""
    rules = [f"📋 技能「{name}」沙箱规则 (分类: {category}):"]

    level_labels = {
        SANDBOX_PERMIT: "✅ 允许",
        SANDBOX_ASK:    "⚠️ 需确认",
        SANDBOX_DENY:   "❌ 禁止",
    }

    rules.append(f"  • 终端命令: {level_labels[config.terminal_level]}")
    rules.append(f"  • 文件写入: {level_labels[config.file_write_level]}")
    rules.append(f"  • 网络请求: {level_labels[config.network_level]}")
    rules.append(f"  • 安装包:   {level_labels[config.pip_install_level]}")
    rules.append(f"  • Git推送:  {level_labels[config.git_push_level]}")

    if config.forbidden_read_paths:
        rules.append(f"  • 禁止读取: {', '.join(config.forbidden_read_paths)}")

    if config.allowed_write_dirs:
        rules.append(f"  • 允许写入到: {', '.join(config.allowed_write_dirs)}")

    rules.append("")
    return "\n".join(rules)


def _generate_warnings(name: str, config: SandboxConfig,
                        category: str) -> list[str]:
    """根据配置生成警告列表。"""
    warnings = []

    if config.git_push_level == SANDBOX_DENY:
        warnings.append("技能被禁止执行 git push 操作")

    if config.file_write_level == SANDBOX_DENY:
        warnings.append("技能被禁止写入文件")

    if config.pip_install_level == SANDBOX_DENY:
        warnings.append("技能被禁止安装 Python 包")

    return warnings


# ── 命令安全过滤 ──────────────────────────────────────────────

# 危险命令模式（执行前过滤）
FORBIDDEN_PATTERNS = [
    re.compile(r'\brm\s+(-rf?|--recursive)\s+(/\s*$|/\*)'),
    re.compile(r'\bsudo\s+rm\b'),
    re.compile(r'\bmkfs\.'),
    re.compile(r'\bdd\s+if=/\s+of='),
    re.compile(r'>\s*/dev/(hd|sd|vd|nvme)'),
    re.compile(r':\(\)\s*\{'),
    re.compile(r'\bchmod\s+777\s+/'),
    re.compile(r'\bwget\s+.+?\|\s*(bash|sh)\b'),
    re.compile(r'\bcurl\s+.+?\|\s*(bash|sh)\b'),
]


def filter_command(command: str, config: Optional[SandboxConfig] = None) -> dict:
    """过滤终端命令，返回是否允许。

    Args:
        command: 要执行的终端命令
        config: 沙箱配置（可选，不指定则用最严格策略）

    Returns:
        {"allowed": bool, "reason": str, "risk": str}
        risk: "safe" / "attention" / "danger" / "forbidden"
    """
    cfg = config or DEFAULT_SANDBOX_CONFIG

    # 1. 禁止模式检查
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(command):
            return {
                "allowed": False,
                "reason": f"命令被沙箱禁止: {pattern.pattern[:50]}",
                "risk": "forbidden",
            }

    # 2. pip install 策略
    if cfg.pip_install_level == SANDBOX_DENY and re.search(r'\bpip\s+install\b', command):
        return {
            "allowed": False,
            "reason": "沙箱禁止安装 Python 包",
            "risk": "denied",
        }

    if cfg.pip_install_level == SANDBOX_ASK and re.search(r'\bpip\s+install\b', command):
        return {
            "allowed": True,
            "reason": "需要用户确认安装 Python 包",
            "risk": "attention",
        }

    # 3. git push 策略
    if cfg.git_push_level == SANDBOX_DENY and re.search(r'\bgit\s+push\b', command):
        return {
            "allowed": False,
            "reason": "沙箱禁止 git push",
            "risk": "denied",
        }

    if cfg.git_push_level == SANDBOX_ASK and re.search(r'\bgit\s+push\b', command):
        return {
            "allowed": True,
            "reason": "需要用户确认 git push",
            "risk": "attention",
        }

    # 4. 文件写入检查
    if cfg.file_write_level == SANDBOX_DENY:
        write_pattern = re.compile(r'[>|]\\s*[^|>]')
        if write_pattern.search(command):
            return {
                "allowed": False,
                "reason": "沙箱禁止文件写入操作",
                "risk": "denied",
            }

    return {"allowed": True, "reason": "", "risk": "safe"}


# ── 集成函数 ──────────────────────────────────────────────────

def get_sandbox_config(skill_name: str) -> Optional[dict]:
    """从技能名称获取沙箱配置。

    Returns:
        {"sandbox_level": ..., "prompt_rules": ..., "config": ..., "warnings": [...]}
        或 None（技能不存在）
    """
    from core.skill_manager import SkillManager
    mgr = SkillManager()

    found = None
    for skill in mgr.list_local() + mgr.list_installed_market():
        if skill.name == skill_name:
            found = skill
            break
    if not found:
        return None

    try:
        import yaml
        from core.skill_manager import ROOT_DIR as SKILL_MGR_ROOT
        fpath = found.file_path
        if not Path(fpath).is_absolute():
            fpath = str(SKILL_MGR_ROOT / found.file_path)
        data = yaml.safe_load(Path(fpath).read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    return build_sandbox_rules(data)


def sandbox_for_category(category: str) -> dict:
    """获取某个分类的默认沙箱配置。"""
    base = CATEGORY_SANDBOX.get(category, CATEGORY_SANDBOX["general"])

    _KEY_MAP = {
        "terminal": "terminal_level",
        "file_write": "file_write_level",
        "network": "network_level",
        "pip_install": "pip_install_level",
        "git_push": "git_push_level",
    }
    config = SandboxConfig()
    for k, v in base.items():
        mapped = _KEY_MAP.get(k, k)
        if hasattr(config, mapped):
            setattr(config, mapped, v)

    prompt = _generate_prompt_rules(f"<{category} 技能>", config, category)
    return {
        "config": config.to_dict(),
        "prompt_rules": prompt,
    }
