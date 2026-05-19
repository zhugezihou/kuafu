"""
夸父 Agent 执行循环 (Agent Loop)

职责：
1. 组装 system prompt（身份 + 规则 + 工具 + 记忆 + 技能）
2. 与 LLM 对话，处理 tool_calls
3. 通过 ToolRegistry 分派工具执行
4. 通过 SessionStore 管理对话历史
5. 任务完成后的自检和进化评估
"""

import json
import time
from pathlib import Path
from typing import Optional, Callable

from core.llm import LLMClient
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine
from core.tool_registry import ToolRegistry
from core.session_store import SessionStore
from core.context_compress import ContextCompressor
from core.safety import SafetyLayer
from core.skill_resolver import discover_skills, match_skills, inject_skills_to_prompt

ROOT_DIR = Path(__file__).resolve().parent.parent


def load_identity_statement() -> str:
    """从 IDENTITY.md 加载身份声明。"""
    id_path = ROOT_DIR / "IDENTITY.md"
    if id_path.exists():
        return id_path.read_text(encoding="utf-8").strip()
    return "你是夸父（Kuafu），一个自我进化的 AI agent。"


class AgentLoop:
    """Agent 执行循环。

    工作流:
    1. 组装 system prompt
    2. 循环: LLM 思考 → 执行工具 → 收集结果 → 继续
    3. 直到 finish() 被调用或达到最大轮次
    """

    MAX_CONTEXT_TOKENS = 14000  # 上下文窗口安全上限
    SYSTEM_PROMPT_RESERVE = 2000  # system prompt 预留

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        memory: Optional[MemoryAPI] = None,
        evolution: Optional[EvolutionEngine] = None,
        tool_registry: Optional[ToolRegistry] = None,
        session_store: Optional[SessionStore] = None,
        max_turns: int = 20,
        on_step: Optional[Callable[[str], None]] = None,
    ):
        self.llm = llm or LLMClient()
        self.memory = memory or MemoryAPI()
        self.evolution = evolution or EvolutionEngine(memory=memory)
        self.tools = tool_registry or ToolRegistry()
        self.sessions = session_store or SessionStore()
        self.max_turns = max_turns
        self.on_step = on_step

        # 上下文压缩器（默认 12000 token 上限，保留 5 轮）
        self.compressor = ContextCompressor(
            max_context_tokens=12000,
            keep_recent_rounds=5,
        )

        # 当前会话 ID（由 run() 创建）
        self.current_session_id: Optional[str] = None

    def build_system_prompt(self, task: str = "") -> str:
        """组装完整的系统 prompt。"""
        parts = []

        # 1. 身份声明
        parts.append(load_identity_statement())
        parts.append("")

        # 2. 核心规则
        parts.append("## 核心规则")
        parts.append("- 你是夸父，一个自我进化的 AI agent")
        parts.append("- 用户是你的主人（在 IDENTITY.md 中定义）")
        parts.append("- 每次任务完成后，必须反思学到了什么")
        parts.append("- 如果用户纠正了你，记住这个教训")
        parts.append("- 绝对不可以修改 core/ 目录下的任何文件")
        parts.append("- 用中文思考和回复")
        parts.append("")

        # 3. 工具说明
        parts.append("## 可用工具")
        parts.append("你有以下工具可用，通过 function_call 调用：")
        for tool_def in self.tools.get_schemas():
            fn = tool_def["function"]
            desc = fn["description"].split("。")[0]
            parts.append(f"- {fn['name']}: {desc}")
        parts.append("")
        parts.append("完成任务后，调用 finish() 工具结束。")
        parts.append("")

        # 4. 输出格式
        parts.append("## 输出格式")
        parts.append("- 你的回复是直接对用户说的话，不是系统日志或任务报告")
        parts.append("- 如果用户问问题，直接回答内容本身，不要说'已回答'、'已介绍'、'已完成'这类")
        parts.append("")

        # 5. 进化状态
        stats = self.evolution.get_evolution_stats()
        parts.append("## 进化状态")
        parts.append(f"- 总进化次数: {stats['total_evolutions']}")
        parts.append(f"- 各级进化: {stats.get('by_level', {})}")
        task_stats = self.evolution.get_task_stats()
        parts.append(f"- 已完成任务: {task_stats['total']}")
        if task_stats["total"] > 0:
            parts.append(f"- 成功率: {task_stats['success_rate']}%")
        parts.append("")

        # 6. 安全规则
        parts.append("## 安全规则")
        parts.append("- 执行命令前会进行风险分级：safe(自动执行) / attention(需确认) / dangerous(需审批) / forbidden(禁止)")
        parts.append("- API key、token、密码等敏感信息在日志和输出中自动脱敏")
        parts.append("- 输出中不会包含环境变量或敏感配置文件内容")
        parts.append("")

        # 7. 历史记忆
        recent = self.memory.recall("", limit=10)
        if recent:
            parts.append("## 相关记忆")
            for m in recent[-5:]:
                parts.append(f"- {m.get('key', '?')}: {m.get('content', '')[:100]}")
            parts.append("")

        # 7. 用户偏好
        prefs_path = ROOT_DIR / "memory" / "user_prefs.json"
        if prefs_path.exists():
            try:
                prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
                if prefs:
                    parts.append("## 用户偏好")
                    for k, v in prefs.items():
                        parts.append(f"- {k}: {v}")
                    parts.append("")
            except (json.JSONDecodeError, OSError):
                pass

        # 8. 可用技能 — 根据任务自动匹配最相关的技能
        if task:
            matched = match_skills(task)
            if matched:
                parts.append("## 相关技能参考")
                parts.append("以下技能与你当前任务匹配，供参考使用：")
                parts.append("")
                for skill in matched[:3]:
                    parts.append(f"---")
                    parts.append(f"### {skill['name']}")
                    if skill.get("description"):
                        parts.append(f"{skill['description']}")
                    parts.append("")
                    if skill.get("steps"):
                        parts.append("**步骤：**")
                        for i, step in enumerate(skill["steps"], 1):
                            parts.append(f"  {i}. {step}")
                        parts.append("")
                    if skill.get("pitfalls"):
                        parts.append("**注意事项：**")
                        for p in skill["pitfalls"]:
                            parts.append(f"  ⚠️ {p}")
                        parts.append("")
                parts.append("---")
                parts.append("技能仅供参考，根据实际情况灵活执行。")
                parts.append("")
        else:
            # 无具体任务时，简略列出所有可用技能
            all_skills = discover_skills()
            if all_skills:
                parts.append("## 可用技能参考")
                for s in all_skills:
                    parts.append(f"- {s['name']}: {s['description']}")
                parts.append("")

        return "\n".join(parts)

    def _log(self, text: str):
        """记录步骤（或通过回调通知）。"""
        if self.on_step:
            self.on_step(text)

    def run(self, task: str) -> dict:
        """执行一次完整任务。

        Returns:
            {
                "success": bool,
                "result": str,
                "summary": str or None,
                "turns": int,
                "evolution": EvolutionEvent or None,
                "errors": list[str],
                "duration": float,
            }
        """
        start = time.time()
        errors = []
        messages = []
        turn_count = 0
        final_result = ""
        final_summary = ""

        # 创建新会话
        self.current_session_id = self.sessions.create_session(title=task[:50])

        # System prompt（含技能注入）
        system_prompt = self.build_system_prompt(task)
        messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": task})
        self.sessions.append_message(self.current_session_id, "user", task)

        # 获取工具 schema
        tool_schemas = self.tools.get_schemas()

        # 执行循环
        for turn in range(self.max_turns):
            turn_count = turn + 1

            self._log(f"🤔 第 {turn_count}/{self.max_turns} 轮 — LLM 思考中...")

            # 上下文压缩检查：每次 LLM 调用前检查是否需要压缩
            if self.compressor.needs_compression(messages):
                self._log(f"📏 上下文超限（{self.compressor._count_tokens(messages)} tokens），执行压缩...")
                result = self.compressor.compress(messages, llm_summarize=None)
                if result.messages_removed > 0:
                    # 保留 system + 压缩摘要 + 最近几轮
                    system_msgs = [m for m in messages if m.get("role") == "system"]
                    summary_msg = {
                        "role": "system",
                        "content": f"[上下文压缩] 移除了 {result.messages_removed} 条旧消息，节省 {result.original_tokens - result.compressed_tokens} tokens。摘要：{result.summary[:300]}"
                    }
                    keep = self.compressor.keep_recent_rounds * 4
                    non_system = [m for m in messages if m.get("role") != "system"]
                    recent_msgs = non_system[-keep:] if len(non_system) > keep else non_system
                    messages = system_msgs + [summary_msg] + recent_msgs
                    self._log(f"✅ 压缩完成: {result.compression_ratio*100:.0f}% 缩减 ({result.original_tokens}→{result.compressed_tokens} tokens)")

            # 调用 LLM
            response = self.llm.chat(messages, tools=tool_schemas)

            if not response["success"]:
                error_msg = response.get("error", "LLM 调用失败")
                errors.append(error_msg)
                break

            # 添加 assistant 消息
            assistant_msg = {"role": "assistant", "content": response["content"]}
            if response.get("tool_calls"):
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": tc["type"],
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": json.dumps(
                                tc["function"]["arguments"], ensure_ascii=False
                            ),
                        },
                    }
                    for tc in response["tool_calls"]
                ]
            messages.append(assistant_msg)
            self.sessions.append_message(
                self.current_session_id, "assistant",
                response["content"] or "(调用了工具)"
            )

            # 检查是否调用了 finish
            finish_called = False
            if response.get("tool_calls"):
                llm_content = response.get("content", "").strip()
                for tc in response["tool_calls"]:
                    if tc["function"]["name"] == "finish":
                        args = tc["function"]["arguments"]
                        if llm_content:
                            final_result = llm_content
                            final_summary = args.get("summary", llm_content[:200])
                        else:
                            final_result = args.get("result", "")
                            final_summary = args.get("summary", "")
                        finish_called = True
                        break
                if finish_called:
                    break

            # 执行工具调用
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    fn_name = tc["function"]["name"]

                    # 跳过 finish
                    if fn_name == "finish":
                        continue

                    arg_preview = json.dumps(
                        tc.get("function", {}).get("arguments", {}),
                        ensure_ascii=False,
                    )[:60]
                    self._log(f"🔧 执行 {fn_name}({arg_preview}...)")

                    tool_result = self.tools.execute(tc)

                    # 安全脱敏：对终端输出中的 API key、token 等脱敏
                    safe_output = SafetyLayer.sanitize_text(
                        str(tool_result.get("output", "(无输出)"))
                    )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": safe_output,
                    })
                    self.sessions.append_message(
                        self.current_session_id, "tool",
                        safe_output[:500],
                    )

                    if not tool_result["success"]:
                        err = f"工具 {fn_name} 失败: {safe_output[:200]}"
                        errors.append(err)
            else:
                # 没有 tool_calls — LLM 直接回复了文本
                final_result = response["content"]
                final_summary = response["content"][:200]
                messages.append({
                    "role": "tool",
                    "tool_call_id": "auto-finish",
                    "content": json.dumps(
                        {"result": final_result, "summary": final_summary},
                        ensure_ascii=False,
                    ),
                })
                break

        # 准备任务结果
        task_result = {
            "success": len(errors) == 0,
            "result": final_result or response.get("content", ""),
            "summary": final_summary,
            "errors": errors,
            "tool_calls": turn_count,
            "task_type": "generic",
            "duration": round(time.time() - start, 3),
        }

        # 归档会话（如果有较多消息）
        if self.current_session_id:
            session = self.sessions.get_session(self.current_session_id)
            if session and session.message_count > 10:
                self.sessions.archive_session(self.current_session_id)

        # 反思：记录任务到记忆
        self.memory.remember(
            key=f"task:{time.strftime('%Y%m%d_%H%M%S')}",
            content=task_result["result"][:200],
            tags=["task", task_result["task_type"]],
        )

        # 深层反思：调用 LLM 分析任务经验，提取可供未来参考的教训
        self._deep_reflect(task_result, messages)

        # 自检
        self._self_check(task_result, messages, start)

        # 用户偏好学习
        self._learn_user_preferences(task_result, task)

        # 进化评估
        evolution_event = self.evolution.evaluate_and_evolve(task_result)
        if evolution_event:
            task_result["evolution"] = evolution_event

        # 质量评分
        quality = self._quality_score(task_result, messages)
        task_result["quality"] = quality

        # 任务报告：复杂任务（多轮交互）生成结构化报告
        if turn_count >= 3:
            task_result["report"] = self._generate_report(task, task_result, messages)

        task_result["turns"] = turn_count
        task_result["messages_count"] = len(messages)
        return task_result

    def _self_check(self, task_result: dict, messages: list, start: float) -> None:
        """任务完成后自检。"""
        result_text = task_result.get("result", "")
        if not result_text:
            return

        # 只检查有代码/文件操作的任务
        tool_names = [
            m.get("tool_calls", [{}])[0].get("function", {}).get("name", "")
            if m.get("tool_calls") else ""
            for m in messages
        ]
        has_code_work = any(
            "write_file" in str(t) or "patch" in str(t) or "terminal" in str(t)
            for t in tool_names
        )
        if not has_code_work:
            return

        self._log("🔍 自检中 — 审视输出是否有问题...")

        check_prompt = (
            "你刚才完成了一个任务。请快速检查你的最终输出，指出是否有以下问题：\n\n"
            "1. 代码有语法错误或明显逻辑错误？\n"
            "2. 生成的文件路径/位置有问题？\n"
            "3. 输出中的代码无法直接运行？\n"
            "4. 运行产生了错误——你修复了还是只报告了？如果只报告没修复，算有问题。\n\n"
            f"你的最终输出:\n```\n{result_text[:1500]}\n```\n\n"
            "如果存在明显问题，先描述问题，再给出修正方案。\n"
            "如果完全没有问题（代码正确、错误已修复），只回复「无问题」三个字。"
        )
        check_msg = [
            {"role": "system", "content": "你是夸父自检器。只检查输出的正确性，不要做无关分析。"},
            {"role": "user", "content": check_prompt},
        ]
        try:
            check_resp = self.llm.chat(check_msg, tools=None)
            if check_resp["success"]:
                feedback = check_resp["content"].strip()
                if feedback != "无问题" and len(feedback) > 10:
                    task_result["self_check"] = feedback
                    task_result["result"] += f"\n\n---\n🔍 自检反馈:\n{feedback}"
                    self._log(f"⚠️ 自检发现问题: {feedback[:120]}...")
                else:
                    self._log("✅ 自检无问题")
        except Exception as e:
            self._log(f"⚠️ 自检异常: {e}")

    # ── 质量评分 ───────────────────────────────────────────────

    def _quality_score(self, task_result: dict, messages: list) -> dict:
        """对任务输出进行质量评分。

        纯静态分析（零 LLM 消耗）：
        - 错误率：errors 数量 / 总工具调用数
        - 完整性：结果文本长度是否达标
        - 代码质量：代码块是否包含错误
        - 自检反馈：如有自检发现问题则减分

        Returns:
            {"score": 0-10, "detail": str, "suggestions": list[str]}
        """
        score = 7  # 基准 7 分
        suggestions = []
        detail_parts = []

        # 1. 错误率
        errors = task_result.get("errors", [])
        if errors:
            penalty = min(len(errors) * 1.5, 4)
            score -= penalty
            detail_parts.append(f"❌ 错误 {len(errors)} 处 (-{penalty})")
            for e in errors[:2]:
                suggestions.append(f"修复错误: {e[:80]}")
        else:
            detail_parts.append("✅ 零错误")

        # 2. 结果完整性
        result_text = task_result.get("result", "")
        if result_text and len(result_text) > 10:
            if len(result_text) < 50:
                detail_parts.append("⚠️ 结果偏短 (-0.5)")
                score -= 0.5
            else:
                detail_parts.append(f"✅ 结果完整 ({len(result_text)} 字符)")
        else:
            detail_parts.append("❌ 结果为空 (-2)")
            score -= 2
            suggestions.append("输出不应为空，至少给出总结")

        # 3. 工具调用成功率
        tool_count = 0
        for m in messages:
            if m.get("tool_calls"):
                tool_count += len(m["tool_calls"])

        if tool_count == 0 and len(result_text or "") < 100:
            # 无工具调用且短回复 — 可能只回答了问题
            pass  # 不减分
        elif tool_count > 0 and errors:
            tool_error_ratio = len(errors) / tool_count
            if tool_error_ratio > 0.5:
                score -= 1
                detail_parts.append(f"⚠️ 工具错误率 {tool_error_ratio:.0%} (-1)")

        # 4. 自检反馈
        self_check = task_result.get("self_check")
        if self_check:
            score -= 1
            detail_parts.append("⚠️ 自检发现可改进项 (-1)")
            suggestions.append("参考自检反馈改进输出")

        # 5. 是否成功
        if not task_result.get("success", True):
            score = min(score, 4)
            detail_parts.append("❌ 任务未成功 (-3)")
            suggestions.append("任务执行失败，需排查错误原因")

        # 约束到 0-10
        score = max(0, min(10, round(score, 1)))

        return {
            "score": score,
            "detail": " | ".join(detail_parts),
            "suggestions": suggestions,
        }

    # ── 任务报告生成 ──────────────────────────────────────────────

    def _generate_report(self, task: str, task_result: dict, messages: list) -> str:
        """为复杂任务生成结构化报告。

        包含：任务摘要、决策过程、关键结果、学到的教训。
        不调用 LLM（纯结构化组装），轻量无消耗。
        """
        success = task_result.get("success", False)
        result_text = task_result.get("result", "")
        error_list = task_result.get("errors", [])
        task_type = task_result.get("task_type", "generic")
        duration = task_result.get("duration", 0)
        turns = task_result.get("turns", 0)

        # 提取关键决策点（工具调用名称）
        tool_calls_in_messages = []
        for m in messages:
            tcs = m.get("tool_calls")
            if tcs:
                for tc in tcs:
                    fn = tc.get("function", {}).get("name", "?")
                    tool_calls_in_messages.append(fn)

        # 去重并计数
        tool_counts = {}
        for t in tool_calls_in_messages:
            tool_counts[t] = tool_counts.get(t, 0) + 1

        # 提取用户的前几个消息作为任务摘要（从 messages 中提取 user 角色）
        user_inputs = []
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                if content and len(content) > 10:
                    user_inputs.append(content[:120])

        # 构建报告
        parts = [
            f"## 任务报告: {task_type}",
            "",
            f"**是否成功**: {'✅' if success else '❌'}",
            f"**耗时**: {duration:.1f}s",
            f"**交互轮次**: {turns}",
            f"**工具调用分布**:",
        ]
        if tool_counts:
            for t_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                parts.append(f"  - {t_name}: {count} 次")
        else:
            parts.append("  - (无工具调用)")

        if user_inputs:
            parts.append("")
            parts.append("**任务目标**:")
            parts.append(f"  {user_inputs[0][:160]}")
            if len(user_inputs) > 1:
                parts.append(f"  ...（共 {len(user_inputs)} 次用户输入）")

        if error_list:
            parts.append("")
            parts.append("**错误**:")
            for e in error_list:
                parts.append(f"  - ⚠️ {e[:100]}")

        parts.append("")
        parts.append("**结果摘要**:")
        parts.append(f"  {result_text[:200]}")

        parts.append("")
        parts.append("---")
        parts.append(f"报告自动生成 | {time.strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(parts)

    # ── 深层反思 ────────────────────────────────────────────────────

    def _deep_reflect(self, task_result: dict, messages: list) -> None:
        """任务完成后的深层反思——分析经验，提炼教训，注入记忆。"""
        success = task_result.get("success", False)
        turns = len(messages)
        task_type = task_result.get("task_type", "generic")
        if success and turns < 8:
            return
        self._log("💭 反思中 — 分析任务经验...")
        result_snippet = task_result.get("result", "")[:800]
        error_list = task_result.get("errors", [])
        error_text = "; ".join(error_list) if error_list else "无错误"
        reflect_prompt = (
            "你刚完成了一个任务。请做一次简短反思，总结可供未来任务参考的经验。\n\n"
            f"任务类型: {task_type}\n"
            f"是否成功: {'是' if success else '否'}\n"
            f"错误: {error_text}\n"
            f"交互轮数: {turns}\n\n"
            f"最终输出摘要:\n{result_snippet}\n\n"
            "请按以下格式输出（不要多余文字）：\n"
            "TITLE: <一句话总结这次任务的关键教训，25字内>\n"
            "TAG: experience\n"
            "CONTENT: <1-3句话，具体可操作的经验，下次遇到类似任务时能有帮助>\n"
        )
        reflect_msg = [
            {"role": "system", "content": "你是夸父反思模块。输出格式固定：TITLE:/TAG:/CONTENT: 三行。"},
            {"role": "user", "content": reflect_prompt},
        ]
        try:
            resp = self.llm.chat(reflect_msg, tools=None)
            if not resp["success"]:
                return
            output = resp["content"].strip()
            title = ""
            tag = "experience"
            content = ""
            for line in output.split("\n"):
                line = line.strip()
                if line.startswith("TITLE:"):
                    title = line[6:].strip()
                elif line.startswith("TAG:"):
                    tag = line[4:].strip()
                elif line.startswith("CONTENT:"):
                    content = line[8:].strip()
            if content:
                self.memory.remember(
                    key=f"reflect:{time.strftime('%Y%m%d_%H%M%S')}",
                    content=f"[{tag}] {title} — {content}",
                    tags=["reflection", tag, task_type],
                )
                self._log(f"💡 学到经验: {title} — {content[:80]}...")
        except Exception as e:
            self._log(f"⚠️ 反思异常: {e}")

    # ── 用户偏好学习 ────────────────────────────────────────────────

    def _learn_user_preferences(self, task_result: dict, task: str) -> None:
        """从当前任务中学习用户偏好，动态更新 user_prefs.json。

        触发条件：
        - 任务成功
        - 用户输入中有明显偏好指示（如「下次」「更喜欢」「用 XX 工具」「别用」等）
        """
        success = task_result.get("success", False)
        if not success:
            return

        # 只在用户输入包含偏好信号时学习
        pref_signals = ["下次", "更喜欢", "别用", "不要用", "应该用", "请用", "用中文", "用英文"]
        has_signal = any(s in task for s in pref_signals)
        if not has_signal:
            return

        prefs_path = ROOT_DIR / "memory" / "user_prefs.json"
        prefs = {}
        if prefs_path.exists():
            try:
                prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                prefs = {}
            if not isinstance(prefs, dict):
                prefs = {}

        # 从用户输入中提取偏好
        self._log("🎯 检测到偏好信号，正在学习...")
        learn_prompt = (
            "分析以下用户输入，提取明确的偏好/要求（如语言、工具、风格、格式等）。\n\n"
            f"用户输入:\n{task}\n\n"
            f"现有偏好:\n{json.dumps(prefs, ensure_ascii=False, indent=2)}\n\n"
            "请输出 JSON 格式（不要多余文字）：\n"
            "{\n"
            '  "add": {"key": "新偏好对名称", "value": "新偏好值"},\n'
            '  "remove": []  // 要删除的偏好键列表（如果有冲突）\n'
            "}\n"
            '如果没有提取到新的有效偏好，输出 {"add": null, "remove": []}'
        )
        learn_msg = [
            {"role": "system", "content": "你是夸父偏好学习模块。输出严格 JSON。"},
            {"role": "user", "content": learn_prompt},
        ]
        try:
            resp = self.llm.chat(learn_msg, tools=None)
            if not resp["success"]:
                return
            result = json.loads(resp["content"].strip())
            add_item = result.get("add")
            if add_item and add_item.get("key") and add_item.get("value"):
                key = add_item["key"].strip()
                value = add_item["value"].strip()
                if key and value:
                    prefs[key] = value
                    # 删除冲突项
                    for k in result.get("remove", []):
                        prefs.pop(k, None)
                    # 写入
                    prefs_path.parent.mkdir(parents=True, exist_ok=True)
                    prefs_path.write_text(
                        json.dumps(prefs, ensure_ascii=False, indent=2)
                    )
                    self._log(f"📝 学到用户偏好: {key} = {value}")
        except Exception as e:
            self._log(f"⚠️ 偏好学习异常: {e}")
