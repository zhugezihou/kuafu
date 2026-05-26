"""
subagent.py — 子 Agent 系统

提供 delegate_task 工具函数，被 agent_loop.py 注册为夸父的一个工具。
子 Agent 在隔离的 AgentLoop 中执行，拥有独立的 ToolRegistry 和对话上下文。

P1-2: 支持 YAML Frontmatter 配置化 (subagent_profiles/ 目录)
- LLM 通过 skill 参数引用预定义的子 Agent 配置
- 配置含 allowed_tools、max_turns、output_rules
- 不指定 skill 时使用原来的直接 goal/context 模式（向后兼容）
"""

import os
import json
import time
import yaml
import logging
import uuid
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger("kuafu.subagent")

MAX_CONCURRENT = 3
MAX_TURNS = 20
TIMEOUT = 300

# P1-2: 子 Agent Profile 目录
SUBAGENT_PROFILES_DIR = Path(__file__).resolve().parent.parent / "subagent_profiles"

# P2-1: 侧链隔离 — 子 Agent 完整对话转录写磁盘
SIDECHAIN_DIR = Path(__file__).resolve().parent.parent / "sidechain_data" / "transcripts"
SIDECHAIN_DIR.mkdir(parents=True, exist_ok=True)


def load_skill_profile(name: str) -> Optional[dict[str, Any]]:
    """加载子 Agent YAML Frontmatter 配置。

    Args:
        name: skill 名称（不含 .yaml 后缀）

    Returns:
        配置字典，含 name, description, allowed_tools, max_turns, output_rules
        不存在或格式错误返回 None
    """
    path = SUBAGENT_PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or not data.get("name"):
            return None
        return {
            "name": data.get("name", name),
            "description": data.get("description", ""),
            "allowed_tools": data.get("allowed_tools", []),
            "max_turns": data.get("max_turns", MAX_TURNS),
            "output_rules": data.get("output_rules", {}),
        }
    except Exception as e:
        logger.warning(f"子 Agent Profile 加载失败 {name}: {e}")
        return None


def list_skill_profiles() -> list[dict[str, str]]:
    """列出所有可用的子 Agent 配置。"""
    if not SUBAGENT_PROFILES_DIR.exists():
        return []
    profiles = []
    for p in sorted(SUBAGENT_PROFILES_DIR.glob("*.yaml")):
        data = load_skill_profile(p.stem)
        if data:
            profiles.append({
                "name": p.stem,
                "description": data["description"][:200],
            })
    return profiles


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
    """返回 delegate_task 工具的 OpenAI function calling schema。

    P1-2: 支持 skill 参数引用 YAML Frontmatter 配置的子 Agent 模板。
    可用技能: {', '.join(p['name'] for p in list_skill_profiles())}。
    """
    # 注入可用 skill 列表到描述中
    available_skills = list_skill_profiles()
    skill_descriptions = "\n".join(
        f"  - {s['name']}: {s['description']}" for s in available_skills
    ) if available_skills else "  暂无预定义技能"

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
                "skill": {
                    "type": "string",
                    "description": f"可选的子 Agent 技能模板名称，对应 subagent_profiles/ 目录下的 YAML 配置。\n可用技能:\n{skill_descriptions}\n\n指定 skill 后，goal 和 context 会被合并到 skill 的提示框架中，同时工具白名单由 skill 配置决定。不指定则使用传统的 goal/context 模式。",
                },
            },
            "required": ["goal", "context"],
        },
    }


def handle_delegate(args: dict) -> dict:
    """处理 delegate_task 工具调用。

    P1-2: 支持 skill 参数，自动加载 subagent_profiles/ 下的 YAML 配置。
    """
    goal = args.get("goal", "")
    context = args.get("context", "")
    tool_whitelist = args.get("tools", None)
    skill_name = args.get("skill", None)

    if not goal:
        return {"success": False, "output": "goal 参数不能为空"}

    # ── P1-2: 加载 skill profile ──────────────────────────────────
    profile = None
    effective_max_turns = MAX_TURNS
    if skill_name:
        profile = load_skill_profile(skill_name)
        if profile:
            logger.info(f"🧩 子 Agent 使用 skill profile: {skill_name}")
            # skill 配置覆盖 max_turns
            if profile.get("max_turns"):
                effective_max_turns = profile["max_turns"]
            # skill 配置的工具白名单优先
            if profile.get("allowed_tools"):
                tool_whitelist = profile["allowed_tools"]
        else:
            logger.warning(f"⚠️ 子 Agent skill profile 不存在: {skill_name}，回退到直接模式")

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
        # ── P2-1: 侧链隔离 — 创建独立会话转录文件 ──
        sidechain_id = uuid.uuid4().hex[:12]
        sidechain_path = SIDECHAIN_DIR / f"{sidechain_id}.jsonl"
        sidechain_messages: list[dict] = []

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
            max_turns=effective_max_turns,
        )
        # 跳过 MCP 加载（子 Agent 不需要外部工具绑定）
        sub_loop.mcp_bridge = None

        # ── P2-1: 转录所有子 Agent 的对话到侧链文件 ──
        original_log = sub_loop._log
        def _transcript_log(text: str):
            """拦截日志，同时写入侧链转录。"""
            original_log(text)
            sidechain_messages.append({"type": "log", "text": text, "time": time.time()})
        sub_loop._log = _transcript_log

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

        # ── P2-1: 写入侧链转录文件（完整对话历史） ──
        # 侧链文件只写磁盘，父 Agent 永不接触
        try:
            sidechain_record = {
                "sidechain_id": sidechain_id,
                "task_id": task_id,
                "goal": goal[:200],
                "context": context[:500] if context else "",
                "success": result.get("success", False),
                "turns": result.get("turns", 0),
                "duration": duration,
                "messages": sidechain_messages,
                "result": result.get("result", "")[:500],
            }
            with open(sidechain_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(sidechain_record, ensure_ascii=False, default=str))
            logger.info(f"📜 侧链转录已保存: {sidechain_path}")
        except Exception as e:
            logger.warning(f"侧链转录写入失败: {e}")
            sidechain_path = None

        raw_output = result.get("result", "") or ""

        # ── P2-2: 强制只返摘要（sidechain 隔离） ──
        # Claude Code 设计启示：子 Agent 的完整对话永远不进父上下文
        # 父 Agent 只收到摘要 + 可选的侧链文件引用
        MAX_SUMMARY_CHARS = 800  # ≈ 500 tokens

        # 用子 Agent 的结果做摘要（原始结果先给摘要器，不再放全量 output 回父上下文）
        summary = _summarize_result(raw_output, max_chars=MAX_SUMMARY_CHARS)

        # 返回：只有 summary，没有全量 output
        ret = {
            "success": result.get("success", False),
            "summary": summary,
            "turns": result.get("turns", 0),
            "duration": duration,
            "task_id": task_id,
        }
        # 可选：附带侧链文件引用（供日志调试）
        if sidechain_path:
            ret["_sidechain"] = str(sidechain_path)
        return ret

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
