"""
core/evolution.py — 增量式进化引擎（三阶段管道）

职责：
将任务结果通过 Observer → Judge → SkillWriter 管道处理，自动提取可复用的技能。

设计原则：
- 增量：只在有新信号时触发，不扫描历史
- 单次 LLM 调用：由 Judge 完成，Observer 和 SkillWriter 零 LLM 成本
- 兼容旧接口：保持 evaluate_and_evolve() 签名，EvolutionEvent 导出
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.judge import Judge
from core.observer import Observer, Observation
from core.evolution_state import EvolutionState


logger = logging.getLogger("kuafu.evolution")

ROOT_DIR = Path(__file__).resolve().parent.parent
EVOLUTION_LOG = ROOT_DIR / "memory" / "evolution_log.json"


class EvolutionEvent:
    """进化事件（兼容旧接口）。"""
    __slots__ = ("level", "action", "target", "payload", "timestamp", "success")
    LEVELS = ("info", "skill", "memory", "warning", "error")

    def __init__(self, level: str, action: str, target: str = "",
                 payload: str = "", timestamp: Optional[float] = None,
                 success: bool = True):
        self.level = level if level in self.LEVELS else "info"
        self.action = action
        self.target = target
        self.payload = str(payload)[:2000] if payload else ""
        self.timestamp = timestamp or time.time()
        self.success = success

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "action": self.action,
            "target": self.target,
            "payload": self.payload[:500],
            "timestamp": self.timestamp,
            "success": self.success,
        }


class EvolutionEngine:
    """进化引擎入口。

    用法（兼容旧接口）：
        engine = EvolutionEngine(memory, llm)
        engine.evaluate_and_evolve(task_result, task, messages)

    新管道：
        Observer 收集信号 → EvolutionState 更新计数器 → Judge 判断+提取 → SkillWriter 写入
    """

    MAX_LOG = 200
    EVOLUTION_LOG = EVOLUTION_LOG

    def __init__(self, memory=None, llm=None, root_dir=None):
        self.memory = memory
        self.root_dir = root_dir or ROOT_DIR

        # 新组件
        self.evolution_state = EvolutionState(root_dir=self.root_dir)
        self.judge = Judge(llm.chat if llm else self._noop_llm)
        self.observers: list[Observer] = [Observer()]

        # 日志/统计
        self._events: list[EvolutionEvent] = []
        self._total = 0
        self._last_trigger_time: float = 0.0
        self._cooldown = 30.0  # 秒，同类型任务冷却

    # ── 新管道入口（供 agent_loop 调用） ──

    def run_pipeline(self, observation: Observation, task_type: str) -> dict:
        """三阶段管道。

        Args:
            observation: Observer 产出的 Observation 对象
            task_type: 任务类型（用于更新 EvolutionState）

        Returns:
            dict: {
                "skill_written": bool,
                "skill_name": str or None,
                "evolution_mode": str or None,
                "reason": str or None,
            }
        """
        result = {
            "skill_written": False,
            "skill_name": None,
            "evolution_mode": None,
            "reason": None,
        }

        # Phase 1: 更新 EvolutionState（零 LLM 成本）
        self.evolution_state.record_result(task_type, observation.success)
        if observation.errors:
            for err in observation.errors:
                self.evolution_state.record_error(err)
        for te in observation.tool_errors:
            self.evolution_state.record_error(te.error_message)

        # Phase 2: 快速过滤 — 没价值就不触发 LLM
        if not observation.has_value():
            return result

        # 冷却检查：同类型任务 30 秒内不重复触发
        state_entry = self.evolution_state._data["task_types"].get(task_type)
        now = time.time()
        if state_entry and (now - state_entry.get("last_seen", 0)) < self._cooldown:
            return result

        # Phase 3: Judge — 单次 LLM 调用
        state_entry = self.evolution_state._data["task_types"].get(task_type)
        decision = self.judge.evaluate(observation, state_entry)

        if not decision["worth_learning"]:
            result["reason"] = decision.get("reason", "不值得学习")
            return result

        # Phase 4: SkillWriter — 写入 skills/ 目录（零 LLM）
        skill = decision.get("skill")
        if skill and skill.get("name"):
            evolution_mode = decision.get("evolution_mode", "CAPTURED")
            self._write_skill(
                skill, task_type,
                evolution_mode=evolution_mode,
                observation=observation,
            )
            result["skill_written"] = True
            result["skill_name"] = skill["name"]
            result["evolution_mode"] = evolution_mode
            result["reason"] = decision.get("reason", "")

        # 记录进化日志
        self._total += 1
        event = EvolutionEvent(
            level="skill" if skill else "info",
            action=f"自动学习: {decision['reason']}",
            target=task_type,
            payload=json.dumps(skill, ensure_ascii=False) if skill else decision['reason'],
            success=True,
        )
        self._events.append(event)
        self._append_log(event)

        return result

    # ── Observer 管理 ──

    def register_observer(self, observer: Observer):
        """注册运行时 Observer（供 agent_loop 调用 on_tool_call）。"""
        if observer not in self.observers:
            self.observers.append(observer)

    # ── 兼容旧接口 ──

    def evaluate_and_evolve(self, task_result: dict, task: str = "",
                            messages: list = None) -> dict:
        """兼容旧接口。从 task_result 构建 Observation 然后走新管道。

        这是旧调用点（core/agent_loop.py 第 602-605 行和 main.py 的 web_learner）的兼容桥。
        """
        obs = Observation(
            success=task_result.get("success", False),
            task_type=task_result.get("task_type", "generic"),
            errors=task_result.get("errors", []),
            result=str(task_result.get("result", ""))[:500],
            user_input=task,
            tool_calls=task_result.get("tool_calls", 0),
            tools_used=set(task_result.get("tools_used", [])),
        )
        self.run_pipeline(obs, obs.task_type)
        return {"success": True, "evolved": self._total}

    def emit(self, level: str, action: str, target: str = "",
             payload: str = "") -> None:
        """兼容旧接口：手动触发进化记录。"""
        if not target:
            target = "generic"
        event = EvolutionEvent(level=level, action=action,
                               target=target, payload=payload,
                               success=level not in ("error",))
        self._events.append(event)
        self._total += 1
        self._append_log(event)

    def get_evolution_stats(self) -> dict:
        """获取进化统计（兼容旧接口）。"""
        return {
            "total_evolutions": self._total,
            "recent_events": [e.to_dict() for e in self._events[-10:]],
            "last_event": self._events[-1].to_dict() if self._events else None,
            "health": self.evolution_state.health_check(),
        }

    # ── 内部方法 ──

    def _write_skill(self, skill: dict, task_type: str, evolution_mode: str = "CAPTURED",
                     observation: Any = None) -> bool:
        """将 Judge 提取的技能写入 skills/ 目录。

        根据 evolution_mode 决定写入行为：
        - CAPTURED：全新写入 skills/{name}.yaml
        - FIX：备份旧版 → 覆盖当前 skills/{name}.yaml
        - DERIVED：生成 skills/{name}_v2.yaml，保留原文件

        Args:
            skill: Judge 的 skill dict
            task_type: 任务类型
            evolution_mode: "CAPTURED" | "FIX" | "DERIVED"
            observation: 原始 Observation（用于错误关联和 skill_name 获取）

        Returns:
            bool: 是否成功
        """
        name = skill.get("name", f"auto-{task_type}-{int(time.time())}")
        trigger = skill.get("trigger", "")
        steps = skill.get("steps", [])
        error_pattern = skill.get("error_pattern", "")

        # 步骤转义
        step_lines = "\n".join(f"  - {s}" for s in steps)

        content = (
            "---\n"
            f"name: {name}\n"
            f"trigger: {trigger}\n"
            f"task_type: {task_type}\n"
        )
        if error_pattern:
            content += f"error_pattern: {error_pattern}\n"
        content += "---\n\n"
        content += "skill steps:\n"
        content += step_lines + "\n"

        skills_dir = self.root_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        parent_version = None
        filepath = skills_dir / f"{name}.yaml"

        if evolution_mode == "FIX":
            # FIX：同名覆盖前先备份为 .bak.v{N}
            if filepath.exists():
                old_content = filepath.read_text(encoding="utf-8")
                bak_path = skills_dir / f"{name}.bak.v{int(time.time())}"
                bak_path.write_text(old_content, encoding="utf-8")
                # 从 evolution_state 找父版本号
                history = self.evolution_state.get_evolution_history(name)
                if history:
                    parent_version = str(history[-1]["v"])

            filepath.write_text(content, encoding="utf-8")

        elif evolution_mode == "DERIVED":
            # DERIVED：生成 {name}_v2.yaml
            v = 2
            while (skills_dir / f"{name}_v{v}.yaml").exists():
                v += 1
            filepath = skills_dir / f"{name}_v{v}.yaml"
            filepath.write_text(content, encoding="utf-8")

            # 找父版本号
            history = self.evolution_state.get_evolution_history(name)
            if history:
                parent_version = str(history[-1]["v"])

        else:
            # CAPTURED：全新写入（去重覆盖）
            filepath.write_text(content, encoding="utf-8")

        # 记录版本链
        self.evolution_state.record_skill_evolution(
            skill_name=name,
            file_path=str(filepath.relative_to(self.root_dir)),
            mode=evolution_mode,
            summary=skill.get("trigger", ""),
            parent=parent_version,
        )

        # 错误关联
        if error_pattern:
            self.evolution_state.associate_error_with_skill(error_pattern, name)

        return True

    def _append_log(self, event: EvolutionEvent) -> None:
        """追加进化日志（内存 + 文件）。"""
        # 裁剪内存日志
        if len(self._events) > self.MAX_LOG:
            self._events[:] = self._events[-self.MAX_LOG:]

        # 写入文件
        try:
            self.EVOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
            logs = []
            if self.EVOLUTION_LOG.exists():
                try:
                    logs = json.loads(self.EVOLUTION_LOG.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    logs = []

            logs.append(event.to_dict())
            if len(logs) > self.MAX_LOG:
                logs = logs[-self.MAX_LOG:]

            # 原子写入
            import tempfile, os
            tmp = self.EVOLUTION_LOG.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(logs, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.EVOLUTION_LOG)
        except OSError as e:
            logger.warning(f"写入进化日志失败: {e}")

    @staticmethod
    def _noop_llm(messages: list) -> dict:
        """降级 LLM（没有 LLM 时返回空）。"""
        return {"content": "{}", "success": True}
