"""
Copyright (c) 2026 zhugezihou

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

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
import os
import time
import threading
import sys
from pathlib import Path
from typing import Optional, Callable

from core.llm import LLMClient
from core.memory import MemoryManager  # Hindsight 记忆系统
from core.evolution import EvolutionEngine
from core.observer import Observer
from core.tool_registry import ToolRegistry
from core.session_store import SessionStore
from core.subagent import get_invoke_expert_schema, get_invoke_experts_schema
from core.context_compress import ContextCompressor, LLMSummarizer, ToolResultStore, ContextCollapse, CollapseResult, budget_reduce_output
from core.budget_allocator import BudgetAllocator, BudgetSnapshot, BudgetPolicy
from core.prompt_template import PromptManager, PromptCache, Section, build_reminders
from core.safety import SafetyLayer
from core.skill_resolver import discover_skills, match_skills, increment_usage, record_usage
from core.whiteboard import Whiteboard, Decomposer, Step, WhiteboardExecutor
from core.mcp_bridge import MCPBridge
from core.approval import pretooluse_check, DenyRules, AutoMode, ApprovalManager
from core.hooks import trigger, trigger_async, trigger_sync, init_hooks, HOOK_EVENTS
# 策略/规则加载：优先从 autonomous.strategy_loader 加载，降级到默认值
try:
    from autonomous.strategy_loader import get_rules as _get_rules
    from autonomous.strategy_loader import get_quality as _get_quality
    _HAS_STRATEGY = True
except ImportError:  # pragma: no cover
    _HAS_STRATEGY = False

    def _get_rules():
        return [
            "1. 直接完成用户请求，不要说'我可以帮你'之类的废话",
            "2. 一次只做一个工具调用，等待结果再继续",
            "3. 完成任务后调用 finish() 工具",
            "4. 你有内置的审批系统：执行某些工具（如 terminal 写文件、sudo 命令等）前会触发权限检查，需要用户确认后才执行",
            "5. 如果用户提到审批系统，告诉他们已经内置了，不需要额外实现",
        ]

    def _get_quality(task_type: str = "generic"):  # pragma: no cover
        return []

get_rules = lambda: _get_rules() if _HAS_STRATEGY else _get_rules()
get_quality = lambda task_type="generic": _get_quality(task_type) if _HAS_STRATEGY else _get_quality(task_type)

ROOT_DIR = Path(__file__).resolve().parent.parent

# task_type 检测：关键词 → 类型映射
_TASK_TYPE_KEYWORDS = {
    "coding": ["代码", "写一个", "实现", "修复", "bug", "写个函数", "编写", "debug", "改代码", "写脚本", "重构"],
    "research": ["搜索", "查找", "调研", "研究", "查一下", "搜索一下", "查资料", "了解", "什么是", "为什么", "分析", "github", "git", "仓库", "开源", "项目", "寻找", "找一下"],
    "file_operation": ["创建文件", "写入", "读取", "修改文件", "删除", "移动", "拷贝", "重命名", "目录"],
    "design": ["设计", "架构", "方案", "规划", "流程图", "画图", "原型"],
    "troubleshooting": ["报错", "错误", "失败", "异常", "连不上", "超时", "挂掉了", "崩溃", "起不来"],
    "devops": ["部署", "发布", "配置", "安装", "docker", "服务器", "nginx", "数据库", "环境", "docker-compose"],
    "analysis": ["对比", "比较", "评估", "优劣势", "哪个好", "区别", "差异"],
    "file_delivery": ["发给我", "传文件", "发我", "发给", "发送文件", "文档", "word", "docx", "pdf", "给我"],
}

def detect_task_type(task: str) -> str:
    """根据任务内容检测任务类型。"""
    if not task:
        return "generic"
    task_lower = task.lower()
    for tt, keywords in _TASK_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in task_lower:
                return tt
    return "generic"


def load_identity_statement() -> str:
    """从 IDENTITY.md 加载身份声明。"""
    id_path = ROOT_DIR / "IDENTITY.md"
    if id_path.exists():
        return id_path.read_text(encoding="utf-8").strip()
    return "你是夸父（Kuafu），一个自我进化的 AI agent。"  # pragma: no cover


_BOOTUP_LOGGED = False  # 模块级 flag：首次初始化的启动日志只打印一次

# 异步后处理：所有非主流程 LLM 调用都在后台执行，不阻塞 run() 返回
def _async_post_task(task_result: dict, messages: list, task: str, loop: 'AgentLoop') -> None:
    """在后台线程执行后处理 LLM 调用，不阻塞 run() 返回。

    包含：深度反思、自检、进化管道、偏好学习、对话记忆提取。
    所有 LLM 调用都在后台执行，主线程只拼接 task_result + 质量评分（零 LLM 成本）。
    """
    def _run():  # pragma: no cover
        try:
            loop._deep_reflect(task_result, messages)
        except Exception:
            pass
        try:
            loop._self_check(task_result, messages, 0)
        except Exception:
            pass
        try:
            loop._run_evolution_pipeline(task_result, task, messages)
        except Exception:
            pass
        try:
            loop._learn_user_preferences(task_result, task)
        except Exception:
            pass
        try:
            loop._extract_conversation_memories(task_result, messages)
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=True, name="async-post-task")
    t.start()


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
        memory: Optional[MemoryManager] = None,
        evolution: Optional[EvolutionEngine] = None,
        tool_registry: Optional[ToolRegistry] = None,
        session_store: Optional[SessionStore] = None,
        max_turns: int = 90,
        on_step: Optional[Callable[[str], None]] = None,
    ):
        self.max_turns = max_turns
        self.llm = llm or LLMClient()
        self.memory = memory or MemoryManager(enable_nmm=True)
        self.evolution = evolution or EvolutionEngine(memory=memory, llm=self.llm)
        self.tools = tool_registry or ToolRegistry()
        self.sessions = session_store or SessionStore()
        self.max_turns = max_turns
        self.on_step = on_step

        # ── 只在新实例（非子 Agent）时打印引导日志 ──
        self._is_top_level = max_turns > 10  # 子 Agent 的 max_turns 通常很小

        # ── 以下全部惰性初始化（_lazy_init 中创建） ──
        self.prompt_cache = None  # PromptCache()
        self.compressor = None  # ContextCompressor()
        self.budget_allocator = None  # BudgetAllocator()
        self._ctx_threshold = 800000  # 启动时由 _lazy_init 覆盖
        self.tool_result_store = None  # ToolResultStore()
        self.collapser = None  # ContextCollapse()
        self.mcp_bridge = None  # MCPBridge()
        self._observer = None  # Observer()
        self.evolution_engine = None  # EvolutionEngine v2
        self._evolution_rules = None
        self._budget_scan_count = 0
        self._mem_maintenance_counter = 0

        # 注册工具（这些不依赖惰性初始化）
        self._register_delegate_tool()
        self._register_skill_rollback()

        # ── Hook 事件系统（只需一次） ──
        self.hooks_enabled = False
        if self._is_top_level:
            self.hooks_enabled = True
            try:
                init_hooks()
                self._log("🔌 Hook 事件系统就绪")
            except Exception as e:  # pragma: no cover
                self._log(f"⚠️ Hook 系统初始化失败: {e}")

        # ── 本地模型辅助（可选，缺失不阻塞） ──
        self._local = None
        try:
            from core.local_helper import LocalHelper
            self._local = LocalHelper()
            if self._local.available():
                self._log("🧠 本地模型辅助就绪")
        except Exception:
            self._local = None

    def _lazy_init(self):
        """惰性初始化（run() 第一次调用时才创建的组件）。"""
        if self.compressor is not None:
            return  # 已初始化

        # ── Permission System ──
        self.permission_enabled = os.environ.get("KUAFU_DISABLE_APPROVAL", "") != "1"
        self._pretooluse_cache: dict = {}

        # ── 审批通知回调 ──
        if not hasattr(self, 'on_approval_request') or self.on_approval_request is None:
            self.on_approval_request = None

        # ── 实时事件回调 ──
        self.on_llm_start: Optional[Callable[[int], None]] = None
        self.on_llm_end: Optional[Callable[[int, str], None]] = None
        self.on_tool_start: Optional[Callable[[str, dict, float], None]] = None
        self.on_tool_end: Optional[Callable[[str, str, float], None]] = None
        self.on_turn: Optional[Callable[[int, dict], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_finish: Optional[Callable[[dict], None]] = None
        self.on_phase: Optional[Callable[[str], None]] = None  # v2: 阶段性总结

        # 上下文压缩器 — 使用动态检测的阈值
        self.compressor = ContextCompressor(
            max_context_tokens=self._ctx_threshold,
            keep_recent_rounds=90,
            summarizer=LLMSummarizer(llm_chat=self._get_local_summarizer()),
        )

        # Budget Allocator: Token 预算分配器
        self.budget_allocator = BudgetAllocator(
            policy=BudgetPolicy(total_budget=self._ctx_threshold),
            on_critical=self._on_budget_critical,
            on_warning=self._on_budget_warning,
        )
        self._budget_scan_count = 0

        # Microcompact: 大型工具结果 → 磁盘存储
        self.tool_result_store = ToolResultStore()

        # ContextCollapse: 非破坏性上下文投影
        self.collapser = ContextCollapse(
            summarizer=LLMSummarizer(llm_chat=self._get_local_summarizer()),
            keep_recent_rounds=90,
        )

        # Observer：运行时工具调用跟踪
        self._observer = Observer()
        self.evolution.register_observer(self._observer)

        # 进化规则引擎（基于 Hindsight 置信度）
        self._evolution_rules = None

        # MCP Server 集成
        self.mcp_bridge: Optional[MCPBridge] = None
        self._init_mcp()
        self.on_llm_start: Optional[Callable[[int], None]] = None
        self.on_llm_end: Optional[Callable[[int, str], None]] = None
        self.on_tool_start: Optional[Callable[[str, dict, float], None]] = None
        self.on_tool_end: Optional[Callable[[str, dict, float, str], None]] = None

    # ── 本地模型辅助接口 ─────────────────────────────────────────

    def _get_local_summarizer(self) -> Optional[callable]:
        """返回本地模型的 chat 函数，供 LLMSummarizer 使用。

        如果本地模型可用，返回一个兼容 (messages) → dict 的函数。
        不可用时返回 None（LLMSummarizer 会用截断兜底）。
        """
        local = getattr(self, '_local', None)
        if local is not None and local.available():
            def _local_chat(messages: list) -> dict:
                prompt = messages[-1]["content"] if messages else ""
                result = local.summarize(prompt, max_chars=600)
                if result:
                    return {"content": result, "success": True}
                return {"content": "", "success": False}
            return _local_chat
        return None

    def _register_delegate_tool(self):
        """注册专家工具（invoke_expert + invoke_experts）。

        主 Agent 将任务委派给领域专家执行。
        专家直接在父 Agent 的 LLM 上做独立推理，不创建子 Agent。
        """
        # 子 Agent 不注册委托工具
        if not self._is_top_level:
            return
        try:
            from core.expert_registry import get_registry

            # ── 注册 invoke_expert（单个专家） ──
            expert_schema = get_invoke_expert_schema()
            self.tools.register("invoke_expert", expert_schema, self._handle_invoke_expert)

            # 注册 invoke_experts（并行多专家）
            experts_schema = get_invoke_experts_schema()
            self.tools.register("invoke_experts", experts_schema, self._handle_invoke_experts)

            registry = get_registry()
            expert_count = len(registry.list())
            if expert_count > 0:
                self._log(f"🧩 专家系统就绪: {expert_count} 个专家可用")  # pragma: no cover

            # 注入父 Agent 的运行时配置（供专家使用）
            import core.subagent as _sa
            _sa.PARENT_LLM_CONFIG = {
                "base_url": self.llm.base_url,
                "model": self.llm.model,
                "max_tokens": self.llm.max_tokens,
                "temperature": self.llm.temperature,
            }
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 专家系统注册失败: {e}")

        # ── P1-3: Memory 工具注册（memory_store / memory_search / memory_reflect） ──
        self._register_memory_tools()

    # ── 专家执行（在父 Agent 内完成，不创建子 Agent） ──

    @staticmethod
    def _parse_expert_args(raw_args) -> dict:
        """解析专家工具调用的 arguments（兼容 JSON 字符串和 dict）。"""
        import json as _j
        if isinstance(raw_args, str):
            try:
                return _j.loads(raw_args)
            except (_j.JSONDecodeError, TypeError):
                return {}
        elif isinstance(raw_args, dict):
            return raw_args
        return {}

    def _build_expert_tools(self, profile) -> Optional[list]:
        """从专家配置的工具白名单构造 tools schema 列表。
        
        从 core + injected + compact + deferred 四个池中搜索工具 schema，
        确保 expert 使用的无论是 core/deferred/compact 工具都能拿到完整描述。
        首次调用 compact 或 deferred 工具时，自动提升其 schema 以便后续 LLM 可见。
        """
        if not profile.tools:
            return None
        all_schemas = list(self.tools.get_schemas())  # core + injected
        # 补充 compact 工具（如 read_file、write_file 等）
        try:
            if hasattr(self.tools, '_compact'):
                for entry in self.tools._compact:
                    if entry["function"]["name"] in profile.tools:
                        all_schemas.append(entry)
        except Exception:
            pass
        # 补充 deferred 工具（如 web_search、tavily_search 等）
        try:
            if hasattr(self.tools, '_deferred'):
                for entry in self.tools._deferred:
                    if entry["schema"]["function"]["name"] in profile.tools:
                        all_schemas.append(entry["schema"])
        except Exception:
            pass
        expert_schemas = [s for s in all_schemas
                          if s["function"]["name"] in profile.tools]
        return expert_schemas or None

    def _exec_expert_tool_calls(self, resp: dict) -> str:
        """执行专家 LLM 返回的工具调用，拼接工具结果。"""
        tool_outputs = []
        for tc in resp.get("tool_calls", []):
            fn_name = tc["function"]["name"]
            args_dict = self._parse_expert_args(tc["function"]["arguments"])
            # 专家内部的工具调用跳过审批，直接执行
            tool_result = self._orchestrator.execute_direct(fn_name, args_dict)
            tool_outputs.append(f"[{fn_name}] {tool_result.output[:500]}")
        return "\n\n".join(tool_outputs)

    def _handle_invoke_expert(self, args: dict) -> dict:
        """执行单个专家任务。

        直接在当前 Agent 的 LLM 上做独立推理。
        专家 identity + task 作为一次 chat 调用，不污染主对话上下文。
        如果专家配置了 tools，会传给 LLM 以便执行实际操作。
        """
        from core.expert_registry import get_registry

        expert_name = args.get("expert", "")
        task = args.get("task", "")

        if not expert_name or not task:
            return {"success": False, "output": "expert 和 task 参数不能为空"}

        registry = get_registry()
        profile = registry.get(expert_name)
        if not profile:
            available = ", ".join(e.name for e in registry.list())
            return {
                "success": False,
                "output": f"专家 '{expert_name}' 不存在。可用专家: {available}",
            }

        try:
            memory_label = getattr(profile, 'memory_label', None)

            # ── 专家记忆注入 ──
            memory_context = ""
            if memory_label:
                try:
                    # 1. 专业领域知识 — 搜索 记忆标签为 memory_label 的内容
                    knowledge = self.memory.search(query=memory_label, limit=5)
                    # 2. 用户历史任务 — 搜索含 memory_label:task 标签的历史
                    task_tag = f"{memory_label}:task"
                    task_history = self.memory.search(query=task_tag, limit=5)
                    parts = []
                    if knowledge:
                        lines = "\n".join(f"  - {m.get('content','')[:300]}" for m in knowledge)
                        parts.append(f"[{memory_label} 专业知识]\n{lines}")
                    if task_history:
                        lines = "\n".join(f"  - {m.get('content','')[:300]}" for m in task_history)
                        parts.append(f"[历史任务记录]\n{lines}")
                    if parts:
                        memory_context = "\n\n".join(parts)
                except Exception:
                    pass

            system_prompt = profile.identity
            if memory_context:
                system_prompt += f"\n\n{memory_context}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]
            tools = self._build_expert_tools(profile)
            resp = self.llm.chat(messages, tools=tools)

            # 确保 orchestrator 已初始化（专家内部工具调用跳过审批）
            self._init_orchestrator()
            content = ""
            if isinstance(resp, dict):
                if not resp.get("success", False):
                    err_msg = resp.get("error", resp.get("content", "LLM 调用失败"))
                    return {"success": False, "output": f"专家 {expert_name} LLM 调用失败: {err_msg}"}
                content = resp.get("content", "") or ""
                tool_text = self._exec_expert_tool_calls(resp)
                if tool_text:
                    content = content + "\n\n" + tool_text if content else tool_text
            elif isinstance(resp, str):
                content = resp

            # ── 空内容 fallback ──
            if not content:
                content = f"专家 {expert_name} 已完成分析"

            # ── 专家记忆持久化 ──
            if memory_label and content:
                try:
                    import time as _t
                    from datetime import datetime as _dt
                    ts = _dt.now().strftime('%Y%m%d_%H%M%S')
                    key = f"{memory_label}:task:{ts}"
                    task_tag = f"{memory_label}:task"
                    self.memory.store(
                        content=f"任务: {task[:200]}\n\n结果:\n{content[:500]}",
                        source=key,
                        tags=[memory_label, task_tag],
                    )
                except Exception:
                    pass

            return {"success": True, "output": content, "expert": expert_name}
        except Exception as e:
            return {"success": False, "output": f"专家执行异常: {e}"}

    def _handle_invoke_experts(self, args: dict) -> dict:
        """并行执行多个专家任务（tools 由各专家 profile 独立配置）。"""
        from core.expert_registry import get_registry

        expert_names = args.get("experts", [])
        task = args.get("task", "")

        if not expert_names or len(expert_names) < 2:
            return {"success": False, "output": "experts 至少需要2个专家"}
        if not task:
            return {"success": False, "output": "task 参数不能为空"}

        registry = get_registry()
        import concurrent.futures

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(expert_names)) as executor:
            future_map = {}
            for name in expert_names:
                profile = registry.get(name)
                if not profile:
                    results[name] = {"success": False, "output": f"专家 '{name}' 不存在"}
                    continue
                fut = executor.submit(self._call_expert_once, profile, task)
                future_map[fut] = name

            for fut in concurrent.futures.as_completed(future_map):
                name = future_map[fut]
                try:
                    results[name] = fut.result()
                except Exception as e:
                    results[name] = {"success": False, "output": str(e)}

        output_parts = [
            f"【{name}】\n{r.get('output', r.get('result', ''))[:1000]}"
            for name, r in results.items()
            if r.get("success")
        ]
        return {
            "success": bool(output_parts),
            "output": "\n\n".join(output_parts) if output_parts else "所有专家均失败",
            "results": results,
        }

    def _call_expert_once(self, profile, task: str) -> dict:
        """一次独立 LLM 推理（供 ThreadPoolExecutor 调用），支持工具。"""
        try:
            memory_label = getattr(profile, 'memory_label', None)

            # ── 专家记忆注入 ──
            memory_context = ""
            if memory_label:
                try:
                    knowledge = self.memory.search(query=memory_label, limit=5)
                    task_tag = f"{memory_label}:task"
                    task_history = self.memory.search(query=task_tag, limit=5)
                    parts = []
                    if knowledge:
                        lines = "\n".join(f"  - {m.get('content','')[:300]}" for m in knowledge)
                        parts.append(f"[{memory_label} 专业知识]\n{lines}")
                    if task_history:
                        lines = "\n".join(f"  - {m.get('content','')[:300]}" for m in task_history)
                        parts.append(f"[历史任务记录]\n{lines}")
                    if parts:
                        memory_context = "\n\n".join(parts)
                except Exception:
                    pass

            system_prompt = profile.identity
            if memory_context:
                system_prompt += f"\n\n{memory_context}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]
            tools = self._build_expert_tools(profile)
            resp = self.llm.chat(messages, tools=tools)

            content = ""
            if isinstance(resp, dict):
                if not resp.get("success", False):
                    err_msg = resp.get("error", resp.get("content", "LLM 调用失败"))
                    return {"success": False, "output": f"专家 {profile.name} LLM 调用失败: {err_msg}"}
                content = resp.get("content", "") or ""
                tool_text = self._exec_expert_tool_calls(resp)
                if tool_text:
                    content = content + "\n\n" + tool_text if content else tool_text
            elif isinstance(resp, str):
                content = resp

            # ── 空内容 fallback ──
            if not content:
                name = getattr(profile, 'name', 'unknown')
                content = f"专家 {name} 已完成分析"

            # ── 专家记忆持久化 ──
            if memory_label and content:
                try:
                    from datetime import datetime as _dt
                    ts = _dt.now().strftime('%Y%m%d_%H%M%S')
                    key = f"{memory_label}:task:{ts}"
                    task_tag = f"{memory_label}:task"
                    self.memory.store(
                        content=f"任务: {task[:200]}\n\n结果:\n{content[:500]}",
                        source=key,
                        tags=[memory_label, task_tag],
                    )
                except Exception:
                    pass

            return {"success": True, "output": content}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _register_skill_rollback(self):  # pragma: no cover
        """注册 skill_rollback 工具（回滚最后一条 skill 进化）。"""
        try:
            schema = {
                "description": "回滚最后一条 skill 进化。如果用户对技能输出不满意，调用此工具恢复到上一个版本。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "要回滚的 skill 名称。留空则回滚最近一次进化。"
                        }
                    },
                    "required": []
                }
            }
            def handler(args: dict) -> dict:
                try:
                    skill = args.get("skill_name", "")
                    result = self.evolution.evolution_state.undo_last_evolution(
                        skill if skill else None
                    )
                    if not skill:
                        # 没传 skill_name：回滚最近一次 skill 进化
                        # 找 skills 列表中最后写入的那个
                        skills_data = getattr(self.evolution.evolution_state, '_data', {}).get("skills", {})
                        last_skill = None
                        last_time = 0
                        for sname, sentry in skills_data.items():
                            lw = sentry.get("last_written", 0)
                            if lw > last_time:
                                last_time = lw
                                last_skill = sname
                        if last_skill:
                            result = self.evolution.evolution_state.undo_last_evolution(last_skill)

                    if result:
                        msg = (
                            f"回滚 skill '{result.get('rolled_back_skill', skill)}': "
                            f"版本 v{result.get('rolled_back_v')} → v{result.get('restored_to_v')}"
                        )
                        self._log(f"↩️ {msg}")
                        return {"success": True, "output": msg}
                    else:
                        return {"success": False, "output": "无可回滚的版本（仅一个版本或无 skill 存在）"}
                except Exception as e:
                    return {"success": False, "output": f"回滚失败: {e}"}
            self.tools.register("skill_rollback", schema, handler)
            if self._is_top_level:
                self._log("↩️ 技能回滚工具已注册: skill_rollback")
        except Exception as e:
            self._log(f"⚠️ skill_rollback 注册失败: {e}")

    # ── P1-3: Memory 工具注册 ──────────────────────────────────────────
    def _register_memory_tools(self):
        """注册记忆工具（memory_store / memory_search / memory_reflect）。"""
        try:
            schemas = self.memory.get_tool_schemas()
            for schema in schemas:
                name = schema["name"]
                params = schema["parameters"]
                desc = schema["description"]
                self.tools.register(name, {
                    "description": desc,
                    "parameters": params,
                }, lambda args, _n=name: self.memory.handle_tool_call(_n, args))
            if self._is_top_level:  # pragma: no cover
                self._log(f"🧠 记忆工具就绪: {', '.join(s['name'] for s in schemas)}")  # pragma: no cover
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 记忆工具注册失败: {e}")  # pragma: no cover

    def _init_mcp(self):  # pragma: no cover
        """初始化 MCP 桥接，加载配置并注册工具。"""
        if getattr(self, '_skip_mcp', False):
            return
        mcp_config_path = ROOT_DIR / "core" / "mcp_config.yaml"
        if not mcp_config_path.exists():
            return
        try:
            bridge = MCPBridge()
            bridge.load_config(str(mcp_config_path))
            failed = bridge.connect_all()
            if failed:
                self._log(f"⚠️ MCP Server 连接失败: {', '.join(failed)}")
            count = bridge.register_to_registry(self.tools)
            if count > 0:
                self._log(f"🔌 MCP 集成就绪: {count} 个外部工具已注册")
            self.mcp_bridge = bridge
        except Exception as e:
            self._log(f"⚠️ MCP 初始化失败: {e}")
            self.mcp_bridge = None

    def _init_evolution_rules(self):
        """初始化进化规则引擎（基于 Hindsight OpinionEngine）。"""
        try:
            from core.evolution_rules import EvolutionRuleManager
            # 尝试从 memory 子系统获取 OpinionEngine
            opinion_engine = getattr(self.memory, '_opinions', None) if hasattr(self, 'memory') else None
            if opinion_engine is None:
                # 尝试通过底层 SQLite 连接
                backend = getattr(self.memory, '_longterm', None) if hasattr(self, 'memory') else None
                if backend and hasattr(backend, '_conn'):
                    from core.memory.hindsight_lite import OpinionEngine
                    opinion_engine = OpinionEngine(backend._conn)
            if opinion_engine:
                self._evolution_rules = EvolutionRuleManager(
                    opinion_engine=opinion_engine,
                    llm_chat_fn=self.llm.chat if hasattr(self, 'llm') else None,
                )
                self._log("🧬 进化规则引擎就绪")
            else:  # pragma: no cover
                self._log("⚠️ 进化规则引擎未初始化（无记忆系统）")
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 进化规则引擎初始化异常: {e}")

    def build_system_prompt(self, task: str = "") -> str:
        """组装结构化 system prompt（PromptTemplate + PromptCache 实现）。

        使用 PromptManager 将 prompt 拆分为独立 section 组合。
        利用 PromptCache 对 L1(immutable) / L2(semi) / L3(variable) 分层缓存，
        """
        # 确保惰性初始化
        if self.prompt_cache is None:
            self.prompt_cache = PromptCache()
            self._lazy_init()

        from core.prompt_template import get_stability, STABILITY_L1_IMMUTABLE, STABILITY_L2_SEMI

        pm = PromptManager(task)

        # ── 1. 身份声明 ──
        pm.add_section(
            section_id="identity",
            title="",
            content=load_identity_statement(),
            order=0,
            budget_tag="system",
        )

        # ── 2. 当前日期与时间 ──
        from datetime import datetime
        now = datetime.now()
        date_cn = f"{now.year}年{now.month}月{now.day}日"
        time_str = now.strftime("%H:%M")
        dow = ['一','二','三','四','五','六','日'][now.weekday()]
        pm.add_section(
            section_id="current_datetime",
            title="",
            content=f"当前日期: {date_cn} 星期{dow} {time_str}\n"
                    f"【重要】纯时间计算（如\"X小时后是几点\"）直接在脑中推理即可，无需调用任何工具。",
            order=1,
            budget_tag="system",
        )

        # ── 3. 核心规则 ──
        rules = get_rules()
        rules_content = "\n".join(f"- {rule}" for rule in rules)

        # 追加运行环境说明
        if os.environ.get("KUAFU_DESKTOP") == "1":
            import platform as _platform
            rules_content += (
                "\n\n## 运行环境\n"
                f"- 操作系统: Windows ({_platform.release()})\n"
                "- 终端命令使用 cmd.exe (Windows), 不是 bash\n"
                "- 文件路径使用 Windows 格式 (C:\\Users\\...)，不是 Linux 格式 (/home/...)\n"
                "- 不要使用 ls、cat、grep 等 Linux 命令\n"
                "- 使用 dir、type、findstr 等 Windows 命令\n"
                "- 不要使用 /tmp/ 路径，使用 %TEMP% 或 C:\\Users\\...\\AppData\\Local\\Temp\n"
            )
        else:
            rules_content += "\n\n## 运行环境\n- 操作系统: Linux\n- 终端使用 bash"

        # 追加进化规则（历史经验总结）
        try:
            if hasattr(self, '_evolution_rules') and self._evolution_rules:
                tt = detect_task_type(task)
                evo_block = self._evolution_rules.build_rules_block(task, tt)
                if evo_block:
                    rules_content += "\n\n" + evo_block
        except Exception:  # pragma: no cover
            pass

        pm.add_section(
            section_id="rules",
            title="核心规则",
            content=rules_content,
            order=1,
            budget_tag="system",
        )

        # ── 3. 工具说明（L2 半稳定） ──
        core_tools = []
        for tool_def in self.tools.get_schemas()[:10]:
            fn = tool_def["function"]
            if fn["name"] == "tool_search":  # pragma: no cover
                continue
            desc = fn["description"].split("。")[0]
            core_tools.append(f"- {fn['name']}: {desc}")

        compact_tools = []
        for name, desc in self.tools.get_compact_tools_description():
            short_desc = desc.split("。")[0]
            compact_tools.append(f"- {name}: {short_desc}")

        tools_content = "## 可用工具\n\n"
        tools_content += "### 核心工具（总是可用，直接 function_call 调用）\n"
        tools_content += "\n".join(core_tools) + "\n\n"
        if compact_tools:
            tools_content += "### 常用工具（仅名称和用途说明，无参数描述）\n"
            tools_content += "以下工具可直接调用——首次调用时系统会自动补充参数信息：\n"
            tools_content += "\n".join(compact_tools) + "\n\n"
        tools_content += "### 隐藏工具（需要先发现）\n"
        tools_content += "需要搜索网页、抓取内容等额外功能时，使用 tool_search 元工具：\n\n"
        tools_content += "1. 调用 tool_search(query=...) 搜索想要的工具\n"
        tools_content += "2. 系统匹配并激活最相关的隐藏工具\n"
        tools_content += "3. 激活后直接调用\n\n"
        tools_content += "完成任务后，调用 finish() 工具结束。"

        # ── 4. 专家系统说明 ──
        try:
            from core.expert_registry import get_registry
            expert_block = get_registry().get_system_prompt_block()
            if expert_block:
                tools_content += "\n\n" + expert_block
        except Exception:
            pass

        pm.add_section(
            section_id="tools",
            title="可用工具",
            content=tools_content,
            order=2,
            budget_tag="system",
        )

        # ── 4. 输出格式 + 执行纪律 ──
        format_content = "- 回复直接对用户说话，不是日志或报告\n"
        format_content += "- 如果用户问问题，直接回答，不要说'已回答'这类\n\n"
        format_content += "## 执行纪律\n"
        format_content += "<tool_persistence>\n"
        format_content += "- 只要工具调用能提升结果的正确性、完整性或准确性，就必须用工具\n"
        format_content += "- 不要因为已有部分结果就停止——如果再多一次工具调用能显著改善结果，继续调用\n"
        format_content += "- 工具返回空或部分结果时，换查询策略重试，不要直接放弃\n"
        format_content += "- 持续调用工具直到：(1) 任务完成，且 (2) 已验证结果\n"
        format_content += "</tool_persistence>\n\n"
        format_content += "<mandatory_tool_use>\n"
        format_content += "以下情况绝对不要靠记忆或脑算——必须用工具：\n"
        format_content += "- 算术、数学、计算 → 用 calculate 或 terminal\n"
        format_content += "- 哈希、编码、校验和 → 用 terminal\n"
        format_content += "- 当前时间、日期、时区 → 用 terminal\n"
        format_content += "- 系统状态：OS、CPU、内存、磁盘、端口、进程 → 用 terminal\n"
        format_content += "- 文件内容、大小、行数 → 用 read_file、search_files 或 terminal\n"
        format_content += "- Git 历史、分支、差异 → 用 terminal\n"
        format_content += "- 当前事实（天气、新闻、版本号）→ 用 web_search\n"
        format_content += "你的记忆描述的是用户，不是当前运行环境。执行环境可能与用户描述的不同。\n"
        format_content += "</mandatory_tool_use>\n\n"
        format_content += "<act_dont_ask>\n"
        format_content += "当问题有显而易见的默认解释时，直接执行，不要问确认。例如：\n"
        format_content += "- '443端口开着吗？' → 查本机（不要问'查哪里'）\n"
        format_content += "- '什么系统？' → 查实际系统（不要从用户描述猜）\n"
        format_content += "- '几点了？' → 跑 date（不要猜）\n"
        format_content += "只有模糊性确实会影响你调用哪个工具时，才需要澄清。\n"
        format_content += "</act_dont_ask>\n\n"
        format_content += "<prerequisite_checks>\n"
        format_content += "- 行动前先确认是否需要先做发现、查询或上下文收集\n"
        format_content += "- 即使最终行动看起来很明显，也不要跳过前置步骤\n"
        format_content += "- 如果任务依赖上一步的输出，先解决依赖再继续\n"
        format_content += "</prerequisite_checks>\n\n"
        format_content += "<verification>\n"
        format_content += "在输出最终结果前自我检查：\n"
        format_content += "- 正确性：输出是否满足所有需求？\n"
        format_content += "- 依据：事实声明是否由工具输出或提供上下文支撑？\n"
        format_content += "- 格式：输出是否符合要求的格式或结构？\n"
        format_content += "- 安全：如果下一步有副作用（文件写、命令执行、API 调用），确认范围后再执行\n"
        format_content += "</verification>\n\n"
        format_content += "<missing_context>\n"
        format_content += "- 如果缺少必要信息，不要猜测或捏造答案\n"
        format_content += "- 可检索的信息用查找工具（search_files、web_search、read_file 等）获取\n"
        format_content += "- 只有在工具确实无法获取的信息时，才问用户\n"
        format_content += "- 如果必须基于不完整信息执行，显式标注假设\n"
        format_content += "- **对用户指令理解不清晰时（如简短模糊指代），简要列出你的理解让用户确认再执行**\n"
        format_content += "</missing_context>"

        pm.add_section(
            section_id="format",
            title="输出格式",
            content=format_content,
            order=3,
            budget_tag="system",
        )

        # ── 5. 进化状态（L1 条件注入） ──
        stats = self.evolution.get_evolution_stats()
        total = stats['total_evolutions']
        if total > 0:
            pm.add_section(
                section_id="evolution",
                title="进化状态",
                content=f"- 已进化 {total} 次",
                order=4,
                budget_tag="system",
            )

        # ── 6. 配置（L1 半稳定，同一 session 固定） ──
        if self.llm:
            backend_name = getattr(self.llm, 'backend', '?')
            model_name = getattr(self.llm, 'model', '?')
            config_content = f"- 后端: {backend_name} | 模型: {model_name}"
            pm.add_section(
                section_id="config",
                title="配置",
                content=config_content,
                order=5,
                budget_tag="system",
            )

        # ── 7. 任务相关：质量标准 + 技能（L3 变量） ──
        if task:
            task_lower = task.lower()
            task_type = "generic"
            for tt in ["coding", "research", "file_operation"]:
                if tt in task_lower:
                    task_type = tt
                    break

            # 质量标准
            quality_rules = get_quality(task_type.replace("file_operation", "file_op")
                                        .replace("generic", "code"))
            if quality_rules:
                quality_items = []
                for qr in quality_rules:
                    icon = {"required": "🔴", "warning": "🟡", "optional": "🟢"}
                    quality_items.append(
                        f"  {icon.get(qr['severity'], '⚪')} [{qr['severity']}] {qr['rule']}"
                    )
                pm.add_section(
                    section_id="quality",
                    title="质量标准",
                    content="完成此任务时请注意以下标准：\n" + "\n".join(quality_items),
                    order=6,
                    budget_tag="system",
                )

            # 技能匹配
            from core.skill_resolver import (
                match_skills, resolve_skill_execution, increment_usage
            )
            matched = match_skills(task)
            if matched:
                simple_skills, complex_skills = resolve_skill_execution(matched)
                if simple_skills:
                    skill_parts = []
                    for skill in simple_skills[:1]:  # 最多注入 1 个好 skill
                        increment_usage(skill['name'])
                        skill_parts.append(f"### {skill['name']}")
                        if skill.get("description"):
                            skill_parts.append(str(skill['description']))
                        # 只注入短 skill（≤5步）的完整步骤，长 skill 只给描述+链接
                        if skill.get("steps"):
                            step_count = len(skill["steps"])
                            if step_count <= 5:  # pragma: no cover
                                skill_parts.append("**步骤：**")
                                for i, step in enumerate(skill["steps"], 1):  # pragma: no cover
                                    skill_parts.append(f"  {i}. {step}")
                            else:
                                skill_parts.append(f"**步骤数：** {step_count} 步（详见 skills/{skill.get('file', '')}）")
                        if skill.get("pitfalls"):
                            skill_parts.append("**注意事项：**")
                            for p in skill["pitfalls"][:3]:  # 最多3条注意事项
                                skill_parts.append(f"  ⚠️ {p}")
                    skill_parts.append("技能仅供参考，不必完全照做。")

                    pm.add_section(
                        section_id="skills",
                        title="相关技能",
                        content="\n".join(skill_parts),
                        order=7,
                        budget_tag="skills",
                    )

            # 错误关联技能
            try:
                err_skill = self.evolution.evolution_state.get_skill_for_error(task)
                if err_skill:
                    import yaml
                    skills_dir = Path(__file__).resolve().parent.parent / "skills"
                    for yf in skills_dir.glob("*.yaml"):  # pragma: no cover
                        with open(yf, "r", encoding="utf-8") as f:  # pragma: no cover
                            sd = yaml.safe_load(f)  # pragma: no cover
                        if sd and sd.get("name") == err_skill:  # pragma: no cover
                            err_parts = [f"### {err_skill}"]  # pragma: no cover
                            if sd.get("description"):  # pragma: no cover
                                err_parts.append(str(sd['description']))  # pragma: no cover
                            if sd.get("steps"):  # pragma: no cover
                                err_parts.append("**步骤：**")  # pragma: no cover
                                for i, step in enumerate(sd["steps"], 1):  # pragma: no cover
                                    err_parts.append(f"  {i}. {step}")  # pragma: no cover
                            if sd.get("pitfalls"):  # pragma: no cover
                                err_parts.append("**注意事项：**")  # pragma: no cover
                                for p in sd["pitfalls"]:  # pragma: no cover
                                    err_parts.append(f"  ⚠️ {p}")  # pragma: no cover
                            err_parts.append("该技能因检测到已知错误模式而自动加载。")

                            pm.add_section(
                                section_id="error_skill",
                                title="⚡ 错误关联技能",
                                content="\n".join(err_parts),
                                order=8,
                                budget_tag="skills",
                            )
                            break
            except Exception:  # pragma: no cover
                pass

        # ── 8. 记忆上下文（L3 变量，预算感知注入） ──
        budget = getattr(self, 'budget_allocator', None)
        budget_ratio = 1.0
        if budget and budget._last_snapshot:
            budget_ratio = budget._last_snapshot.overall_ratio
        memory_block = self.memory.build_memory_block(
            budget_ratio=budget_ratio,
            include_search=task if task else "",
        )
        if memory_block:
            pm.add_section(
                section_id="memory_context",
                title="记忆上下文",
                content=memory_block,
                order=8,
                budget_tag="memory",
            )

        # ── 9. 自我认知 + 用户偏好 ──
        try:
            all_skills = discover_skills()
            skills_count = len(all_skills) if all_skills else 0
            prefs_path = ROOT_DIR / "memory" / "user_prefs.json"
            pref_count = 0
            pref_lines = []
            if prefs_path.exists():
                try:
                    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
                    if isinstance(prefs, dict):
                        pref_count = len(prefs)
                        for k, v in list(prefs.items())[:8]:
                            pref_lines.append(f"  • {k}: {v[:100]}")
                except Exception:  # pragma: no cover
                    pass
            self_awareness = f"📚 {skills_count} 技能 | 👤 {pref_count} 用户偏好 | ⚡ {total} 次进化"
            if pref_lines:
                self_awareness += "\n\n**用户偏好**\n" + "\n".join(pref_lines)
            pm.add_section(
                section_id="self_awareness",
                title="自我认知",
                content=self_awareness,
                order=99,
                budget_tag="system",
            )
        except Exception:  # pragma: no cover
            pass

        # ── 组装：利用 PromptCache 分块缓存 ──
        # 将 sections 按稳定性分组，L1/L2 命中缓存不重复拼接
        l1_sections = []
        l2_sections = []
        l3_sections = []
        for sec in pm.sections:
            stab = get_stability(sec.id)
            if stab == STABILITY_L1_IMMUTABLE:
                l1_sections.append(sec)
            elif stab == STABILITY_L2_SEMI:
                l2_sections.append(sec)
            else:
                l3_sections.append(sec)

        # L1 + L2 用缓存，L3 每次都重建
        l1_block = self.prompt_cache.get_block(l1_sections, STABILITY_L1_IMMUTABLE)
        l2_block = self.prompt_cache.get_block(l2_sections, STABILITY_L2_SEMI)

        # L3 变量区直接组装
        l3_text = ""
        if l3_sections:
            from core.prompt_template import PromptAssembly
            l3_assembly = PromptAssembly()
            l3_assembly.sections = l3_sections
            l3_text = l3_assembly.assemble()

        parts = []
        if l1_block.content:
            parts.append(l1_block.content)
        if l2_block.content:
            parts.append(l2_block.content)
        if l3_text:
            parts.append(l3_text)

        prompt = "\n".join(parts)
        return prompt

    def _log(self, text: str):
        """记录步骤（或通过回调通知）。"""
        if self.on_step:
            self.on_step(text)

        # 安全打印：替换无法编码的字符而非抛异常
        try:
            encoding = sys.stdout.encoding or 'utf-8'
            safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(f"  {safe}", flush=True)
        except Exception:
            print(f"  [日志编码错误]", flush=True)
    def _on_budget_warning(self, snapshot, critical_categories):
        """预算预警回调：当某类别达到 warning 阈值时触发。"""
        self._log(f"⚠️ Budget Warning: {', '.join(critical_categories)} "
                  f"({snapshot.total_used}/{snapshot.total_budget} tokens)")

    def _on_budget_critical(self, snapshot, critical_categories):
        """预算危险回调：当某类别达到 critical/over 阈值时触发。"""
        self._log(f"🚨 Budget Critical: {', '.join(critical_categories)} "
                  f"({snapshot.total_used}/{snapshot.total_budget} tokens)")
        # 钩子事件 on_budget_critical 已在 HOOK_EVENTS 注册，需要时注册 handler 即可响应

    # ── ToolOrchestrator 集成的四阶段编排 ──────────────────────────
    def _init_orchestrator(self):
        """初始化 ToolOrchestrator（惰性）。"""
        if hasattr(self, '_orchestrator') and self._orchestrator is not None:
            return
        from core.tool_orchestrator import ToolOrchestrator, ToolOrchestratorConfig
        self._orchestrator = ToolOrchestrator(
            tool_registry=self.tools,
            config=ToolOrchestratorConfig(
                enable_approval=self.permission_enabled,
            ),
        )
        # 打通 on_approval_request → 通道推送
        if getattr(self, 'on_approval_request', None):
            self._orchestrator.set_approval_callback(self.on_approval_request)

    def _execute_via_orchestrator(self, fn_name: str, args_dict: dict,
                                   tool_call_id: str = "") -> 'ToolExecutionResult':
        """通过 ToolOrchestrator 执行工具（四阶段：Approval → Safety → Execute → Retry）。

        如果 orchestrator 未初始化，自动初始化。兼容 agent_loop 现有的 on_approval_request 回调。
        """
        from core.tool_orchestrator import ToolExecutionRequest
        self._init_orchestrator()

        # 强制 gateway 模式：审批走通道推送不阻塞
        os.environ["KUAFU_GATEWAY_RUNNING"] = "1"

        req = ToolExecutionRequest(
            tool_name=fn_name,
            args=args_dict,
            tool_call_id=tool_call_id,
        )
        return self._orchestrator.execute(req)

    def _try_delegate_complex_skills(self, task: str) -> Optional[dict]:  # pragma: no cover
        """检测复杂 skill 并委派子 Agent 执行。

        当任务匹配的 skill 中包含复杂 skill（步骤>=5 或跨领域工具>=3），
        直接在任务正式执行前委派子 Agent 完成。

        Args:
            task: 用户任务文本

        Returns:
            委派结果 dict（包含 summary），若无复杂 skill 或委派失败返回 None
        """
        try:
            from core.skill_resolver import (
                match_skills, resolve_skill_execution, build_delegation_prompt
            )

            matched = match_skills(task)
            if not matched:
                return None

            simple_skills, complex_skills = resolve_skill_execution(matched)
            if not complex_skills:
                return None

            # 对第一个最匹配的复杂 skill 执行委派
            top_skill = complex_skills[0]
            self._log(f"🧩 检测到复杂 skill: {top_skill['name']} ({len(top_skill.get('steps', []))} 步) → 委派子 Agent")

            # 构建子 Agent prompt
            sub_prompt = build_delegation_prompt(top_skill, task)

            # 使用子 Agent 执行
            from core.subagent import handle_delegate
            result = handle_delegate({
                "goal": sub_prompt,
                "context": "",
            })

            # 记录使用
            from core.skill_resolver import increment_usage, record_usage
            increment_usage(top_skill['name'])
            record_usage(top_skill['name'], task, result.get("success", False), result.get("duration", 0))

            if result.get("success"):
                self._log(f"✅ 复杂 skill '{top_skill['name']}' 委派成功 ({result.get('duration', 0):.1f}s)")
                return {
                    "skill": top_skill['name'],
                    "summary": result.get("summary", "")[:500],
                    "details": result.get("output", "")[:1000],
                }
            else:
                self._log(f"⚠️ 复杂 skill '{top_skill['name']}' 委派失败: {result.get('output', '')[:100]}")
                return None

        except Exception as e:
            self._log(f"⚠️ 复杂 skill 委派异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def run(self, task: str,
            resume_from: Optional[str] = None,
            resume_mode: str = "brief",  # "brief" | "fork" | "full"
            resume_max_tokens: int = 4000) -> dict:
        """执行一次完整任务。

        Args:
            task: 用户任务描述
            resume_from: 可选。从指定会话 ID 恢复上下文
            resume_mode: 恢复模式
                - "brief"（默认）：注入上下文简报
                - "fork"：fork 出子会话继续
                - "full"：直接从原会话继续（不创建新会话）
            resume_max_tokens: 恢复数据的最大 token 数

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

        # 惰性初始化：需要时才创建（减少启动开销）
        self._lazy_init()
        final_result = ""
        final_summary = ""

        # ── 触发 on_task_start 钩子（异步） ──
        if self.hooks_enabled:
            trigger_async("on_task_start", {
                "task": task[:200],
                "task_type": detect_task_type(task),
            })

        # ── 会话初始化（创建 / resume / fork） ──────────────────
        if resume_from and resume_mode == "full":
            # 全量恢复：直接使用原会话
            self.current_session_id = resume_from
            # 加载历史消息
            history = self.sessions.get_messages(resume_from, max_tokens=0)
            if history:
                messages = history[:]
                self._log(f"📋 从会话 {resume_from} 全量恢复 ({len(history)} 条消息)")
            else:
                self.current_session_id = self.sessions.create_session(title=task[:50])
        elif resume_from and resume_mode == "fork":
            # Fork 模式：创建子会话并注入历史
            fork_id = self.sessions.fork_session(
                resume_from, title=task[:50], max_tokens=resume_max_tokens
            )
            if fork_id:
                self.current_session_id = fork_id
                self._log(f"🍴 从 {resume_from} fork 出新会话 {fork_id}")
            else:
                self.current_session_id = self.sessions.create_session(title=task[:50])
        else:
            # 普通模式：创建新会话
            self.current_session_id = self.sessions.create_session(title=task[:50])

        # System prompt（含技能注入）
        system_prompt = self.build_system_prompt(task)

        # ── Resume brief 模式：在 system prompt 尾部注入上下文简报 ──
        if resume_from and resume_mode == "brief":
            brief = self.sessions.resume_context(resume_from, max_tokens=resume_max_tokens)
            if brief:
                system_prompt += f"\n\n## 上下文简报（来自历史会话 {resume_from}）\n{brief}"
                self._log(f"📋 已注入会话 {resume_from} 的上下文简报")

        messages.append({"role": "system", "content": system_prompt})

        # ── 复杂 skill 预处理：已被 invoke_expert 专家系统替代 ──
        self._delegation_result = None
        self._delegation_thread = None

        messages.append({"role": "user", "content": task})
        self.sessions.append_message(self.current_session_id, "user", task)

        # 执行循环
        last_tool_results: list[str] = []
        _phase_tools_run = []  # 工具执行记录
        _phase_contexts: list[str] = []  # 本阶段的 LLM 回复/关键发现
        for turn in range(self.max_turns):
            turn_count = turn + 1

            # ── System Reminders: 第 2 轮起，每次用户消息前注入 1-3 条提醒 ──
            if turn > 0:
                reminders = build_reminders(
                    task=task,
                    turn_count=turn,
                    last_tool_results=last_tool_results if last_tool_results else None,
                )
                if reminders:
                    # 插入为 system 消息（不占用 user/assistant 位置）
                    messages.append({
                        "role": "system",
                        "content": reminders,
                    })
                    self._log(f"💡 System Reminder: {reminders[:80]}...")

            self._log(f"🤔 第 {turn_count}/{self.max_turns} 轮 — LLM 思考中...")
            llm_start_ts = time.time()
            if self.on_llm_start:
                self.on_llm_start(turn_count)

            # 上下文压缩检查：每次 LLM 调用前检查是否需要压缩
            if self.compressor.needs_compression(messages):
                self._log(f"📏 上下文超限（{self.compressor._count_tokens(messages)} tokens），执行压缩...")
                # 使用本地 LLM 智能摘要压缩（方案二）
                result = self.compressor.compress_with_local_llm(messages)
                if result.messages_removed > 0:
                    # 保留 system + 摘要 + 最近完整轮次（至少保留最后一轮user+assistant+tools）
                    system_msgs = [m for m in messages if m.get("role") == "system"]
                    recent_non_system = [m for m in messages if m.get("role") != "system"]
                    keep_count = min(self.compressor.keep_recent_rounds * 4, len(recent_non_system))
                    recent_msgs = recent_non_system[-keep_count:] if keep_count > 0 else []
                    messages = system_msgs + [{
                        "role": "system",
                        "content": f"【上下文压缩】以下是对旧对话的摘要，请基于此继续当前任务，不要重新自我介绍：\n{result.summary}",
                    }] + recent_msgs
                    self._log(f"✅ 压缩完成: {result.compression_ratio*100:.0f}% 缩减 ({result.original_tokens}→{result.compressed_tokens} tokens)")
                    if result.summary:
                        self._log(f"📝 摘要: {result.summary[:150]}...")

            # Budget Allocator 扫描：每次 LLM 调用前检查预算
            self._budget_scan_count += 1
            budget_snapshot = self.budget_allocator.scan(messages)
            budget_actions = self.budget_allocator.get_actions(budget_snapshot)
            if budget_actions:
                for action in budget_actions:
                    if action.action_type == "collapse" and action.severity in ("critical", "over"):
                        self._log(f"📏 Budget 驱动压缩: {action.description}")
                        # 由 ContextCollapse 接管——让 LLM 调用的错误处理去触发 collapse
                        # 这里只做日志记录，实际 collapse 在 LLM 返回 400 时触发
                    elif action.action_type == "microcompact" and action.severity == "warning":
                        self._log(f"📦 Budget 提示: {action.description}")
                    elif action.action_type == "compress" and action.severity == "warning":
                        self._log(f"📏 Budget 预警压缩: {action.description}")

            # 调用 LLM
            response = self.llm.chat(messages, tools=self.tools.get_schemas())

            # ── LLM 调用结束通知 ──
            llm_elapsed = time.time() - llm_start_ts
            if self.on_llm_end:
                success_status = "success" if response.get("success") else "error"
                self.on_llm_end(turn_count, success_status)

            # ── LLM 调用失败处理 ─────────────────────────────────
            if not response["success"]:
                error_msg = response.get("error", "LLM 调用失败")
                # 上下文超限：优先尝试非破坏性 ContextCollapse，失败再暴力截断
                if "exceed" in error_msg.lower() or "context" in error_msg.lower() or "400" in error_msg:
                    self._log(f"📏 LLM 返回上下文超限错误，尝试非破坏性压缩...")

                    # 第一步：尝试 ContextCollapse（非破坏性投影）
                    collapse_result = self.collapser.collapse(
                        messages=messages,
                        session_id=self.current_session_id or "",
                    )

                    # 触发 on_context_exceed / on_collapse 钩子（异步）
                    if self.hooks_enabled:
                        if collapse_result.collapsed:
                            trigger_async("on_collapse", {
                                "task": task[:100],
                                "before": collapse_result.original_count,
                                "after": collapse_result.collapsed_count,
                                "tokens_saved": collapse_result.tokens_saved,
                                "method": "context_collapse",
                            })
                        trigger_async("on_context_exceed", {
                            "tokens": self.compressor._count_tokens(messages),
                            "method": "context_collapse" if collapse_result.collapsed else "truncate",
                            "collapsed": collapse_result.collapsed,
                            "task": task[:100],
                        })

                    if collapse_result.collapsed_count < collapse_result.original_count:
                        # 使用 CollapseResult 重建压缩后的消息列表
                        summary = collapse_result.summary
                        system_msgs = [m for m in messages if m.get("role") == "system"]
                        non_system = [m for m in messages if m.get("role") != "system"]
                        keep_count = self.collapser.keep_recent_rounds * 4
                        recent_msgs = non_system[-keep_count:] if len(non_system) > keep_count else non_system
                        collapse_note = {
                            "role": "system",
                            "content": (
                                f"【上下文投影】以下 {collapse_result.original_count - len(recent_msgs)} 条旧消息已被压缩为摘要。\n"
                                f"原始数据完整保留在磁盘 session '{self.current_session_id or '?'}' 的 JSONL 中。\n"
                                f"通过 session_store.get_raw_messages() 可按需读取原始细节。\n\n"
                                f"摘要：\n{summary}"
                            ),
                        }
                        messages = system_msgs + [collapse_note] + recent_msgs
                        self._log(f"✅ 非破坏性压缩完成: 节省约 {collapse_result.tokens_saved} tokens")
                        # 重新调用 LLM
                        response = self.llm.chat(messages, tools=self.tools.get_schemas())
                        if response["success"]:
                            pass
                        else:
                            error_msg = response.get("error", "压缩后 LLM 仍然失败")
                            errors.append(error_msg)
                            break
                    else:
                        # ContextCollapse 不可用（太少轮次），降级到暴力截断
                        self._log(f"⚠️ ContextCollapse 跳过（轮次少），暴力截断至最近2轮")
                        # 原有暴力截断逻辑
                        original_tokens = self.compressor._count_tokens(messages)
                        system_msgs = [m for m in messages if m.get("role") == "system"]
                        recent_msgs = [m for m in messages if m.get("role") != "system"][-8:]
                        keep = system_msgs + recent_msgs
                        keep_tokens = self.compressor._count_tokens(keep)
                        self._log(f"   {original_tokens} → {keep_tokens} tokens")
                        messages = keep
                        response = self.llm.chat(messages, tools=self.tools.get_schemas())
                        if not response["success"]:
                            errors.append(response.get("error", "截断后 LLM 仍然失败"))
                            break
                else:
                    # 非上下文超限错误，直接放弃
                    errors.append(error_msg)
                    break

            # 添加 assistant 消息
            # 记录 LLM 回复内容到阶段上下文
            llm_content = response.get("content", "") or ""
            if llm_content.strip() and "finish" not in str(response.get("tool_calls", [])):
                _phase_contexts.append(f"第 {turn_count} 轮: {llm_content[:200]}")

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
                        raw_args = tc["function"]["arguments"]
                        # arguments 可能是 JSON 字符串或 dict
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                args = {"result": raw_args}
                        elif isinstance(raw_args, dict):
                            args = raw_args
                        else:
                            args = {"result": str(raw_args) if raw_args else ""}
                        if llm_content:
                            final_result = llm_content
                            final_summary = args.get("summary", llm_content[:200])
                        else:
                            final_result = args.get("result", "")
                            final_summary = args.get("summary", "")
                        # finish 中带 send_files 参数 → 自动发送文件
                        send_files = args.get("send_files", [])
                        if send_files:
                            self._log(f"📎 finish 中带文件发送请求: {send_files}")
                            # 根据触发通道选择发送方式
                            platform = getattr(self, '_current_platform', '') or os.environ.get('KUAFU_CURRENT_PLATFORM', '')
                            for fp in send_files:
                                p = Path(fp).expanduser().resolve()
                                if not p.exists() or not p.is_file():
                                    self._log(f"⚠️ 文件不存在: {fp}")
                                    continue
                                if platform == "wechat":
                                    from core.tool_registry import ToolRegistry
                                    r = ToolRegistry._send_via_wechat(str(p))
                                    if r["success"]:
                                        self._log(f"✅ 文件已通过微信发送: {p.name}")
                                    else:
                                        self._log(f"⚠️ 微信发送失败: {r['output']}")
                                elif platform == "feishu":
                                    chat_id = getattr(self, '_current_chat_id', '')
                                    if not chat_id:
                                        self._log(f"⚠️ 飞书 chat_id 未知，无法发送")
                                        continue
                                    from core.tool_registry import ToolRegistry
                                    r = ToolRegistry._send_via_feishu(str(p), chat_id=chat_id)
                                    if r["success"]:
                                        self._log(f"✅ 文件已通过飞书发送: {p.name}")
                                    else:
                                        self._log(f"⚠️ 飞书发送失败: {r['output']}")
                                else:
                                    self._log(f"⚠️ 当前通道({platform})不支持文件发送")
                        finish_called = True
                        break
                if finish_called:
                    break

            # 执行工具调用
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    fn_name = tc["function"]["name"]

                    # 跳过 finish
                    if fn_name == "finish":  # pragma: no cover
                        continue

                    arg_preview = json.dumps(
                        tc.get("function", {}).get("arguments", {}),
                        ensure_ascii=False,
                    )[:60]
                    self._log(f"🔧 执行 {fn_name}({arg_preview}...)")
                    tool_start_ts = time.time()
                    if self.on_tool_start:
                        self.on_tool_start(fn_name, tc.get("function", {}).get("arguments", {}), tool_start_ts)

                    # ── PreToolUse: 权限检查（Deny 规则 → 自动模式 → 人工审批） ──
                    if self.permission_enabled and fn_name not in ("finish", "delegate_task", "skill_rollback", "send_file_to_user"):
                        raw_args = tc.get("function", {}).get("arguments", {})
                        # 解析 arguments：可能是 JSON 字符串或 dict
                        if isinstance(raw_args, str):  # pragma: no cover
                            try:
                                args_dict = json.loads(raw_args)
                            except json.JSONDecodeError:  # pragma: no cover
                                args_dict = {}
                        elif isinstance(raw_args, dict):
                            args_dict = raw_args
                        else:  # pragma: no cover
                            args_dict = {}
                        # 触发 on_tool_before 钩子（同步，可阻止）
                        if self.hooks_enabled:
                            hook_results = trigger_sync("on_tool_before", {
                                "tool": fn_name,
                                "args": args_dict,
                                "task": task[:100],
                                "turn": turn_count,
                            })
                            blocked = any(r.blocked for r in hook_results)
                            if blocked:
                                blocked_by = [r.handler_id for r in hook_results if r.blocked]
                                msg = f"⛔ 工具 {fn_name} 被钩子阻止: {blocked_by}"
                                self._log(msg)
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": msg,
                                })
                                self.sessions.append_message(
                                    self.current_session_id, "tool", msg[:500],
                                )
                                # 触发 on_tool_rejected 钩子
                                if self.hooks_enabled:
                                    trigger_async("on_tool_rejected", {
                                        "tool": fn_name, "reason": "hook_blocked",
                                        "blocked_by": blocked_by,
                                    })
                                continue

                        # ── 权限检查由 ToolOrchestrator 内部处理（Approval → Safety → Execute 四阶段） ──
                        # 不需要在此单独调用，_execute_via_orchestrator 会自动处理
                        pass

                    # ── 工具执行（总是执行，独立于权限检查） ──
                    raw_args = tc.get("function", {}).get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            args_dict = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args_dict = {}
                    elif isinstance(raw_args, dict):
                        args_dict = raw_args
                    else:
                        args_dict = {}

                    orchestrator_result = self._execute_via_orchestrator(
                        fn_name=fn_name,
                        args_dict=args_dict,
                        tool_call_id=tc["id"],
                    )

                    if not orchestrator_result.success:
                        msg = orchestrator_result.output
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": msg,
                        })
                        self.sessions.append_message(
                            self.current_session_id, "tool", msg[:500],
                        )
                        if self.hooks_enabled:
                            trigger_async("on_tool_rejected", {
                                "tool": fn_name, "reason": "denied",
                            })
                        err_msg = f"工具 {fn_name} 执行失败: {msg[:200]}"
                        errors.append(err_msg)
                        continue

                    # ── 成功执行 ──
                    tool_result = {"success": True, "output": orchestrator_result.output}

                    # ── 工具执行结束通知 ──
                    if self.on_tool_end:
                        tool_elapsed = time.time() - tool_start_ts
                        self.on_tool_end(fn_name, tc.get("function", {}).get("arguments", {}),
                                         tool_elapsed, "success")

                    # ── 安全脱敏：对终端输出中的 API key、token 等脱敏
                    raw_output = str(tool_result.get("output", "(无输出)"))
                    safe_output = SafetyLayer.sanitize_text(raw_output)

                    # ── Microcompact：大工具结果 → 磁盘摘要 ──
                    # Budget Aware：如果 TOOLS 预算超限，降低 microcompact 阈值
                    budget_tools_alert = False
                    if self._budget_scan_count > 0:
                        last_snap = self.budget_allocator._last_snapshot
                        if last_snap:
                            tools_usage = last_snap.categories.get("tools")
                            if tools_usage and tools_usage.status in ("warning", "critical", "over"):
                                budget_tools_alert = True

                    should_microcompact = (
                        ToolResultStore.should_compact(safe_output)
                        or (budget_tools_alert and len(safe_output) > 800)  # 预算预警时阈值从2000降到800
                    )
                    # read_tool_result 本身就是要读数据，不再次 microcompact（防死循环）
                    if fn_name == "read_tool_result":  # pragma: no cover
                        should_microcompact = False  # pragma: no cover
                    # invoke_expert/expert 的返回是给用户的最终输出，不压缩
                    if fn_name in ("invoke_expert", "invoke_experts"):
                        should_microcompact = False
                    # 大上下文模型（≥100K tokens）提高阈值，只压缩超大结果
                    if should_microcompact:
                        ctx = self.llm.get_context_window()
                        if ctx >= 100000:
                            # 只压缩超过 10000 chars 的结果
                            should_microcompact = len(safe_output) > 10000

                    if should_microcompact:
                        meta = self.tool_result_store.store(fn_name, safe_output)
                        compact_text = meta["compact"]
                        # 写磁盘后，放更紧凑的占位进上下文
                        safe_output_for_context = compact_text
                        self._log(f"📦 Microcompact: {fn_name} 结果 {len(raw_output)} chars → 磁盘 ({meta['file_path']})")
                    else:
                        safe_output_for_context = safe_output

                    # ── P0-1: BudgetReduction（零 token 成本裁剪） ──────────────
                    # Microcompact 未命中（非结构化的超大纯文本），做就地裁剪
                    _budget_reduced = budget_reduce_output(
                        safe_output_for_context,
                        tool_name=fn_name,
                    )
                    if _budget_reduced != safe_output_for_context:
                        self._log(
                            f"⚡ BudgetReduction: {fn_name} 结果 "
                            f"{len(safe_output_for_context)} → {len(_budget_reduced)} chars"
                        )
                        safe_output_for_context = _budget_reduced

                    # Observer：跟踪工具调用
                    tool_result_for_obs = {
                        "success": tool_result.get("success", False),
                        "output": safe_output[:500],
                    }
                    self._observer.on_tool_call(
                        fn_name,
                        tc.get("function", {}).get("arguments", {}),
                        tool_result_for_obs,
                    )

                    # ── 工具结果过滤：让 LLM 快速判断结果是否有贡献 ──
                    # 条件：结果超过 200 字符 且 工具调用成功 且 非错误
                    should_keep = True
                    needs_filter = (
                        len(safe_output) > 500  # 只有大结果才判，小结果直接保留
                        and tool_result["success"]
                        and fn_name not in ("web_search", "web_extract", "web_crawl", "read_file", "invoke_expert", "invoke_experts", "terminal")  # 搜索/提取/读文件/终端/专家输出默认保留
                    )
                    if needs_filter:  # pragma: no cover
                        filter_prompt = (  # pragma: no cover
                            "你是一个结果过滤器。用户正在做一个任务，下面是一个工具调用的返回结果。\\n"  # pragma: no cover
                            "判断这个结果对当前任务是否有实质贡献（有帮助的信息/数据/代码片段），\\n"  # pragma: no cover
                            "还是只是过程性/噪音内容。\\n\\n"  # pragma: no cover
                            f"当前任务：{task[:100]}\\n"  # pragma: no cover
                            f"工具名称：{fn_name}\\n"  # pragma: no cover
                            f"结果预览（前500字）：\\n{safe_output[:500]}\\n\\n"  # pragma: no cover
                            "只回复 'keep' 或 'discard'，不要其他内容。"  # pragma: no cover
                        )  # pragma: no cover
                        try:  # pragma: no cover
                            filter_resp = self.llm.chat([{  # pragma: no cover
                                "role": "system",  # pragma: no cover
                                "content": "你是一个简洁的结果过滤器。只回复 keep 或 discard。"  # pragma: no cover
                            }, {  # pragma: no cover
                                "role": "user",  # pragma: no cover
                                "content": filter_prompt,  # pragma: no cover
                            }], tools=None)  # pragma: no cover
                            if filter_resp["success"]:  # pragma: no cover
                                decision = filter_resp["content"].strip().lower()  # pragma: no cover
                                if decision.startswith("discard"):  # pragma: no cover
                                    should_keep = False  # pragma: no cover
                                    self._log(f"🗑️ 过滤掉 {fn_name} 结果 ({len(safe_output)} chars) — 判定无贡献")  # pragma: no cover
                        except Exception:  # pragma: no cover
                            pass  # 过滤失败则保留结果（保守策略）

                    if should_keep:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": safe_output_for_context,
                        })
                    else:  # pragma: no cover
                        # 丢弃但留一个简短的占位
                        messages.append({  # pragma: no cover
                            "role": "tool",  # pragma: no cover
                            "tool_call_id": tc["id"],  # pragma: no cover
                            "content": f"[工具 {fn_name} 的结果被过滤（判定无贡献），原长 {len(safe_output)} 字符]",  # pragma: no cover
                        })  # pragma: no cover

                    # ── P0-2: 渐进式 PostToolUse 压缩管线 ────────────────────────
                    # Claude Code 参考：5-stage 渐进管线，从零成本到高成本
                    # 第 1 层：BudgetReduction — 已在结果进入 messages 前完成
                    # 第 2 层：Snip — clean_old_tool_results（零 token 成本）
                    # 第 3 层：LLM 摘要 — 只有前两层还不够才触发
                    post_tool_tokens = self.compressor._count_tokens(messages)
                    if post_tool_tokens > self.compressor.max_context_tokens * 0.85:
                        self._log(
                            f"📏 PostToolUse 管线: {post_tool_tokens}/"
                            f"{self.compressor.max_context_tokens} tokens"
                        )

                        # ── 第 2 层：Snip（零成本裁剪旧工具结果） ──
                        snip_msgs, snip_saved = self.compressor.clean_old_tool_results(
                            messages, max_rounds=4, keep_summary_chars=100
                        )
                        snip_tokens = self.compressor._count_tokens(snip_msgs)
                        self._log(
                            f"🔧 Snip: 节省 ~{snip_saved} tokens "
                            f"(now {snip_tokens}/{self.compressor.max_context_tokens})"
                        )

                        # Snip 后 recheck
                        if snip_tokens <= self.compressor.max_context_tokens * 0.85:
                            messages = snip_msgs
                            self._log(f"✅ Snip 足够，无需 LLM 压缩")
                        else:  # pragma: no cover
                            # ── 第 3 层：LLM 摘要（兜底） ──
                            self._log(f"🧠 Snip 不够，触发 LLM 摘要兜底")  # pragma: no cover
                            ctx_result = self.compressor.compress_with_local_llm(messages)  # pragma: no cover
                            if ctx_result.messages_removed > 0:  # pragma: no cover
                                system_msgs = [m for m in messages if m.get("role") == "system"]  # pragma: no cover
                                recent_non_system = [m for m in messages if m.get("role") != "system"]  # pragma: no cover
                                keep_count = min(self.compressor.keep_recent_rounds * 4, len(recent_non_system))  # pragma: no cover
                                recent_msgs = recent_non_system[-keep_count:] if keep_count > 0 else []  # pragma: no cover
                                messages = system_msgs + [{  # pragma: no cover
                                    "role": "system",  # pragma: no cover
                                    "content": f"【上下文压缩】以下是对旧对话的摘要，请基于此继续当前任务：\n{ctx_result.summary}",  # pragma: no cover
                                }] + recent_msgs  # pragma: no cover
                                self._log(  # pragma: no cover
                                    f"✅ LLM 压缩完成: {ctx_result.compression_ratio*100:.0f}% 缩减 "  # pragma: no cover
                                    f"({ctx_result.original_tokens}→{ctx_result.compressed_tokens} tokens)"  # pragma: no cover
                                )  # pragma: no cover

                    self.sessions.append_message(
                        self.current_session_id, "tool",
                        safe_output[:500],
                    )

                    if not tool_result["success"]:
                        err = f"工具 {fn_name} 失败: {safe_output[:200]}"
                        errors.append(err)
                        # 记录失败的工具结果供下一轮 System Reminders 使用
                        last_tool_results.append(f"{fn_name}:fail:{safe_output[:80]}")
                        _phase_tools_run.append(f"❌ {fn_name}")
                        self._log(f"❌ 工具 {fn_name} 执行失败")
                        # 触发 on_tool_error 钩子（异步）
                        if self.hooks_enabled:
                            trigger_async("on_tool_error", {
                                "tool": fn_name,
                                "args": tc.get("function", {}).get("arguments", {}),
                                "error": safe_output[:500],
                                "task": task[:100],
                            })
                    else:
                        _phase_tools_run.append(f"✅ {fn_name}")
                        self._log(f"✅ 工具 {fn_name} 执行成功")
                        # 有实质产出的工具 → 立即推送阶段性简报
                        _phase_push_tools = {"write_file", "patch", "terminal", "execute_code",
                                              "web_search", "web_extract", "browser_navigate",
                                              "vision_analyze", "text_to_speech"}
                        if self.on_phase and fn_name in _phase_push_tools and safe_output.strip():
                            _phase_lines = []
                            if _phase_contexts:
                                _recent = _phase_contexts[-1].split(":", 1)[-1].strip()
                                if _recent:
                                    _phase_lines.append(f">{_recent}")
                            # 提取工具结果摘要
                            _out = safe_output.strip()[:200]
                            _phase_lines.append(f"📌 {fn_name} → {_out}")
                            if errors:
                                _phase_lines.append(f"⚠️ {errors[-1][:120]}")
                            _phase_summary = "\n".join(_phase_lines)
                            try:
                                self.on_phase(_phase_summary)
                            except Exception:
                                pass
                        # 触发 on_tool_after 钩子（异步）
                        if self.hooks_enabled:
                            trigger_async("on_tool_after", {
                                "tool": fn_name,
                                "args": tc.get("function", {}).get("arguments", {}),
                                "output_length": len(safe_output),
                                "task": task[:100],
                                "turn": turn_count,
                            })
            if not response.get("tool_calls"):
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
            "task_type": detect_task_type(task),
            "duration": round(time.time() - start, 3),
            "session_id": self.current_session_id or "",
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

        # 后处理（所有 LLM 调用已移入后台线程，主线程不阻塞）
        _async_post_task(task_result, messages, task, self)

        # 质量评分（零 LLM 成本，纯静态分析，在主线程执行）
        quality = self._quality_score(task_result, messages)
        task_result["quality"] = quality

        # 任务报告：复杂任务（多轮交互）生成结构化报告
        if turn_count >= 3:
            task_result["report"] = self._generate_report(task, task_result, messages)

        task_result["turns"] = turn_count
        task_result["messages_count"] = len(messages)

        # 定时记忆维护（每 10 轮触发一次）
        self._mem_maintenance_counter += 1
        if self._mem_maintenance_counter >= 10:  # pragma: no cover
            self._mem_maintenance_counter = 0  # pragma: no cover
            try:  # pragma: no cover
                result = self.memory.maintenance()  # pragma: no cover
                if result["expired"] > 0 or result["merged"] > 0:  # pragma: no cover
                    self._log(f"记忆维护: 清理 {result['expired']} 过期 + 合并 {result['merged']} 条")  # pragma: no cover
            except Exception as e:  # pragma: no cover
                self._log(f"记忆维护异常: {e}")  # pragma: no cover

        # ── 触发 on_task_end 钩子（同步完成，异步发送） ──
        if self.hooks_enabled:
            trigger_async("on_task_end", {
                "task": task[:200],
                "success": task_result.get("success", False),
                "turns": task_result.get("turns", 0),
                "errors": task_result.get("errors", [])[:3],
                "duration": task_result.get("duration", 0),
                "result_summary": task_result.get("result", "")[:200],
            })

        return task_result

    # ── 三阶段进化管道 ────────────────────────────────────────────────

    def _run_evolution_pipeline(self, task_result: dict, task: str, messages: list) -> None:
        """三阶段进化管道（Observer → EvolutionState → Judge → SkillWriter）。

        替换旧的 P1 Learner + EvolutionEngine 两条独立调用链。
        只做 1 次 LLM 调用（当 Observer 信号表明"可能有价值"时）。
        """
        try:
            task_type = task_result.get("task_type", "generic")
            errors = task_result.get("errors", [])
            success = task_result.get("success", False)

            # 从 Observer 获取运行时摘要 + errors
            # 构造一个精简的 task_result dict 喂给 on_task_complete
            obs_task_result = {
                "success": success,
                "task_type": task_type,
                "errors": errors,
                "result": task_result.get("result", ""),
                "duration": task_result.get("duration", 0.0),
                "tool_calls": task_result.get("tool_calls", 0),
            }
            obs = self._observer.on_task_complete(obs_task_result, user_input=task)

            # 后验注入：user_correction 和 errors 已在 on_task_complete 中由 Observer 自己检测
            # 但 agent_loop 的 _detect_user_correction 使用 messages 更精确，覆盖之
            if self._detect_user_correction(messages):
                obs.has_user_correction = True

            # 注入进化状态信息
            try:
                obs.is_novel_task = self.evolution.evolution_state.is_novel(task_type)
                obs.is_repeated_failure = self.evolution.evolution_state.is_repeated_failure(task_type)
                obs.task_type_history = self.evolution.evolution_state.get_task_type_count(task_type)
                if errors:
                    obs.has_unknown_error = any(
                        self.evolution.evolution_state.is_unknown_error(e) for e in errors
                    )
            except Exception:  # pragma: no cover
                pass  # 进化状态信息非关键

            # 运行三阶段管道
            evo_result = self.evolution.run_pipeline(obs, task_type)

            # 记录进化结果
            task_result["evolution"] = evo_result or {}

            # ── 新增：质量评分持久化 ────────────────────────────
            # 如果进化管道产生了 skill（Judge 决定值得学），
            # 将 _quality_score 的评分持久化到 skill 版本链
            try:
                if evo_result and evo_result.get("skill_written"):
                    skill_name = evo_result.get("skill_name", "")
                    if skill_name:
                        # 已经调用了 _quality_score，从 task_result 取
                        quality = task_result.get("quality", {})
                        qscore = quality.get("score", 7)
                        # 归一化到 0-1
                        norm_score = max(0.0, min(1.0, qscore / 10.0))
                        self.evolution.evolution_state.record_skill_quality(
                            skill_name, norm_score
                        )
            except Exception:  # pragma: no cover
                pass

            # ── 新增：evolution_mode 通知 ───────────────────────
            # 根据 pipeline 中 Judge 的 evolution_mode 做不同反应
            try:
                if evo_result and evo_result.get("evolution_mode"):
                    mode = evo_result["evolution_mode"]
                    skill_name = evo_result.get("skill_name", "")
                    if mode == "CAPTURED":
                        self._log(f"🧬 新技能捕获: {skill_name}（首次发现）")
                    elif mode == "FIX":  # pragma: no cover
                        self._log(f"🔧 技能修复: {skill_name}（覆盖旧版本）")
                    elif mode == "DERIVED":  # pragma: no cover
                        self._log(f"🌿 技能衍生: {skill_name}（派生新版本）")
            except Exception:  # pragma: no cover
                pass

            # 健康检查
            health = self.evolution.evolution_state.health_check()
            if health:
                self._log(f"⚠️ 进化健康: {health}")

            # ── 进化规则分析：失败任务 → LLM 分析 → 生成规则 ──
            try:
                self._trigger_evolution_rule_analysis(task_result, task, messages)
            except Exception:  # pragma: no cover
                pass

        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 进化管道异常: {e}")

    def _trigger_evolution_rule_analysis(self, task_result: dict,
                                          task: str, messages: list) -> None:
        """分析任务结果 → 进化规则生成 + 置信度更新。

        触发条件（满足任一）：
        1. 任务失败（有 errors）
        2. 用户纠正（_detect_user_correction）
        3. 3+ 轮交互的复杂任务完成
        """
        if not self._evolution_rules:
            return

        success = task_result.get("success", False)
        errors = task_result.get("errors", [])
        turns = task_result.get("turns", 0)
        has_correction = self._detect_user_correction(messages)
        is_significant = turns >= 3 and len(task_result.get("result", "")) > 50

        if not errors and not has_correction and not is_significant:
            return

        # 失败或纠正 → LLM 分析并生成规则
        should_evolve = (not success and errors) or has_correction
        if should_evolve or is_significant:
            analysis = self._evolution_rules.analyze_failure(task, task_result, messages)
            if analysis:
                rule = analysis.get("rule", "")
                category = analysis.get("category", "rule")
                keywords = analysis.get("keywords", [])
                ttype = analysis.get("task_type", "")
                if rule:
                    result = self._evolution_rules.add_rule(
                        rule, category=category, task_type=ttype,
                        keywords=keywords, source=f"task:{task[:50]}",
                    )
                    if result.get("action") in ("created", "reinforced"):
                        self._log(f"🧬 进化规则: {rule[:60]}... ({result['action']}, c={result.get('confidence',0):.2f})")

        # 成功 → 强化匹配的规则
        if success and hasattr(self, '_evolution_rules') and self._evolution_rules:
            matched = self._evolution_rules.match_rules(task)
            if matched:
                for r in matched:
                    from core.evolution_rules import EvolutionRuleManager as _ERM
                    topic = _ERM.make_topic_static(r["rule"])
                    self._evolution_rules.report_success(topic)
                    self._log(f"🧬 规则强化: {r['rule'][:40]}...")

    def _detect_user_correction(self, messages: list) -> bool:
        """从对话中检测用户纠正信号。

        关键词：别、不对、错了、重新、改成、注意、但是+不、不用这样
        """
        correction_markers = [
            "别", "不对", "错了", "不是", "重新", "改成",
            "注意", "但是不", "不用这样", "不是这样",
        ]
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                for marker in correction_markers:
                    if marker in content:
                        return True
        return False

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
                if feedback != "无问题" and len(feedback) > 10:  # pragma: no cover
                    task_result["self_check"] = feedback  # pragma: no cover
                    task_result["result"] += f"\n\n---\n🔍 自检反馈:\n{feedback}"  # pragma: no cover
                    self._log(f"⚠️ 自检发现问题: {feedback[:120]}...")  # pragma: no cover
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
        except Exception as e:  # pragma: no cover
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

        prefs_path = ROOT_DIR / "memory" / "user_prefs.json"  # pragma: no cover
        prefs = {}  # pragma: no cover
        if prefs_path.exists():  # pragma: no cover
            try:  # pragma: no cover
                prefs = json.loads(prefs_path.read_text(encoding="utf-8"))  # pragma: no cover
            except (json.JSONDecodeError, OSError):  # pragma: no cover
                prefs = {}  # pragma: no cover
            if not isinstance(prefs, dict):  # pragma: no cover
                prefs = {}  # pragma: no cover

        # 从用户输入中提取偏好
        self._log("🎯 检测到偏好信号，正在学习...")  # pragma: no cover
        learn_prompt = (  # pragma: no cover
            "分析以下用户输入，提取明确的偏好/要求（如语言、工具、风格、格式等）。\n\n"  # pragma: no cover
            f"用户输入:\n{task}\n\n"  # pragma: no cover
            f"现有偏好:\n{json.dumps(prefs, ensure_ascii=False, indent=2)}\n\n"  # pragma: no cover
            "请输出 JSON 格式（不要多余文字）：\n"  # pragma: no cover
            "{\n"  # pragma: no cover
            '  "add": {"key": "新偏好对名称", "value": "新偏好值"},\n'  # pragma: no cover
            '  "remove": []  // 要删除的偏好键列表（如果有冲突）\n'  # pragma: no cover
            "}\n"  # pragma: no cover
            '如果没有提取到新的有效偏好，输出 {"add": null, "remove": []}'  # pragma: no cover
        )  # pragma: no cover
        learn_msg = [  # pragma: no cover
            {"role": "system", "content": "你是夸父偏好学习模块。输出严格 JSON。"},  # pragma: no cover
            {"role": "user", "content": learn_prompt},  # pragma: no cover
        ]  # pragma: no cover
        try:  # pragma: no cover
            resp = self.llm.chat(learn_msg, tools=None)  # pragma: no cover
            if not resp["success"]:  # pragma: no cover
                return  # pragma: no cover
            result = json.loads(resp["content"].strip())  # pragma: no cover
            add_item = result.get("add")  # pragma: no cover
            if add_item and add_item.get("key") and add_item.get("value"):  # pragma: no cover
                key = add_item["key"].strip()  # pragma: no cover
                value = add_item["value"].strip()  # pragma: no cover
                if key and value:  # pragma: no cover
                    prefs[key] = value  # pragma: no cover
                    # 删除冲突项
                    for k in result.get("remove", []):  # pragma: no cover
                        prefs.pop(k, None)  # pragma: no cover
                    # 写入
                    prefs_path.parent.mkdir(parents=True, exist_ok=True)  # pragma: no cover
                    prefs_path.write_text(  # pragma: no cover
                        json.dumps(prefs, ensure_ascii=False, indent=2)  # pragma: no cover
                    )  # pragma: no cover
                    self._log(f"📝 学到用户偏好: {key} = {value}")  # pragma: no cover
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 偏好学习异常: {e}")  # pragma: no cover

    # ── 对话记忆提取 ────────────────────────────────────────────────

    def _extract_conversation_memories(self, task_result: dict, messages: list) -> None:
        """从对话中提取用户事实/重要信息，写入 MemoryManager（Hindsight + NMM）。

        与 _deep_reflect（存工具经验教训）互补：
        - _deep_reflect 存工具使用经验
        - _extract_conversation_memories 存对话中的用户事实

        分两级：
        1. 快速路径（本方法）：轻量 LLM 提取用户事实，走 MemoryManager.store()
        2. 深度路径（TwoPhaseExtractor）：完整两阶段提取+去重
        """
        # 只对较长对话提取（≥4轮交互）
        if len(messages) < 6:
            return

        # 提取用户消息中的关键事实
        user_facts = []
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "").strip()
                if content and len(content) > 10:
                    if not content.startswith("{") and not content.startswith("["):
                        user_facts.append(content[:500])

        if not user_facts:
            return

        # 用本地模型或云端 LLM 提取事实
        if self._local and self._local.available():
            extracted = self._local.extract_facts(user_facts[-6:])
            if extracted:
                for line in extracted:
                    self.memory.store(
                        content=line[:500],
                        source="conversation",
                        tags=["user_fact"],
                        bypass_gate=True,
                    )
                self._log(f"💾 本地提取 {len(extracted)} 条用户事实")
                return  # 本地完成，跳过云端路径
        fact_prompt = (
            "从以下用户消息中提取可复用的**用户事实**（偏好、项目信息、决策、重要上下文）。\n"
            "不要提取：技术经验、错误日志、命令用法。\n"
            "每条用陈述句，一行一条，不要序号和标记。\n"
            f"用户消息：\n{chr(10).join(user_facts[-6:])}"
        )
        try:
            resp = self.llm.chat(
                [{"role": "system", "content": "你是一个记忆提取器。只输出事实，每行一条，不要多余文字。"},
                 {"role": "user", "content": fact_prompt}],
                tools=None,
            )
            if not resp["success"]:
                return
            output = resp["content"].strip()
            extracted = []
            for line in output.split("\n"):
                line = line.strip()
                if len(line) > 5:
                    extracted.append(line[:500])
                    # 通过 MemoryManager.store() 写入 — 自动分类到四网络 + NMM
                    self.memory.store(
                        content=line[:500],
                        source="conversation",
                        tags=["user_fact"],
                        bypass_gate=True,
                    )
            if extracted:
                self._log(f"💾 提取 {len(extracted)} 条用户事实")
        except Exception as e:
            self._log(f"⚠️ 记忆提取异常: {e}")

        # 深度路径：调用 TwoPhaseExtractor（更长对话且成功时）
        try:
            task = task_result.get("task", "") or task_result.get("result", "")[:200]
            if len(messages) >= 12 and task_result.get("success", False):
                from core.memory.two_phase_extract import TwoPhaseExtractor
                extractor = TwoPhaseExtractor(
                    llm_client=self.llm,
                    memory_manager=self.memory,
                )
                deep_facts = extractor.extract_from_conversation(messages, task=task)
                if deep_facts:
                    self._log(f"🧠 深度提取: {len(deep_facts)} 条精炼知识")
        except Exception:
            pass

    # ── 白板模式 ──────────────────────────────────────────────────

    def run_whiteboard(self, task: str) -> dict:
        """白板模式：分解 → 逐步执行 → 汇总。

        核心思路：将复杂任务分解为多个小步骤，
        每个 step 有独立的上下文窗口，避免累积。
        步骤之间的信息通过 Whiteboard 传递（只传摘要，不传原始对话）。

        启动流程：
        1. 构建 system_prompt（含白板工具 whiteboard_read/write）
        2. 调用 LLM 获取步骤分解 + 白板策略
        3. 逐个 step 执行，每个 step 是独立的 agent_loop 子调用
        4. 汇总所有步骤结果
        """
        start = time.time()
        errors = []
        final_result = ""

        # 1. 创建白板实例
        whiteboard = getattr(self, 'whiteboard', None) or Whiteboard()

        # 2. 构建系统提示（增加白板模式说明）
        system_prompt = self.build_system_prompt(task) + """

## 白板模式

你当前处于**白板模式**。任务将按以下方式执行：

### 步骤分解
1. 先分析任务，将其分解为 **3-8 个独立步骤**
2. 每个步骤用 `whiteboard_write` 写入白板（含类型、描述、依赖）
3. 按步骤顺序逐个执行

### 白板工具
- `whiteboard_read(partition)` — 读取白板特定分区的内容
- `whiteboard_write(partition, content)` — 写入信息到白板

### 白板分区
- `current_state`: 当前进度描述
- `completed`: 已完成的工作摘要
- `next_plan`: 下一步计划
- `intermediate`: 中间结果
- `excluded_paths`: 已排除的尝试（避免重复踩坑）
- `hypotheses`: 假设或推测
- `logs`: 执行日志

### 执行规则
- 先写分解计划到白板，然后逐个步骤执行
- 每一步完成后用 `whiteboard_write(completed, ...)` 记录
- 遇到问题时用 `whiteboard_write(excluded_paths, ...)` 记录排除的路径
- 最后用 `whiteboard_write(current_state, ...)` 更新全局状态

### 步骤模板
每步应包含：
- **type**: research / code / file / verify / test
- **description**: 具体做什么
- **context**: 前置步骤的摘要（最大 200 字）
"""

        # 3. 创建专用 session
        self.current_session_id = self.sessions.create_session(title=f"[whiteboard] {task[:40]}")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.sessions.append_message(self.current_session_id, "user", task)

        # 4. 执行白板循环（所有步骤在同一 session 中完成）
        for turn in range(self.max_turns):
            self._log(f"🤔 白板第 {turn + 1}/{self.max_turns} 轮 — LLM 思考中...")

            # 上下文压缩
            if self.compressor.needs_compression(messages):
                self._log(f"📏 白板上下文超限，压缩...")
                result = self.compressor.compress_with_local_llm(messages)
                if result.messages_removed > 0:
                    system_msgs = [m for m in messages if m.get("role") == "system"]
                    recent = [m for m in messages if m.get("role") != "system"][-8:]
                    messages = system_msgs + [{
                        "role": "system",
                        "content": f"【上下文压缩】以下是对旧对话的摘要，请基于此继续当前任务：\n{result.summary}",
                    }] + recent

            # 调用 LLM
            response = self.llm.chat(messages, tools=self.tools.get_schemas())
            if not response["success"]:
                error_msg = response.get("error", "LLM 调用失败")
                errors.append(error_msg)
                break

            # 添加 assistant 回复
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
            self.sessions.append_message(self.current_session_id, "assistant",
                                         response["content"] or "(调用了工具)")

            # 检查 finish
            finish_called = False
            final_result = ""
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    if tc["function"]["name"] == "finish":
                        args = tc["function"]["arguments"]
                        final_result = args.get("result", response.get("content", ""))
                        finish_called = True
                        break
                if finish_called:
                    # 执行剩余的 tool calls（非 finish）
                    non_finish_calls = [tc for tc in response["tool_calls"]
                                        if tc["function"]["name"] != "finish"]
                    for tc in non_finish_calls:
                        fn_name = tc["function"]["name"]
                        self._log(f"🔧 白板: 执行 {fn_name}(...)")
                        tool_result = self.tools.execute(tc)
                        safe_output = str(tool_result.get("output", "(无输出)"))
                        # ── Microcompact ──
                        should_mcompact = ToolResultStore.should_compact(safe_output)
                        if should_mcompact:  # pragma: no cover
                            meta = self.tool_result_store.store(fn_name, safe_output)  # pragma: no cover
                            context_output = meta["compact"]  # pragma: no cover
                            self._log(f"📦 Microcompact 白板: {fn_name} 结果 {len(safe_output)} chars → 磁盘")  # pragma: no cover
                        else:
                            context_output = safe_output
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": context_output,
                        })
                    break

            # 执行工具调用
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    arg_preview = json.dumps(tc.get("function", {}).get("arguments", {}),
                                             ensure_ascii=False)[:60]
                    self._log(f"🔧 白板: 执行 {fn_name}({arg_preview}...)")

                    tool_result = self.tools.execute(tc)

                    safe_output = str(tool_result.get("output", "(无输出)"))
                    # ── Microcompact ──
                    should_mcompact = ToolResultStore.should_compact(safe_output)
                    if should_mcompact:
                        meta = self.tool_result_store.store(fn_name, safe_output)
                        context_output = meta["compact"]
                        self._log(f"📦 Microcompact 白板: {fn_name} 结果 {len(safe_output)} chars → 磁盘")
                    else:
                        context_output = safe_output
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": context_output,
                    })
                    self.sessions.append_message(self.current_session_id, "tool",
                                                 safe_output[:500])

                    if not tool_result["success"]:
                        err = f"白板工具 {fn_name} 失败: {safe_output[:200]}"
                        errors.append(err)
            else:
                # LLM 直接回复（极少情况）
                final_result = response["content"]
                break

        # 5. 提取白板内容作为最终结果
        if not final_result:
            try:
                board_state = whiteboard.read("current_state")
                completed = whiteboard.read("completed")
                plans = whiteboard.read("next_plan")
                final_result = f"当前状态: {board_state}\n\n已完成:\n{completed}\n\n下一步:\n{plans}"
            except Exception:  # pragma: no cover
                final_result = response.get("content", "(无输出)")  # pragma: no cover

        # 6. 构建标准结果
        try:
            summary_text = whiteboard.read("completed")[:500] if whiteboard else final_result[:200]
        except Exception:  # pragma: no cover
            summary_text = final_result[:200]  # pragma: no cover
        task_result = {
            "success": len(errors) == 0,
            "result": final_result,
            "summary": summary_text,
            "errors": errors,
            "tool_calls": len(messages),
            "task_type": "whiteboard",
            "duration": round(time.time() - start, 3),
        }

        # 后处理（与普通 run 相同的反思/自检等）
        if self.current_session_id:
            session = self.sessions.get_session(self.current_session_id)
            if session and session.message_count > 10:
                self.sessions.archive_session(self.current_session_id)

        self.memory.remember(
            key=f"wb_task:{time.strftime('%Y%m%d_%H%M%S')}",
            content=final_result[:200],
            tags=["task", "whiteboard"],
        )

        self._deep_reflect(task_result, messages)
        self._extract_conversation_memories(task_result, messages)
        self._self_check(task_result, messages, start)
        self._learn_user_preferences(task_result, task)

        self._run_evolution_pipeline(task_result, task, messages)

        quality = self._quality_score(task_result, messages)
        task_result["quality"] = quality
        task_result["turns"] = len(messages)
        task_result["messages_count"] = len(messages)

        return task_result