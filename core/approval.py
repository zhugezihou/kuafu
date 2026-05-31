"""
core/approval.py — Permission System（审批 + 权限 + Deny 优先规则）

职责：
提供三层安全防护：
  Layer 1: Deny 优先规则（静态黑名单 — 直接拒绝，不创建审批）
  Layer 2: 自动模式分类器（根据历史批准率 + 操作类型自动决策）
  Layer 3: 审批请求（人工确认，原 ApprovalManager 升级版）

权限检查流程：
  1. PreToolUse → check_permission(tool_name, args, context)
  2. DenyRules 先查（硬禁止 → 直接拒绝）
  3. AutoMode 查（自动决策 → 通过/拒绝无需审批）
  4. 以上都不匹配 → 走人工审批

适用范围：
  - P4 自检清理（删除孤立记忆/去重 rules/清理缓存）
  - L2+ 进化（修改 strategy/ quality.yaml / prompts.yaml）
  - 自我修复（修改核心代码 core/*.py / capabilities/*.py）
  - 策略重写（大幅度改写 strategy/ 内容）
  - 记忆重写（批量修改/删除 memory/ 内容）
  - PreToolUse 拦截的所有工具调用

不适用（无需审批/拦截）：
  - 只读操作（search_files, read_file, web_search）
  - L0/L1 进化（写一条记忆、记录日志，无副作用）
  - 自检 dry-run（只读不修改）
  - 普通任务执行按风险等级判定
"""

import json
import os
import time
import logging
import sys
import threading
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger("kuafu.approval")

ROOT_DIR = Path(__file__).resolve().parent.parent
APPROVALS_DIR = ROOT_DIR / "memory" / "approvals"
DENY_RULES_PATH = ROOT_DIR / "memory" / "deny_rules.json"
AUTO_MODE_PATH = ROOT_DIR / "memory" / "auto_mode_history.json"


def _is_interactive() -> bool:
    """判断当前是否在交互式终端中运行。

    检查顺序：
    1. KUAFFU_GATEWAY_RUNNING=1 强制非交互（gateway 模式）
    2. 环境变量 KUAFFU_INTERACTIVE=1 强制交互（kuafu.sh 启动时自动设置）
    3. TTY 检测
    """
    # Gateway 模式下即使有 TTY 也走非交互审批
    if os.environ.get("KUAFFU_GATEWAY_RUNNING") == "1":
        return False
    return (
        os.environ.get("KUAFFU_INTERACTIVE") == "1"
        or (sys.stdin.isatty() and sys.stdout.isatty())
    )


def _get_approval_timeout() -> int:
    """获取审批超时秒数（优先从配置读取，默认 300s）。"""
    try:
        from core.config import APPROVAL_TIMEOUT
        return APPROVAL_TIMEOUT
    except (ImportError, AttributeError):
        return 300


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 1: Deny 优先规则
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DenyRule:
    """一条 Deny 规则。"""
    id: str
    tool: str                # 工具名称（支持 * 通配）
    pattern: str             # 参数匹配模式（正则）
    reason: str              # 拒绝原因说明
    created_at: float
    expires_at: Optional[float] = None  # 过期时间，None 表示永久


