"""P3 — LLM 驱动的技能自动提取器 (SkillExtractor)。

职责：
1. 接收 L3 进化事件 + 任务历史，用 LLM 生成有实操价值的 skill
2. 生成的 skill 包含具体步骤、实际参数、真实陷阱
3. 质量校验——不合格的 skill 不写入

区别于原有 _extract_skill（纯模板填充，steps 是空话），
SkillExtractor 让 LLM 真正分析「这个类型的任务到底是怎么完成的」。

核心设计原则：
- 用 LLM 分析任务历史中的具体操作模式，而非填空
- 生成的 skill 必须包含可执行的步骤、具体的工具调用示例
- 质量门槛：必须提取出至少 2 个有意义的步骤和 1 个陷阱
"""

import json
import time
import hashlib
import re
from pathlib import Path
from typing import Any, Optional, Callable

ROOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT_DIR / "skills"


def _generate_id() -> str:
    raw = f"skill_extract|{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class SkillExtractor:
    """LLM 驱动的技能自动提取器。

    用法:
        extractor = SkillExtractor(llm_chat_fn)
        result = extractor.extract(task_history, task_type="research", trigger="...")
        if result and result.get("quality") == "pass":
            # result["path"] 指向写入的 YAML 文件路径
    """

    def __init__(
        self,
        llm_chat_fn: Callable,
        memory_remember_fn: Optional[Callable] = None,
    ):
        """
        Args:
            llm_chat_fn: callable(messages: list[dict]) -> dict
                调用 LLM 分析任务历史。返回值格式: {"success": bool, "content": str}
            memory_remember_fn: Optional callable(key, content, tags)
                可选，写入记忆。
        """
        self._llm_chat = llm_chat_fn
        self._remember = memory_remember_fn
        self._log: list[dict] = []

    # ─── 公开接口 ───────────────────────────

    def extract(
        self,
        task_history: list[dict],
        task_type: str,
        trigger: str,
    ) -> Optional[dict]:
        """用 LLM 从任务历史中抽取 skill，写入文件。

        Args:
            task_history: evolution.py 的 task_history（完整历史）
            task_type: 任务类型（如 "research"、"file_operations"）
            trigger: 进化触发原因（用于记录）

        Returns:
            {
                "path": str,         # 写入的文件路径
                "quality": str,      # "pass" | "fail"
                "name": str,         # skill 名称
                "reason": str,       # 质量判断原因
                "id": str,           # 本次提取的唯一 ID
            }
            或 None（LLM 调用失败）
        """
        # 1. 筛选相关任务
        relevant = [
            t for t in task_history
            if t.get("task_type") == task_type and t.get("success")
        ]
        if not relevant:
            return self._result(None, "fail", "无相关成功任务", task_type)

        # 2. 构建 LLM 输入
        llm_result = self._call_llm_for_skill(relevant, task_type)
        if not llm_result:
            return None

        # 3. 解析 LLM 输出
        parsed = self._parse_llm_output(llm_result)
        if not parsed:
            return self._result(None, "fail", "LLM 输出解析失败", task_type)

        # 4. 质量校验
        quality = self._validate(parsed)
        if quality != "pass":
            return self._result(parsed, quality, self._last_validate_reason, task_type)

        # 5. 写入文件
        filepath = self._write_skill(parsed, task_type)

        # 6. 记录
        extract_id = _generate_id()
        self._log.append({
            "id": extract_id,
            "task_type": task_type,
            "trigger": trigger[:120],
            "quality": quality,
            "path": str(filepath),
            "timestamp": time.time(),
        })

        # 7. 记忆联动
        if self._remember:
            try:
                self._remember(
                    key=f"skill:{task_type}:{int(time.time())}",
                    content=(
                        f"【P3 SkillExtractor】从 {len(relevant)} 次成功任务中"
                        f"提取技能「{task_type}」→ {filepath.name}"
                    ),
                    tags=["evolution", "skill_extract", "P3"],
                )
            except Exception:
                pass

        return {
            "path": str(filepath),
            "quality": "pass",
            "name": task_type,
            "reason": f"基于 {len(relevant)} 次成功经验生成",
            "id": extract_id,
        }

    def get_log(self) -> list[dict]:
        """获取提取历史。"""
        return list(self._log)

    # ─── 内部方法 ───────────────────────────

    def _build_task_summary(self, relevant: list[dict], max_tasks: int = 5) -> str:
        """构建给 LLM 的任务摘要。"""
        recent = relevant[-max_tasks:]
        lines = []
        for i, t in enumerate(recent, 1):
            result_text = (t.get("result") or t.get("summary") or "")[:500]
            user_correction = t.get("user_correction", "")
            lines.append(f"## 任务 {i}")
            lines.append(f"- 用户请求摘要: {result_text[:200]}")
            lines.append(f"- 耗时: {t.get('duration', 0):.1f}s")
            lines.append(f"- 工具调用次数: {t.get('tool_calls', 0)}")
            if user_correction:
                lines.append(f"- 用户纠正: {user_correction[:150]}")
            lines.append("")
        return "\n".join(lines)

    def _call_llm_for_skill(self, relevant: list[dict], task_type: str) -> Optional[str]:
        """调用 LLM 生成 skill 内容。"""
        task_summary = self._build_task_summary(relevant)
        task_count = len(relevant)

        prompt = _BUILD_PROMPT(task_summary, task_type, task_count)

        try:
            result = self._llm_chat([
                {
                    "role": "system",
                    "content": (
                        "你是夸父的技能提取专家。只输出严格 JSON，不要任何 markdown 标记或额外文字。"
                    ),
                },
                {"role": "user", "content": prompt},
            ])
            if isinstance(result, dict) and result.get("success"):
                return result["content"].strip()
            elif isinstance(result, str):
                return result.strip()
            return None
        except Exception as e:
            # 失败时记录
            self._log.append({
                "id": _generate_id(),
                "task_type": task_type,
                "error": str(e),
                "timestamp": time.time(),
            })
            return None

    def _parse_llm_output(self, content: str) -> Optional[dict]:
        """从 LLM 输出中解析 JSON。"""
        # 尝试直接解析
        content = content.strip()
        # 移除可能的 markdown 围栏
        if content.startswith("```"):
            # 找第一个 { 和最后一个 }
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start : end + 1]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # 尝试修复常见格式问题
            try:
                # 移除注释（// 风格）
                cleaned = re.sub(r'(?<!")\s*//[^\n]*', '', content)
                # 移除尾部逗号
                cleaned = re.sub(r',\s*}', '}', cleaned)
                cleaned = re.sub(r',\s*]', ']', cleaned)
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                return None

        # 验证必要字段
        if not isinstance(parsed, dict):
            return None
        name = parsed.get("name", "")
        steps = parsed.get("steps", [])
        if not name or not isinstance(steps, list) or len(steps) < 1:
            return None

        return {
            "name": str(name).strip(),
            "description": str(parsed.get("description", "")).strip(),
            "steps": [str(s).strip() for s in steps if s],
            "pitfalls": [str(p).strip() for p in parsed.get("pitfalls", []) if p],
            "example_scenario": str(parsed.get("example_scenario", "")).strip(),
            "example_steps": [str(s).strip() for s in parsed.get("example_steps", []) if s],
        }

    _last_validate_reason: str = ""

    def _validate(self, parsed: dict) -> str:
        """质量校验。

        Returns:
            "pass": 质量合格
            "fail": 质量不合格
        """
        steps = parsed.get("steps", [])
        pitfalls = parsed.get("pitfalls", [])
        desc = parsed.get("description", "")
        example = parsed.get("example_scenario", "")

        reason_parts = []

        if len(steps) < 2:
            reason_parts.append(f"steps 不足 2 条（共 {len(steps)} 条）")
        if not pitfalls:
            reason_parts.append("无 pitfalls")
        if len(desc) < 10:
            reason_parts.append("description 太短")
        if not example:
            reason_parts.append("无示例场景")

        # 检查 steps 是否为空话（常见空话模式）
        vague_patterns = [
            "根据具体需求", "灵活应用", "根据实际情况",
            "根据当前任务", "完成主要工作", "报告最终结果",
        ]
        for s in steps:
            for pattern in vague_patterns:
                if pattern in s and s not in reason_parts:
                    # 如果所有步骤都是空话，判为 fail
                    pass

        # 如果所有步骤都是空话
        if len(steps) >= 2:
            vague_count = sum(
                1 for s in steps
                if any(p in s for p in vague_patterns)
            )
            if vague_count == len(steps):
                reason_parts.append("所有 steps 均为空话")
            else:
                # 只要有一部分具体步骤就算 pass
                reason_parts = [r for r in reason_parts if "不足 2 条" not in r]

        if reason_parts:
            self._last_validate_reason = "; ".join(reason_parts)
            return "fail"
        return "pass"

    def _write_skill(self, parsed: dict, task_type: str) -> Path:
        """将解析后的 skill 写入 YAML 文件。"""
        name = parsed.get("name", task_type)
        desc = parsed.get("description", f"从 {task_type} 任务中自动提取的技能")
        steps = parsed.get("steps", [])
        pitfalls = parsed.get("pitfalls", [])
        example_scenario = parsed.get("example_scenario", "")
        example_steps = parsed.get("example_steps", [])

        # 从 description 中提取关键词
        keywords = [task_type.replace("_", " ")]
        desc_words = re.findall(r'[\u4e00-\u9fff\w]+', desc)
        keywords.extend([w for w in desc_words if 2 <= len(w) <= 8])
        keywords = list(dict.fromkeys(keywords))[:10]  # 去重 + 最多 10 个

        lines = [
            f'name: "{name}"',
            f'description: "{desc}"',
            "keywords:",
        ]
        for kw in keywords:
            lines.append(f'  - "{kw}"')
        lines.append("steps:")
        for s in steps:
            lines.append(f'  - "{s}"')
        lines.append("examples:")
        if example_scenario:
            lines.append(f'  - "场景: {example_scenario}"')
        for s in example_steps:
            lines.append(f'  - "{s}"')
        lines.append("pitfalls:")
        for p in pitfalls:
            lines.append(f'  - "{p}"')
        lines.append(f"usage_count: 0")
        lines.append(f"created_at: {int(time.time())}")
        lines.append(f"source: auto_extracted_from_P3")

        content = "\n".join(lines) + "\n"
        filepath = SKILLS_DIR / f"{name}.yaml"
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _result(self, parsed: Optional[dict], quality: str, reason: str, task_type: str) -> dict:
        return {
            "path": str(SKILLS_DIR / f"{task_type}.yaml"),
            "quality": quality,
            "name": task_type,
            "reason": reason,
            "id": _generate_id(),
        }


