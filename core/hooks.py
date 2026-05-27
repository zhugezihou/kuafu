"""
core/hooks.py — 事件钩子系统（Hook Event System）

从 Claude Code 学来的事件驱动架构。夸父在关键生命周期点触发钩子，
钩子可以连接到 4 种执行类型，实现可插拔的横切关注点。

28 个钩子事件点（按生命周期分组）：
  Agent 生命周期:
    1. on_agent_start      — Agent 启动
    2. on_agent_end        — Agent 结束
    3. on_session_create   — 会话创建
    4. on_session_end      — 会话结束

  LLM 相关:
    5. on_llm_call_before  — LLM 调用前
    6. on_llm_call_after   — LLM 调用后
    7. on_llm_error        — LLM 调用失败

  工具相关:
    8. on_tool_before       — 工具执行前（PreToolUse）
    9. on_tool_after        — 工具执行后
    10. on_tool_error       — 工具执行失败
    11. on_tool_rejected    — 工具被拒绝（Deny/审批否决）

  记忆相关:
    12. on_memory_write     — 记忆写入
    13. on_memory_read      — 记忆读取
    14. on_memory_delete    — 记忆删除
    15. on_memory_maintenance — 记忆维护（去重/合并）

  任务相关:
    16. on_task_start       — 任务开始
    17. on_task_end         — 任务结束
    18. on_task_error       — 任务失败

  进化相关:
    19. on_evolution_before — 进化前
    20. on_evolution_after  — 进化后
    21. on_skill_create     — Skill 创建
    22. on_skill_update     — Skill 更新

  系统相关:
    23. on_context_exceed   — 上下文超限
    24. on_collapse         — Context Collapse 触发
    25. on_cron_tick        — Cron 任务执行

  审批相关:
    26. on_permission_check — 权限检查（PreToolUse 之后）
    27. on_approval_result  — 审批结果（通过/拒绝）
    28. on_budget_critical   — Token 预算超限告警

4 种执行类型：
  - shell:      执行 shell 命令
  - llm:        调用 LLM 分析
  - webhook:    HTTP 回调
  - subagent:   子 Agent 验证器
"""

import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("kuafu.hooks")

ROOT_DIR = Path(__file__).resolve().parent.parent
HOOKS_CONFIG_PATH = ROOT_DIR / "memory" / "hooks_config.json"


# ═══════════════════════════════════════════════════════════════════════════════
# 事件类型定义
# ═══════════════════════════════════════════════════════════════════════════════

# 全部 27 个钩子事件名
HOOK_EVENTS = frozenset({
    "on_agent_start", "on_agent_end",
    "on_session_create", "on_session_end",
    "on_llm_call_before", "on_llm_call_after", "on_llm_error",
    "on_tool_before", "on_tool_after", "on_tool_error", "on_tool_rejected",
    "on_memory_write", "on_memory_read", "on_memory_delete", "on_memory_maintenance",
    "on_task_start", "on_task_end", "on_task_error",
    "on_evolution_before", "on_evolution_after",
    "on_skill_create", "on_skill_update",
    "on_context_exceed", "on_collapse", "on_cron_tick",
    "on_permission_check", "on_approval_result",
    "on_budget_critical",
})


# ═══════════════════════════════════════════════════════════════════════════════
# Hook 处理器的数据模型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HookHandler:
    """一条钩子处理器的配置。"""
    id: str
    event: str                        # 事件名
    type: str                         # shell / llm / webhook / subagent
    config: dict = field(default_factory=dict)  # 按类型不同
    
    # shell: {"command": "echo 'Tool called: {{tool}}'"}
    # llm: {"prompt": "分析这个工具调用: {{args}}", "model": "qwen-turbo"}
    # webhook: {"url": "https://...", "method": "POST", "headers": {...}}
    # subagent: {"goal": "验证这个操作是否安全: {{args}}", "toolsets": [...]}
    
    enabled: bool = True
    async_: bool = True               # True=异步（不阻塞主流程）, False=同步（等待结果）
    priority: int = 0                 # 执行优先级（大的先执行）
    created_at: float = field(default_factory=time.time)
    description: str = ""
    max_retries: int = 0
    timeout: int = 10                 # 秒


@dataclass
class HookResult:
    """一次钩子执行的结果。"""
    handler_id: str
    event: str
    type: str
    success: bool
    output: str
    duration: float
    error: Optional[str] = None
    blocked: bool = False             # 是否阻止主流程继续