class DenyRules:
    """Deny 规则管理器 — 硬阻止列表。

    规则优先级（按顺序检查，命中最先匹配的一条）：
      1. 精确 tool 名 + 精确参数匹配
      2. 精确 tool 名 + 模糊参数匹配
      3. tool 通配 + 精确参数匹配
      4. tool 通配 + 模糊参数匹配
    """

    _rules: list[DenyRule] = []

    @classmethod
    def load(cls) -> list[DenyRule]:
        """从磁盘加载 Deny 规则。"""
        if not DENY_RULES_PATH.exists():
            cls._rules = []
            return []
        try:
            data = json.loads(DENY_RULES_PATH.read_text(encoding="utf-8"))
            cls._rules = [DenyRule(**r) for r in data]
            return cls._rules
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Deny 规则加载失败: {e}")
            cls._rules = []
            return []

    @classmethod
    def save(cls):
        """保存 Deny 规则到磁盘。"""
        DENY_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(r) for r in cls._rules]
        DENY_RULES_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def add(cls, tool: str, pattern: str, reason: str,
            expires_at: Optional[float] = None) -> str:
        """添加一条 Deny 规则。返回规则 ID。"""
        import hashlib
        rule_id = f"deny_{int(time.time())}_{abs(hash(tool + pattern)) % 10000:04d}"
        rule = DenyRule(
            id=rule_id,
            tool=tool,
            pattern=pattern,
            reason=reason,
            created_at=time.time(),
            expires_at=expires_at,
        )
        cls._rules.append(rule)
        cls.save()
        logger.info(f"🛡️ 添加 Deny 规则 [{rule_id}] {tool}({pattern}) — {reason}")
        return rule_id

    @classmethod
    def remove(cls, rule_id: str) -> bool:
        """移除一条 Deny 规则。"""
        before = len(cls._rules)
        cls._rules = [r for r in cls._rules if r.id != rule_id]
        if len(cls._rules) < before:
            cls.save()
            logger.info(f"🗑️ 移除 Deny 规则 {rule_id}")
            return True
        return False

    @classmethod
    def check(cls, tool: str, args: dict) -> Optional[DenyRule]:
        """检查工具调用是否被 Deny 规则阻止。

        Args:
            tool: 工具名
            args: 参数字典

        Returns:
            匹配的 DenyRule（应阻止），或 None（允许通过）
        """
        now = time.time()
        import re

        for rule in cls._rules[:]:  # 遍历副本
            # 清理过期规则
            if rule.expires_at and now > rule.expires_at:
                cls._rules.remove(rule)
                cls.save()
                continue

            # tool 名匹配（支持 * 通配）
            if rule.tool != "*" and rule.tool != tool:
                if not (rule.tool.endswith("*") and tool.startswith(rule.tool[:-1])):
                    continue

            # 参数匹配：检查 args 的 JSON 字符串是否匹配模式
            arg_str = json.dumps(args, ensure_ascii=False)
            try:
                if re.search(rule.pattern, arg_str):
                    return rule
            except re.error:
                # 模式不是正则 → 尝试精确匹配
                if rule.pattern == arg_str:
                    return rule

        return None

    @classmethod
    def list_rules(cls) -> list[DenyRule]:
        """列出所有有效规则。"""
        now = time.time()
        valid = [r for r in cls._rules if not r.expires_at or now < r.expires_at]
        return valid


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2: 自动模式分类器
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AutoDecision:
    """一条自动决策记录。"""
    id: str
    tool: str
    risk: str            # low / medium / high
    context_type: str     # 上下文类型描述
    auto_approved: bool   # True=自动通过, False=自动拒绝
    confidence: float     # 0.0 ~ 1.0
    timestamp: float
    reason: str


