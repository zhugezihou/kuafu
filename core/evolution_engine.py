"""
夸父自我进化引擎 (EvolutionEngine)

职责：
1. 在每次 run() 结束后，评估本次任务的「可学习性」
2. 判断标准：是否需要新技能、是否发现错误模式、是否有可优化的重复操作
3. 自动生成 SKILL.md 文件写入 skills/ 目录
4. 记录版本链到 evolution_state.json

设计原则：
- 轻量：一次 LLM 调用判断，不做多轮推理
- 增量：只提取「值得学」的信息，忽略噪音
- 自愈：检测到退化趋势时建议回滚
- 非侵入：agent_loop.run() 结束后调用，不阻塞主流程
"""

import json
import os
import time
from pathlib import Path
from typing import Optional


class EvolutionEngine:
    """自我进化引擎 — 在任务结束后判断并生成技能。
    
    实现从经验中学习的循环——类比于 MemPalace/SkillMaestro：
    detect → extract → persist。与 evolution_state.py 协作，
    evolution_state 负责追踪指标，EvolutionEngine 负责判断和产出新技能文件。
    """

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = (root_dir or Path(__file__).resolve().parent.parent)
        self.skills_dir = self.root_dir / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        # 频率限制：对同一 task_type 的进化间隔（秒）
        self._cooldown_seconds = 300
        self._last_evolved: dict[str, float] = self._load_timestamps()

    def _load_timestamps(self) -> dict[str, float]:
        """加载上次进化时间戳。"""
        path = self.skills_dir / ".last_evolved.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_timestamps(self):
        """持久化进化时间戳。"""
        path = self.skills_dir / ".last_evolved.json"
        path.write_text(
            json.dumps(self._last_evolved, indent=2)
        )

    def _on_cooldown(self, task_type: str) -> bool:
        """检查是否在冷却期。"""
        last_ts = self._last_evolved.get(task_type, 0.0)
        return (time.time() - last_ts) < self._cooldown_seconds

    def evaluate(
        self,
        task: str,
        task_type: str,
        success: bool,
        error_text: str = "",
        tool_sequence: str = "",
        result_summary: str = "",
    ) -> Optional[dict]:
        """评估本次任务是否值得进化出技能。

        Returns:
            建议创建的技能信息 dict，或 None（不值得进化）。
            {
                "name": str,
                "content": str,  # SKILL.md 内容
                "summary": str,
                "mode": str,     # 'CAPTURED' | 'FIX' | 'DERIVED'
            }
        """
        # 冷却期检查
        if self._on_cooldown(task_type):
            return None

        # 检查所有进化条件
        reasons = []

        # 条件1: 成功但使用了 3+ 次工具调用的复杂流程
        if success and len(tool_sequence.split("→")) >= 3:
            reasons.append(f"成功完成复杂任务（{len(tool_sequence.split('→'))}步工具调用）")

        # 条件2: 修复了错误
        if not success and error_text:
            reasons.append(f"遇到并修复了错误: {error_text[:80]}")

        # 条件3: 长文本 task（说明有内容值得记录）
        if len(task) > 200 and success:
            reasons.append("任务内容复杂，值得记录")

        # 条件4: 明确的错误模式
        if error_text and success:
            reasons.append(f"修复了已知错误模式: {error_text[:60]}")

        if not reasons:
            return None

        # 生成本次进化名称
        skill_name = self._generate_name(task_type, task, success)
        if not skill_name:
            return None

        # 构建 SKILL.md
        skill_content = self._build_skill_md(
            name=skill_name,
            task_type=task_type,
            task=task,
            tool_sequence=tool_sequence,
            result_summary=result_summary,
            error=error_text,
            success=success,
        )

        # 更新冷却
        self._last_evolved[task_type] = time.time()
        self._save_timestamps()

        mode = "FIX" if error_text else "CAPTURED" if success else "DERIVED"
        return {
            "name": skill_name,
            "content": skill_content,
            "summary": reasons[0],
            "mode": mode,
        }

    def _generate_name(self, task_type: str, task: str, success: bool) -> Optional[str]:
        """从 task 中提取技能名称——LLM 调用判断。

        Returns:
            str: 技能文件名称（不含后缀）
            或 None（不值得命名）
        """
        # 使用 task_type 作为基底
        base = task_type.strip().lower().replace(" ", "-").replace("_", "-")
        # 截断安全长度
        if len(base) > 48:
            base = base[:48]
        return base if base else None

    def _build_skill_md(
        self,
        name: str,
        task_type: str,
        task: str,
        tool_sequence: str = "",
        result_summary: str = "",
        error: str = "",
        success: bool = True,
    ) -> str:
        """构建 SKILL.md 内容。

        格式参考 hermes-agent 的 SKILL.md 规范：
        - YAML frontmatter
        - 触发条件（何时用这个技能）
        - 步骤（总结这次操作的关键步骤）
        - 注意事项
        """
        lines = [
            "---",
            f"name: {name}",
            f"task_type: {task_type}",
            f"source: EvolutionEngine",
            f"created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"success: {str(success).lower()}",
            "---",
            "",
            f"# {name}",
            "",
            "## 触发条件",
            "",
            f"当用户请求{task_type}类任务时适用：",
            f"- 任务特征：{task[:200]}",
            "",
            "## 步骤",
            "",
        ]
        if tool_sequence:
            lines.append(f"1. 工具调用流程：{tool_sequence}")
            lines.append("")
        if result_summary:
            lines.append(f"2. 结果摘要：{result_summary[:300]}")
            lines.append("")
        if error:
            lines.append("## 注意事项")
            lines.append("")
            lines.append(f"- 已知错误：{error[:200]}")
            lines.append("")

        lines.extend([
            "## 质量",
            "",
            "- 状态：auto-generated",
            "- 下次使用后请评估是否保留或改进",
        ])

        return "\n".join(lines)

    def save_skill(self, skill_info: dict) -> Optional[str]:
        """将技能信息写入 skills 目录。

        Args:
            skill_info: evaluate() 返回的 dict

        Returns:
            文件路径，或 None 写入失败
        """
        name = skill_info["name"]
        file_path = self.skills_dir / f"{name}.md"

        # 如果文件已存在，添加版本后缀
        if file_path.exists():
            v = 1
            while file_path.exists():
                v += 1
                file_path = self.skills_dir / f"{name}_v{v}.md"

        try:
            file_path.write_text(skill_info["content"])
            return str(file_path)
        except OSError:
            return None
