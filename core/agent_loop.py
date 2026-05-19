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
from core.skill_resolver import discover_skills, inject_skills_to_prompt

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
        self.evolution = evolution or EvolutionEngine()
        self.tools = tool_registry or ToolRegistry()
        self.sessions = session_store or SessionStore()
        self.max_turns = max_turns
        self.on_step = on_step

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

        # 6. 历史记忆
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

        # 8. 可用技能
        all_skills = discover_skills()
        if all_skills:
            parts.append("## 可用技能参考")
            parts.append("以下技能可供参考（根据你的任务自动匹配）：")
            parts.append("")
            for s in all_skills:
                parts.append(f"- {s['name']}: {s['description']}")
            parts.append("")
            parts.append("技能已经融入你的知识体系，不必显式调用。根据任务需求选择性地使用。")
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

        # System prompt
        system_prompt = self.build_system_prompt(task)
        messages.append({"role": "system", "content": system_prompt})

        # 注入匹配的技能 prompt
        skill_injected = inject_skills_to_prompt(task, "")
        if skill_injected:
            messages.append({"role": "system", "content": skill_injected})

        messages.append({"role": "user", "content": task})
        self.sessions.append_message(self.current_session_id, "user", task)

        # 获取工具 schema
        tool_schemas = self.tools.get_schemas()

        # 执行循环
        for turn in range(self.max_turns):
            turn_count = turn + 1

            self._log(f"🤔 第 {turn_count}/{self.max_turns} 轮 — LLM 思考中...")

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
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(tool_result.get("output", "(无输出)")),
                    })
                    self.sessions.append_message(
                        self.current_session_id, "tool",
                        str(tool_result.get("output", ""))[:500],
                    )

                    if not tool_result["success"]:
                        err = f"工具 {fn_name} 失败: {tool_result.get('output', '')}"
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

        # 自检
        self._self_check(task_result, messages, start)

        # 进化评估
        evolution_event = self.evolution.evaluate_and_evolve(task_result)
        if evolution_event:
            task_result["evolution"] = evolution_event

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