# ═══════════════════════════════════════════════════════════════════════════════
# Hook 注册中心
# ═══════════════════════════════════════════════════════════════════════════════

class HookRegistry:
    """钩子注册中心 — 管理所有钩子处理器的注册、发现、触发。"""

    _handlers: dict[str, list[HookHandler]] = {}  # event_name -> [handlers]
    _initialized: bool = False

    @classmethod
    def init(cls):
        """从磁盘加载配置。"""
        if cls._initialized:
            return
        cls._handlers = {}
        if HOOKS_CONFIG_PATH.exists():
            try:
                data = json.loads(HOOKS_CONFIG_PATH.read_text(encoding="utf-8"))
                for event, handlers in data.items():
                    if event in HOOK_EVENTS:
                        cls._handlers[event] = [
                            HookHandler(**h) for h in handlers
                        ]
                logger.info(f"🔌 加载 {sum(len(v) for v in cls._handlers.values())} 个钩子处理器")
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"钩子配置加载失败: {e}")
        cls._initialized = True

    @classmethod
    def save(cls):
        """保存所有钩子配置到磁盘。"""
        HOOKS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for event, handlers in cls._handlers.items():
            data[event] = [{
                "id": h.id, "event": h.event, "type": h.type,
                "config": h.config, "enabled": h.enabled,
                "async_": h.async_, "priority": h.priority,
                "created_at": h.created_at, "description": h.description,
                "max_retries": h.max_retries, "timeout": h.timeout,
            } for h in handlers]
        HOOKS_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def register(cls, event: str, handler_type: str, config: dict,
                 description: str = "", priority: int = 0,
                 async_: bool = True, max_retries: int = 0,
                 timeout: int = 10) -> str:
        """注册一个钩子处理器。返回处理器 ID。"""
        if event not in HOOK_EVENTS:
            raise ValueError(f"未知事件: {event}，可用: {sorted(HOOK_EVENTS)}")

        import hashlib
        handler_id = f"hook_{int(time.time())}_{abs(hash(event + handler_type)) % 10000:04d}"

        handler = HookHandler(
            id=handler_id,
            event=event,
            type=handler_type,
            config=config,
            enabled=True,
            async_=async_,
            priority=priority,
            created_at=time.time(),
            description=description,
            max_retries=max_retries,
            timeout=timeout,
        )

        if event not in cls._handlers:
            cls._handlers[event] = []
        cls._handlers[event].append(handler)
        # 按优先级排序
        cls._handlers[event].sort(key=lambda h: h.priority, reverse=True)
        cls.save()

        logger.info(f"🔌 注册钩子 [{handler_id}] {event} → {handler_type}")
        return handler_id

    @classmethod
    def unregister(cls, handler_id: str) -> bool:
        """注销一个钩子处理器。"""
        for event, handlers in cls._handlers.items():
            before = len(handlers)
            handlers[:] = [h for h in handlers if h.id != handler_id]
            if len(handlers) < before:
                cls.save()
                logger.info(f"🔌 注销钩子 {handler_id}")
                return True
        return False

    @classmethod
    def get_handlers(cls, event: str) -> list[HookHandler]:
        """获取某事件的所有已启用的处理器。"""
        if not cls._initialized:
            cls.init()
        handlers = cls._handlers.get(event, [])
        return [h for h in handlers if h.enabled]


# ═══════════════════════════════════════════════════════════════════════════════
# 模板渲染（在 config 中替换 {{变量}}）
# ═══════════════════════════════════════════════════════════════════════════════

def _render_template(text: str, context: dict) -> str:
    """渲染模板：将 {{var}} 替换为 context 中的值。"""
    import re
    def replacer(match):
        key = match.group(1)
        val = context.get(key, match.group(0))
        if isinstance(val, dict) or isinstance(val, list):
            return json.dumps(val, ensure_ascii=False)
        return str(val)
    return re.sub(r'\{\{(\w+)\}\}', replacer, text)


def _render_config(config: dict, context: dict) -> dict:
    """递归渲染配置字典中的模板变量。"""
    result = {}
    for k, v in config.items():
        if isinstance(v, str):
            result[k] = _render_template(v, context)
        elif isinstance(v, dict):
            result[k] = _render_config(v, context)
        elif isinstance(v, list):
            result[k] = [
                _render_template(item, context) if isinstance(item, str)
                else item
                for item in v
            ]
        else:
            result[k] = v
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 4 种执行引擎
# ═══════════════════════════════════════════════════════════════════════════════

