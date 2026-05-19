"""
夸父 Agent 执行循环 — LLM + 工具调用的核心引擎。

职责：
1. 组装完整的 system prompt（身份 + 规则 + 记忆 + 进化状态）
2. 调用 LLM 获取响应
3. 解析 tool_calls 并执行
4. 收集观察结果返回给 LLM
5. 任务完成后触发反思和进化

流程：
    [用户任务] → 组装 prompt → LLM 推理
        ├── 有 tool_calls → 执行工具 → 返回观察 → LLM 继续
        └── 无 tool_calls → 输出结果 → 反思 → 进化 → [完成]
"""

import json
import time
from pathlib import Path
from typing import Any, Optional

from core.identity import load_identity_statement
from core.llm import LLMClient
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine

ROOT_DIR = Path(__file__).resolve().parent.parent

# ---- 工具定义（OpenAI Function Call 格式） ----

TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "在 Linux 终端中执行命令。可以运行 shell 命令、脚本、编译代码等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录（绝对路径），默认为项目根目录",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时时间（秒），默认 30",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容。返回带行号的文本。不支持二进制文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对或相对）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-indexed），默认 1",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回行数，默认 500",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件。会完全覆盖已有内容。会检查 core/ 目录写保护。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对或相对）",
                    },
                    "content": {
                        "type": "string",
                        "description": "完整的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "对文件进行精确的文本替换编辑。比 write_file 更适合修改已有文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要被替换的旧文本（必须是唯一的）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "新的文本",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "搜索文件内容或查找文件名。用于代码搜索、文档检索等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式。内容搜索用正则，文件搜索用 glob 模式",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["content", "files"],
                        "description": "'content' 搜索文件内容，'files' 查找文件名",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索路径",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "文件筛选模式（如 *.py）",
                    },
                },
                "required": ["pattern", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网。用于获取最新信息、调研项目、查找文档等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页内容。用于阅读在线文档、文章、README 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "完成任务并返回最终结果。调用此工具表示任务已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "最终结果摘要",
                    },
                    "summary": {
                        "type": "string",
                        "description": "详细的任务完成报告",
                    },
                },
                "required": ["result", "summary"],
            },
        },
    },
]


