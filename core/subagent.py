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

        raw_output = result.get("result", "") or ""
        raw_summary = result.get("result", "") or ""

        # ── P0-2: 子 Agent 输出智能压缩 ──
        # 父 Agent 上下文是宝贵的，子 Agent 的输出必须压缩到 1K tokens 以内
        MAX_OUTPUT_CHARS = 1600  # ≈ 1K tokens
        MAX_SUMMARY_CHARS = 800  # ≈ 500 tokens

        # output 截断保护
        if len(raw_output) > MAX_OUTPUT_CHARS:
            output = raw_output[:MAX_OUTPUT_CHARS]
            if len(raw_output) > MAX_OUTPUT_CHARS + 100:
                output += f"\n\n[... 完整输出过长，已截断至 {MAX_OUTPUT_CHARS} chars (原 {len(raw_output)} chars)]"
        else:
            output = raw_output

        # summary 做智能摘要（优先用本地 LLM）
        summary = _summarize_result(raw_summary, max_chars=MAX_SUMMARY_CHARS)

        return {
            "success": result.get("success", False),
            "output": output,
            "summary": summary,
            "turns": result.get("turns", 0),
            "duration": duration,
            "task_id": task_id,
        }

    except Exception as e:
        return {"success": False, "output": f"子 Agent 执行异常: {e}"}

    finally:
        with _delegate_lock:
            _active_subagents -= 1


# ── P0-2: 子 Agent 结果摘要工具 ──────────────────────────────────

_SUMMARIZER_CACHE = None


def _get_summarizer() -> Optional[object]:
    """懒加载 LocalSummarizer（避免循环导入）。"""
    global _SUMMARIZER_CACHE
    if _SUMMARIZER_CACHE is not None:
        return _SUMMARIZER_CACHE
    try:
        from core.context_compress import LocalSummarizer
        s = LocalSummarizer()
        if s.is_available():
            _SUMMARIZER_CACHE = s
            return s
    except Exception as e:
        logger.warning(f"子 Agent 摘要器初始化失败: {e}")
    _SUMMARIZER_CACHE = False  # 标记不可用
    return None


def _summarize_result(text: str, max_chars: int = 800) -> str:
    """对子 Agent 的输出做摘要。

    优先级：
      1. 本地 LLM（llama-server）智能摘要
      2. 内置关键字提取（回退方案）

    Args:
        text: 输入文本
        max_chars: 摘要最大字符数

    Returns:
        摘要文本（不超过 max_chars）
    """
    if not text:
        return "(空结果)"

    # 如果文本很短，直接返回
    if len(text) <= max_chars:
        return text

    # 尝试本地 LLM 摘要
    summarizer = _get_summarizer()
    if summarizer:
        try:
            summary = summarizer.summarize(text)
            if summary and len(summary) > 10:
                # 再截断一次确保不超过 max_chars
                if len(summary) > max_chars:
                    summary = summary[:max_chars] + "..."
                return summary
        except Exception as e:
            logger.warning(f"子 Agent LLM 摘要失败: {e}")

    # 回退：关键行提取 + 首尾截断
    lines = text.split("\n")
    important = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 优先保留短的行（数据/结论行通常较短）
        # 以及包含关键字的行
        keywords = ["结果", "完成", "成功", "失败", "错误", "总结", "结论",
                     "result", "done", "success", "error", "fail", "summary"]
        is_important = any(k in line.lower() for k in keywords)
        is_short = len(line) < 200
        if is_important or is_short:
            important.append(line)

    # 如果关键行不够，补充首尾行
    if len(important) < 3:
        important = []
        # 取前 N 行 + 后 N 行
        for line in lines[:5]:
            if line.strip():
                important.append(line.strip())
        if len(lines) > 10:
            important.append("...")
            for line in lines[-5:]:
                if line.strip():
                    important.append(line.strip())

    result = "\n".join(important)
    if len(result) > max_chars:
        result = result[:max_chars] + "..."
    return result
