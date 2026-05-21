"""白板执行器 (WhiteboardExecutor) — 基于白板的分步循环执行。

执行流程：
  1. 分解器将任务拆为 Step 列表 → 写入白板 next_plan
  2. 循环: 取下一个 pending step → 构建精简 prompt（只含当前步+白板上下文）→ LLM 执行 → 写回白板
  3. 检查点: 每 N 步或遇到失败时做状态检查
  4. 重复直到全部完成或失败超过阈值

关键设计:
  - 每步 prompt 只含当前 step + 相关中间结果，< 2K tokens
  - 中间结果存在白板 intermediate 分区，不塞进 LLM 上下文
  - 失败时自动重规划（调用 decomposer.replan）
"""

import json
import time
from pathlib import Path
from typing import Any, Optional, Callable

from core.llm import LLMClient
from core.tool_registry import ToolRegistry
from core.whiteboard.whiteboard import Whiteboard
from core.whiteboard.decomposer import Decomposer, Step


# 默认 system prompt 片段，引导 LLM 按白板模式工作
WHITEBOARD_SYSTEM_PROMPT = """## 白板工作模式
你在"白板模式"下工作。这是一个分步执行系统：

**规则：**
1. 你只看到当前需要执行的步骤和相关上下文
2. 中间结果存储在外部白板上，你不需要记住它们
3. 每步只做一件事：完成当前步骤描述的任务
4. 完成后调用 finish_step() 告诉系统你已完成，系统会自动记录结果到白板
5. 不要试图一次性做完所有事情，分步执行

**当前步骤：**
{step_description}

**相关上下文：**
{step_context}"""