class AgentLoop:
    """Agent 执行循环引擎。

    负责：
    - 组装 system prompt
    - 调用 LLM
    - 执行工具
    - 循环直到 finish
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        memory: Optional[MemoryAPI] = None,
        evolution: Optional[EvolutionEngine] = None,
        max_turns: int = 20,
    ):
        self.llm = llm or LLMClient()
        self.memory = memory or MemoryAPI()
        self.evolution = evolution or EvolutionEngine()
        self.max_turns = max_turns

    def build_system_prompt(self) -> str:
        """组装完整的系统 prompt。"""
        parts = []

        # 1. 身份声明
        parts.append(load_identity_statement())
        parts.append("")

        # 2. 核心规则
        parts.append("## 核心规则")
        parts.append("- 你是夸父，一个自我进化的 AI agent")
        parts.append(f"- 用户是你的主人（在 IDENTITY.md 中定义）")
        parts.append("- 每次任务完成后，必须反思学到了什么")
        parts.append("- 如果用户纠正了你，记住这个教训")
        parts.append("- 绝对不可以修改 core/ 目录下的任何文件")
        parts.append("- 用中文思考和回复")
        parts.append("")

        # 3. 工具说明
        parts.append("## 可用工具")
        parts.append("你有以下工具可用，通过 function_call 调用：")
        for tool_def in TOOLS_DEFINITIONS:
            fn = tool_def["function"]
            desc = fn["description"].split("。")[0]
            parts.append(f"- {fn['name']}: {desc}")
        parts.append("")
        parts.append("完成任务后，调用 finish() 工具结束。")
        parts.append("")

        # 4. 进化状态
        stats = self.evolution.get_evolution_stats()
        parts.append("## 进化状态")
        parts.append(f"- 总进化次数: {stats['total_evolutions']}")
        parts.append(f"- 各级进化: {stats.get('by_level', {})}")
        task_stats = self.evolution.get_task_stats()
        parts.append(f"- 已完成任务: {task_stats['total']}")
        if task_stats["total"] > 0:
            parts.append(f"- 成功率: {task_stats['success_rate']}%")
        parts.append("")

        # 5. 历史记忆
        recent = self.memory.recall("", limit=10)
        if recent:
            parts.append("## 相关记忆")
            for m in recent[-5:]:
                parts.append(f"- {m.get('key', '?')}: {m.get('content', '')[:100]}")
            parts.append("")

        return "\n".join(parts)

    def _execute_tool(self, tool_call: dict) -> dict:
        """执行一个工具调用。"""
        fn_name = tool_call.get("function", {}).get("name", "")
        args = tool_call.get("function", {}).get("arguments", {})

        try:
            if fn_name == "terminal":
                return self._tool_terminal(args)
            elif fn_name == "read_file":
                return self._tool_read_file(args)
            elif fn_name == "write_file":
                return self._tool_write_file(args)
            elif fn_name == "patch":
                return self._tool_patch(args)
            elif fn_name == "search_files":
                return self._tool_search_files(args)
            elif fn_name == "web_search":
                return {"success": True, "output": "使用 web_search 需要互联网连接。请确认网络可用。"}
            elif fn_name == "web_fetch":
                return {"success": True, "output": "使用 web_fetch 需要互联网连接。请确认网络可用。"}
            elif fn_name == "finish":
                return {"success": True, "output": json.dumps(args, ensure_ascii=False)}
            else:
                return {"success": False, "output": f"未知工具: {fn_name}"}
        except Exception as e:
            return {"success": False, "output": f"工具执行异常: {e}"}

    def _tool_terminal(self, args: dict) -> dict:
        """执行终端命令。"""
        import subprocess
        command = args.get("command", "")
        workdir = args.get("workdir", str(ROOT_DIR))
        timeout = args.get("timeout", 30)

        if not command:
            return {"success": False, "output": "命令不能为空"}

        # 安全检查
        from core.sandbox import validate_command
        safe, risk, reason = validate_command(command)
        if not safe:
            return {"success": False, "output": f"安全拦截: {reason}"}

        try:
            r = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=timeout,
            )
            output = r.stdout
            if r.stderr:
                output += "\n--- stderr ---\n" + r.stderr
            if output.strip():
                output = output[:3000]  # 截断防止 context 溢出
                if len(output) >= 3000:
                    output += "\n... (输出已截断)"
            return {
                "success": r.returncode == 0,
                "output": output or "(无输出)",
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "output": f"命令执行超时（{timeout}s）"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _tool_read_file(self, args: dict) -> dict:
        path = Path(args.get("path", ""))
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.exists():
            return {"success": False, "output": f"文件不存在: {path}"}
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
            offset = args.get("offset", 1)
            limit = args.get("limit", 500)
            selected = lines[offset - 1 : offset - 1 + limit]
            output = "\n".join(
                f"{offset + i}|{line}" for i, line in enumerate(selected)
            )
            return {"success": True, "output": output, "total_lines": len(lines)}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _tool_write_file(self, args: dict) -> dict:
        path = Path(args.get("path", ""))
        content = args.get("content", "")
        if not path.is_absolute():
            path = ROOT_DIR / path

        # 安全检查：禁止写入 core/
        from core.sandbox import is_path_allowed_for_write
        if not is_path_allowed_for_write(str(path)):
            return {"success": False, "output": f"安全拦截: {path} 在保护区内，禁止写入"}

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"success": True, "output": f"已写入 {path} ({len(content)} bytes)"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _tool_patch(self, args: dict) -> dict:
        """精确文本替换。"""
        path = Path(args.get("path", ""))
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        if not path.is_absolute():
            path = ROOT_DIR / path

        if not path.exists():
            return {"success": False, "output": f"文件不存在: {path}"}

        try:
            content = path.read_text(encoding="utf-8")
            if old not in content:
                return {"success": False, "output": f"未找到匹配文本: {old[:50]}..."}
            count = content.count(old)
            if count > 1 and not args.get("replace_all"):
                return {"success": False, "output": f"匹配到 {count} 处，请使用更精确的文本"}
            new_content = content.replace(old, new, 1 if not args.get("replace_all") else -1)
            path.write_text(new_content, encoding="utf-8")
            return {"success": True, "output": f"已替换 1 处"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _tool_search_files(self, args: dict) -> dict:
        """搜索文件内容或文件名。"""
        pattern = args.get("pattern", "")
        target = args.get("target", "content")
        search_path = args.get("path", str(ROOT_DIR))
        file_glob = args.get("file_glob")

        import subprocess
        cmd_parts = []

        if target == "content":
            cmd_parts = ["rg", "-n", "--max-count", "5", pattern]
            if file_glob:
                cmd_parts.extend(["-g", file_glob])
            cmd_parts.append(search_path)
        else:
            cmd_parts = ["find", search_path, "-name", pattern, "-type", "f"]

        try:
            r = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=10)
            output = r.stdout[:2000] or "(无匹配结果)"
            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "output": str(e)}

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

        # System prompt
        system_prompt = self.build_system_prompt()
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})

        # 执行循环
        for turn in range(self.max_turns):
            turn_count = turn + 1

            # 调用 LLM
            response = self.llm.chat(messages, tools=TOOLS_DEFINITIONS)

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
                            "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in response["tool_calls"]
                ]
            messages.append(assistant_msg)

            # 检查是否调用了 finish
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    if tc["function"]["name"] == "finish":
                        args = tc["function"]["arguments"]
                        final_result = args.get("result", "")
                        final_summary = args.get("summary", "")
                        break
                if final_result:
                    break

            # 执行工具调用
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    tool_result = self._execute_tool(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(tool_result.get("output", "(无输出)")),
                    })
                    if not tool_result["success"]:
                        errors.append(
                            f"工具 {tc['function']['name']} 失败: {tool_result.get('output', '')}"
                        )
            else:
                # 没有 tool_calls 也没 finish，LLM 直接回复了文本
                # 这是最终的文本回复，自动调用 finish
                finish_payload = {
                    "result": response["content"][:200],
                    "summary": response["content"][:1000],
                }
                messages.append({
                    "role": "user",
                    "content": f"你的回复我已收到。请调用 finish() 提交最终结果。\n结果摘要: {response['content'][:200]}"
                })
                # 再给 LLM 一次机会调用 finish
                finish_response = self.llm.chat(messages, tools=TOOLS_DEFINITIONS)
                if finish_response["success"] and finish_response.get("tool_calls"):
                    for tc in finish_response["tool_calls"]:
                        if tc["function"]["name"] == "finish":
                            args = tc["function"]["arguments"]
                            final_result = args.get("result", "")
                            final_summary = args.get("summary", response["content"][:1000])
                            break
                if not final_result:
                    # LLM 仍然没调用 finish，强制完成
                    final_result = response["content"][:200]
                    final_summary = response["content"][:1000]
                messages.append({
                    "role": "tool",
                    "tool_call_id": "auto-finish",
                    "content": json.dumps({"result": final_result, "summary": final_summary}, ensure_ascii=False),
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

        # 反思：记录任务到记忆
        self.memory.remember(
            key=f"task:{task[:40]}",
            content=task_result["result"][:200],
            tags=["task", task_result["task_type"]],
        )

        # 进化评估
        evolution_event = self.evolution.evaluate_and_evolve(task_result)
        if evolution_event:
            task_result["evolution"] = evolution_event

        task_result["turns"] = turn_count
        task_result["messages_count"] = len(messages)
        return task_result
