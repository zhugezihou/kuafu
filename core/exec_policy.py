"""
core/exec_policy.py — 命令执行策略管理器（规则文件 + 命令降级）

源自 Codex CLI ExecPolicyManager：
  - 规则文件（.rules）定义 allow / prompt / forbid 策略
  - 命令降级解析：bash -lc "cmd" → cmd
  - 无锁热加载（ArcSwap 模式）
"""

import json
import re
import time
import logging
from pathlib import Path
from enum import Enum
from typing import Optional, Union

logger = logging.getLogger("kuafu.exec_policy")

ROOT_DIR = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT_DIR / "memory" / "rules"


class RuleAction(Enum):
    ALLOW = "allow"
    PROMPT = "prompt"
    FORBID = "forbid"


class ExecRule:
    """单条执行策略规则。"""

    def __init__(self, rule_id: str, pattern: str,
                 action: Union[str, RuleAction],
                 reason: str = "", enabled: bool = True,
                 created_at: Optional[float] = None):
        self.id = rule_id
        self._pattern_str = pattern
        self._regex = re.compile(pattern, re.IGNORECASE)
        if isinstance(action, str):
            self.action = RuleAction(action)
        else:
            self.action = action
        self.reason = reason
        self.enabled = enabled
        self.created_at = created_at or time.time()

    def matches(self, command: str) -> bool:
        return bool(self._regex.search(command))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "pattern": self._pattern_str,
            "action": self.action.value, "reason": self.reason,
            "enabled": self.enabled, "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecRule":
        return cls(
            rule_id=d["id"], pattern=d["pattern"], action=d["action"],
            reason=d.get("reason", ""), enabled=d.get("enabled", True),
            created_at=d.get("created_at"),
        )


# =========================================================================
# 命令降级解析
# =========================================================================

def canonicalize_command(command: str) -> list[str]:
    """将 shell 命令降级为实际执行的命令列表。

    处理：bash -lc "cmd" → cmd, sudo cmd → cmd, time cmd → cmd
    """
    results = [command]
    stripped = command.strip()

    # bash/sh -c/l/c 'cmd'
    m = re.match(
        r"^(?:bash|sh|zsh|ksh)\s+(?:-[a-z]*[cl][a-z]*\s+)?['\"]?(.+?)['\"]?\s*$",
        stripped, re.IGNORECASE,
    )
    if m:
        inner = m.group(1).strip()
        results.append(inner)
        results.extend(canonicalize_command(inner))

    # sudo cmd
    m = re.match(r"^sudo\s+(.+)$", stripped, re.IGNORECASE)
    if m:
        inner = m.group(1).strip()
        results.append(inner)
        results.extend(canonicalize_command(inner))

    # time cmd
    m = re.match(r"^time\s+(.+)$", stripped, re.IGNORECASE)
    if m:
        inner = m.group(1).strip()
        results.append(inner)
        results.extend(canonicalize_command(inner))

    return results


# =========================================================================
# 策略管理器
# =========================================================================

class ExecPolicyManager:
    """命令执行策略管理器。规则文件 + 内置规则。"""

    def __init__(self, rules_dir: Optional[Path] = None):
        self._rules_dir = rules_dir or RULES_DIR
        self._custom_rules: list[ExecRule] = []
        self._loaded = False

    def load(self):
        """从 memory/rules/*.rules 加载。"""
        if self._loaded:
            return
        self._rules_dir.mkdir(parents=True, exist_ok=True)
        loaded = []
        for f in sorted(self._rules_dir.glob("*.rules")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                loaded.extend(ExecRule.from_dict(item) for item in data.get("rules", []))
            except Exception as e:
                logger.warning(f"规则文件 {f.name} 加载失败: {e}")
        self._custom_rules = loaded
        self._loaded = True
        logger.info(f"📜 加载 {len(loaded)} 条自定义执行策略")

    def save(self):
        """保存到 custom.rules。"""
        self._rules_dir.mkdir(parents=True, exist_ok=True)
        path = self._rules_dir / "custom.rules"
        path.write_text(
            json.dumps({"rules": [r.to_dict() for r in self._custom_rules]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_rule(self, rule_id: str, pattern: str,
                 action: Union[str, RuleAction], reason: str = "") -> ExecRule:
        rule = ExecRule(rule_id, pattern, action, reason)
        self._custom_rules = [r for r in self._custom_rules if r.id != rule_id]
        self._custom_rules.append(rule)
        self.save()
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self._custom_rules)
        self._custom_rules = [r for r in self._custom_rules if r.id != rule_id]
        if len(self._custom_rules) < before:
            self.save()
            return True
        return False

    def list_rules(self) -> list[ExecRule]:
        self.load()
        return self._builtin_rules() + self._custom_rules

    def check(self, command: str) -> tuple[RuleAction, str, str]:
        """检查命令策略。返回 (action, rule_id, reason)。"""
        self.load()
        candidates = canonicalize_command(command)
        all_rules = self._builtin_rules() + self._custom_rules
        for rule in all_rules:
            if not rule.enabled:
                continue
            for cmd in candidates:
                if rule.matches(cmd):
                    return (rule.action, rule.id, rule.reason)
        return (RuleAction.ALLOW, "", "")

    # ── 内置规则 ──

    def _builtin_rules(self) -> list[ExecRule]:
        return [
            ExecRule("blt_rm_rf", r"\brm\s+(-rf?|--recursive)\b",
                     RuleAction.PROMPT, "递归删除文件"),
            ExecRule("blt_mkfs", r"\bmkfs\b", RuleAction.FORBID, "格式化磁盘"),
            ExecRule("blt_dd", r"\bdd\s+if=", RuleAction.FORBID, "dd 覆盖磁盘"),
            ExecRule("blt_shutdown", r"\b(shutdown|reboot|poweroff|init\s+0)\b",
                     RuleAction.FORBID, "关机/重启"),
            ExecRule("blt_fork", r":\(\)\s*\{", RuleAction.FORBID, "Fork 炸弹"),
            ExecRule("blt_git_force", r"\bgit\s+push\b.*--force\b",
                     RuleAction.PROMPT, "强制推送重写远程历史"),
            ExecRule("blt_git_reset", r"\bgit\s+reset\s+--hard\b",
                     RuleAction.PROMPT, "丢弃本地更改"),
            ExecRule("blt_curl_sh", r"\b(curl|wget)\b.*\|.+sh\b",
                     RuleAction.PROMPT, "远程脚本直接执行"),
            ExecRule("blt_write_dev", r">\s+/dev/sd[a-z]", RuleAction.FORBID, "写入块设备"),
        ]
