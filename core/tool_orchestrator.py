"""
core/tool_orchestrator.py — 工具执行编排系统

四阶段设计（源自 Codex CLI ToolOrchestrator 模式）：
  Phase 1: Approval — 权限审批（委托给 PolicyManager）
  Phase 2: Safety — 安全检查（命令降级解析、危险操作检测）
  Phase 3: Execute — 实际执行（调用 ToolRegistry handler）
  Phase 4: Retry — 失败重试（可选降级重试策略）

v2 变更：Phase 1 使用统一的 PolicyManager 替代直接调用 DenyRules/AutoMode
"""

import json
import time
import logging
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

from core.policy_manager import PolicyManager, PolicyAction, PolicyDecision
from core.safety import SafetyLayer, CommandLevel
from core.hooks import trigger_sync

logger = logging.getLogger("kuafu.orchestrator")

# =========================================================================
# 类型定义
# =========================================================================

class ApprovalDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class SafetyDecision(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class RetryDecision(Enum):
    NO_RETRY = "no_retry"
    RETRY_SAME = "retry_same"
    RETRY_ESCALATE = "retry_escalate"
    RETRY_FALLBACK = "retry_fallback"


@dataclass
class ToolExecutionRequest:
    tool_name: str
    args: dict
    tool_call_id: str = ""
    source: str = "llm"
    req_id: str = ""  # 审批请求 ID（ESCALATE 时设置）
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolExecutionResult:
    success: bool
    output: str
    tool_name: str
    duration_ms: float
    decision: ApprovalDecision = ApprovalDecision.ALLOW
    retry_count: int = 0
    error: Optional[str] = None
    approved_by: Optional[str] = None


@dataclass
class ToolOrchestratorConfig:
    enable_approval: bool = True
    enable_safety: bool = True
    enable_retry: bool = True
    max_retries: int = 2
    retry_delay_ms: float = 500
    timeout_ms: float = 30000


# =========================================================================
# Phase Hooks
# =========================================================================

class PhaseHook:
    def on_approval(self, req: ToolExecutionRequest, decision: PolicyDecision) -> Optional[PolicyDecision]:
        return None
    def on_safety(self, req: ToolExecutionRequest, decision: SafetyDecision) -> Optional[SafetyDecision]:
        return None
    def on_execute_start(self, req: ToolExecutionRequest):
        pass
    def on_execute_end(self, req: ToolExecutionRequest, result: ToolExecutionResult):
        pass
    def on_retry(self, req: ToolExecutionRequest, attempt: int, error: str) -> Optional[RetryDecision]:
        return None


# =========================================================================
# 核心编排器
# =========================================================================

class ToolOrchestrator:
    """工具执行编排器——四阶段封装。"""

    def __init__(
        self,
        tool_registry,
        policy_manager: Optional[PolicyManager] = None,
        config: Optional[ToolOrchestratorConfig] = None,
    ):
        self.tools = tool_registry
        self.policy = policy_manager or PolicyManager()
        self.config = config or ToolOrchestratorConfig()
        self.hooks: list[PhaseHook] = []
        self._approval_callback = None
        self._lock = threading.Lock()

    def register_hook(self, hook: PhaseHook):
        with self._lock:
            self.hooks.append(hook)

    def set_approval_callback(self, callback):
        """设置审批通知回调。"""
        self._approval_callback = callback
        self.policy.set_approval_callback(callback)

    # ── 主入口 ──

    def execute(self, req: ToolExecutionRequest) -> ToolExecutionResult:
        t0 = time.monotonic()

        decision = self._phase_approval(req)
        if decision == ApprovalDecision.DENY:
            return self._result(req, success=False,
                                output="🔒 被审批系统拒绝", decision=ApprovalDecision.DENY)
        if decision == ApprovalDecision.ESCALATE:
            # 审批已提交，等待用户决策
            if req.req_id:
                from core.approval import ApprovalManager
                import time as _time
                deadline = _time.time() + 300  # 5 分钟超时
                while _time.time() < deadline:
                    req_info = ApprovalManager._resolve(req.req_id)
                    if req_info and req_info.status == "approved":
                        logger.info(f"✅ 审批通过: {req.req_id}")
                        break
                    elif req_info and req_info.status == "rejected":
                        logger.info(f"❌ 审批拒绝: {req.req_id}")
                        return self._result(req, success=False,
                                            output="🔒 审批被拒绝", decision=ApprovalDecision.DENY)
                    _time.sleep(1)
                else:
                    logger.info(f"⏰ 审批超时: {req.req_id}")
                    return self._result(req, success=False,
                                        output="⏰ 审批超时", decision=ApprovalDecision.DENY)

        safety = self._phase_safety(req)
        if safety == SafetyDecision.BLOCK:
            return self._result(req, success=False,
                                output="🛡️ 被安全系统拦截", decision=ApprovalDecision.DENY)

        result = self._phase_execute_with_retry(req)
        result.duration_ms = (time.monotonic() - t0) * 1000
        return result

    def execute_direct(self, tool_name: str, args: dict) -> ToolExecutionResult:
        """直接执行（跳过审批和安全检查）。用于内部调用。"""
        req = ToolExecutionRequest(tool_name=tool_name, args=args, source="internal")
        return self._phase_execute_with_retry(req)

    # ── Phase 1: Approval（委托 PolicyManager）──

    def _phase_approval(self, req: ToolExecutionRequest) -> ApprovalDecision:
        if not self.config.enable_approval:
            return ApprovalDecision.ALLOW

        policy_decision = self.policy.decide(req.tool_name, req.args)

        # 允许 hooks 覆盖决策
        for hook in self.hooks:
            override = hook.on_approval(req, policy_decision)
            if override is not None:
                policy_decision = override

        if policy_decision.action == PolicyAction.DENY:
            logger.info(f"🛡️ 策略拒绝: {req.tool_name} — {policy_decision.reason}")
            return ApprovalDecision.DENY

        if policy_decision.action in (PolicyAction.ALLOW, PolicyAction.FAST_PATH):
            return ApprovalDecision.ALLOW

        # ESCALATE → 需要用户审批
        # 触发审批回调（飞书/微信等通道通知）
        if policy_decision.req_id:
            # 保存 req_id 到请求对象，供 execute 等待时使用
            req.req_id = policy_decision.req_id
            # 通过 ToolOrchestrator 的审批回调通知通道推送
            if hasattr(self, '_approval_callback') and self._approval_callback:
                try:
                    self._approval_callback(req.tool_name, req.args, policy_decision.req_id)
                except Exception:
                    pass

        logger.info(f"🟡 需要审批: {req.tool_name} — {policy_decision.reason}")
        return ApprovalDecision.ESCALATE

    # ── Phase 2: Safety（三态决策）──

    def _phase_safety(self, req: ToolExecutionRequest) -> SafetyDecision:
        if not self.config.enable_safety:
            return SafetyDecision.ALLOW
        try:
            if req.tool_name == "terminal":
                cmd = req.args.get("command", "") if isinstance(req.args, dict) else ""
                tri_state = SafetyLayer.get_tri_state(cmd)
                decision = tri_state["decision"]
                level = tri_state["level"]
                reason = tri_state["reason"]

                # 发射安全检查事件
                trigger_sync("on_safety_check", {
                    "tool": req.tool_name,
                    "command": cmd[:100],
                    "level": level,
                    "decision": decision,
                    "reason": reason,
                    "suggestions": tri_state["suggestions"],
                })

                if decision == "block":
                    logger.warning(f"🛡️ 安全拦截: {reason}")
                    return SafetyDecision.BLOCK

                if decision == "escalate":
                    logger.info(f"⚠️ 安全升级: {reason}")
                    trigger_sync("on_safety_escalate", {
                        "tool": req.tool_name,
                        "command": cmd[:100],
                        "reason": reason,
                        "suggestions": tri_state["suggestions"],
                    })
                    return SafetyDecision.ESCALATE

            return SafetyDecision.ALLOW
        except Exception as e:
            logger.error(f"安全检测异常: {e}")
            return SafetyDecision.ALLOW

    # ── Phase 3+4: Execute + Retry ──

    def _phase_execute_with_retry(self, req: ToolExecutionRequest) -> ToolExecutionResult:
        last_error = None
        for attempt in range(self.config.max_retries + 1):
            if attempt > 0:
                time.sleep(self.config.retry_delay_ms / 1000)

            for hook in self.hooks:
                hook.on_execute_start(req)

            try:
                raw = self.tools.execute({
                    "id": req.tool_call_id or f"orc_{int(time.time()*1000)}",
                    "function": {"name": req.tool_name, "arguments": req.args},
                })
                success = raw.get("success", False) if isinstance(raw, dict) else True
                output = raw.get("output", str(raw)) if isinstance(raw, dict) else str(raw)

                result = ToolExecutionResult(
                    success=success, output=output,
                    tool_name=req.tool_name, duration_ms=0, retry_count=attempt,
                )
                for hook in self.hooks:
                    hook.on_execute_end(req, result)
                if success:
                    return result
                last_error = output

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

            if attempt < self.config.max_retries and self.config.enable_retry:
                if self._decide_retry(req, attempt, last_error or "") == RetryDecision.NO_RETRY:
                    break

        return ToolExecutionResult(
            success=False, output=last_error or "执行失败",
            tool_name=req.tool_name, duration_ms=0,
            retry_count=self.config.max_retries, error=last_error,
        )

    def _decide_retry(self, req, attempt, error) -> RetryDecision:
        for hook in self.hooks:
            d = hook.on_retry(req, attempt, error)
            if d is not None:
                return d
        return RetryDecision.NO_RETRY

    def _result(self, req, success, output, duration_ms=0, decision=ApprovalDecision.ALLOW):
        return ToolExecutionResult(
            success=success, output=output, tool_name=req.tool_name,
            duration_ms=duration_ms, decision=decision,
        )
