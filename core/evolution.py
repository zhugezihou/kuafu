"""
夸父进化系统 — 「即兴进化」模式。

D 方案（2026-05-24）：
废除 L0-L5 分级体系，改为：
1. 每次任务完成后，让 LLM 直接判断「有什么值得学的」
2. 如果有实质内容 → 当场调用 LLM 提取经验，写入 memory 和 SKILL.md
3. 没有值得学的 → 安静跳过（不为进化而进化）
4. 不再有冷却时间、分级计数、后台审批排队

核心理念：务实即兴进化 — 发现问题 → 当场解决 → 学完就忘掉流程。
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
    level: int = 1               # 始终为 1（D 方案只有一种等级）
    trigger: str = ""            # 触发原因
    action: str = ""             # 具体做了什么
    target: str = ""             # 改了什么文件/配置
    timestamp: float = field(default_factory=time.time)
    hash: str = ""

    def __post_init__(self):
        raw = f"{self.level}|{self.trigger}|{self.action}|{self.timestamp}"
        self.hash = hashlib.sha256(raw.encode()).hexdigest()[:12]


class EvolutionEngine:
    """进化引擎 — D 方案「即兴进化」。

    每次任务完成后调用 eveluate_and_evolve()，LLM 当场判断：
    - 本次任务是否值得学？
    - 值得学的话提取出什么经验？
    - 直接写入 memory + SKILL.md

    不再有分级、排队、冷却、审批。
    """

    def __init__(self, task_history: Optional[list] = None, memory=None, llm=None):
        self._task_history = task_history or []
        self._log_path = EVOLUTION_LOG
        self._ensure_log()
        self._memory = memory
        self._llm = llm

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
        """D 方案：每次任务完成后 LLM 当场判断是否需要学。

        核心逻辑：
        1. 先记录任务
        2. 如果 LLM 不可用 → 跳过
        3. 让 LLM 判断本次任务是否有值得学习的经验
        4. 有实质内容 → 提取并写入 skills/
        5. 没有 → 安静跳过

        Args:
            task_result: {
                "success": bool,
                "errors": list[str],
                "tool_calls": int,
                "task_type": str,
                "duration": float,
                "result": str,
            }
        """
        self._record_task(task_result)

        # 没有 LLM 就别折腾了
        if self._llm is None:
            return None

        # ### 核心：让 LLM 当场判断本次任务是否有值得学的 ###
        try:
            should_learn = self._judge_learning_value(task_result)
        except Exception:
            return None

        if not should_learn:
            # 没有值得学的 → 安静跳过
            return None

        # 有值得学的 → 当场提取
        try:
            skill_path = self._extract_skill_immediately(task_result)
        except Exception:
            return None

        if not skill_path:
            return None

        event = EvolutionEvent(
            level=1,
            trigger=f"从「{task_result.get('task_type', 'generic')}」任务中提取经验",
            action=f"生成技能文件: {Path(skill_path).name}",
            target=skill_path,
        )
        self._log_event(event)
        return event

    def _judge_learning_value(self, task_result: dict) -> bool:
        """让 LLM 判断本次任务是否有值得学的内容。

        返回 True/False —— 快速、轻量、一次 LLM 调用。
        """
        prompt = (
            "你是一个经验判断器。用户的 AI agent 刚刚完成了一项任务。\n"
            "请判断：从这项任务中是否能提取出值得保存的经验/教训/最佳实践？\n\n"
            "任务信息:\n"
            f"- 任务类型: {task_result.get('task_type', 'generic')}\n"
            f"- 是否成功: {'是' if task_result.get('success') else '否'}\n"
            f"- 工具调用次数: {task_result.get('tool_calls', 0)}\n"
            f"- 结果摘要: {str(task_result.get('result', ''))[:300]}\n"
            f"- 错误: {str(task_result.get('errors', []))[:200]}\n\n"
            "判断标准（宽松导向——宁可多学不要错过）：\n"
            "1. 任务涉及任何具体操作步骤、技巧、配置方法 → 值得学\n"
            "2. 用户纠正了 agent 的行为 → 值得学\n"
            "3. 遇到了错误（无论是否修复成功）→ 值得学\n"
            "4. 发现了更好的实现方式 → 值得学\n"
            "5. 任务中包含了具体的技术名词、工具名、库名（如 asyncio、weakref、Docker 等）→ 值得学\n"
            "6. 工具调用次数 >= 3 → 值得学（说明任务需要多步操作）\n"
            "7. 只有一条标准可以判不值得学：纯粹问候/闲聊，且没有任何技术内容\n\n"
            "请只输出 'yes' 或 'no'，不要其他内容。"
        )

        response = self._llm.chat(messages=[
            {"role": "system", "content": "你是一个简洁的判断器。只输出 yes 或 no。"},
            {"role": "user", "content": prompt},
        ])

        content = ""
        if isinstance(response, dict) and response.get("success"):
            content = response.get("content", "").strip().lower()
        elif isinstance(response, str):
            content = response.strip().lower()

        return content.startswith("yes")

    def _extract_skill_immediately(self, task_result: dict) -> Optional[str]:
        """LLM 提取技能并直接写入 skills/ 目录。

        不经过 L0-L5 分级、不排队、不审批。
        """
        task_type = task_result.get("task_type", "generic")

        # 收集相关历史（同类任务最多最近 5 条）
        same_type = [t for t in self._task_history[-20:]
                     if t.get("task_type") == task_type][-5:]

        prompt = (
            "你是一位经验提取专家。从 AI agent 的任务执行记录中，\n"
            "提取出有价值的经验和步骤，生成一个可复用的技能 guide。\n\n"
            f"## 任务类型\n{task_type}\n\n"
            "## 最近的同类任务记录\n"
        )
        for i, t in enumerate(same_type):
            prompt += (
                f"\n### 任务 {i+1}\n"
                f"- 成功: {'是' if t.get('success') else '否'}\n"
                f"- 用户请求: {str(t.get('result', ''))[:200]}\n"
                f"- 错误: {str(t.get('errors', []))[:150]}\n"
            )

        prompt += (
            "\n## 提取要求\n"
            "请分析这些任务，输出一个结构化的技能，包括：\n"
            "1. 技能名称（简洁，如 '代码审查技能'、'Linux 故障排查'）\n"
            "2. 描述（一句话说明这个技能是做什么的）\n"
            "3. 步骤（3-8 步，具体可执行，每步 10-30 字）\n"
            "4. 注意事项（如果有坑要提醒）\n"
            "5. 触发关键词（3-5 个，用户说什么时可以用到这个技能）\n\n"
            "## 输出格式\n"
            "请用以下格式输出（不要用 markdown 代码块包裹）：\n"
            "---\n"
            "name: 技能名称\n"
            "description: 一句话描述\n"
            "task_type: {task_type}\n"
            "keywords:\n"
            "  - 关键词1\n"
            "  - 关键词2\n"
            "steps:\n"
            "  - 步骤1\n"
            "  - 步骤2\n"
            "  - 步骤3\n"
            "pitfalls:\n"
            "  - 注意事项1\n"
            "---\n"
        )

        response = self._llm.chat(messages=[
            {"role": "system", "content": "你是一个技能提取专家。输出结构化内容，不要多余的话。"},
            {"role": "user", "content": prompt},
        ])

        content = ""
        if isinstance(response, dict) and response.get("success"):
            content = response.get("content", "").strip()
        elif isinstance(response, str):
            content = response.strip()
        else:
            return None

        if not content:
            return None

        # 从 LLM 输出中提取 YAML frontmatter
        return self._write_skill_yaml(task_type, content)

    def _write_skill_yaml(self, task_type: str, content: str) -> Optional[str]:
        """将 LLM 提取的技能内容写入 skills/ 目录的 YAML 文件。"""
        # 解析 YAML frontmatter
        name = task_type
        description = f"从{task_type}类任务中自动提取的技能"
        keywords = [task_type]
        steps = []
        pitfalls = []

        # 从 --- 包裹的 frontmatter 中提取
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                body = parts[1].strip()
                for line in body.split("\n"):
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("\"'")
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip("\"'")
                    elif line.startswith("  - ") and "steps:" in body:
                        # steps 和 keywords/pitfalls 的解析放在下面
                        pass

                # 暴力解析各字段
                in_section = None
                for line in body.split("\n"):
                    line = line.strip()
                    if line == "keywords:":
                        in_section = "keywords"
                        continue
                    elif line == "steps:":
                        in_section = "steps"
                        continue
                    elif line == "pitfalls:":
                        in_section = "pitfalls"
                        continue
                    elif line.startswith("name:") or line.startswith("description:") or line.startswith("task_type:")\
                            or line == "" or line.startswith("---"):
                        in_section = None
                        continue

                    if in_section == "keywords" and line.startswith("- "):
                        kw = line[2:].strip().strip("\"'")
                        if kw and kw not in keywords:
                            keywords.append(kw)
                    elif in_section == "steps" and line.startswith("- "):
                        s = line[2:].strip().strip("\"'")
                        if s and s not in steps:
                            steps.append(s)
                    elif in_section == "pitfalls" and line.startswith("- "):
                        p = line[2:].strip().strip("\"'")
                        if p and p not in pitfalls:
                            pitfalls.append(p)

        # 如果 LLM 输出不是标准的 frontmatter 格式，尝试直接解析 name 和描述
        if not steps:
            # 再次尝试：从非 frontmatter 文本中提取步骤行
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("- ") and len(line) > 4 and not line.startswith("- 关键词"):
                    if line[2:].strip() not in steps:
                        steps.append(line[2:].strip())

        if not steps:
            steps = [f"执行 {task_type} 类型任务的标准流程"]

        # 构建最终 YAML
        lines = [
            f'name: "{name}"',
            f'description: "{description[:200]}"',
            f"task_type: {task_type}",
            "keywords:",
        ]
        for kw in keywords:
            lines.append(f'  - "{kw}"')
        lines.append("steps:")
        for s in steps[:10]:  # 最多 10 步
            lines.append(f'  - "{s}"')
        lines.append("pitfalls:")
        for p in pitfalls[:5]:
            lines.append(f'  - "{p}"')
        lines.append(f"usage_count: 0")
        lines.append(f"created_at: {int(time.time())}")
        lines.append(f"source: kuafu_impromptu_evolution")

        yaml_content = "\n".join(lines) + "\n"

        # 写文件
        skills_dir = ROOT_DIR / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        safe_name = name.lower().replace(" ", "_").replace("-", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        if not safe_name:
            safe_name = task_type
        filepath = skills_dir / f"{safe_name}.yaml"

        filepath.write_text(yaml_content, encoding="utf-8")

        # 同时写入 memory
        if self._memory is not None:
            try:
                self._memory.remember(
                    key=f"skill:{safe_name}:{int(time.time())}",
                    content=f"发现新技能「{name}」: {description[:100]}",
                    tags=["skill", "evolution", task_type],
                )
            except Exception:
                pass

        return str(filepath)

    def _log_event(self, event: EvolutionEvent):
        try:
            logs = json.loads(self._log_path.read_text(encoding="utf-8"))
            logs.append(asdict(event))
            self._log_path.write_text(
                json.dumps(logs, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ---- 查询 ----

    def get_evolution_history(self, limit: int = 20) -> list[dict]:
        try:
            logs = json.loads(self._log_path.read_text(encoding="utf-8"))
            return logs[-limit:]
        except Exception:
            return []

    def get_evolution_stats(self) -> dict:
        logs = self.get_evolution_history(limit=9999)
        return {
            "total_evolutions": len(logs),
            "by_level": {1: len(logs)},
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

    def get_task_type_count(self, task_type: str) -> int:
        """返回某种任务类型的历史记录总数。"""
        return sum(1 for t in self._task_history if t.get("task_type") == task_type)
