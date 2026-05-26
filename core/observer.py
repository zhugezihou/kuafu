"""
core/observer.py — 运行时+后验 Observer

职责：
在 agent_loop 执行过程中实时收集信号，在任务完成后整合为 Observation。

核心理念：
- 零 LLM 成本，纯规则收集
- 运行中 Observer 监听每个 tool_call 的结果
- 后验 Observer 在任务结束后从 task_result 收集摘要信息
- 两者合为一个 Observation 对象

输入：
- 运行时信号（on_tool_call）：工具名、参数、结果、错误
- 后验信号（on_task_complete）：task_result dict + 用户原始输入

输出：
- Observation dataclass，包含所有信号
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core.safety import SafetyLayer


@dataclass
class ToolError:
    """一次工具调用错误记录。"""
    tool_name: str
    error_message: str
    retry_count: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class Observation:
    """Observer 收集的所有运行时和后验信号。

    零 LLM 成本，纯规则收集。
    """
    # ── 运行时信号（agent_loop 执行过程中实时收集） ──
    tool_errors: list[ToolError] = field(default_factory=list)
    tool_error_count: int = 0
    tool_error_names: set[str] = field(default_factory=set)
    tool_chain: list[str] = field(default_factory=list)   # 工具名按调用顺序
    tool_calls: int = 0
    tools_used: set[str] = field(default_factory=set)

    # ── 后验信号（任务结束后从 task_result 收集） ──
    success: bool = False
    task_type: str = "generic"
    errors: list[str] = field(default_factory=list)
    result: str = ""
    duration: float = 0.0

    # ── 用户输入分析 ──
    user_input: str = ""
    has_user_correction: bool = False

    # ── 拒绝跟踪信号 ──
    denials: int = 0                    # 本轮任务中被拒绝的命令数
    has_auto_block: bool = False        # 是否触发了自动阻止
    has_auto_allow: bool = False        # 是否触发了自动放行

    # ── 增量状态（由 EvolutionState 填充） ──
    is_novel_task: bool = False          # 首次遇到的 task_type
    is_repeated_failure: bool = False    # 同类任务连续失败 ≥ 2 次
    task_type_history: int = 0           # 该 task_type 出现过多少次
    has_unknown_error: bool = False

    def merge(self, other: 'Observation') -> 'Observation':
        """合并两个 Observation。"""
        self.tool_errors.extend(other.tool_errors)
        self.tool_error_count += other.tool_error_count
        self.tool_error_names.update(other.tool_error_names)
        self.tool_chain.extend(other.tool_chain)
        self.tool_calls += other.tool_calls
        self.tools_used.update(other.tools_used)
        self.errors.extend(other.errors)
        return self

    def has_value(self) -> bool:
        """快速判断是否有值得学的东西（零 LLM 成本）。"""
        if self.tool_error_count > 0:
            return True
        if self.has_user_correction:
            return True
        if self.is_repeated_failure:
            return True
        if self.tool_calls >= 5:
            return True
        if self.is_novel_task:
            return True
        return False


_CORRECTION_SIGNALS = [
    "不要", "别用", "不要用", "不可以", "不对", "错了",
    "应该用", "请用", "用这个", "换成", "改用",
    "注意", "记住", "以后", "建议",
]


def _detect_user_correction(text: str) -> bool:
    """纯规则检测纠正信号（不用 LLM）。"""
    return any(kw in text for kw in _CORRECTION_SIGNALS)


class Observer:
    """运行时观察者 — 在 agent_loop 执行过程中收集信号。

    用法：
        observer = Observer()
        # 在 _handle_tool_call() 末尾调用
        observer.on_tool_call(tool_name, args, result)
        # 在任务完成后调用
        obs = observer.on_task_complete(task_result, user_input)
    """

    def __init__(self):
        self._runtime_errors: list[ToolError] = []
        self._tool_chain: list[str] = []
        self._tools_used: set[str] = set()
        self._tool_calls: int = 0
        self._current_tool: str = ""
        self._current_retry: int = 0
        self._prev_denial_total: int = 0

    def on_tool_call(self, tool_name: str, args: dict, result: Any):
        """每个 tool call 完成后调用（零成本）。

        Args:
            tool_name: 工具名称
            args: 工具参数 dict
            result: 工具返回结果（可以是 dict 或字符串）
        """
        self._tool_calls += 1
        self._tool_chain.append(tool_name)
        self._tools_used.add(tool_name)

        # 检测错误
        error_msg = ""
        if isinstance(result, dict):
            if not result.get("success", True):
                error_msg = result.get("output", str(result))[:200]
            elif "error" in result:
                error_msg = str(result["error"])[:200]
        elif isinstance(result, str) and ("error" in result.lower() or "exception" in result.lower()):
            error_msg = result[:200]

        if error_msg:
            # 连续相同工具的错误 → 重试计数
            if tool_name == self._current_tool:
                self._current_retry += 1
            else:
                self._current_tool = tool_name
                self._current_retry = 0

            self._runtime_errors.append(ToolError(
                tool_name=tool_name,
                error_message=error_msg,
                retry_count=self._current_retry,
            ))

    def on_task_complete(self, task_result: dict, user_input: str) -> Observation:
        """任务完成后整合所有信号 → Observation。

        Args:
            task_result: AgentLoop.run() 返回的 dict
            user_input: 原始用户输入

        Returns:
            Observation 对象（纯数据，零 LLM 调用）
        """
        # 获取拒绝跟踪统计
        denial_stats = self._get_denial_stats_since_last()

        obs = Observation(
            success=task_result.get("success", False),
            task_type=task_result.get("task_type", "generic"),
            errors=task_result.get("errors", []),
            result=str(task_result.get("result", ""))[:500],
            duration=task_result.get("duration", 0.0),
            user_input=user_input,
            has_user_correction=_detect_user_correction(user_input or ""),
            tool_errors=list(self._runtime_errors),
            tool_error_count=len(self._runtime_errors),
            tool_error_names={e.tool_name for e in self._runtime_errors},
            tool_chain=list(self._tool_chain),
            tool_calls=self._tool_calls,
            tools_used=set(self._tools_used),
            denials=denial_stats.get("recent_denials", 0),
            has_auto_block=denial_stats.get("has_auto_block", False),
            has_auto_allow=denial_stats.get("has_auto_allow", False),
        )

        # 自检是否有未知错误
        if obs.errors and not any(e.error_message in str(obs.errors) for e in obs.tool_errors):
            obs.has_unknown_error = True

        # 清理运行时状态（为下一次任务做准备）
        self._reset()

        return obs

    def _get_denial_stats_since_last(self) -> dict:
        """获取自上次 reset 后的拒绝统计。
        
        DenialTracker 是全局的，这里取总数快照差来估算本轮新增。
        """
        try:
            stats = SafetyLayer.denial_tracker.get_stats()
            prev_total = self._prev_denial_total
            self._prev_denial_total = stats["total_denials"]
            recent = stats["total_denials"] - prev_total
            return {
                "recent_denials": max(0, recent),
                "total_denials": stats["total_denials"],
                "has_auto_block": any(
                    v.get("degraded") 
                    for v in stats.get("patterns", {}).values()
                ),
                "has_auto_allow": stats["degraded_count"] > 0,
            }
        except Exception:
            return {"recent_denials": 0, "total_denials": 0, "has_auto_block": False, "has_auto_allow": False}

    def _reset(self):
        """重置运行时状态。"""
        self._runtime_errors = []
        self._tool_chain = []
        self._tools_used = set()
        self._tool_calls = 0
        self._current_tool = ""
        self._current_retry = 0
