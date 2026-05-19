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
import re
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

    def __init__(self, task_history: Optional[list] = None, memory=None):
        self._task_history = task_history or []
        self._log_path = EVOLUTION_LOG
        self._ensure_log()
        # 进化频率控制：同一级别每次进化后需等待最小间隔
        self._last_level_time: dict[int, float] = {}
        # 记忆联动：注入 MemoryAPI 实例，使进化事件持久化为记忆
        self._memory = memory
        self._min_interval = 60.0  # 同一级别至少间隔 60 秒

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

        # L2: 策略进化 — 同类型任务连续失败 3 次（不跨类型计数）
        task_type = result.get("task_type", "generic")
        recent = self._task_history[-10:]
        same_type_failures = [t for t in recent
                              if not t.get("success")
                              and t.get("task_type") == task_type]
        if len(same_type_failures) >= 3:
            # 检查 3 次失败是否在 5 分钟内
            times = [t.get("timestamp", 0) for t in same_type_failures[-3:]]
            if max(times) - min(times) <= 300:
                return self._evolve(
                    level=2,
                    trigger=f"「{task_type}」连续 {len(same_type_failures)} 次失败",
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

        # L2: 策略进化 — 同类型任务成功 5 次且时间跨度 ≥ 10 分钟（防止短循环刷进化）
        same_type = [
            t for t in successes
            if t.get("task_type") == result.get("task_type")
        ]
        if len(same_type) >= 5:
            times = [t.get("timestamp", 0) for t in same_type[-5:]]
            if max(times) - min(times) >= 600:
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

    def _evolve(self, level: int, trigger: str, action: str, target: str) -> Optional[EvolutionEvent]:
        # 频率控制：同一级别在 min_interval 内不重复触发
        last = self._last_level_time.get(level, 0.0)
        now = time.time()
        if now - last < self._min_interval:
            return None
        self._last_level_time[level] = now

        event = EvolutionEvent(
            level=level,
            trigger=trigger,
            action=action,
            target=target,
        )
        self._log_event(event)

        # L3: 实际写入技能文件，不仅是记录事件
        if level >= 3 and target.startswith("skills/"):
            self._extract_skill(target, trigger)

        # 记忆联动：将进化事件持久化为记忆，供后续任务参考
        if self._memory is not None:
            level_label = {1: "即时优化", 2: "策略进化", 3: "技能提取"}.get(level, f"L{level}")
            try:
                self._memory.remember(
                    key=f"evolve:{int(time.time())}",
                    content=f"【{level_label}】触发器: {trigger} → 动作: {action} → 目标: {target}",
                    tags=["evolution", f"level_{level}"],
                )
            except Exception:
                pass  # 记忆失败不影响进化核心流程

        return event

    # ---- L3 技能提取 ----

    def _extract_skill(self, target: str, trigger: str) -> Optional[str]:
        """真正将进化事件转化为 skills/ 下的 YAML 技能文件。

        Args:
            target: 目标路径，如 "skills/research.yaml"
            path: 目标文件路径
            task_type: 触发进化的任务类型
        """
        path = ROOT_DIR / target
        task_type = path.stem  # e.g. "research" from "skills/research.yaml"

        # 从任务历史中找到这个类型的成功案例
        relevant_tasks = [
            t for t in self._task_history
            if t.get("task_type") == task_type and t.get("success")
        ]

        # 构建描述和关键词
        desc = f"夸父自动提取的技能包。触发: {trigger[:80]}"
        keywords = [task_type.replace("_", ""), "自动生成"]

        # 如果有具体任务内容，从结果中提取关键词
        if relevant_tasks:
            last_result = relevant_tasks[-1].get("result", "")
            desc = f"从「{task_type}」类型任务中自动提取的最佳实践（基于 {len(relevant_tasks)} 次成功经验）"
            # 从结果中提取可能的关键词
            result_words = re.findall(r'[\u4e00-\u9fff\w]+', last_result[:500])
            extra_kw = [w for w in result_words if 2 <= len(w) <= 8]
            keywords = list(set(keywords + extra_kw[:6]))

        # 构建 steps 和 pitfalls
        steps = [
            f"这是从 {len(relevant_tasks)} 次成功「{task_type}」任务中自动提取的经验",
            "请根据当前任务的具体需求，灵活应用之前的成功模式",
            "完成主要工作后，报告最终结果",
        ]
        pitfalls = ["自动提取的技能包，请确认步骤适用于当前任务"]

        # 如果任务历史中有搜索结果或用户纠正，补充为提示
        if relevant_tasks:
            last = relevant_tasks[-1]
            if last.get("user_correction"):
                pitfalls.append(f"历史教训: {last['user_correction'][:100]}")

        # 构造 YAML 内容
        lines = [
            f'name: "{task_type}"',
            f'description: "{desc}"',
            "keywords:",
        ]
        for kw in keywords:
            lines.append(f'  - "{kw}"')
        lines.append("steps:")
        for s in steps:
            lines.append(f'  - "{s}"')
        lines.append("examples:")
        lines.append("  - \"无\"")
        lines.append("pitfalls:")
        for p in pitfalls:
            lines.append(f'  - "{p}"')
        lines.append(f"usage_count: 0")
        lines.append(f"created_at: {int(time.time())}")
        lines.append(f"source: auto_extracted_from_L3")

        content = "\n".join(lines) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

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