# ─── 工具函数 ──────────────────────────────


def _BUILD_PROMPT(task_summary: str, task_type: str, task_count: int) -> str:
    """构造 LLM 用的 prompt。

    放在模块级函数中避免 f-string 的复杂转义问题。
    """
    nl = "\n"
    return (
        f"你是一位经验丰富的 AI Agent 技能工程师。{nl}"
        f"请基于以下 {task_count} 次成功完成的「{task_type}」类型任务，{nl}"
        f"提取一份可复用的技能包（对应夸父 AI Agent 的 skills/ 目录）。{nl}"
        f"{nl}"
        f"## 任务历史{nl}"
        f"{nl}"
        f"{task_summary}{nl}"
        f"## 要求{nl}"
        f"{nl}"
        f"请严格按以下 JSON 格式输出（不要加 markdown 代码块标记，纯 JSON 字符串）：{nl}"
        f"{nl}"
        f"{_JSON_SCHEMA()}{nl}"
        f"## 质量要求{nl}"
        f"{nl}"
        f"- steps 必须基于实际任务历史，给出具体可操作的步骤，不是空话{nl}"
        f"- 至少 2 个 steps，最多 6 个{nl}"
        f"- pitfalls 至少 1 条，最多 4 条{nl}"
        f"- 所有的步骤和陷阱必须针对「{task_type}」类型任务{nl}"
        f"- 如果任务历史中有用户纠正，务必在 pitfalls 中体现{nl}"
        f"- 示例场景和步骤要真实，不要说「根据具体需求灵活应用」这种空话{nl}"
    )


def _JSON_SCHEMA() -> str:
    """返回 JSON 格式模板（普通函数，避免多层转义）。"""
    return """{
  "name": "skill 名称（英文小写，如 web_search）",
  "description": "一句话描述这个技能做什么",
  "steps": [
    "第1步：具体的执行步骤，包含具体工具名和参数",
    "第2步：...",
    "第3步：..."
  ],
  "pitfalls": [
    "第1个容易踩的坑",
    "第2个容易踩的坑"
  ],
  "example_scenario": "一个典型的任务场景描述（用户会怎么问）",
  "example_steps": [
    "具体示例步骤 1",
    "具体示例步骤 2"
  ]
}"""
