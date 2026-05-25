"""
subagent.py — 子 Agent 系统

提供 delegate_task 工具函数，被 agent_loop.py 注册为夸父的一个工具。
子 Agent 在隔离的 AgentLoop 中执行，拥有独立的 ToolRegistry 和对话上下文。
"""

import time
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("kuafu.subagent")

MAX_CONCURRENT = 3
MAX_TURNS = 20
TIMEOUT = 300


@dataclass
class SubAgentResult:
    task_id: str
    success: bool
    summary: str
    output: str = ""
    turns: int = 0
    duration: float = 0.0


# ── delegate_task 工具处理函数 ──────────────────────────────────────

_delegate_lock = __import__("threading").Lock()
_active_subagents = 0


def get_delegate_schema() -> dict:
    """返回 delegate_task 工具的 OpenAI function calling schema。"""
    return {
        "description": "将一个独立的子任务委托给隔离的子 Agent 执行。子 Agent 拥有独立的上下文和工具集，适合并行处理边界清晰的任务。注意：子 Agent 的记忆是空的，需要把所有相关信息通过 context 参数传递。",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "子任务的目标描述",
                },
                "context": {
                    "type": "string",
                    "description": "任务相关的上下文信息（文件路径、配置、背景等）—— 因为子 Agent 没有记忆，所有需要的信息必须在这里提供",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "允许子 Agent 使用的工具列表（可选，默认全部）。例如: ['terminal', 'read_file', 'write_file', 'web_search']",
                },
            },
            "required": ["goal", "context"],
        },
    }


def handle_delegate(args: dict) -> dict:
    """处理 delegate_task 工具调用。"""
    goal = args.get("goal", "")
    context = args.get("context", "")
    tool_whitelist = args.get("tools", None)

    if not goal:
        return {"success": False, "output": "goal 参数不能为空"}

    # 检查并发上限
    global _active_subagents
    with _delegate_lock:
        if _active_subagents >= MAX_CONCURRENT:
            return {
                "success": False,
                "output": f"已达到最大并发子 Agent 数 ({MAX_CONCURRENT})，请等待当前子 Agent 完成后重试",
            }
        _active_subagents += 1

    try:
        # 创建隔离的 ToolRegistry
        from core.tool_registry import ToolRegistry
        sub_tools = ToolRegistry()

        # 如果指定了工具白名单，过滤
        if tool_whitelist:
            for name in sub_tools.list_tools():
                if name not in tool_whitelist:
                    sub_tools.unregister(name)

        # 创建隔离的 AgentLoop（不加载 MCP，不加载记忆）
        from core.agent_loop import AgentLoop
        sub_loop = AgentLoop(
            tool_registry=sub_tools,
            max_turns=MAX_TURNS,
        )
        # 跳过 MCP 加载（子 Agent 不需要外部工具绑定）
        sub_loop.mcp_bridge = None

        # 组装提示
        prompt = goal
        if context:
            prompt = f"""[上下文]
{context}

[任务]
{goal}"""

        start = time.time()
        result = sub_loop.run(prompt)
        duration = round(time.time() - start, 2)
        task_id = f"sub_{int(start * 1000)}"

        return {
            "success": result.get("success", False),
            "output": result.get("result", ""),
            "summary": result.get("result", "")[:500],
            "turns": result.get("turns", 0),
            "duration": duration,
            "task_id": task_id,
        }

    except Exception as e:
        return {"success": False, "output": f"子 Agent 执行异常: {e}"}

    finally:
        with _delegate_lock:
            _active_subagents -= 1
