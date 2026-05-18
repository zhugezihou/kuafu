"""
夸父进化系统 — 不可变的核心层。

职责：
1. 五级进化机制 (L1-L5)
2. 进化触发条件判断
3. 进化动作执行
4. 进化日志记录

进化原则：事件驱动，不依赖 cron。
每次任务完成后自然触发反思 → 进化决策。

进化等级：
- L1: 即时优化（修复/改进当前任务的 tool usage / 参数 / 流程）
- L2: 策略进化（更新 strategy/ 下的 prompt 模板或默认策略）
- L3: 技能提取（从重复经验中抽象出可复用 skill，写入 skills/）
- L4: Prompt 进化（发现更有效的系统 prompt 表述）
- L5: 元学习（技能组合创新 / 工作流自动生成）
"""

import json
import time
import hashlib
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field, asdict

ROOT_DIR = Path(__file__).resolve().parent.parent
EVOLUTION_LOG = ROOT_DIR / "memory" / "evolution_log.json"


@dataclass
class EvolutionEvent:
    """一次进化事件记录。"""
    level: int               # 1-5
    trigger: str             # 触发原因
    action: str              # 具体做了什么
    target: str              # 改了什么文件/配置
    timestamp: float = field(default_factory=time.time)
    hash: str = ""

    def __post_init__(self):
        raw = f"{self.level}|{self.trigger}|{self.action}|{self.timestamp}"
        self.hash = hashlib.sha256(raw.encode()).hexdigest()[:12]


class EvolutionEngine:
    """进化引擎。
    
    评估是否触发进化、执行进化动作、记录进化历史。
    """

    def __init__(self, task_history: Optional[list] = None):
        self._task_history = task_history or []
        self._log_path = EVOLUTION_LOG
        self._ensure_log()

    def _ensure_log(self):
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._log_path.exists():
            self._log_path.write_text("[]", encoding="utf-8")

    # ---- 公开接口 ----

    def _record_task(self, task_result: dict):
        """记录一次任务完成的结果（内部使用）。"""
        self._task_history.append({
            **task_result,
            "timestamp": time.time(),
        })

    def evaluate_and_evolve(self, task_result: dict) -> Optional[EvolutionEvent]:
        """评估当前任务结果，决定是否触发进化。

        这是唯一入口。每次任务完成后应调用一次。
        
        Args:
            task_result: {
                "success": bool,
                "errors": list[str],
                "tool_calls": int,
                "task_type": str,
                "duration": float,
                "user_correction": str or None
            }
            
        Returns:
            触发的进化事件，或 None（不进化）
        """
        # 先记录任务（让历史包含这次结果）
        self._record_task(task_result)
        
        if not task_result.get("success"):
            return self._evaluate_failure(task_result)
        return self._evaluate_success(task_result)

    # ---- 评估逻辑 ----

    def _evaluate_failure(self, result: dict) -> Optional[EvolutionEvent]:
        errors = result.get("errors", [])

        # L2: 策略进化 — 连续失败 3 次（优先于L1，策略级修正优先级更高）
        recent_n = self._task_history[-5:]
        failures = [t for t in recent_n if not t.get("success")]
        if len(failures) >= 3 and len(self._task_history) >= 3:
            return self._evolve(
                level=2,
                trigger=f"连续 {len(failures)} 次失败",
                action="更新策略模板以适应此类任务",
                target="strategy/prompts.yaml",
            )

        # L1: 即时优化 — 重复出现相同错误
        if errors and len(self._task_history) > 1:
            for hist_task in self._task_history[:-1]:  # 排除当前这条
                hist_errors = hist_task.get("errors", [])
                if hist_errors and hist_errors[0] == errors[0]:
                    return self._evolve(
                    level=1,
                    trigger=f"重复错误: {errors[0]}",
                    action="优化 task 策略以避免此错误",
                    target="strategy/prompts.yaml",
                )

        return None

    def _evaluate_success(self, result: dict) -> Optional[EvolutionEvent]:
        recent_n = self._task_history[-5:]
        successes = [t for t in recent_n if t.get("success")]

        # L2: 策略进化 — 同类型任务成功 5 次
        same_type = [
            t for t in successes
            if t.get("task_type") == result.get("task_type")
        ]
        if len(same_type) >= 5:
            return self._evolve(
                level=2,
                trigger=f"「{result.get('task_type')}」类型任务成功 {len(same_type)} 次",
                action="固化此类任务的成功策略模板",
                target="strategy/prompts.yaml",
            )

        # L3: 技能提取 — 同类型任务成功 ≥3 次且用户有纠正
        if len(same_type) >= 3 and result.get("user_correction"):
            return self._evolve(
                level=3,
                trigger=f"「{result.get('task_type')}」频繁执行 + 用户纠正",
                action="提取为可复用的技能包",
                target=f"skills/{result.get('task_type', 'generic')}.yaml",
            )

        return None

    # ---- 进化执行 ----

    def _evolve(self, level: int, trigger: str, action: str, target: str) -> EvolutionEvent:
        event = EvolutionEvent(
            level=level,
            trigger=trigger,
            action=action,
            target=target,
        )
        self._log_event(event)
        return event

    def _log_event(self, event: EvolutionEvent):
        logs = json.loads(self._log_path.read_text(encoding="utf-8"))
        logs.append(asdict(event))
        self._log_path.write_text(
            json.dumps(logs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- 查询 ----

    def get_evolution_history(self, limit: int = 20) -> list[dict]:
        logs = json.loads(self._log_path.read_text(encoding="utf-8"))
        return logs[-limit:]

    def get_evolution_stats(self) -> dict:
        logs = json.loads(self._log_path.read_text(encoding="utf-8"))
        levels = {}
        for log in logs:
            lv = log.get("level", 0)
            levels[lv] = levels.get(lv, 0) + 1
        return {
            "total_evolutions": len(logs),
            "by_level": levels,
            "latest": logs[-1] if logs else None,
        }

    def get_task_stats(self) -> dict:
        total = len(self._task_history)
        if total == 0:
            return {"total": 0, "success_rate": 0, "by_type": {}}
        successes = sum(1 for t in self._task_history if t.get("success"))
        by_type = {}
        for t in self._task_history:
            tt = t.get("task_type", "unknown")
            by_type.setdefault(tt, {"total": 0, "success": 0})
            by_type[tt]["total"] += 1
            if t.get("success"):
                by_type[tt]["success"] += 1
        return {
            "total": total,
            "success_rate": round(successes / total * 100, 1),
            "by_type": by_type,
        }
