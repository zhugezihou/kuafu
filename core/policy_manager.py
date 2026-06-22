"""
core/policy_manager.py — 统一策略管理器

将 DenyRules + AutoMode + ApprovalManager + 安全命令白名单
合并为一个统一的 PolicyManager，对外暴露三个清晰的方法：

  - decide(tool, args, context) → PolicyDecision  # 三阶段决策（供 orchestrator 使用）
  - submit_approval(title, ...) → str              # 非阻塞提交审批
  - resolve_approval(req_id, action) → bool        # 审批决策

向后兼容：
  - pretooluse_check(tool, args, context) 保持功能不变，内部调用 PolicyManager
  - ApprovalManager 保留，作为 PolicyManager 的内部组件
  - 现有 import 不受影响
"""

import json
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.approval import DenyRules, AutoMode, ApprovalManager

logger = logging.getLogger("kuafu.policy")


# =========================================================================
# 统一决策结果类型
# =========================================================================

class PolicyAction(Enum):
    """策略决策动作"""
    ALLOW = "allow"              # 放行
    DENY = "deny"               # 拒绝（硬阻止）
    ESCALATE = "escalate"       # 需要人工审批
    FAST_PATH = "fast_path"     # 安全命令快速放行


@dataclass
class PolicyDecision:
    """统一策略决策结果。"""
    action: PolicyAction
    reason: str = ""
    rule_id: Optional[str] = None
    req_id: Optional[str] = None
    auto: bool = True
    risk: str = "low"
    metadata: dict = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "✅ 放行", **kwargs) -> "PolicyDecision":
        return cls(action=PolicyAction.ALLOW, reason=reason, **kwargs)

    @classmethod
    def deny(cls, reason: str = "🛡️ 策略拒绝", rule_id: Optional[str] = None,
             **kwargs) -> "PolicyDecision":
        return cls(action=PolicyAction.DENY, reason=reason, rule_id=rule_id,
                   auto=True, **kwargs)

    @classmethod
    def escalate(cls, reason: str = "🟡 需要审批", req_id: Optional[str] = None,
                 risk: str = "medium", **kwargs) -> "PolicyDecision":
        return cls(action=PolicyAction.ESCALATE, reason=reason, req_id=req_id,
                   risk=risk, auto=False, **kwargs)

    @classmethod
    def fast_path(cls, reason: str = "🔓 安全命令快速放行", **kwargs) -> "PolicyDecision":
        return cls(action=PolicyAction.FAST_PATH, reason=reason, **kwargs)

    def to_legacy_dict(self) -> dict:
        """转为 agent_loop 兼容的旧格式。"""
        action_map = {
            PolicyAction.ALLOW: True,
            PolicyAction.DENY: False,
            PolicyAction.ESCALATE: None,
            PolicyAction.FAST_PATH: True,
        }
        approach_map = {
            PolicyAction.ALLOW: "auto_approve",
            PolicyAction.DENY: "deny_rule",
            PolicyAction.ESCALATE: "pending_approval",
            PolicyAction.FAST_PATH: "fast_path",
        }
        return {
            "allowed": action_map.get(self.action, True),
            "reason": self.reason,
            "approach": approach_map.get(self.action, "auto_approve"),
            "rule_id": self.rule_id,
            "req_id": self.req_id,
            "auto": self.auto,
        }


# =========================================================================
# 安全命令快检（原 agent_loop 中的 _safe 白名单）
# 安全命令快检（原 agent_loop 中的 _safe 白名单）
# 委托到 Platform 获取跨平台列表

from core.platform import Platform
_SAFE_COMMANDS = Platform.safe_commands()


