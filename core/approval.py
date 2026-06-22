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
from typing import Optional, Any, ClassVar
from dataclasses import dataclass, asdict

logger = logging.getLogger("kuafu.approval")

ROOT_DIR = Path(__file__).resolve().parent.parent
APPROVALS_DIR = ROOT_DIR / "memory" / "approvals"
DENY_RULES_PATH = ROOT_DIR / "memory" / "deny_rules.json"
AUTO_MODE_PATH = ROOT_DIR / "memory" / "auto_mode_history.json"


def _is_interactive() -> bool:
    """判断当前是否在交互式终端中运行。

    检查顺序：
    1. KUAFU_GATEWAY_RUNNING=1 强制非交互（gateway 模式）
    2. 环境变量 KUAFU_INTERACTIVE=1 强制交互（kuafu.sh 启动时自动设置）
    3. TTY 检测
    """
    # Gateway 模式下即使有 TTY 也走非交互审批
    if os.environ.get("KUAFU_GATEWAY_RUNNING") == "1":
        return False
    # 有飞书或微信通道注册时，也走非交互审批
    if os.environ.get("FEISHU_APP_ID") or os.environ.get("WECHAT_ILINK_DATA_DIR"):
        return False
    return (
        os.environ.get("KUAFU_INTERACTIVE") == "1"
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
      - 重复命令抑制：同一内容指纹在短时间窗口内重复出现 → 直接批准
    """

    _history: list[AutoDecision] = []

    # 短时重复抑制：记录近期批准的内容指纹
    _recent_auto_approvals: dict[str, float] = {}  # 指纹 → 批准时间戳
    RECENT_WINDOW_SECONDS = 120  # 2 分钟内相同命令不再弹审批

    # 风险等级判定（工具名 → 风险）
    TOOL_RISK_MAP = {
        # 高风险（不可逆操作，需要人工确认）
        "delete_file": "high",
        "mcp_*": "high",       # MCP 操作默认高风险
        "terminal": "high",     # terminal 操作默认高风险（走审批推送）

        # 中风险（可逆操作，但需谨慎）
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
    AUTO_TOOLS_MEDIUM = {"write_file", "patch", "execute_code"}

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
    def _get_content_fingerprint(cls, tool: str, args: dict) -> str:
        """从工具参数中提取内容指纹，用于区分同类工具的不同操作。

        terminal → 命令的前 40 个字符（用于前缀匹配）
        write_file → 文件路径
        patch → 文件路径
        delete_file → 文件路径
        mcp_* → 前 40 个参数的 JSON 摘要
        """
        if not isinstance(args, dict):
            return ""
        if tool == "terminal":
            cmd = args.get("command", "").strip()[:40]
            return cmd
        if tool in ("write_file", "patch", "delete_file"):
            return args.get("path", "")
        if tool.startswith("mcp_"):
            arg_str = json.dumps(args, ensure_ascii=False)[:60]
            return arg_str
        return ""

    @classmethod
    def _match_fingerprint(cls, fp: str, history_reason: str) -> bool:
        """检查历史记录的 reason 是否匹配内容指纹。

        terminal 命令用前缀匹配：
          fp='pip install flask' 匹配 '[pip install flask -U] 自动通过'
        路径类用精确子串匹配：
          fp='/tmp/x' 匹配 '[/tmp/x] 拒绝'
        """
        if not fp:
            return False
        # 提取括号内的指纹
        if history_reason.startswith("[") and "]" in history_reason:
            stored_fp = history_reason[1:history_reason.index("]")]
            # terminal 命令用前缀匹配
            if fp.startswith(stored_fp) or stored_fp.startswith(fp):
                return True
        return False

    @classmethod
    def _get_approval_rate(cls, tool: str, risk: str, args: Optional[dict] = None) -> float:
        """计算某工具+风险级别+内容指纹的历史批准率。

        先按 tool+risk 粗筛，再按内容指纹精筛：
        - 有内容指纹匹配且 >= 3 条记录 → 用精确匹配的批准率
        - 有内容指纹匹配但 < 3 条记录 → 中性 0.5（数据不足）
        - 无内容指纹匹配 → 降级到工具级别的整体批准率
        """
        matching = [d for d in cls._history
                    if d.tool == tool and d.risk == risk]
        if not matching:
            return 0.5  # 无历史记录 → 中性

        # 如果有 args，尝试按内容指纹精筛
        if args is not None:
            fp = cls._get_content_fingerprint(tool, args)
            if fp:
                fp_matching = [d for d in matching if cls._match_fingerprint(fp, d.reason)]
                if len(fp_matching) >= 3:
                    approved = sum(1 for d in fp_matching if d.auto_approved)
                    return approved / len(fp_matching)
                # 内容指纹匹配记录不足 3 条
                # 检查历史中是否包含任何内容指纹（以 [ 开头）
                has_any_fingerprint = any(d.reason and d.reason.startswith("[") for d in matching)
                if len(fp_matching) == 0:
                    if has_any_fingerprint:
                        # 有指纹但没匹配到当前命令 → 确实是新命令，中性 0.5
                        return 0.5
                    # else: 历史数据太老没有指纹 → 降级到工具级

        # 降级到工具级别的整体批准率
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

        # ── 短时重复抑制：同一内容指纹 2 分钟内被批准过 → 自动通过 ──
        fp = cls._get_content_fingerprint(tool, args)
        if fp:
            now = time.time()
            # 清理过期条目
            stale = [k for k, t in cls._recent_auto_approvals.items() if now - t > cls.RECENT_WINDOW_SECONDS]
            for k in stale:
                del cls._recent_auto_approvals[k]
            if fp in cls._recent_auto_approvals:
                cls._record_decision(tool, "high", True, 0.95,
                                     f"[{fp}] 重复命令自动批准",
                                     args=args)
                return True

        # 低风险工具自动通过
        if tool in cls.AUTO_TOOLS_LOW:
            return True

        # 中风险工具自动通过（write_file/patch 等可逆操作不再审批）
        if tool in cls.AUTO_TOOLS_MEDIUM:
            return True

        # 获取风险等级
        risk = cls._get_tool_risk(tool)

        # 只有高风险才走人工审批
        if risk == "high":
            rate = cls._get_approval_rate(tool, risk, args)
            if rate > 0.9:
                return True
            if rate < 0.2:
                cls._record_decision(tool, risk, False, 1.0 - rate,
                                     f"高风险工具 {tool} 历史批准率{rate:.0%}过低",
                                     args=args)
                return False
            return None  # 高风险 + 不确定 → 人工审批

        return True

    @classmethod
    def _record_decision(cls, tool: str, risk: str, approved: bool,
                         confidence: float, reason: str,
                         args: Optional[dict] = None):
        """记录一次自动决策。"""
        import hashlib
        # 在 reason 中嵌入内容指纹，供后续 _get_approval_rate 精筛
        if args is not None:
            fp = cls._get_content_fingerprint(tool, args)
            if fp:
                reason = f"[{fp}] {reason}"
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
                        human_decision: bool, args: Optional[dict] = None):
        """记录自动决策与人工决策的不一致，用于改进分类器。"""
        cls._record_decision(
            tool=tool, risk=risk,
            approved=human_decision,
            confidence=0.5,
            reason=f"自动{'通过' if auto_decision else '拒绝'}但人工{'拒绝' if auto_decision != human_decision else '一致'}",
            args=args,
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
        """权限检查 — 向后兼容入口，委托给 PolicyManager。

        Args:
            tool: 工具名
            args: 工具参数
            context: 上下文信息（任务类型、轮次等）
            auto_override: 保留参数（PolicyManager 内部已处理）

        Returns:
            {"allowed": bool|None, "reason": str, "approach": str,
             "rule_id": str|None, "req_id": str|None, "auto": bool}
        """
        # 防御：args 可能不是 dict
        if not isinstance(args, dict):
            args = {}

        # 安全 terminal 命令直接放行
        if tool == "terminal":
            cmd = args.get("command", "")
            if _is_safe_terminal(cmd):
                return {
                    "allowed": True,
                    "reason": "✅ 安全终端命令自动通过",
                    "approach": "fast_path",
                    "rule_id": None,
                    "req_id": None,
                    "auto": True,
                }

        # 委托给 PolicyManager
        from core.policy_manager import get_policy
        policy = get_policy()
        decision = policy.decide(tool, args, context, auto_override=auto_override)

        # 转为旧格式
        return decision.to_legacy_dict()

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
        # 非交互模式 → 直接拒绝
        if not _is_interactive():
            return False

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

    # ── 决策接口（供 夸父/外部调用） ──────────────────────────────────
    # 审批事件字典：req_id → threading.Event，用于非阻塞通知
    # Event 对象挂 _approved 属性（True=批准, False=拒绝），省去磁盘读取
    _decision_events: dict = {}
    _decision_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _get_event(cls, req_id: str) -> threading.Event:
        with cls._decision_lock:
            if req_id not in cls._decision_events:
                ev = threading.Event()
                ev._approved = None  # 未知
                cls._decision_events[req_id] = ev
            return cls._decision_events[req_id]

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
        # 触发事件通知等待线程，状态直接挂载到 Event 对象
        ev = ApprovalManager._get_event(req.id)
        ev._approved = True
        ev.set()
        # 清理事件对象（等待线程已收到通知，不再需要）
        with ApprovalManager._decision_lock:
            ApprovalManager._decision_events.pop(req.id, None)
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
        # 触发事件通知等待线程，状态直接挂载到 Event 对象
        ev = ApprovalManager._get_event(req.id)
        ev._approved = False
        ev.set()
        # 清理事件对象（等待线程已收到通知，不再需要）
        with ApprovalManager._decision_lock:
            ApprovalManager._decision_events.pop(req.id, None)
        logger.info(f"❌ 审批拒绝: {req.title}")
        return True

    @staticmethod
    def _resolve(req_id: str = "") -> Optional[ApprovalRequest]:
        """解析请求 ID。空串时找最新 pending 的请求。

        支持完整 req_id 或后 8 位短 ID 匹配。
        """
        if req_id:
            req = _load(req_id)
            if req:
                return req
            # 短 ID 匹配：遍历 pending 列表，匹配后 8 位
            pending = ApprovalManager.list_pending()
            for p in pending:
                if p.id.endswith(req_id):
                    return p
            return None
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

# 全局审批回调（废弃 — 由 ToolOrchestrator._approval_callback 替代）
# 保留声明避免旧代码 import 报错，已不再被调用
ON_APPROVAL_REQUEST_CB: Optional[callable] = None


def _is_safe_terminal(cmd: str) -> bool:
    """判断 terminal 命令是否安全（只读/查询类）。

    委托给 PolicyManager 中的实现，避免重复。
    """
    from core.policy_manager import _is_safe_terminal as _policy_safe
    return _policy_safe(cmd)


def pretooluse_check(tool: str, args: dict, context: Optional[dict] = None) -> dict:
    """PreToolUse 权限检查 — 向后兼容入口。

    委托给 PolicyManager.decide()。agent_loop 现在走 ToolOrchestrator，
    此入口保留供旧代码/测试使用。
    """
    from core.policy_manager import get_policy
    policy = get_policy()
    decision = policy.decide(tool, args, context)
    return decision.to_legacy_dict()


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


def check_approval_decision(text: str) -> Optional[dict]:
    """检查用户消息是否是审批决策。

    支持格式：
    - 1 abc123 / 0 abc123（短指令）
    - 批准 abc123 / 拒绝 abc123（文字指令）
    - approve abc123 / reject abc123（英文指令）
    - list / list abc123（查看待审批/查看某条详细）
    - 1 abc123 2（逐条批准：批准合并审批中的第2项）
    - 批准 abc123 1-3（逐条批准：批准第1到第3项）

    短指令只匹配 req_id 后 8 位。
    """
    text = text.strip()
    import re

    # list 指令：查看待审批列表或某条详情
    if text.lower() in ("list", "审批列表", "待审批"):
        return {"action": "list"}
    m = re.match(r"^(?:list|查看|详情)\s+(\S+)", text, re.IGNORECASE)
    if m:
        return {"action": "list", "req_id": m.group(1)}

    # 逐条指令：1 abc123 2（批准合并审批第2项）
    m = re.match(r"^([10])\s+(\S{4,})\s+(\d+)(?:-(\d+))?$", text)
    if m:
        raw_action = m.group(1)
        raw_req_id = m.group(2)
        start_idx = int(m.group(3))
        end_idx = int(m.group(4)) if m.group(4) else start_idx
        return {
            "action": "approve" if raw_action == "1" else "reject",
            "req_id": raw_req_id, "fuzzy": True,
            "items": list(range(start_idx, end_idx + 1)),
        }

    # 短指令：1 / 0 + 短ID（4位或以上）
    m = re.match(r"^([10])\s+(\S{4,})$", text)
    if m:
        raw_action = m.group(1)
        raw_req_id = m.group(2)
        return {"action": "approve" if raw_action == "1" else "reject", "req_id": raw_req_id, "fuzzy": True}

    # 文字指令：批准 / 拒绝 + req_id
    m = re.match(r"^(批准|拒绝|approve|reject)\s+(\S+)", text, re.IGNORECASE)
    if not m:
        return None
    action_word = m.group(1).lower()
    req_id = m.group(2)
    if action_word in ("批准", "approve"):
        action = "approve"
    else:
        action = "reject"
    return {"action": action, "req_id": req_id}


def handle_approval_decision(decision: dict, chat_id: str = "", channel=None, **kwargs) -> str:
    """执行审批决策并返回结果文本。如果 channel 和 chat_id 提供，会自动回复。"""
    from core.approval import ApprovalManager
    action = decision["action"]

    # ── list 指令：查看待审批列表 ──
    if action == "list":
        req_id = decision.get("req_id", "")
        if req_id:
            # 查看某条审批详情
            req = ApprovalManager._resolve(req_id)
            if not req:
                # 尝试模糊匹配
                pending = ApprovalManager.list_pending()
                matches = [r for r in pending if r.id.endswith(req_id)]
                if len(matches) == 1:
                    req = matches[0]
                elif len(matches) > 1:
                    reply = f"⚠️ 找到 {len(matches)} 个匹配，请输入更长的 ID"
                    return _send_and_return(reply, channel, chat_id, **kwargs)
                else:
                    reply = f"❌ 未找到匹配 {req_id} 的审批"
                    return _send_and_return(reply, channel, chat_id, **kwargs)
            if req:
                lines = []
                lines.append(f"📋 **{req.title}**")
                lines.append(f"风险: {req.risk} | ID: `{req.id[-8:]}`")
                lines.append(f"---")
                if req.detail:
                    # 合并审批详情：逐行展示
                    detail_lines = req.detail.strip().split("\n")
                    for i, dl in enumerate(detail_lines, 1):
                        dl_clean = dl.strip()
                        if dl_clean:
                            lines.append(f"  {i}. {dl_clean}")
                lines.append(f"---")
                lines.append(f"回复「1 {req.id[-8:]} 序号」逐条批准")
                lines.append(f"回复「0 {req.id[-8:]} 序号」逐条拒绝")
                lines.append(f"回复「1 {req.id[-8:]}」全部批准")
                reply = "\n".join(lines)
                return _send_and_return(reply, channel, chat_id, **kwargs)
        else:
            # 查看全部待审批
            pending = ApprovalManager.list_pending()
            if not pending:
                reply = "✅ 当前没有待审批的请求"
                return _send_and_return(reply, channel, chat_id, **kwargs)
            lines = ["📋 **待审批列表**\n"]
            for i, p in enumerate(pending, 1):
                short = p.id[-8:]
                risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(p.risk, "🟡")
                title_short = p.title[:40]
                lines.append(f"{i}. {risk_icon} {title_short}  `{short}`")
            lines.append(f"\n回复「list `短ID`」查看详情")
            lines.append(f"回复「1 `短ID`」全部批准")
            reply = "\n".join(lines)
            return _send_and_return(reply, channel, chat_id, **kwargs)

    # ── 普通/逐条审批 ──
    req_id = decision["req_id"]
    fuzzy = decision.get("fuzzy", False)
    items = decision.get("items", None)  # 逐条操作索引列表

    # 模糊匹配：用短ID查找匹配的审批请求
    if fuzzy and len(req_id) < 20:
        pending = ApprovalManager.list_pending()
        matches = [r for r in pending if r.id.endswith(req_id)]
        if len(matches) == 1:
            req_id = matches[0].id
        elif len(matches) > 1:
            reply = f"⚠️ 找到 {len(matches)} 个匹配的审批，请输入更长的 ID"
            return _send_and_return(reply, channel, chat_id, **kwargs)
        else:
            reply = f"❌ 未找到匹配 {req_id} 的审批请求"
            return _send_and_return(reply, channel, chat_id, **kwargs)

    if items:
        # 逐条操作：只操作指定的索引
        req = ApprovalManager._resolve(req_id)
        if not req:
            reply = f"❌ 审批请求 {req_id} 不存在或已处理"
            return _send_and_return(reply, channel, chat_id, **kwargs)
        detail_lines = req.detail.strip().split("\n") if req.detail else []
        operated = []
        for idx in items:
            if 1 <= idx <= len(detail_lines):
                item_text = detail_lines[idx - 1].strip()
                operated.append(f"  {idx}. {item_text}")
            else:
                operated.append(f"  {idx}. (超出范围)")
        detail_preview = "\n".join(operated) if operated else "(无)"
        reply = (
            f"{'✅' if action == 'approve' else '⛔'} "
            f"{'已批准' if action == 'approve' else '已拒绝'} "
            f"{len(items)} 项操作\n"
            f"{detail_preview}"
        )
        # 注意：逐条操作并不实际执行（只返回说明），
        # 因为合并审批的物理粒度为整个 req_id。
        # 逐条用于飞书卡片查看/微信短指令查看。
        # 实际用户需要全部批/全部拒时用无 items 的指令。
        return _send_and_return(reply, channel, chat_id, **kwargs)

    if action == "approve":
        ok = ApprovalManager.approve(req_id)
        reply = f"✅ 已批准 `{req_id[-8:]}`" if ok else f"❌ 审批失败: {req_id} 不存在或已处理"
    else:
        ok = ApprovalManager.reject(req_id)
        reply = f"⛔ 已拒绝 `{req_id[-8:]}`" if ok else f"❌ 拒绝失败: {req_id} 不存在或已处理"

    return _send_and_return(reply, channel, chat_id, **kwargs)


def _send_and_return(reply: str, channel, chat_id: str = "", **kwargs) -> str:
    """发送消息到通道并返回回复文本。"""
    if channel and chat_id:
        try:
            send_kwargs = {"chat_id": chat_id}
            send_kwargs.update(kwargs)
            channel.send(reply, **send_kwargs)
        except Exception:
            pass
    return reply
