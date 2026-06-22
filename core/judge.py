"""single-LLM-call judge + extractor for kuafu evolution.

职责：
仅在 Observer 信号表明"可能有价值"时触发，用一次 LLM 调用完成：
1. 判断当前经验是否值得写入技能
2. 提取技能名称、触发条件、步骤
3. 选择进化模式：CAPTURED / FIX / DERIVED
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger("kuafu.judge")


SKILL_EXTRACT_PROMPT = """你是一个经验提取器。分析以下任务结果，判断是否值得保存为可复用的技能。

## 输入
- 任务类型: {task_type}
- 是否成功: {success}
- 工具调用次数: {tool_calls}
- 使用工具: {tools_used}
- 是否有用户纠正: {has_user_correction}
- 连续失败次数: {consecutive_failures}
- 错误数量: {error_count}
- 同类任务历史次数: {task_history}
- 是否有新错误类型: {has_unknown_error}
- 错误摘要: {error_summary}
- 结果摘要: {result_summary}
- 已有同名技能: {existing_skill}
- 该技能的历史使用信息: {skill_usage_stats}

## 对话上下文（最近关键轮次）
{dialogue_context}

## 判断标准
返回 "worth_learning": true 的场景：
1. 用户纠正 (has_user_correction=true) — 用户的明确指导必须记住
2. 连续失败 ≥ 2 次 — 需要修正避免继续失败
3. 成功完成了 5+ 步工具的复杂任务 — 工作流值得复用
4. 发现了新错误类型 — 记录错误处理策略
5. 是之前没见过的任务类型 (task_history ≤ 1) — 探索新领域记录

返回 "worth_learning": false 的场景：
1. 简单任务 (≤ 3 步工具调用) 且成功 — 太 trivial
2. 只读操作（查看、读取、搜索）且成功
3. 所有错误都是已知错误
4. **已有同名技能但历史成功率低于 30%** — 这个技能模式不可靠，不应继续学习

## 历史使用信息说明
{skill_usage_guide}

## 进化模式选择（evolution_mode）
当 worth_learning=true 时，选择以下一种模式：

- "CAPTURED": 全新的技能模式。判断依据：没有相同或高度相似的已有技能；或这是一个全新的任务类型。
- "FIX": 修复已有技能。判断依据：已有同名技能（existing_skill 不为空），且当前执行暴露了其中的错误/遗漏。会覆盖原始文件。
- "DERIVED": 从已有技能衍生增强版。判断依据：已有类似技能但当前经验提供了新的变体/场景。会创建新文件（原名称加 _v2）。

## 输出格式（严格 JSON，不要多余文字）
{{
    "worth_learning": true|false,
    "evolution_mode": "CAPTURED"|"FIX"|"DERIVED",
    "reason": "简短理由，用中文",
    "skill": null 或 {{
        "name": "技能名（必须英文小写，hyphens连接，如 fix-pip-install）",
        "trigger": "什么场景触发（中文，一句话）",
        "steps": ["步骤1", "步骤2"],
        "error_pattern": "相关错误模式（如果有）"
    }}
}}

