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
from core.memory import MemoryManager as MemoryAPI  # 新三层记忆系统，兼容旧接口
from core.evolution import EvolutionEngine
from core.observer import Observer
from core.tool_registry import ToolRegistry
from core.session_store import SessionStore
from core.context_compress import ContextCompressor, LocalSummarizer, ToolResultStore, ContextCollapse, CollapseResult
from core.budget_allocator import BudgetAllocator, BudgetSnapshot, BudgetPolicy
from core.prompt_template import PromptManager, Section
from core.safety import SafetyLayer
from core.skill_resolver import discover_skills, match_skills, inject_skills_to_prompt, increment_usage, record_usage
from core.whiteboard import Whiteboard, Decomposer, Step, WhiteboardExecutor
from core.mcp_bridge import MCPBridge
from core.approval import pretooluse_check, DenyRules, AutoMode, ApprovalManager
from core.hooks import trigger, trigger_async, trigger_sync, init_hooks, HOOK_EVENTS
# 策略/规则加载：优先从 autonomous.strategy_loader 加载，降级到默认值
try:
    from autonomous.strategy_loader import get_rules as _get_rules
    from autonomous.strategy_loader import get_quality as _get_quality
    _HAS_STRATEGY = True
except ImportError:
    _HAS_STRATEGY = False

    def _get_rules():
        return [
            "1. 直接完成用户请求，不要说'我可以帮你'之类的废话",
            "2. 一次只做一个工具调用，等待结果再继续",
            "3. 完成任务后调用 finish() 工具",
        ]

    def _get_quality(task_type: str = "generic"):
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
        self.evolution = evolution or EvolutionEngine(memory=memory, llm=self.llm)
        self.tools = tool_registry or ToolRegistry()
        self.sessions = session_store or SessionStore()
        self.max_turns = max_turns
        self.on_step = on_step

        # 上下文压缩器 — 阈值根据后端动态设置
        # 本地 Qwen3.5-9B: -c 32768，threshold=28000（留充足冗余给摘要调用和实时输出）
        # 云端 DeepSeek: 64K+ context，threshold=12000
        local_backend = getattr(self.llm, 'backend', 'cloud') == 'local'
        ctx_threshold = 28000 if local_backend else 12000
        self.compressor = ContextCompressor(
            max_context_tokens=ctx_threshold,
            keep_recent_rounds=5,
            summarizer=LocalSummarizer(),
        )

        # Budget Allocator: Token 预算分配器
        local_backend = getattr(self.llm, 'backend', 'cloud') == 'local'
        ctx_threshold = 28000 if local_backend else 12000
        self.budget_allocator = BudgetAllocator(
            policy=BudgetPolicy(total_budget=ctx_threshold),
            on_critical=self._on_budget_critical,
            on_warning=self._on_budget_warning,
        )
        self._budget_scan_count = 0  # 预算扫描计数器

        # Microcompact: 大型工具结果 → 磁盘存储
        self.tool_result_store = ToolResultStore()

        # ContextCollapse: 非破坏性上下文投影
        self.collapser = ContextCollapse(
            summarizer=LocalSummarizer(),
            keep_recent_rounds=5,
        )

        # 当前会话 ID（由 run() 创建）
        self.current_session_id: Optional[str] = None

        # 注册 delegate_task 工具（子 Agent 系统）
        self._register_delegate_tool()

        # 加载 MCP Server 集成
        self.mcp_bridge: Optional[MCPBridge] = None
        self._init_mcp()

        # 记忆维护计数器（每 10 轮触发一次去重/过期清理/合并）
        self._mem_maintenance_counter = 0

        # EvolutionEngine：自我进化（任务结束时判断是否生成技能）
        from core.evolution_engine import EvolutionEngine as EvolEngineV2
        self.evolution_engine = EvolEngineV2(root_dir=ROOT_DIR)

        # Observer：运行时工具调用跟踪
        self._observer = Observer()
        self.evolution.register_observer(self._observer)

        # 注册 skill_rollback 工具
        self._register_skill_rollback()

        # ── Hook 事件系统 ──
        self.hooks_enabled = True
        try:
            init_hooks()
            self._log("🔌 Hook 事件系统就绪")
        except Exception as e:
            self._log(f"⚠️ Hook 系统初始化失败: {e}")

        # ── Permission System（PreToolUse 权限检查） ──
        self.permission_enabled = True  # 设为 False 可完全绕过权限检查
        self._pretooluse_cache: dict = {}

    def _register_delegate_tool(self):
        """注册 delegate_task 工具（子 Agent 系统）。"""
        try:
            from core.subagent import get_delegate_schema, handle_delegate
            schema = get_delegate_schema()
            self.tools.register("delegate_task", schema, handle_delegate)
            self._log("🧩 子 Agent 系统就绪: delegate_task 工具已注册")
        except Exception as e:
            self._log(f"⚠️ 子 Agent 注册失败: {e}")

    def _register_skill_rollback(self):
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
            self._log("↩️ 技能回滚工具已注册: skill_rollback")
        except Exception as e:
            self._log(f"⚠️ skill_rollback 注册失败: {e}")

    def _init_mcp(self):
        """初始化 MCP 桥接，加载配置并注册工具。"""
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

    def build_system_prompt(self, task: str = "") -> str:
        """组装结构化 system prompt（PromptTemplate 实现）。

        使用 PromptManager 将 prompt 拆分为独立 section 组合。
        每个 section 有 ID、标题、条件、budget_tag，支持条件注入。
        """
        pm = PromptManager(task)

        # 1. 身份声明
        pm.add_section(
            section_id="identity",
            title="",
            content=load_identity_statement(),
            order=0,
            budget_tag="system",
        )

        # 2. 核心规则
        rules = get_rules()
        rules_content = "\n".join(f"- {rule}" for rule in rules)
        pm.add_section(
            section_id="rules",
            title="核心规则",
            content=rules_content,
            order=1,
            budget_tag="system",
        )

        # 3. 工具说明
        # L0 核心工具（始终全量 schema 对 LLM 可见）
        core_tools = []
        for tool_def in self.tools.get_schemas()[:10]:
            fn = tool_def["function"]
            if fn["name"] == "tool_search":
                continue
            desc = fn["description"].split("。")[0]
            core_tools.append(f"- {fn['name']}: {desc}")

        # L1 紧凑工具（仅提示词中描述，无 schema 参数，首次调用后自动提升）
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

        pm.add_section(
            section_id="tools",
            title="可用工具",
            content=tools_content,
            order=2,
            budget_tag="system",
        )

        # 4. 输出格式 + 执行规则
        format_content = "- 回复直接对用户说话，不是日志或报告\n"
        format_content += "- 如果用户问问题，直接回答，不要说'已回答'这类\n\n"
        format_content += "## 执行规则\n"
        format_content += "- 一次只做一个工具调用\n"
        format_content += "- 不要同时调用多个工具\n"
        format_content += "- 每步 tool 调用前先输出简短思考\n"
        format_content += "- 工具结果返回后，判断是否足够再决定下一步"

        pm.add_section(
            section_id="format",
            title="输出格式",
            content=format_content,
            order=3,
            budget_tag="system",
        )

        # 5. 进化状态（条件注入）
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

        # 6. 配置
        if self.llm:
            config_content = f"- 后端: {self.llm.backend} | 模型: {self.llm.model}"
            pm.add_section(
                section_id="config",
                title="配置",
                content=config_content,
                order=5,
                budget_tag="system",
            )

        # 7. 任务相关：质量标准 + 技能（条件注入）
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
                    for skill in simple_skills[:2]:
                        increment_usage(skill['name'])
                        skill_parts.append(f"### {skill['name']}")
                        if skill.get("description"):
                            skill_parts.append(str(skill['description']))
                        if skill.get("steps"):
                            skill_parts.append("**步骤：**")
                            for i, step in enumerate(skill["steps"], 1):
                                skill_parts.append(f"  {i}. {step}")
                        if skill.get("pitfalls"):
                            skill_parts.append("**注意事项：**")
                            for p in skill["pitfalls"]:
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
                    for yf in skills_dir.glob("*.yaml"):
                        with open(yf, "r", encoding="utf-8") as f:
                            sd = yaml.safe_load(f)
                        if sd and sd.get("name") == err_skill:
                            err_parts = [f"### {err_skill}"]
                            if sd.get("description"):
                                err_parts.append(str(sd['description']))
                            if sd.get("steps"):
                                err_parts.append("**步骤：**")
                                for i, step in enumerate(sd["steps"], 1):
                                    err_parts.append(f"  {i}. {step}")
                            if sd.get("pitfalls"):
                                err_parts.append("**注意事项：**")
                                for p in sd["pitfalls"]:
                                    err_parts.append(f"  ⚠️ {p}")
                            err_parts.append("该技能因检测到已知错误模式而自动加载。")

                            pm.add_section(
                                section_id="error_skill",
                                title="⚡ 错误关联技能",
                                content="\n".join(err_parts),
                                order=8,
                                budget_tag="skills",
                            )
                            break
            except Exception:
                pass

        # 8. 记忆上下文（三层记忆，预算感知注入）
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

        # 9. 自我认知
        try:
            all_skills = discover_skills()
            skills_count = len(all_skills) if all_skills else 0
            prefs_path = ROOT_DIR / "memory" / "user_prefs.json"
            pref_count = 0
            if prefs_path.exists():
                try:
                    pref_count = len(json.loads(prefs_path.read_text(encoding="utf-8")))
                except Exception:
                    pass
            pm.add_section(
                section_id="self_awareness",
                title="自我认知",
                content=f"📚 {skills_count} 技能 | 👤 {pref_count} 用户偏好 | ⚡ {total} 次进化",
                order=99,  # 最后
                budget_tag="system",
            )
        except Exception:
            pass

        # 组装
        prompt = pm.assemble()

        return prompt

    def _log(self, text: str):
        """记录步骤（或通过回调通知）。"""
        if self.on_step:
            self.on_step(text)

    def _on_budget_warning(self, snapshot, critical_categories):
        """预算预警回调：当某类别达到 warning 阈值时触发。"""
        self._log(f"⚠️ Budget Warning: {', '.join(critical_categories)} "
                  f"({snapshot.total_used}/{snapshot.total_budget} tokens)")

    def _on_budget_critical(self, snapshot, critical_categories):
        """预算危险回调：当某类别达到 critical/over 阈值时触发。"""
        self._log(f"🚨 Budget Critical: {', '.join(critical_categories)} "
                  f"({snapshot.total_used}/{snapshot.total_budget} tokens)")
        # 自动触发 Hook 事件
        try:
            from core.hooks import trigger_async
            trigger_async("on_budget_critical", {
                "snapshot": snapshot.to_dict(),
                "critical": critical_categories,
            })
        except Exception:
            pass

    def _try_delegate_complex_skills(self, task: str) -> Optional[dict]:
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

        # ── 复杂 skill 预处理：检测并委派子 Agent ──
        complex_delegation_result = self._try_delegate_complex_skills(task)
        if complex_delegation_result:
            self._log(f"🧩 复杂 skill 委派完成：{complex_delegation_result['summary'][:100]}")
            # 将委派结果注入 user message，让 LLM 知晓并继续执行
            delegation_note = (
                f"[子任务执行结果]\n"
                f"以下子任务已由独立的子 Agent 自动完成：\n"
                f"{complex_delegation_result['summary']}\n\n"
                f"请基于此结果继续执行后续步骤（如有）并完成最终输出。"
            )
            messages.append({"role": "user", "content": delegation_note})
            self.sessions.append_message(self.current_session_id, "user", delegation_note)

        messages.append({"role": "user", "content": task})
        self.sessions.append_message(self.current_session_id, "user", task)

        # 执行循环
        for turn in range(self.max_turns):
            turn_count = turn + 1

            self._log(f"🤔 第 {turn_count}/{self.max_turns} 轮 — LLM 思考中...")

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

                    # ── PreToolUse: 权限检查（Deny 规则 → 自动模式 → 人工审批） ──
                    if self.permission_enabled and fn_name not in ("finish", "delegate_task", "skill_rollback"):
                        args_dict = tc.get("function", {}).get("arguments", {})
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

                        # Permission System 检查
                        perm = pretooluse_check(fn_name, args_dict,
                                                 {"task": task[:200], "turn": turn_count})

                        # 触发 on_permission_check 钩子
                        if self.hooks_enabled:
                            trigger_async("on_permission_check", {
                                "tool": fn_name, "args": args_dict,
                                "result": perm,
                            })

                        if not perm["allowed"]:
                            if perm["approach"] == "deny_rule":
                                msg = f"🛡️ {fn_name} 被 Deny 规则拒绝: {perm['reason']}"
                            elif perm["approach"] == "auto_reject":
                                msg = f"⛔ {fn_name} 被自动拒绝: {perm['reason']}"
                            else:
                                msg = f"🟡 {fn_name} 待审批 (ID: {perm.get('req_id', '?')})"

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
                                    "tool": fn_name, "reason": perm["approach"],
                                    "perm_result": perm,
                                })
                            continue

                    tool_result = self.tools.execute(tc)

                    # 安全脱敏：对终端输出中的 API key、token 等脱敏
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

                    if should_microcompact:
                        meta = self.tool_result_store.store(fn_name, safe_output)
                        compact_text = meta["compact"]
                        # 写磁盘后，放更紧凑的占位进上下文
                        safe_output_for_context = compact_text
                        self._log(f"📦 Microcompact: {fn_name} 结果 {len(raw_output)} chars → 磁盘 ({meta['file_path']})")
                    else:
                        safe_output_for_context = safe_output

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
                        and fn_name not in ("web_search", "web_extract", "web_crawl", "read_file")  # 搜索/提取/读文件默认保留
                    )
                    if needs_filter:
                        filter_prompt = (
                            "你是一个结果过滤器。用户正在做一个任务，下面是一个工具调用的返回结果。\n"
                            "判断这个结果对当前任务是否有实质贡献（有帮助的信息/数据/代码片段），\n"
                            "还是只是过程性/噪音内容。\n\n"
                            f"当前任务：{task[:100]}\n"
                            f"工具名称：{fn_name}\n"
                            f"结果预览（前500字）：\n{safe_output[:500]}\n\n"
                            "只回复 'keep' 或 'discard'，不要其他内容。"
                        )
                        try:
                            filter_resp = self.llm.chat([{
                                "role": "system",
                                "content": "你是一个简洁的结果过滤器。只回复 keep 或 discard。"
                            }, {
                                "role": "user",
                                "content": filter_prompt,
                            }], tools=None)
                            if filter_resp["success"]:
                                decision = filter_resp["content"].strip().lower()
                                if decision.startswith("discard"):
                                    should_keep = False
                                    self._log(f"🗑️ 过滤掉 {fn_name} 结果 ({len(safe_output)} chars) — 判定无贡献")
                        except Exception:
                            pass  # 过滤失败则保留结果（保守策略）

                    if should_keep:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": safe_output_for_context,
                        })
                    else:
                        # 丢弃但留一个简短的占位
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": f"[工具 {fn_name} 的结果被过滤（判定无贡献），原长 {len(safe_output)} 字符]",
                        })

                    self.sessions.append_message(
                        self.current_session_id, "tool",
                        safe_output[:500],
                    )

                    if not tool_result["success"]:
                        err = f"工具 {fn_name} 失败: {safe_output[:200]}"
                        errors.append(err)
                        # 触发 on_tool_error 钩子（异步）
                        if self.hooks_enabled:
                            trigger_async("on_tool_error", {
                                "tool": fn_name,
                                "args": tc.get("function", {}).get("arguments", {}),
                                "error": safe_output[:500],
                                "task": task[:100],
                            })
                    else:
                        # 触发 on_tool_after 钩子（异步）
                        if self.hooks_enabled:
                            trigger_async("on_tool_after", {
                                "tool": fn_name,
                                "args": tc.get("function", {}).get("arguments", {}),
                                "output_length": len(safe_output),
                                "task": task[:100],
                                "turn": turn_count,
                            })
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
            "task_type": detect_task_type(task),
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

        # ── 三阶段进化管道（Observer → EvolutionState → Judge → SkillWriter）──
        self._run_evolution_pipeline(task_result, task, messages)

        # 质量评分
        quality = self._quality_score(task_result, messages)
        task_result["quality"] = quality

        # 任务报告：复杂任务（多轮交互）生成结构化报告
        if turn_count >= 3:
            task_result["report"] = self._generate_report(task, task_result, messages)

        task_result["turns"] = turn_count
        task_result["messages_count"] = len(messages)

        # 定时记忆维护（每 10 轮触发一次）
        self._mem_maintenance_counter += 1
        if self._mem_maintenance_counter >= 10:
            self._mem_maintenance_counter = 0
            try:
                result = self.memory.maintenance()
                if result["expired"] > 0 or result["merged"] > 0:
                    self._log(f"记忆维护: 清理 {result['expired']} 过期 + 合并 {result['merged']} 条")
            except Exception as e:
                self._log(f"记忆维护异常: {e}")

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
            except Exception:
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
            except Exception:
                pass

            # ── 新增：evolution_mode 通知 ───────────────────────
            # 根据 pipeline 中 Judge 的 evolution_mode 做不同反应
            try:
                if evo_result and evo_result.get("evolution_mode"):
                    mode = evo_result["evolution_mode"]
                    skill_name = evo_result.get("skill_name", "")
                    if mode == "CAPTURED":
                        self._log(f"🧬 新技能捕获: {skill_name}（首次发现）")
                    elif mode == "FIX":
                        self._log(f"🔧 技能修复: {skill_name}（覆盖旧版本）")
                    elif mode == "DERIVED":
                        self._log(f"🌿 技能衍生: {skill_name}（派生新版本）")
            except Exception:
                pass

            # 健康检查
            health = self.evolution.evolution_state.health_check()
            if health:
                self._log(f"⚠️ 进化健康: {health}")

        except Exception as e:
            self._log(f"⚠️ 进化管道异常: {e}")

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

        # 1. 创建白板实例
        whiteboard = Whiteboard()

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
            except Exception:
                final_result = response.get("content", "(无输出)")

        # 6. 构建标准结果
        task_result = {
            "success": len(errors) == 0,
            "result": final_result,
            "summary": whiteboard.read("completed")[:500] if whiteboard else final_result[:200],
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
        self._self_check(task_result, messages, start)
        self._learn_user_preferences(task_result, task)

        self._run_evolution_pipeline(task_result, task, messages)

        quality = self._quality_score(task_result, messages)
        task_result["quality"] = quality
        task_result["turns"] = len(messages)
        task_result["messages_count"] = len(messages)

        return task_result