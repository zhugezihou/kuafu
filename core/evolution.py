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
- L0: 基础进化（首次任务 / 每 3 次成功任务触发，让用户感知进化系统）
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

    def __init__(self, task_history: Optional[list] = None, memory=None, llm=None):
        self._task_history = task_history or []
        self._log_path = EVOLUTION_LOG
        self._ensure_log()
        # 进化频率控制：同一级别每次进化后需等待最小间隔
        self._last_level_time: dict[int, float] = {}
        # 记忆联动：注入 MemoryAPI 实例，使进化事件持久化为记忆
        self._memory = memory
        # LLM 客户端：用于 L0 经验笔记生成
        self._llm = llm
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
        same_type = [
            t for t in successes
            if t.get("task_type") == result.get("task_type")
        ]

        total_success_count = sum(1 for t in self._task_history if t.get("success"))

        # L0: 经验沉淀 — 每完成 3 次成功任务，调用 LLM 从任务历史中提取经验笔记
        # 「进化」的意义在于让夸父变得更好，L0 就是最简单的方式：
        # 每次把完成的任务沉淀为可检索的记忆，让下一次同类任务做得更好
        if total_success_count > 0 and total_success_count % 3 == 0:
            return self._evolve(
                level=0,
                trigger=f"已完成 {total_success_count} 次成功任务",
                action="提取最近 3 次任务经验，生成经验笔记存入记忆",
                target="memory/evolution_log.json",
            )

        # L2: 策略进化 — 同类型任务成功 5 次且时间跨度 ≥ 10 分钟（防止短循环刷进化）
        if len(same_type) >= 5:
            times = [t.get("timestamp", 0) for t in same_type[-5:]]
            if max(times) - min(times) >= 600:
                return self._evolve(
                    level=2,
                    trigger=f"「{result.get('task_type')}」类型任务成功 {len(same_type)} 次",
                    action="固化此类任务的成功策略模板",
                    target="strategy/prompts.yaml",
                )

        # L3: 技能提取 — 同类型任务成功 ≥3 次（无需用户纠正也自动提取）
        if len(same_type) >= 3:
            return self._evolve(
                level=3,
                trigger=f"「{result.get('task_type')}」类型任务成功 {len(same_type)} 次，自动提取技能",
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

        # L0: 生成经验笔记并写入记忆（实际沉淀，不仅是记录事件）
        if level == 0 and self._memory is not None:
            try:
                lesson = self._extract_lesson()
                if lesson and lesson.strip():
                    self._memory.remember(
                        key=f"lesson:{int(time.time())}",
                        content=lesson,
                        tags=["evolution", "lesson", "L0"],
                    )
                    # 同时写入 strategy/task_strategies.yaml 的 notes
                    try:
                        strategy_dir = ROOT_DIR / "strategy"
                        strategies_path = strategy_dir / "task_strategies.yaml"
                        import yaml
                        if strategies_path.exists():
                            with open(strategies_path, "r", encoding="utf-8") as f:
                                existing = yaml.safe_load(f) or {}
                        else:
                            existing = {"generic": {}}
                        if "generic" not in existing:
                            existing["generic"] = {}
                        existing["generic"].setdefault("notes", [])
                        # 从 lesson 中提取第一行作为简洁笔记
                        lesson_first_line = lesson.strip().split("\n")[0][:80]
                        if lesson_first_line not in existing["generic"]["notes"]:
                            existing["generic"]["notes"].append(lesson_first_line)
                        with open(strategies_path, "w", encoding="utf-8") as f:
                            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    except Exception:
                        pass  # 写入 notes 失败不影响主流程
            except Exception:
                pass  # 经验提取失败不影响进化核心流程

        # L3: 实际写入技能文件，不仅是记录事件
        if level >= 3 and target.startswith("skills/"):
            self._extract_skill(target, trigger)

        # L2: 进化后同步更新 strategy/ 文件（双向同步）
        if level == 2:
            self._sync_strategy(trigger)

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

    # ---- L2: 策略双向同步 ----

    def _sync_strategy(self, trigger: str):
        """L2 进化后，同步更新 strategy/ 目录下的文件。

        核心逻辑：
        - quality.yaml: 从 task_history 中提取新的质量规则
        - prompts.yaml: 更新默认 prompt 模板
        - task_strategies.yaml: 更新策略参数

        如果 LLM 可用，让 LLM 从任务历史中提炼优化建议；
        如果 LLM 不可用，使用保守的模板更新（追加建议）。
        """
        strategy_dir = ROOT_DIR / "strategy"
        strategy_dir.mkdir(parents=True, exist_ok=True)

        try:
            if self._llm is not None:
                self._sync_strategy_with_llm(trigger, strategy_dir)
            else:
                self._sync_strategy_template(trigger, strategy_dir)
        except Exception as e:
            # 策略同步失败不影响核心流程
            self._log_event(EvolutionEvent(
                level=2,
                trigger=f"策略同步失败: {e}",
                action="跳过本次 strategy/ 更新",
                target="strategy/",
            ))

    def _sync_strategy_with_llm(self, trigger: str, strategy_dir: Path):
        """用 LLM 分析任务历史，生成有实质内容的策略更新。"""
        recent = self._task_history[-10:]
        if not recent:
            return

        # 构建任务摘要
        summary_lines = []
        for t in recent:
            summary_lines.append(
                f"- 类型: {t.get('task_type', '?')} | "
                f"成功: {t.get('success')} | "
                f"工具: {t.get('tool_calls', 0)}次 | "
                f"结果: {str(t.get('result', ''))[:120]}"
            )
        summary = "\n".join(summary_lines)

        prompt = (
            f"你是夸父的策略优化引擎。基于最近 {len(recent)} 次任务执行记录，\n"
            f"分析出可以改进的策略建议。\n\n"
            f"## 最近任务\n{summary}\n\n"
            f"## 触发原因\n{trigger}\n\n"
            f"## 输出要求\n"
            f"请输出 JSON 格式（不要其他内容）：\n"
            f"{{\n"
            f'  "rules": ["新规则1", "新规则2"],\n'
            f'  "quality_rules": [\n'
            f'    {{"severity": "warning", "rule": "具体标准描述"}}\n'
            f"  ],\n"
            f'  "strategy_updates": {{"max_retries": 3}}\n'
            f"}}\n"
            f"规则应具体、可执行，每条不超过 80 字。\n"
            f"如果没有建议，对应字段为空列表/空对象。\n"
        )

        response = self._llm.chat(messages=[
            {"role": "system", "content": "你是一个策略优化 AI。输出纯净 JSON，不要 markdown 包裹。"},
            {"role": "user", "content": prompt},
        ])

        content = ""
        if isinstance(response, dict) and response.get("success"):
            content = response["content"]
        elif isinstance(response, str):
            content = response
        else:
            content = str(response)

        # 尝试解析 JSON
        try:
            import json as _json
            # 去掉可能的 markdown 包裹
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1]
                clean = clean.rsplit("```", 1)[0]
            data = _json.loads(clean.strip())
        except (json.JSONDecodeError, ValueError):
            data = {}

        # 更新 quality.yaml
        new_rules = data.get("quality_rules", [])
        if new_rules:
            quality_path = strategy_dir / "quality.yaml"
            if quality_path.exists():
                import yaml
                with open(quality_path, "r", encoding="utf-8") as f:
                    existing = yaml.safe_load(f) or []
            else:
                existing = []
            existing.extend(new_rules)
            # 去重（按 rule 字段）
            seen = set()
            deduped = []
            for r in existing:
                if r["rule"] not in seen:
                    seen.add(r["rule"])
                    deduped.append(r)
            import yaml
            with open(quality_path, "w", encoding="utf-8") as f:
                yaml.dump(deduped, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # 更新 task_strategies.yaml 的规则
        new_strategy_rules = data.get("rules", [])
        if new_strategy_rules:
            strategies_path = strategy_dir / "task_strategies.yaml"
            if strategies_path.exists():
                import yaml
                with open(strategies_path, "r", encoding="utf-8") as f:
                    existing = yaml.safe_load(f) or {}
            else:
                existing = {}
            existing["generic"] = existing.get("generic", {})
            existing["generic"].setdefault("notes", [])
            existing["generic"]["notes"].extend(new_strategy_rules)
            import yaml
            with open(strategies_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def _sync_strategy_template(self, trigger: str, strategy_dir: Path):
        """LLM 不可用时的降级同步：根据触发原因生成有实质内容的教训。"""
        import yaml

        # 根据 trigger 提炼有意义的具体教训文本
        trigger_lower = trigger.lower()
        if "失败" in trigger:
            # 失败教训：提取领域并给出具体改进方向
            lesson = trigger.replace("(自动) ", "").strip()
            note = f"需要加强 {lesson} 类任务的预处理和边界检查"
            quality_rule = {
                "severity": "required",
                "rule": f"执行 {lesson} 类任务前先检查前置条件和错误边界"
            }
        elif "成功" in trigger:
            # 成功经验：提取可复用模式
            lesson = trigger.replace("(自动) ", "").strip()
            note = f"在 {lesson} 类任务中形成了有效的工作模式，可复用"
            quality_rule = {
                "severity": "optional",
                "rule": f"遇到 {lesson} 类任务时参考以往成功的工作流"
            }
        else:
            note = f"从实践中获得的经验: {trigger[:80]}"
            quality_rule = {
                "severity": "warning",
                "rule": f"注意: {trigger[:80]}"
            }

        # 更新 quality.yaml
        quality_path = strategy_dir / "quality.yaml"
        if quality_path.exists():
            with open(quality_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or []
        else:
            existing = []
        # 去重：避免同一条规则反复追加
        if quality_rule["rule"] not in [r.get("rule", "") for r in existing]:
            existing.append(quality_rule)
            with open(quality_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # 更新 task_strategies.yaml — 在 generic 下追加 notes
        strategies_path = strategy_dir / "task_strategies.yaml"
        if strategies_path.exists():
            with open(strategies_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        else:
            existing = {"generic": {}}
        if "generic" not in existing:
            existing["generic"] = {}
        existing["generic"].setdefault("notes", [])
        if note not in existing["generic"]["notes"]:
            existing["generic"]["notes"].append(note)
        with open(strategies_path, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ---- L0: LLM 任务经验提取 ----

    def _extract_lesson(self, recent_count: int = 3) -> Optional[str]:
        """从最近 successful 任务中提取经验笔记，供记忆沉淀。

        调用 LLM 分析最近 N 次成功任务，生成结构化经验笔记。
        这保证了即便最简单的任务，夸父也在持续积累可检索的知识。
        """
        if self._llm is None:
            return None

        successes = [t for t in self._task_history if t.get("success")][-recent_count:]
        if not successes:
            return None

        # 构建任务摘要供 LLM 分析
        task_summaries = []
        for t in successes:
            task_summaries.append(
                f"- 任务类型: {t.get('task_type', '未知')}\n"
                f"  用户请求: {t.get('result', '')[:200]}\n"
                f"  耗时: {t.get('duration', 0):.1f}s\n"
                f"  工具调用: {t.get('tool_calls', 0)} 次"
            )
        tasks_text = "\n".join(task_summaries)

        prompt = (
            f"你是一位经验丰富的 AI 助手，正在回顾最近完成的 {recent_count} 个任务。\n"
            f"请从这些任务中提取有价值的经验笔记，供未来参考。\n\n"
            f"## 最近完成的任务\n\n"
            f"{tasks_text}\n\n"
            f"## 要求\n"
            f"用中文输出，格式如下（不要多余内容）：\n"
            f"## 经验笔记\n"
            f"- 核心模式: 这些任务共同体现了什么能力？\n"
            f"- 可复用的方法: 每个任务具体是怎么完成的？\n"
            f"- 适用场景: 这些经验以后在什么情况下可以复用？\n"
        )

        try:
            response = self._llm.chat(messages=[
                {"role": "system", "content": "你是一个善于总结的学习型 AI。"},
                {"role": "user", "content": prompt},
            ])
            if isinstance(response, dict) and response.get("success"):
                return response["content"].strip()
            elif isinstance(response, str):
                return response.strip()
            return f"生成经验笔记失败: {response.get('error', '未知错误')}"
        except Exception as e:
            return f"生成经验笔记失败: {e}"

    # ---- L3 技能提取 ----

    def _extract_skill(self, target: str, trigger: str) -> Optional[str]:
        """真正将进化事件转化为 skills/ 下的 YAML 技能文件。

        P3 增强：如果有 LLM 客户端，使用 LLM 从任务历史中生成有实质内容的 skill；
        如果 LLM 不可用或生成失败，降级为保底模板填充。
        """
        task_type = Path(target).stem  # e.g. "research" from "skills/research.yaml"

        # P3: 优先用 LLM 生成有实质内容的 skill
        if self._llm is not None:
            try:
                from autonomous.skill_extractor import SkillExtractor
                extractor = SkillExtractor(self._llm.chat, self._memory)
                result = extractor.extract(
                    task_history=self._task_history,
                    task_type=task_type,
                    trigger=trigger,
                )
                if result and result.get("quality") == "pass":
                    return result["path"]
                # LLM 提取失败或质量不合格，降级到模板保底
                fallback_reason = result.get("reason", "质量不合格") if result else "LLM 调用失败"
                self._log_event(EvolutionEvent(
                    level=3,
                    trigger=f"LLM skill 提取降级: {fallback_reason}",
                    action="使用模板保底写入",
                    target=target,
                ))
            except ImportError:
                # autonomous.skill_extractor 不可用（如依赖不完整），降级
                pass
            except Exception as e:
                # LLM 调用异常，降级
                pass

        # ─── 保底：模板填充（原始方案） ───
        return self._legacy_extract_skill(target, task_type)

    def _legacy_extract_skill(self, target: str, task_type: str) -> Optional[str]:
        """保底方案：模板填充生成 skill。"""
        path = ROOT_DIR / target
        relevant_tasks = [
            t for t in self._task_history
            if t.get("task_type") == task_type and t.get("success")
        ]

        desc = f"夸父自动提取的「{task_type}」技能包（保底模板）"
        keywords = [task_type.replace("_", ""), "自动生成"]
        if relevant_tasks:
            last_result = relevant_tasks[-1].get("result", "")
            desc = f"从「{task_type}」类型任务中自动提取的最佳实践（基于 {len(relevant_tasks)} 次成功经验）"
            result_words = re.findall(r'[\u4e00-\u9fff\w]+', last_result[:500])
            extra_kw = [w for w in result_words if 2 <= len(w) <= 8]
            keywords = list(set(keywords + extra_kw[:6]))

        # 构建 steps 和 pitfalls
        steps = [
            f"这是从 {len(relevant_tasks)} 次成功「{task_type}」任务中自动提取的经验",
            "请在任务中应用之前的成功模式",
            "完成后生成结果报告",
        ]
        pitfalls = ["自动保底生成的技能包，请酌情使用"]

        if relevant_tasks:
            last = relevant_tasks[-1]
            if last.get("user_correction"):
                pitfalls.append(f"历史教训: {last['user_correction'][:100]}")

        lines = [f'name: "{task_type}"',
            f'description: "{desc}"',
            "keywords:",
        ]
        for kw in keywords:
            lines.append(f'  - "{kw}"')
        lines.append("steps:")
        for s in steps:
            lines.append(f'  - "{s}"')
        lines.append("examples:")
        lines.append('  - "无（保底生成）"')
        lines.append("pitfalls:")
        for p in pitfalls:
            lines.append(f'  - "{p}"')
        lines.append(f"usage_count: 0")
        lines.append(f"created_at: {int(time.time())}")
        lines.append(f"source: auto_extracted_from_L3_fallback")

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