def _execute_shell(handler: HookHandler, context: dict) -> HookResult:
    """执行 shell 类型的钩子。"""
    start = time.time()
    command = _render_template(handler.config.get("command", ""), context)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=handler.timeout,
        )
        output = result.stdout.strip()
        if result.stderr:
            output += f"\nSTDERR: {result.stderr.strip()}"
        return HookResult(
            handler_id=handler.id,
            event=handler.event,
            type="shell",
            success=result.returncode == 0,
            output=output[:1000],
            duration=time.time() - start,
            error=None if result.returncode == 0 else result.stderr.strip()[:500],
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="shell",
            success=False, output="", duration=time.time() - start,
            error=f"超时 ({handler.timeout}s)",
        )
    except Exception as e:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="shell",
            success=False, output="", duration=time.time() - start,
            error=str(e),
        )


def _execute_llm(handler: HookHandler, context: dict) -> HookResult:
    """执行 LLM 类型的钩子。"""
    start = time.time()
    prompt = _render_template(handler.config.get("prompt", ""), context)
    model = handler.config.get("model", "qwen-turbo")

    try:
        from core.llm import LLMClient
        llm = LLMClient()
        response = llm.chat([{
            "role": "system",
            "content": "你是一个钩子分析器。分析给定的上下文并输出结果。"
        }, {
            "role": "user",
            "content": prompt,
        }], tools=None)
        if response["success"]:
            return HookResult(
                handler_id=handler.id, event=handler.event, type="llm",
                success=True, output=response["content"][:1000],
                duration=time.time() - start,
            )
        return HookResult(
            handler_id=handler.id, event=handler.event, type="llm",
            success=False, output="", duration=time.time() - start,
            error=response.get("error", "LLM 调用失败"),
        )
    except Exception as e:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="llm",
            success=False, output="", duration=time.time() - start,
            error=str(e),
        )


def _execute_webhook(handler: HookHandler, context: dict) -> HookResult:
    """执行 webhook 类型的钩子。"""
    start = time.time()
    import urllib.request
    import urllib.error

    config = _render_config(handler.config, context)
    url = config.get("url", "")
    method = config.get("method", "POST").upper()
    headers = config.get("headers", {})
    body = config.get("body", "")

    if not url:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="webhook",
            success=False, output="", duration=time.time() - start,
            error="缺少 url",
        )

    try:
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method,
                                       headers=headers)
        with urllib.request.urlopen(req, timeout=handler.timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")[:1000]
            return HookResult(
                handler_id=handler.id, event=handler.event, type="webhook",
                success=True, output=content,
                duration=time.time() - start,
            )
    except urllib.error.HTTPError as e:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="webhook",
            success=False, output="", duration=time.time() - start,
            error=f"HTTP {e.code}: {e.reason}",
        )
    except Exception as e:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="webhook",
            success=False, output="", duration=time.time() - start,
            error=str(e),
        )


def _execute_subagent(handler: HookHandler, context: dict) -> HookResult:
    """执行 subagent 类型的钩子（验证器）。"""
    start = time.time()
    goal = _render_template(handler.config.get("goal", ""), context)
    toolsets = handler.config.get("toolsets", ["search", "terminal"])

    try:
        from core.subagent import delegate_to_subagent
        result = delegate_to_subagent(
            goal=goal,
            context=json.dumps(context, ensure_ascii=False),
            toolsets=toolsets,
            timeout=handler.timeout,
        )
        success = result.get("success", False)
        output = result.get("summary", str(result)[:1000])
        blocked = handler.config.get("block_on_failure", False) and not success
        return HookResult(
            handler_id=handler.id, event=handler.event, type="subagent",
            success=success, output=output, duration=time.time() - start,
            blocked=blocked,
        )
    except Exception as e:
        return HookResult(
            handler_id=handler.id, event=handler.event, type="subagent",
            success=False, output="", duration=time.time() - start,
            error=str(e),
        )


# 执行器分发映射
_EXECUTORS = {
    "shell": _execute_shell,
    "llm": _execute_llm,
    "webhook": _execute_webhook,
    "subagent": _execute_subagent,
}


# ═══════════════════════════════════════════════════════════════════════════════
# 事件触发入口
# ═══════════════════════════════════════════════════════════════════════════════