def _is_safe_terminal(cmd: str) -> bool:
    """判断是否为安全（只读）终端命令。"""
    if not isinstance(cmd, str):
        return False
    stripped = cmd.strip()
    if not stripped:
        return False

    lower = stripped.lower()

    # ── 明确安全的 kuafu 管理命令（不触发审批） ──
    # 这些命令是夸父自身的管理 CL，只读或受控操作，无需人工审批
    KUAFU_SAFE_PREFIXES = (
        "kuafu cron list",
        "kuafu cron remove",
        "kuafu cron stop",
        "kuafu cron start",
        "kuafu cron status",
        "kuafu gateway status",
        "kuafu gateway stop",
        "kuafu gateway start",
        "kuafu sessions list",
        "kuafu restart",
        "bash /home/asus/kuafu/restart.sh",
        "curl -X POST http://localhost:8765/api/restart",
        "curl -X POST http://127.0.0.1:8765/api/restart",
    )
    for prefix in KUAFU_SAFE_PREFIXES:
        if lower.startswith(prefix):
            return True

    # ── 额外的安全命令（无法被 _SAFE_COMMANDS 匹配） ──
    EXTRA_SAFE_PREFIXES = (
        "python3 -c", "python -c",
        "docker ps", "docker images",
    )
    for prefix in EXTRA_SAFE_PREFIXES:
        if lower.startswith(prefix):
            return True

    # 先检测危险关键词——包含这些的命令需要审批
    danger_kw = [
        "pip install", "pip3 install", "npm install", "apt ", "apt-get",
        "wget ", "curl -o", "curl -O", "> ", ">> ",
        "| python3", "| python ", "| bash", "| sh",
        "sudo ", "chmod ", "chown ", "kill ", "rm ", "mv ",
        "dd if=", "mkfs", "fdisk", "write_file",
        "playwright install", "playwright open",
    ]
    for kw in danger_kw:
        if kw in lower:
            return False

    # 安全前缀匹配
    if any(lower.startswith(p) for p in _SAFE_COMMANDS):
        return True

    # 没被识别为危险，也没匹配安全前缀——需要审批
    return False


# =========================================================================
# 统一策略管理器
# =========================================================================

