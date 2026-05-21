"""分解器 (Decomposer) — 将复杂任务分解为可执行的原子步骤。

设计原则：
  - 轻量级：不调用 LLM，纯规则分解
  - 决定"做什么"，不决定"怎么做"
  - 输出是 Step 列表，写入白板的 next_plan 分区
"""

import re
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    """单个可执行步骤。"""
    id: str = ""
    description: str = ""          # 步骤描述
    status: str = "pending"        # pending / in_progress / completed / failed / skipped
    depends_on: list[str] = field(default_factory=list)  # 前置步骤 ID
    output_key: str = ""           # 中间结果存储键名
    estimated_complexity: str = "simple"  # simple / medium / complex
    result_summary: str = ""       # 执行后的结果摘要

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class Decomposer:
    """规则驱动的任务分解器。"""

    # 任务类型 → 默认分解模板
    DEFAULT_TEMPLATES: dict[str, list[str]] = {
        "coding": [
            "理解需求：梳理清楚用户要什么",
            "设计方案：确定技术方案和文件结构",
            "实现核心逻辑：编写主要代码",
            "处理边界情况：错误处理、异常输入",
            "测试验证：至少跑一次确认能用",
        ],
        "research": [
            "明确研究目标：确定要调研的具体问题",
            "收集信息：搜索相关资料",
            "分析整理：提取关键信息",
            "综合成文：组织成结构化输出",
        ],
        "file_operation": [
            "确认文件状态：检查文件是否存在、可读写",
            "执行操作：读取/写入/修改",
            "验证结果：确认操作成功",
        ],
        "analysis": [
            "数据收集：获取待分析的数据",
            "初步分析：快速扫描数据特征",
            "深入分析：挖掘关键模式",
            "总结输出：撰写分析结论",
        ],
    }

    # 子任务关键词 → 分解模板（比 DEFAULT_TEMPLATES 更细粒度）
    SUBTASK_MAP: dict[str, list[str]] = {
        "web": [
            "确定搜索关键词",
            "搜索并获取结果",
            "提取关键信息",
            "验证信息可靠性",
            "整理输出",
        ],
        "api": [
            "查阅 API 文档/接口规范",
            "构造请求参数",
            "发送请求",
            "处理响应数据",
            "错误处理与重试",
        ],
        "file": [
            "确定文件路径",
            "读取文件内容",
            "处理/转换数据",
            "写入输出文件",
            "验证文件完整性",
        ],
        "install": [
            "检查依赖状态",
            "执行安装命令",
            "验证安装成功",
            "记录版本信息",
        ],
    }

    def __init__(self, templates_path: Optional[Path] = None):
        self.templates_path = templates_path
        self._custom_templates: dict[str, list[str]] = {}
        self._load_custom_templates()

    def _load_custom_templates(self):
        """加载自定义分解模板。"""
        if self.templates_path and self.templates_path.exists():
            try:
                self._custom_templates = json.loads(
                    self.templates_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                pass

    def decompose(self, task: str, context: Optional[dict] = None) -> list[Step]:
        """将任务分解为步骤列表。

        Args:
            task: 用户任务描述
            context: 可选的上下文信息（如已有中间结果、白板状态）

        Returns:
            Step 列表
        """
        task_lower = task.lower()
        detected_types = self._detect_task_types(task_lower)

        # 优先使用匹配的子任务模板
        steps = []
        for ttype, template in self.SUBTASK_MAP.items():
            if ttype in detected_types:
                steps.extend(self._template_to_steps(template, ttype))

        # 再用主模板补充
        for ttype, template in self.DEFAULT_TEMPLATES.items():
            if ttype in detected_types:
                # 避免与 SUBTASK_MAP 完全重复
                existing_descs = {s.description for s in steps}
                new_steps = [
                    s for s in self._template_to_steps(template, ttype)
                    if s.description not in existing_descs
                ]
                steps.extend(new_steps)

        # 如果没有任何模板匹配，用通用分解
        if not steps:
            steps = self._generic_decompose(task)

        # 如果没有得到任何步骤，给一个兜底
        if not steps:
            steps = [Step(
                id="step_0",
                description=f"完成任务: {task[:100]}",
                status="pending",
                estimated_complexity="medium",
            )]

        # 设置依赖关系
        self._set_dependencies(steps)

        return steps

    def _detect_task_types(self, task_lower: str) -> set[str]:
        """检测任务包含的关键词类型。"""
        types = set()

        # 子任务类型检测
        type_patterns = {
            "web": r"\b(搜索|查找|搜|search|look up|browse|爬|抓取|scrape|crawl|fetch)\b",
            "api": r"\b(api|接口|调用|request|post|get|fetch)\b",
            "file": r"\b(文件|file|目录|directory|读取|写入|read|write|save|load|打开)\b",
            "install": r"\b(安装|install|pip|npm|apt|brew|部署|deploy|setup)\b",
        }

        # 主任务类型检测
        type_patterns_main = {
            "coding": r"\b(编写|写|实现|编码|code|program|script|function|class|实现|开发|develop)\b",
            "research": r"\b(研究|调研|分析|research|investigate|compare|对比|评估|evaluate|总结|summarize)\b",
            "file_operation": r"\b(移动|复制|删除|rename|move|copy|delete|mv|cp|rm)\b",
            "analysis": r"\b(分析|统计|统计|plot|chart|可视化|visualize|分析|analyze)\b",
        }

        for ttype, pattern in type_patterns.items():
            if re.search(pattern, task_lower):
                types.add(ttype)

        for ttype, pattern in type_patterns_main.items():
            if re.search(pattern, task_lower):
                types.add(ttype)

        return types

    def _template_to_steps(self, template: list[str], prefix: str) -> list[Step]:
        """将模板转为 Step 列表。"""
        steps = []
        for i, desc in enumerate(template):
            steps.append(Step(
                id=f"{prefix}_{i}",
                description=desc,
                status="pending",
                output_key=f"{prefix}_result_{i}",
                estimated_complexity="simple" if len(template) <= 5 else "medium",
            ))
        return steps

    def _generic_decompose(self, task: str) -> list[Step]:
        """通用分解：当没有模板匹配时，按任务长度和复杂度分解。"""
        task_len = len(task)

        if task_len < 30:
            # 非常简短的任务 → 直接一步
            return [Step(
                id="step_0",
                description=f"{task[:120]}",
                status="pending",
                estimated_complexity="simple",
            )]
        elif task_len < 100:
            # 中等长度 → 两步
            return [
                Step(id="step_0", description="分析需求并规划方案", status="pending",
                     estimated_complexity="simple"),
                Step(id="step_1", description="执行并输出结果", status="pending",
                     depends_on=["step_0"], estimated_complexity="simple"),
            ]
        else:
            # 长任务 → 四步
            return [
                Step(id="step_0", description="理解需求：拆解任务目标", status="pending",
                     estimated_complexity="simple"),
                Step(id="step_1", description="收集信息：获取所需数据/代码", status="pending",
                     depends_on=["step_0"], estimated_complexity="medium"),
                Step(id="step_2", description="执行核心逻辑", status="pending",
                     depends_on=["step_1"], estimated_complexity="complex"),
                Step(id="step_3", description="验证并输出最终结果", status="pending",
                     depends_on=["step_2"], estimated_complexity="simple"),
            ]

    def _set_dependencies(self, steps: list[Step]):
        """自动设置串联依赖（如果没有任何依赖关系）。"""
        has_deps = any(s.depends_on for s in steps)
        if not has_deps and len(steps) > 1:
            for i in range(1, len(steps)):
                if not steps[i].depends_on:
                    steps[i].depends_on = [steps[i - 1].id]

    def replan(self, task: str, current_steps: list[Step],
               completed_ids: set[str], failed_ids: set[str],
               whiteboard_summary: dict) -> list[Step]:
        """根据当前执行状态重新规划后续步骤。

        Args:
            task: 原始任务
            current_steps: 当前计划中的所有步骤
            completed_ids: 已完成的步骤 ID 集合
            failed_ids: 失败的步骤 ID 集合
            whiteboard_summary: 白板摘要（当前状态信息）

        Returns:
            更新后的步骤列表
        """
        # 保留已完成的状态
        remaining = [
            s for s in current_steps
            if s.id not in completed_ids
        ]

        # 标记失败的步骤
        for step in remaining:
            if step.id in failed_ids:
                step.status = "failed"

        # 如果有失败步骤，尝试插入修复步骤
        if failed_ids:
            repair_steps = []
            for fid in failed_ids:
                failed_step = next((s for s in current_steps if s.id == fid), None)
                if failed_step and len(repair_steps) < 2:
                    repair_steps.append(Step(
                        id=f"repair_{fid}",
                        description=f"重新{ failed_step.description }（修复）",
                        status="pending",
                        depends_on=[],
                        estimated_complexity="medium",
                        output_key=f"repair_{failed_step.output_key}",
                    ))

            # 重算依赖：修复步骤依赖最后一个已完成步骤
            if completed_ids:
                last_completed = list(completed_ids)[-1] if not isinstance(
                    list(completed_ids)[-1], str) else list(completed_ids)[-1]
                for rs in repair_steps:
                    rs.depends_on = [last_completed]

            remaining = repair_steps + remaining

        return remaining

    def save_templates(self, path: Optional[Path] = None) -> None:
        """保存当前模板到文件。"""
        save_path = path or self.templates_path
        if save_path:
            all_templates = {}
            all_templates.update(self.DEFAULT_TEMPLATES)
            all_templates.update(self.SUBTASK_MAP)
            all_templates.update(self._custom_templates)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                json.dumps(all_templates, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