重要：name 字段必须使用英文小写字母、数字和连字符(-)，不能包含中文、空格或特殊字符。
如果技能是对现有技能的修复或衍生，name 必须与已有技能名相同。
"""


def build_digest(observation: Any, state: Any, messages: Optional[list] = None) -> dict:
    """从 Observation 和 EvolutionState 构建 LLM 输入摘要。"""
    consecutive = 0
    task_history = 0
    if state:
        consecutive = state.get("consecutive_fail", 0)
        task_history = state.get("count", 0)

    errors = getattr(observation, 'errors', []) or []
    tool_errors = getattr(observation, 'tool_errors', []) or []

    all_errors = list(errors)
    for te in tool_errors:
        all_errors.append(te.error_message)

    # 检查是否有同名已有技能（通过 Observation 的 skill_name 字段）
    existing_skill = getattr(observation, 'skill_name', None) or ""

    # 从对话历史提取关键上下文
    dialogue_context = ""
    if messages:
        recent = messages[-12:] if len(messages) > 12 else messages
        lines = []
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or role == "system":
                continue
            snippet = content[:200].replace("\n", " ")
            lines.append(f"[{role}] {snippet}")
        dialogue_context = "\n".join(lines[-6:]) if lines else "无"

    # 从 skill_usage.jsonl 读取历史使用信息
    skill_usage_stats = ""
    skill_usage_guide = ""
    _completion_log = Path(__file__).resolve().parent.parent / "logs" / "skill_usage.jsonl"
    if _completion_log.exists():
        try:
            _lines = _completion_log.read_text(encoding="utf-8").strip().split("\n")
            from collections import Counter as _Counter
            _total = _Counter()
            _ok = _Counter()
            for _line in _lines[-1000:]:
                try:
                    _r = json.loads(_line)
                    _sn = _r.get("skill", "")
                    _total[_sn] += 1
                    if _r.get("success"):
                        _ok[_sn] += 1
                except:
                    pass
            if _total:
                _stats = []
                for _sn, _n in _total.most_common(10):
                    _ok_n = _ok.get(_sn, 0)
                    _rate = f"{_ok_n*100//_n}%" if _n > 0 else "N/A"
                    _stats.append(f"  {_sn}: 使用{_n}次, 成功{_ok_n}次({_rate})")
                skill_usage_stats = "\n".join(_stats)
                _guides = []
                for _sn, _n in _total.most_common(10):
                    _ok_n = _ok.get(_sn, 0)
                    _r = _ok_n / _n if _n > 0 else 0
                    if _r < 0.3 and _n >= 3:
                        _guides.append(f"  {_sn}: 成功率{_r:.0%}({_ok_n}/{_n}) - 建议不再学习这类技能")
                    elif _r >= 0.8 and _n >= 3:
                        _guides.append(f"  {_sn}: 成功率{_r:.0%}({_ok_n}/{_n}) - 可靠技能，可继续迭代")
                if _guides:
                    skill_usage_guide = "基于历史数据的使用建议：\n" + "\n".join(_guides)
                else:
                    skill_usage_guide = "暂无足够使用数据（各技能使用次数不足3次）"
        except Exception:
            skill_usage_stats = "读取失败"
            skill_usage_guide = "数据不可用"

    return {
        "task_type": getattr(observation, 'task_type', 'generic'),
        "success": getattr(observation, 'success', False),
        "tool_calls": getattr(observation, 'tool_calls', 0),
        "tools_used": ", ".join(sorted(getattr(observation, 'tools_used', set()))),
        "has_user_correction": getattr(observation, 'has_user_correction', False),
        "consecutive_failures": consecutive,
        "error_count": len(all_errors),
        "task_history": task_history,
        "has_unknown_error": getattr(observation, 'has_unknown_error', False),
        "error_summary": "; ".join(all_errors[:3])[:300] if all_errors else "",
        "result_summary": (getattr(observation, 'result', '') or '')[:300],
        "existing_skill": existing_skill,
        "dialogue_context": dialogue_context,
        "skill_usage_stats": skill_usage_stats,
        "skill_usage_guide": skill_usage_guide,
    }


class Judge:
    """单次 LLM 调用的判断器。

    用法：
        judge = Judge(llm_chat_fn)
        result = judge.evaluate(observation, state_entry)
        # result.worth_learning → bool
        # result.skill → dict | None (when worth_learning=True)
    """

    def __init__(self, llm_chat_fn: callable):
        self._llm_chat = llm_chat_fn

    def evaluate(self, observation: Any, state_entry: Optional[dict] = None, messages: Optional[list] = None) -> dict:
        """一次 LLM 调用：判断+提取。

        Args:
            observation: Observer 产出的 Observation 对象
            state_entry: EvolutionState 中该 task_type 的 entry（可选）
            messages: 完整对话历史（可选）

        Returns:
            dict: {
                "worth_learning": bool,
                "evolution_mode": "CAPTURED" | "FIX" | "DERIVED",
                "reason": str,
                "skill": dict | None,
            }
        """
        digest = build_digest(observation, state_entry, messages=messages)
        # 确保 digest 中不含未转义的 { 和 }，防止 str.format 崩溃
        safe_digest = {}
        for k, v in digest.items():
            if isinstance(v, str):
                v = v.replace("{", "{{").replace("}", "}}")
            safe_digest[k] = v
        prompt = SKILL_EXTRACT_PROMPT.format(**safe_digest)

        try:
            result = self._llm_chat([
                {"role": "system", "content": "你是夸父的进化判断模块。严格按输出格式返回 JSON。"},
                {"role": "user", "content": prompt},
            ])
            content = self._parse_content(result)
            if not content:
                return self._default_fallback("LLM 返回空内容")

            parsed = json.loads(content)
            if "worth_learning" not in parsed:
                return self._default_fallback("缺少 worth_learning 字段")

            return {
                "worth_learning": bool(parsed["worth_learning"]),
                "evolution_mode": parsed.get("evolution_mode", "CAPTURED"),
                "reason": parsed.get("reason", ""),
                "skill": parsed.get("skill"),
            }

        except json.JSONDecodeError:
            return self._default_fallback(
                f"JSON 解析失败: {content[:200] if content else '空'}"
            )
        except Exception as e:
            return self._default_fallback(f"Judge 异常: {e}")

    # ── 内部 ──

    @staticmethod
    def _parse_content(result) -> str:
        if isinstance(result, dict):
            content = result.get("content", "")
            if isinstance(content, str):
                return content
        elif isinstance(result, str):
            return result
        return ""

    @staticmethod
    def _default_fallback(reason: str) -> dict:
        return {
            "worth_learning": False,
            "evolution_mode": "CAPTURED",
            "reason": f"降级（未学）: {reason}",
            "skill": None,
        }