class PolicyManager:
    """统一策略管理器——三层策略决策 + 审批提交/解决。

    设计源自 Codex CLI 的 ExecPolicyManager，结合夸父现有的三层审批：
      Layer 1: DenyRules — 硬黑名单
      Layer 2: AutoMode — 自动分类决策
      Layer 3: 人工审批 — 文件/终端/通道审批

    支持合并审批（Merge Approval）：同一 LLM 轮次内连续触发审批的同类工具
    合并为一个审批请求，避免每个工具调用都独立弹审批。
    """

    # 可自动放行的只读工具（不经过审批系统）
    READONLY_TOOLS = frozenset({
        "read_file", "search_files", "web_search", "memory_search",
        "memory_reflect", "list_tools", "web_scrape", "web_submit",
    })

    # 硬黑名单工具（直接拒绝）
    HARD_DENY_TOOLS = frozenset({"shutdown", "self_modify"})

    # 合并审批配置
    MERGE_WINDOW_SECONDS = 30      # 合并时间窗口
    MERGE_MAX_TOOLS = 20           # 单次合并最多包含的工具数

    def __init__(self):
        """初始化 PolicyManager。不加载文件，惰性加载。"""
        self._loaded = False
        self._approval_callback = None
        # ── 合并审批状态 ──
        self._merge_req_id: Optional[str] = None   # 当前合并批次的审批 ID
        self._merge_tools: list[dict] = []          # 合并批次中的工具列表 [{tool, args, ts}]
        self._merge_deadline: float = 0             # 合并窗口截止时间
        self._merge_notified: bool = False          # 是否已通知用户

    def set_approval_callback(self, callback):
        """设置审批通知回调（用于通道推送）。"""
        self._approval_callback = callback

    # ── 主决策入口 ────────────────────────────────────────────────

    def decide(self, tool: str, args: dict,
               context: Optional[dict] = None,
               auto_override: bool = True) -> PolicyDecision:
        """三层策略决策入口。

        流程：
          Pre-check: 硬黑名单 / 只读工具 / 安全 terminal 命令
          Layer 1:   DenyRules 静态规则
          Layer 2:   AutoMode 自动分类器（auto_override=False 时跳过）
          Layer 3:   人工审批（提交审批请求）

        关键变更：每个决策结果都发射 Hook 事件，形成 Hook → Approval 的完整链路。
        """
        self._lazy_load()

        if not isinstance(args, dict):
            args = {}

        # ── Pre-check ──
        if tool in self.HARD_DENY_TOOLS:
            decision = PolicyDecision.deny(f"🛑 工具 {tool} 被硬黑名单禁止")
            self._emit_hooks(tool, args, decision)
            return decision

        if tool in self.READONLY_TOOLS:
            decision = PolicyDecision.allow(f"📖 只读工具 {tool} 自动放行")
            self._emit_hooks(tool, args, decision)
            return decision

        if tool == "terminal":
            cmd = args.get("command", "")
            if _is_safe_terminal(cmd):
                decision = PolicyDecision.fast_path(f"🔓 安全终端命令: {cmd[:60]}")
                self._emit_hooks(tool, args, decision)
                return decision

        # ── Layer 1: DenyRules ──
        denied = DenyRules.check(tool, args)
        if denied:
            decision = PolicyDecision.deny(
                f"🛡️ Deny 规则 [{denied.id}]: {denied.reason}",
                rule_id=denied.id,
            )
            self._emit_hooks(tool, args, decision)
            return decision

        # ── Layer 2: AutoMode ──
        if auto_override:
            auto = AutoMode.should_auto_approve(tool, args)
            if auto is True:
                decision = PolicyDecision.allow("✅ 自动模式通过")
                self._emit_hooks(tool, args, decision)
                return decision
            if auto is False:
                decision = PolicyDecision.deny("⛔ 自动模式拒绝")
                self._emit_hooks(tool, args, decision)
                return decision
        # auto_override=False 或 AutoMode 不确定 → 走 Layer 3

        # ── Layer 3: 人工审批（合并审批） ──
        risk = AutoMode._get_tool_risk(tool)

        # 仅高风险走人工，中低风险自动通过
        if risk != "high":
            decision = PolicyDecision.allow(f"✅ 风险 {risk} 自动通过")
            self._emit_hooks(tool, args, decision)
            return decision

        return self._merge_or_submit(tool, args, risk)

    # ── 合并审批 ──────────────────────────────────────────────────

    def clear_merge_state(self):
        """清除合并审批状态（由 ToolOrchestrator 在每轮 LLM 调用后调用）。"""
        self._merge_req_id = None
        self._merge_tools = []
        self._merge_deadline = 0
        self._merge_notified = False

    def _merge_or_submit(self, tool: str, args: dict, risk: str) -> PolicyDecision:
        """合并审批或新建审批请求。

        同一窗口内连续触发的审批合并为一个请求：
        - 第一个工具触发新建审批
        - 后续工具在窗口内返回同一个 req_id（pending）
        - 窗口到期或审批被处理后自动重置
        """
        now = time.time()

        # 检查当前合并批次是否过期
        if self._merge_req_id and now >= self._merge_deadline:
            self._flush_merge()

        # 如果已有合并批次且未过期
        if self._merge_req_id and now < self._merge_deadline:
            if len(self._merge_tools) < self.MERGE_MAX_TOOLS:
                self._merge_tools.append({
                    "tool": tool,
                    "args": dict(args),
                    "ts": now,
                })
                # 更新审批请求详情（追加工具）
                self._update_merge_approval()
                # 返回同一个 req_id（pending状态）
                decision = PolicyDecision.escalate(
                    reason=f"🟡 批量审批 (ID: {self._merge_req_id}) — 第{len(self._merge_tools)}个工具等待决策",
                    req_id=self._merge_req_id, risk=risk,
                )
                # 首次合并时不重复发射回调
                self._emit_hooks(tool, args, decision)
                return decision
            # 超出合并上限 → 重新创建审批
            self._flush_merge()

        # 第一个工具或重新开始 → 创建审批请求
        title = f"审批工具调用: {tool}"
        if tool == "terminal":
            cmd = args.get("command", "")[:60]
            title = f"终端: {cmd}"

        self._merge_tools = [{"tool": tool, "args": dict(args), "ts": now}]
        self._merge_deadline = now + self.MERGE_WINDOW_SECONDS
        self._merge_notified = False

        detail = self._build_merge_detail()

        req_id = ApprovalManager.submit(
            title=detail[:80] + "…" if len(detail) > 80 else detail,
            detail=detail,
            risk=risk,
            tool=tool,
            args_snapshot=json.dumps(args, ensure_ascii=False)[:500],
            context_type=f"policy_merge_{tool}",
        )
        self._merge_req_id = req_id

        decision = PolicyDecision.escalate(
            reason=f"🟡 需要审批 (ID: {req_id}) — {title}",
            req_id=req_id, risk=risk,
        )
        self._emit_hooks(tool, args, decision)
        return decision

    def _build_merge_detail(self) -> str:
        """构建合并审批的详情文本。"""
        parts = []
        for i, item in enumerate(self._merge_tools, 1):
            t = item["tool"]
            a = item["args"]
            if t == "terminal":
                cmd = a.get("command", "")[:80]
                parts.append(f"  {i}. 终端: `{cmd}`")
            elif t == "write_file":
                path = a.get("path", "?")
                parts.append(f"  {i}. 写入: {path}")
            elif t == "patch":
                path = a.get("path", "?")
                parts.append(f"  {i}. 编辑: {path}")
            else:
                arg_preview = json.dumps(a, ensure_ascii=False)[:60]
                parts.append(f"  {i}. {t}: {arg_preview}")
        return "\n".join(parts)

    def _update_merge_approval(self):
        """更新合并审批请求的详情（追加新工具到已提交的审批）。"""
        if not self._merge_req_id:
            return
        req = ApprovalManager._resolve(self._merge_req_id)
        if not req:
            return
        detail = self._build_merge_detail()
        # 更新磁盘上的请求详情
        req.detail = detail
        from core.approval import _save as _save_req
        _save_req(req)

    def _flush_merge(self):
        """强制结束当前合并批次（超出上限时），重置状态。"""
        self._merge_req_id = None
        self._merge_tools = []
        self._merge_deadline = 0
        self._merge_notified = False

    def _emit_hooks(self, tool: str, args: dict, decision: PolicyDecision):
        """发射权限相关的 Hook 事件，打通 Hook → Approval 链路。"""
        try:
            hook_ctx = {
                "tool": tool,
                "args": json.dumps(args, ensure_ascii=False)[:500],
                "action": decision.action.value,
                "reason": decision.reason,
                "rule_id": decision.rule_id or "",
                "req_id": decision.req_id or "",
                "risk": decision.risk,
            }
            # 发射权限检查事件
            from core.hooks import trigger_async, trigger_sync
            trigger_async("on_permission_check", hook_ctx)

            # 如果是 deny/escalate，额外发射 on_tool_rejected
            if decision.action in (PolicyAction.DENY, PolicyAction.ESCALATE):
                trigger_async("on_tool_rejected", {
                    "tool": tool,
                    "reason": decision.reason,
                    "action": decision.action.value,
                    "req_id": decision.req_id or "",
                })
        except Exception as e:
            logger.warning(f"Hook 发射失败: {e}")

    # ── 审批操作 ──────────────────────────────────────────────────

    def submit_approval(self, title: str, detail: str = "",
                        risk: str = "medium", **kwargs) -> str:
        """非阻塞提交审批请求。"""
        return ApprovalManager.submit(
            title=title, detail=detail, risk=risk, **kwargs
        )

    def resolve_approval(self, req_id: str, action: str) -> bool:
        """解决审批请求。action: 'approve' | 'reject'"""
        if action == "approve":
            return ApprovalManager.approve(req_id)
        elif action == "reject":
            return ApprovalManager.reject(req_id)
        return False

    def list_pending(self) -> list:
        """列出待处理审批。"""
        return ApprovalManager.list_pending()

    # ── 辅助 ──────────────────────────────────────────────────────

    def _lazy_load(self):
        """惰性加载文件数据。"""
        if self._loaded:
            return
        DenyRules.load()
        AutoMode.load()
        self._loaded = True


# =========================================================================
# 全局单例（兼容 layer 3 以上级别的全局引用）
# =========================================================================

_GLOBAL_POLICY: Optional[PolicyManager] = None


def get_policy() -> PolicyManager:
    """获取全局 PolicyManager 单例。"""
    global _GLOBAL_POLICY
    if _GLOBAL_POLICY is None:
        _GLOBAL_POLICY = PolicyManager()
    return _GLOBAL_POLICY


# =========================================================================
# 向后兼容：pretooluse_check 保留功能不变，内部调用 PolicyManager
# =========================================================================

def pretooluse_check(tool: str, args: dict,
                     context: Optional[dict] = None) -> dict:
    """向后兼容的 PreToolUse 检查入口。

    内部调用 PolicyManager.decide()，但返回旧格式 dict。
    供尚未迁移到 ToolOrchestrator 的代码使用。
    """
    policy = get_policy()
    decision = policy.decide(tool, args, context)
    return decision.to_legacy_dict()