def trigger(event: str, context: Optional[dict] = None,
            synchronous: bool = False) -> list[HookResult]:
    """触发一个钩子事件。

    Args:
        event: 事件名
        context: 上下文数据（会填充模板变量）
        synchronous: True=同步执行（等待所有处理器完成且可阻止主流程）

    Returns:
        所有处理器的执行结果列表
    """
    if event not in HOOK_EVENTS:
        logger.warning(f"未知钩子事件: {event}")
        return []

    handlers = HookRegistry.get_handlers(event)
    if not handlers:
        return []

    context = context or {}
    results = []
    blocked = False

    for handler in handlers:
        if blocked and synchronous:
            # 前一个同步处理器阻止了流程 → 跳过剩余
            result = HookResult(
                handler_id=handler.id, event=event, type=handler.type,
                success=False, output="", duration=0,
                error="上游处理器阻止了流程",
                blocked=False,
            )
            results.append(result)
            continue

        executor = _EXECUTORS.get(handler.type)
        if not executor:
            results.append(HookResult(
                handler_id=handler.id, event=event, type=handler.type,
                success=False, output="", duration=0,
                error=f"未知执行类型: {handler.type}",
            ))
            continue

        # 执行（带重试）
        last_error = None
        for attempt in range(handler.max_retries + 1):
            try:
                result = executor(handler, context)
                last_error = result.error

                # 检查是否阻止主流程
                if synchronous and handler.config.get("block_on_failure", False):
                    if not result.success:
                        result.blocked = True
                        blocked = True

                results.append(result)
                break
            except Exception as e:
                last_error = str(e)
                if attempt < handler.max_retries:
                    time.sleep(1)
                    continue
                results.append(HookResult(
                    handler_id=handler.id, event=event, type=handler.type,
                    success=False, output="", duration=time.time() - time.time(),
                    error=last_error,
                ))

        # 异步处理器的日志
        if not handler.async_ or synchronous:
            log_msg = (
                f"🔔 钩子 {event} → {handler.type}"
                f" {'✅' if results[-1].success else '❌'}"
            )
            if results[-1].blocked:
                log_msg += " ⛔ 阻止主流程"
            logger.info(log_msg)

    return results


# ── 便捷触发函数（供集成点调用） ──────────────────────────────────────────

def trigger_async(event: str, context: Optional[dict] = None):
    """异步触发钩子事件（不等待、不阻塞）。"""
    import threading
    t = threading.Thread(target=trigger, args=(event, context),
                         daemon=True)
    t.start()


def trigger_sync(event: str, context: Optional[dict] = None) -> list[HookResult]:
    """同步触发钩子事件（等待所有处理器完成，可阻止主流程）。"""
    return trigger(event, context, synchronous=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 快速注册函数
# ═══════════════════════════════════════════════════════════════════════════════

def on_tool_before_shell(command: str, description: str = "",
                         priority: int = 0) -> str:
    """在工具执行前运行 shell 命令。"""
    return HookRegistry.register(
        event="on_tool_before", handler_type="shell",
        config={"command": command},
        description=description or f"PreToolUse shell: {command[:50]}",
        priority=priority,
    )


def on_tool_before_llm(prompt: str, model: str = "qwen-turbo",
                       description: str = "", priority: int = 0,
                       block_on_failure: bool = False) -> str:
    """在工具执行前用 LLM 分析。"""
    return HookRegistry.register(
        event="on_tool_before", handler_type="llm",
        config={"prompt": prompt, "model": model,
                "block_on_failure": block_on_failure},
        description=description or f"PreToolUse LLM: {prompt[:50]}",
        priority=priority,
        async_=True,  # LLM 分析默认异步不阻塞
    )


def on_approval_notify_webhook(url: str, method: str = "POST",
                                description: str = "") -> str:
    """审批结果通知 webhook。"""
    return HookRegistry.register(
        event="on_approval_result", handler_type="webhook",
        config={"url": url, "method": method},
        description=description or f"审批通知: {url}",
    )


# ── 初始化 ────────────────────────────────────────────────────────────────────

def init_hooks():
    """初始化钩子系统（启动时调用一次）。"""
    HookRegistry.init()
    logger.info(f"🔌 钩子系统就绪 — 支持 {len(HOOK_EVENTS)} 个事件点 × 4 种执行类型")