class WhiteboardExecutor:
    """白板执行器 — 驱动白板模式下 Agent 的分步执行。"""

    def __init__(
        self,
        llm: LLMClient,
        tool_registry: ToolRegistry,
        whiteboard: Optional[Whiteboard] = None,
        decomposer: Optional[Decomposer] = None,
        max_retries: int = 2,
        checkpoint_interval: int = 5,
        on_step: Optional[Callable[[str], None]] = None,
    ):
        self.llm = llm
        self.tools = tool_registry
        self.whiteboard = whiteboard or Whiteboard()
        self.decomposer = decomposer or Decomposer()
        self.max_retries = max_retries
        self.checkpoint_interval = checkpoint_interval
        self.on_step = on_step

        # 运行时状态
        self._current_step_idx = 0
        self._failed_steps: set[str] = set()
        self._completed_steps: set[str] = set()
        self._total_steps = 0
        self._retry_count = 0
        self._start_time = 0.0

    def run(self, task: str, context: Optional[str] = None) -> dict:
        """完整执行一个任务（分解→循环执行→聚合）。

        Args:
            task: 用户任务描述
            context: 可选的额外上下文

        Returns:
            {"success": bool, "result": str, "summary": str,
             "steps_completed": int, "steps_total": int,
             "duration": float, "errors": list[str]}
        """
        self._start_time = time.time()
        self._failed_steps = set()
        self._completed_steps = set()
        self._retry_count = 0
        errors = []
        final_result = ""
        final_summary = ""

        # 1. 分解任务
        self._log("📋 分解任务...")
        initial_context = {"task": task, "extra_context": context or ""}
        steps = self.decomposer.decompose(task, initial_context)
        self._total_steps = len(steps)

        if not steps:
            return {
                "success": False,
                "result": "任务分解失败，未能生成任何步骤",
                "summary": "",
                "steps_completed": 0,
                "steps_total": 0,
                "duration": round(time.time() - self._start_time, 3),
                "errors": ["任务分解结果为空"],
            }

        # 写入白板
        self.whiteboard.clear()
        for step in steps:
            self.whiteboard.append("next_plan", step.to_dict())
        self.whiteboard.append("current_state", {
            "task": task,
            "step_index": 0,
            "phase": "executing",
        })

        self._log(f"📋 分解完成：{len(steps)} 步，开始执行")

        # 2. 分步执行循环
        step_idx = 0
        max_iterations = len(steps) * 3  # 安全上限（含重试）

        for iteration in range(max_iterations):
            # 取下一个 pending 步骤
            step = self._next_pending_step(steps)
            if step is None:
                self._log("✅ 所有步骤已完成")
                break

            # 检查是否卡住（同一 step 重试超限）
            if step.id in self._failed_steps:
                self._retry_count += 1
                if self._retry_count > self.max_retries:
                    err = f"步骤 {step.id} 重试 {self.max_retries} 次仍失败，放弃"
                    errors.append(err)
                    self._log(f"❌ {err}")
                    step.status = "skipped"
                    self._update_step_in_whiteboard(step)
                    self._retry_count = 0
                    continue
            else:
                self._retry_count = 0

            # 标记为执行中
            step.status = "in_progress"
            self._update_step_in_whiteboard(step)
            self.whiteboard.append("current_state", {
                "task": task,
                "step_index": step_idx,
                "current_step_id": step.id,
                "current_step": step.description,
                "phase": "executing",
            })

            self._log(f"🔄 步骤 {step_idx+1}/{self._total_steps}: {step.description[:80]}")

            # 构建当前步的上下文（精简版）
            step_context = self._build_step_context(step)

            # 3. 执行当前步骤
            result = self._execute_step(task, step, step_context, errors)

            # 4. 处理结果
            if result["success"]:
                step.status = "completed"
                step.result_summary = result.get("summary", result.get("output", ""))[:200]
                self._completed_steps.add(step.id)
                if step.id in self._failed_steps:
                    self._failed_steps.discard(step.id)
                self._log(f"✅ 步骤完成: {step.description[:50]}")

                # 保存中间结果
                if step.output_key and result.get("output"):
                    self.whiteboard.append("intermediate", {
                        "key": step.output_key,
                        "step_id": step.id,
                        "content": result["output"][:500],
                        "type": "step_result",
                    })

                # 保存最终结果
                if result.get("output"):
                    final_result = result["output"]
                    final_summary = result.get("summary", result["output"][:200])
            else:
                step.status = "failed"
                self._failed_steps.add(step.id)
                err_msg = result.get("error", f"步骤 {step.id} 执行失败")
                errors.append(err_msg)
                self._log(f"❌ 步骤失败: {err_msg[:80]}")

                # 尝试重规划
                self._log("🔄 尝试重规划...")
                steps = self.decomposer.replan(
                    task, steps, self._completed_steps, self._failed_steps,
                    self.whiteboard.summary()
                )
                self._total_steps = len(steps)
                self._log(f"🔄 重规划完成，剩余 {len([s for s in steps if s.status == 'pending'])} 步")

                # 写回白板
                self.whiteboard.clear("next_plan")
                for s in steps:
                    self.whiteboard.append("next_plan", s.to_dict())

            self._update_step_in_whiteboard(step)
            step_idx += 1

        # 5. 最终聚合
        self.whiteboard.append("current_state", {
            "task": task,
            "step_index": step_idx,
            "phase": "complete",
        })

        # 从不成功的步骤提取错误信息
        if not final_result and errors:
            final_result = f"任务执行过程中遇到 {len(errors)} 个错误:\n" + "\n".join(errors[:5])

        duration = round(time.time() - self._start_time, 3)
        result = {
            "success": len(errors) == 0,
            "result": final_result,
            "summary": final_summary,
            "steps_completed": len(self._completed_steps),
            "steps_total": self._total_steps,
            "duration": duration,
            "errors": errors,
        }

        # 检查点
        if step_idx >= self.checkpoint_interval:
            self.whiteboard.checkpoint()

        return result

    def _execute_step(self, task: str, step: Step,
                      context: str, errors: list) -> dict:
        """执行单个步骤（调用 LLM + 工具）。

        Returns:
            {"success": bool, "output": str, "summary": str, "error": str}
        """
        # 构建步骤 prompt（精简）
        step_prompt = step.description
        if context:
            step_prompt = f"{step.description}\n\n上下文:\n{context}"

        # 构建 system prompt（含白板模式指示）
        system_prompt = WHITEBOARD_SYSTEM_PROMPT.format(
            step_description=step.description,
            step_context=context or "（无额外上下文）",
        )

        # 获取工具
        tool_schemas = self.tools.get_schemas()

        # 调用 LLM
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": step_prompt},
        ]

        try:
            response = self.llm.chat(messages, tools=tool_schemas)

            if not response["success"]:
                return {
                    "success": False,
                    "output": "",
                    "summary": "",
                    "error": response.get("error", "LLM 调用失败"),
                }

            # 处理工具调用
            output_parts = []
            content = response.get("content", "").strip()
            if content:
                output_parts.append(content)

            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    if fn_name == "finish_step":
                        # finish_step 工具：标记步骤完成
                        args = tc["function"]["arguments"]
                        if isinstance(args, dict):
                            step_output = args.get("output", "")
                            step_summary = args.get("summary", "")
                            if step_output:
                                output_parts.append(str(step_output))
                            if step_summary:
                                return {
                                    "success": True,
                                    "output": step_output or "\n".join(output_parts),
                                    "summary": step_summary,
                                    "error": "",
                                }
                    elif fn_name == "finish":
                        # 全局 finish：视为步骤完成
                        return {
                            "success": True,
                            "output": "\n".join(output_parts),
                            "summary": content[:200],
                            "error": "",
                        }
                    else:
                        # 执行常规工具
                        tool_result = self.tools.execute(tc)
                        output = str(tool_result.get("output", ""))
                        if output:
                            output_parts.append(f"[{fn_name}] {output[:300]}")

            return {
                "success": True,
                "output": "\n".join(output_parts),
                "summary": content[:200] if content else "",
                "error": "",
            }

        except Exception as e:
            return {
                "success": False,
                "output": "",
                "summary": "",
                "error": f"步骤执行异常: {e}",
            }

    def _build_step_context(self, step: Step) -> str:
        """为当前步骤构建上下文（从白板读取相关中间结果）。"""
        ctx_parts = []

        # 1. 已完成步骤摘要
        completed = self.whiteboard.read("completed")
        if completed:
            recent = completed[-3:]
            ctx_parts.append("已完成步骤:")
            for s in recent:
                desc = s.get("description", str(s))[:80]
                result = s.get("result_summary", "")[:60]
                ctx_parts.append(f"  - {desc}")
                if result:
                    ctx_parts.append(f"    → {result}")

        # 2. 依赖步骤的中间结果
        if step.depends_on:
            intermediates = self.whiteboard.read("intermediate")
            if intermediates:
                ctx_parts.append("\n相关中间结果:")
                for dep_id in step.depends_on:
                    for item in intermediates:
                        if item.get("step_id") == dep_id:
                            content = item.get("content", "")[:200]
                            ctx_parts.append(f"  [{dep_id}]: {content}")
                            break

        # 3. 全局状态
        current = self.whiteboard.current_task()
        if current:
            ctx_parts.append(f"\n当前进度: 步骤 {current.get('step_index', '?')}/{self._total_steps}")

        return "\n".join(ctx_parts)

    def _next_pending_step(self, steps: list[Step]) -> Optional[Step]:
        """找出下一个可以执行的 pending 步骤（依赖已完成）。"""
        for step in steps:
            if step.status != "pending":
                continue
            # 检查依赖
            if step.depends_on:
                if not all(dep in self._completed_steps for dep in step.depends_on):
                    continue
            return step
        return None

    def _update_step_in_whiteboard(self, step: Step):
        """更新白板中的步骤状态。"""
        plan = self.whiteboard.read("next_plan")
        for i, s in enumerate(plan):
            if s.get("id") == step.id:
                plan[i] = step.to_dict()
                break
        self.whiteboard.write("next_plan", plan)

        # 如果完成，追加到 completed 分区
        if step.status == "completed":
            self.whiteboard.append("completed", step.to_dict())

    def _log(self, text: str):
        """日志输出。"""
        if self.on_step:
            self.on_step(text)