class AutoMode:
    """自动模式分类器。

    根据历史数据自动决定是否跳过审批：
      - 低风险 + 高历史批准率 → 自动通过
      - 高风险 + 低历史批准率 → 自动拒绝
      - 模糊 → 留给人工审批
    """

    _history: list[AutoDecision] = []

    # 风险等级判定（工具名 → 风险）
    TOOL_RISK_MAP = {
        # 高风险（不可逆操作，需要人工确认）
        "delete_file": "high",
        "mcp_*": "high",       # MCP 操作默认高风险

        # 中风险（可逆操作，但需谨慎）
        "terminal": "medium",  # 具体看命令
        "write_file": "medium",
        "patch": "medium",
        "execute_code": "medium",

        # 低风险
        "web_scrape": "low",
        "web_submit": "low",
        "feishu_send": "low",
        "feishu_doc_write": "low",
        "web_search": "low",
        "read_file": "low",
        "search_files": "low",
    }

    # 可自动决策的工具
    AUTO_TOOLS_LOW = {"web_search", "search_files", "read_file", "memory_store", "memory_search", "memory_reflect",
                      "web_scrape", "web_submit", "feishu_send", "feishu_doc_write"}
    AUTO_TOOLS_MEDIUM = {"terminal", "write_file", "patch", "execute_code"}

    @classmethod
    def load(cls):
        """从磁盘加载自动决策历史。"""
        if not AUTO_MODE_PATH.exists():
            cls._history = []
            return
        try:
            data = json.loads(AUTO_MODE_PATH.read_text(encoding="utf-8"))
            cls._history = [AutoDecision(**d) for d in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            cls._history = []

    @classmethod
    def save(cls):
        """保存自动决策历史。"""
        AUTO_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(d) for d in cls._history[-100:]]  # 只保留最近 100 条
        AUTO_MODE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _get_tool_risk(cls, tool: str) -> str:
        """获取工具的风险等级。"""
        # 先精确匹配
        if tool in cls.TOOL_RISK_MAP:
            return cls.TOOL_RISK_MAP[tool]
        # 通配匹配
        for pattern, risk in cls.TOOL_RISK_MAP.items():
            if pattern.endswith("*") and tool.startswith(pattern[:-1]):
                return risk
        # 根据操作类型猜测
        if tool.startswith("write_") or tool.startswith("delete_") or tool.startswith("patch"):
            return "high"
        if tool.startswith("read_") or tool.startswith("search_"):
            return "low"
        return "medium"

    @classmethod
    def _get_approval_rate(cls, tool: str, risk: str) -> float:
        """计算某工具+风险级别的历史批准率。"""
        matching = [d for d in cls._history
                    if d.tool == tool and d.risk == risk]
        if not matching:
            return 0.5  # 无历史记录 → 中性
        approved = sum(1 for d in matching if d.auto_approved)
        return approved / len(matching)

    @classmethod
    def should_auto_approve(cls, tool: str, args: dict) -> Optional[bool]:
        """自动判断是否应通过审批。

        策略：
          - 低风险  → 自动通过
          - 中风险  → 自动通过（write_file/patch/terminal 均为可逆操作）
          - 高风险  → 走人工审批（仅 delete_file / mcp_*）
          - terminal 特别处理：检查命令内容

        Returns:
            True = 自动通过
            False = 自动拒绝
            None = 走人工审批（仅高风险时）
        """
        # 防御：args 可能不是 dict
        if not isinstance(args, dict):
            args = {}

        # 低风险工具自动通过
        if tool in cls.AUTO_TOOLS_LOW:
            return True

        # 中风险工具自动通过（write_file/patch 等可逆操作不再审批）
        if tool in cls.AUTO_TOOLS_MEDIUM:
            # terminal 特别检查危险命令
            if tool == "terminal":
                cmd = args.get("command", "")
                lower_cmd = cmd.lower().strip()
                if any(danger in lower_cmd for danger in ["rm -rf /", "dd if=", "> /dev/sda", "mkfs", "fdisk"]):
                    cls._record_decision(tool, "high", False, 0.95,
                                         f"危险命令: {cmd[:50]}")
                    return False
            return True

        # 获取风险等级
        risk = cls._get_tool_risk(tool)

        # 只有高风险才走人工审批
        if risk == "high":
            rate = cls._get_approval_rate(tool, risk)
            if rate > 0.9:
                return True
            if rate < 0.2:
                cls._record_decision(tool, risk, False, 1.0 - rate,
                                     f"高风险工具 {tool} 历史批准率{rate:.0%}过低")
                return False
            return None  # 高风险 + 不确定 → 人工审批

        return True

    @classmethod
    def _record_decision(cls, tool: str, risk: str, approved: bool,
                         confidence: float, reason: str):
        """记录一次自动决策。"""
        import hashlib
        decision = AutoDecision(
            id=f"auto_{int(time.time())}_{abs(hash(tool)) % 10000:04d}",
            tool=tool,
            risk=risk,
            context_type="auto_classifier",
            auto_approved=approved,
            confidence=confidence,
            timestamp=time.time(),
            reason=reason,
        )
        cls._history.append(decision)
        cls.save()

    @classmethod
    def record_mismatch(cls, tool: str, risk: str, auto_decision: bool,
                        human_decision: bool):
        """记录自动决策与人工决策的不一致，用于改进分类器。"""
        cls._record_decision(
            tool=tool, risk=risk,
            approved=human_decision,
            confidence=0.5,
            reason=f"自动{'通过' if auto_decision else '拒绝'}但人工{'拒绝' if auto_decision != human_decision else '一致'}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 3: 人工审批（原 ApprovalManager 升级版）
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ApprovalRequest:
    """一次审批请求的数据记录。"""
    id: str
    title: str
    detail: str
    risk: str                     # low / medium / high
    status: str                   # pending / approved / rejected / expired
    created_at: float
    decided_at: Optional[float] = None
    timeout: int = 86400          # 默认 24 小时过期
    tool: str = ""                # 触发审批的工具名
    args_snapshot: str = ""       # 工具参数快照（JSON）
    context_type: str = ""        # 上下文类型描述


def _req_id(title: str) -> str:
    return f"appr_{int(time.time())}_{abs(hash(title)) % 10000:04d}"


def _save(req: ApprovalRequest):
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    path = APPROVALS_DIR / f"{req.id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(req), f, ensure_ascii=False, indent=2)


def _load(req_id: str) -> Optional[ApprovalRequest]:
    path = APPROVALS_DIR / f"{req_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApprovalRequest(**data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


class ApprovalManager:
    """审批管理器，纯静态方法。"""

    # 终端审批全局锁：确保同时只有一个终端提示在等待用户输入
    _terminal_lock = threading.Lock()

    # ── 权限检查主入口（三合一） ──────────────────────────────────

    @staticmethod
    def check_permission(
        tool: str,
        args: dict,
        context: Optional[dict] = None,
        auto_override: bool = True,
    ) -> dict:
        """权限检查主入口 — Layer 1→2→3 链式检查。

        Args:
            tool: 工具名
            args: 工具参数
            context: 上下文信息（任务类型、轮次等）
            auto_override: 是否启用自动模式（Layer 2）

        Returns:
            {"allowed": bool, "reason": str, "approach": str,
             "rule_id": str|None, "req_id": str|None, "auto": bool}
        """
        # 防御：args 可能不是 dict（Mock LLM 或工具调用参数异常时）
        if not isinstance(args, dict):
            args = {}

        # Layer 1: Deny 优先规则
        denied = DenyRules.check(tool, args)
        if denied:
            return {
                "allowed": False,
                "reason": f"🛡️ Deny 规则阻止: {denied.reason}",
                "approach": "deny_rule",
                "rule_id": denied.id,
                "req_id": None,
                "auto": True,
            }

        # Layer 2: 自动模式
        if auto_override:
            auto = AutoMode.should_auto_approve(tool, args)
            if auto is True:
                return {
                    "allowed": True,
                    "reason": "✅ 自动模式通过（低风险/高批准率）",
                    "approach": "auto_approve",
                    "rule_id": None,
                    "req_id": None,
                    "auto": True,
                }
            if auto is False:
                return {
                    "allowed": False,
                    "reason": "⛔ 自动模式拒绝（高风险/低批准率）",
                    "approach": "auto_reject",
                    "rule_id": None,
                    "req_id": None,
                    "auto": True,
                }

        # Layer 3: 人工审批
        risk = AutoMode._get_tool_risk(tool) if hasattr(AutoMode, '_get_tool_risk') else "medium"

        # 只保留高风险走人工审批，中/低风险自动通过
        if risk not in ("high",):
            return {
                "allowed": True,
                "reason": f"✅ 风险 {risk} 自动通过（仅高风险需审批）",
                "approach": "auto_approve",
                "rule_id": None,
                "req_id": None,
                "auto": True,
            }

        title = f"审批工具调用: {tool}"
        if tool == "terminal":
            title = f"终端: {args.get('command', '')[:60]}"
        detail = json.dumps(args, ensure_ascii=False, indent=2)[:500]

        # 无论是否交互，先提交审批请求并推送通道通知
        req_id = ApprovalManager.submit(
            title=title,
            detail=detail,
            risk=risk,
            tool=tool,
            args_snapshot=json.dumps(args, ensure_ascii=False),
            context_type=f"check_permission_{tool}",
        )
        print(f"\n[Gateway] 🔐 审批请求已提交 (ID: {req_id})", flush=True)

        # 交互终端 → 阻塞等待用户 y/N 决策
        if _is_interactive():
            allowed = ApprovalManager.terminal_prompt(
                title=title,
                detail=detail,
                risk=risk,
                timeout=_get_approval_timeout(),
            )
            return {
                "allowed": allowed,
                "reason": f"{'✅' if allowed else '⛔'} 终端审批: {title}",
                "approach": "terminal_prompt",
                "rule_id": None,
                "req_id": req_id,
                "auto": False,
            }
        else:
            # Gateway/cron 模式：等待通道回复审批
            return {
                "allowed": None,  # 待人工决策
                "reason": f"🟡 需要审批 (ID: {req_id})",
                "approach": "pending_approval",
                "rule_id": None,
                "req_id": req_id,
                "auto": False,
            }

        raise RuntimeError("审批决策遗漏")  # 不应到达

    # ── 提交审批 ──────────────────────────────────────────────────

    @staticmethod
    def submit(
        title: str,
        detail: str = "",
        risk: str = "medium",
        timeout: int = 86400,
        tool: str = "",
        args_snapshot: str = "",
        context_type: str = "",
    ) -> str:
        """非阻塞提交审批请求。

        写入文件后立即返回，不等待用户决策。
        适用于后台线程、PreToolUse 等不能阻塞的场景。
        返回请求 ID，供后续查询。
        """
        req_id = _req_id(title)
        req = ApprovalRequest(
            id=req_id,
            title=title,
            detail=detail,
            risk=risk,
            status="pending",
            created_at=time.time(),
            timeout=timeout,
            tool=tool,
            args_snapshot=args_snapshot,
            context_type=context_type,
        )
        _save(req)
        logger.info(f"📋 提交审批 [{risk.upper()}] {title}  (ID: {req_id})")
        return req_id

    @staticmethod
    def terminal_prompt(
        title: str,
        detail: str = "",
        risk: str = "medium",
        timeout: int = 300,
    ) -> bool:
        """终端交互式审批。

        打印报告 + 显示 [y/N] 选项，等待用户输入。
        全局锁确保同时只有一个终端提示等待用户输入。
        适用于夸父对话中需要用户当场确认的场景。
        返回 True（批准）/ False（拒绝/超时）。
        """
        with ApprovalManager._terminal_lock:
            import sys
            from select import select

            req_id = _req_id(title)
            req = ApprovalRequest(
                id=req_id,
                title=title,
                detail=detail,
                risk=risk,
                status="pending",
                created_at=time.time(),
                timeout=timeout,
            )

            # 打印审批信息
            risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk, "🟡")
            print(f"\n{'='*60}")
            print(f"  {risk_icon}  审批请求 [{risk.upper()}]")
            print(f"  标题: {title}")
            print(f"  详情: ")
            for line in detail.strip().split("\n"):
                print(f"    {line}")
            print(f"{'='*60}")
            print()

            print(f"是否执行？ [y/N] （{timeout}s 后自动拒绝）: ", end="", flush=True)

            # 用 select + sys.stdin.readline 替代 input()，避免与主循环的 input() 冲突
            # 每 10 秒重打一次提示，避免异步线程输出打乱输入行
            try:
                elapsed = 0
                answer = ""
                while elapsed < timeout:
                    ready, _, _ = select([sys.stdin], [], [], 10)
                    if ready:
                        answer = sys.stdin.readline().strip().lower()
                        break
                    elapsed += 10
                    remaining = timeout - elapsed
                    if remaining > 0:
                        print(f"\r是否执行？ [y/N] （{remaining}s 后自动拒绝）: ", end="", flush=True)
                    else:
                        answer = ""
            except (EOFError, KeyboardInterrupt):
                answer = ""

            approved = answer in ("y", "yes", "是", "批准", "确认", "ok")
            req.status = "approved" if approved else "rejected"
            req.decided_at = time.time()
            _save(req)

            if approved:
                print(f"  ✅ 已批准: {title}")
            else:
                print(f"  ⏭️  已跳过: {title}")

            logger.info(f"{'✅' if approved else '❌'} 审批 {'通过' if approved else '拒绝'}: {title}")
            return approved

    # ── 决策接口（供 Hermes/外部调用） ──────────────────────────────────

    @staticmethod
    def approve(req_id: str = "") -> bool:
        """批准一个待审批请求。req_id 为空时找最新 pending 的请求。"""
        req = ApprovalManager._resolve(req_id)
        if req is None:
            logger.error("没有可批准的待审批请求")
            return False
        if req.status != "pending":
            logger.warning(f"审批请求 {req.id} 状态为 {req.status}，不能重复审批")
            return False
        req.status = "approved"
        req.decided_at = time.time()
        _save(req)
        logger.info(f"✅ 审批通过: {req.title}")
        return True

    @staticmethod
    def reject(req_id: str = "") -> bool:
        """拒绝一个待审批请求。req_id 为空时找最新 pending 的请求。"""
        req = ApprovalManager._resolve(req_id)
        if req is None:
            logger.error("没有可拒绝的待审批请求")
            return False
        if req.status != "pending":
            logger.warning(f"审批请求 {req.id} 状态为 {req.status}，不能重复审批")
            return False
        req.status = "rejected"
        req.decided_at = time.time()
        _save(req)
        logger.info(f"❌ 审批拒绝: {req.title}")
        return True

    @staticmethod
    def _resolve(req_id: str = "") -> Optional[ApprovalRequest]:
        """解析请求 ID。空串时找最新 pending 的请求。"""
        if req_id:
            return _load(req_id)
        pending = ApprovalManager.list_pending()
        if not pending:
            return None
        pending.sort(key=lambda r: r.created_at, reverse=True)
        return pending[0]

    # ── 查询 ──────────────────────────────────────────────────────────

    @staticmethod
    def list_pending() -> list[ApprovalRequest]:
        """获取所有待审批的请求。"""
        if not APPROVALS_DIR.exists():
            return []
        now = time.time()
        pending = []
        for f in sorted(APPROVALS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                req = ApprovalRequest(**data)
                if req.status == "pending":
                    if now - req.created_at > req.timeout:
                        req.status = "expired"
                        req.decided_at = now
                        _save(req)
                        continue
                    pending.append(req)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return pending

    @staticmethod
    def list_recent(limit: int = 10) -> list[ApprovalRequest]:
        """列出最近的审批历史。"""
        if not APPROVALS_DIR.exists():
            return []
        items = []
        for f in sorted(APPROVALS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                items.append(ApprovalRequest(**data))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if len(items) >= limit:
                break
        return items


# ── 快速权限检查（单函数入口，供 PreToolUse 集成调用） ────────────────────

# 全局对像：PreToolUse 检查器
_pretooluse_cache: dict = {}

# 全局审批回调（由 Web UI 等注入，接收 tool, args, req_id）
ON_APPROVAL_REQUEST_CB: Optional[callable] = None


def pretooluse_check(tool: str, args: dict, context: Optional[dict] = None) -> dict:
    """PreToolUse 权限检查 — 装饰器/钩子入口。

    这是 agent_loop 在每次工具调用前调用的单函数入口。
    使用缓存避免同一工具+参数在连续轮次中重复检查。
    """
    # 加载配置（延迟初始化）
    if not _pretooluse_cache:
        DenyRules.load()
        AutoMode.load()

    result = ApprovalManager.check_permission(tool, args, context)

    # 如果有审批请求已提交，通过全局回调通知通道推送
    req_id = result.get("req_id")
    if req_id and ON_APPROVAL_REQUEST_CB:
        try:
            ON_APPROVAL_REQUEST_CB(tool, args, req_id)
        except Exception:
            pass

    return result


# ── 格式化输出（供展示） ────────────────────────────────────────────────

def format_approval(req: ApprovalRequest) -> str:
    """格式化一条审批请求为可读文本。"""
    risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(req.risk, "🟡")
    return (
        f"{risk_icon} **{req.title}**  `{req.id}`\n"
        f"  {req.detail[:300]}"
    )


def format_pending_summary() -> str:
    """返回待审批请求摘要。无待审批时返回空字符串。"""
    pending = ApprovalManager.list_pending()
    if not pending:
        return ""
    lines = ["**📋 夸父待审批事项**\n"]
    for i, req in enumerate(pending, 1):
        risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(req.risk, "🟡")
        detail_short = req.detail[:120].replace("\n", " ")
        lines.append(
            f"{i}. {risk_icon} **{req.title}**\n"
            f"   `{req.id}`\n"
            f"   {detail_short}..."
        )
    lines.append("\n输入「批准 `{id}`」或「拒绝 `{id}`」决策")
    return "\n".join(lines)
