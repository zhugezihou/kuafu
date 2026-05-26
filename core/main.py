"""
夸父 (Kuafu) — 自我进化的 AI Agent 入口。

用法:
    # Python 调用
    from core.main import KuafuAgent
    
    agent = KuafuAgent()
    result = agent.run("帮我写一个 Python 脚本读取 CSV")
    print(result["result"])

    # CLI 调用
    python -m kuafu "你的任务"
    python -m kuafu --status
"""

import os
import sys
import json
import time
import re
from pathlib import Path
from typing import Any, Optional

# 核心模块
from core.identity import load_identity_statement, detect_identity_impersonation
from core.sandbox import is_path_allowed_for_write, validate_command
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine, EvolutionEvent
from core.llm import LLMClient
from core.model_manager import ModelManager, ALIASES, MODEL_TEMPLATES
from core.agent_loop import AgentLoop, detect_task_type

# 飞书通知（仅发送，不启用轮询）
try:
    from core.feishu_bot import FeishuBot, FEISHU_ENABLED as _FEISHU_ENABLED
except ImportError:
    FeishuBot = None
    _FEISHU_ENABLED = False

# P1: 后台复盘线程（可选加载）
try:
    from autonomous.reviewer import ReviewerThread
    _HAS_REVIEWER = True
except ImportError:
    _HAS_REVIEWER = False

# P2: 自主决策模块（可选加载）
try:
    from autonomous.prioritizer import IdlePrioritizer, EvolutionScheduler
    _HAS_PRIORITIZER = True
except ImportError:
    _HAS_PRIORITIZER = False

ROOT_DIR = Path(__file__).resolve().parent.parent


class KuafuAgent:
    """夸父 Agent。

    组装：
    - 身份声明 → 系统 prompt
    - 沙盒 → 安全执行
    - 记忆 → 长期记忆
    - 进化 → 自我改进
    - LLM → AI 执行引擎

    支持两种模式：
    - run(): 单次任务执行（隔离上下文）
    - converse(): 多轮对话（延续上下文，支持追问/修正）
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.name = "夸父"
        self.version = "0.4.0"
        self.memory = MemoryAPI()
        self.llm = llm_client or LLMClient()
        self.evolution = EvolutionEngine(memory=self.memory, llm=self.llm)
        self.model_manager = ModelManager()
        # 同步 ModelManager 与 LLMClient：以 LLMClient 为准
        self._sync_model_manager_with_llm()
        self._task_count = 0
        self._conversation = None
        self._conversation_messages = []

        # ── 飞书通知（仅发送，不启动轮询）──
        self._feishu_bot = None
        if _FEISHU_ENABLED and FeishuBot is not None:
            try:
                self._feishu_bot = FeishuBot()  # 从环境变量自动读取
            except Exception:
                self._feishu_bot = None

        self._setup()

        # P0: 启动后台复盘线程（可选，daemon=True）
        if _HAS_REVIEWER:
            self._reviewer_thread = ReviewerThread(
                llm_chat_fn=self.llm.chat,
                memory_remember_fn=lambda key, content, tags: self.memory.remember(
                    key=key, content=content, tags=tags
                ),
            )
            self._reviewer_thread.start()
        else:
            self._reviewer_thread = None

        # P2: 启动自主决策线程（daemon=True，周期性检查空闲状态）
        self._prioritizer_thread: Optional[threading.Thread] = None
        self._init_prioritizer()

        # P3: 启动主动网络学习引擎（daemon=True，每隔数小时自动学习）
        self._web_learner: Optional[Any] = None
        self._init_web_learner()

        # P4: 启动自检优化程序（可选，daemon=True，每 4 小时自动体检）
        self._health_checker_thread: Optional[Any] = None
        try:
            from autonomous.self_health import HealthCheckerThread
            self._health_checker = HealthCheckerThread(
                memory_remember_fn=lambda key, content, tags: self.memory.remember(
                    key=key, content=content, tags=tags
                ),
            )
            self._health_checker_thread = self._health_checker
            self._health_checker_thread.start()
        except ImportError:
            self._health_checker = None

        # P5: WebHook 服务器（可选）
        self._webhook_server = None

    def _setup(self):
        """首次启动设置。"""
        for d in ["strategy", "skills", "memory", "logs", "tests"]:
            (ROOT_DIR / d).mkdir(parents=True, exist_ok=True)
        self.memory.remember(
            key="system:startup",
            content=f"夸父 v{self.version} 启动，LLM: {self.llm.model}",
            tags=["system", "startup"],
        )

    # ---- P2: 自主决策初始化 ----

    def _init_prioritizer(self):
        """初始化自主决策系统（可选）。"""
        if not _HAS_PRIORITIZER:
            return
        try:
            import threading

            # 创建 IdlePrioritizer，注入记忆和进化状态查询
            self._idle_prioritizer = IdlePrioritizer(
                memory_recall_fn=lambda query, limit=5: self.memory.recall(query, limit=limit),
                evolution_stats_fn=self.evolution.get_evolution_stats,
            )
            # 创建进化调度器（包装 IdlePrioritizer 的进化时机决策）
            self._evolution_scheduler = EvolutionScheduler(self._idle_prioritizer)

            # 后台线程：每 5 分钟检查一次空闲决策
            def _prioritizer_loop():
                import time as _time
                while True:
                    _time.sleep(300)  # 5 分钟间隔
                    try:
                        decision = self._idle_prioritizer.decide()
                        if decision:
                            # 不再写入记忆（避免污染系统 prompt）
                            # self.memory.remember(
                            #     key=f"priority:{int(_time.time())}",
                            #     content=f"【空闲决策】{decision.title} ({decision.priority_score:.0f}分)",
                            #     tags=["decision", "prioritizer", decision.category],
                            # )
                            pass
                    except Exception:
                        pass

            self._prioritizer_thread = threading.Thread(
                target=_prioritizer_loop,
                daemon=True,
                name="kuafu-prioritizer",
            )
            self._prioritizer_thread.start()
        except Exception as e:
            self._prioritizer_thread = None
            import logging
            logging.warning(f"P2 Prioritizer 启动失败: {e}")

    # ---- P3: 主动网络学习引擎 ----

    def _init_web_learner(self):
        """初始化主动网络学习引擎（可选）。"""
        try:
            from autonomous.web_learner import WebLearner

            self._web_learner = WebLearner(
                llm_chat_fn=self.llm.chat,
                memory_remember_fn=lambda key, content, tags: self.memory.remember(
                    key=key, content=content, tags=tags or []
                ),
                memory_recall_fn=lambda query, limit=5: self.memory.recall(query, limit=limit),
                evolution_emit_fn=lambda level, action, target, payload: (
                    self.evolution.evaluate_and_evolve(
                        task_result={"success": True, "task_type": target, "result": action},
                        task=action,
                        messages=[],
                    )
                    if hasattr(self.evolution, 'evaluate_and_evolve')
                    else None
                ),
                learn_interval=21600,   # 6 小时
                max_per_cycle=8,
            )
            self._web_learner.start(daemon=True)
        except Exception as e:
            self._web_learner = None
            import logging
            logging.warning(f"P3 WebLearner 启动失败: {e}")

    @property
    def identity(self) -> str:
        """获取身份声明。"""
        return load_identity_statement()

    @property
    def sandbox(self) -> dict:
        """获取沙盒安全信息。"""
        from core.sandbox import PROTECTED_DIRS, ALLOWED_WRITE_DIRS
        return {
            "protected_dirs": [str(d) for d in PROTECTED_DIRS],
            "allowed_write_dirs": [str(d) for d in ALLOWED_WRITE_DIRS],
        }

    # ---- 核心循环 ----

    def build_system_prompt(self) -> str:
        """组装完整的系统 prompt。

        结构：
        1. 身份声明（IDENTITY.md）（不可变）
        2. 核心规则
        3. 可用工具
        4. 记忆上下文
        5. 进化状态
        """
        parts = []

        # 1. 身份声明 — 最前面
        parts.append(load_identity_statement())
        parts.append("")

        # 2. 核心规则
        parts.append("## 核心规则")
        parts.append("- 每次任务完成后，必须反思：我学到了什么？")
        parts.append("- 如果用户纠正了你，记住这个教训")
        parts.append("- 发现可以改进的地方，记录下来")
        parts.append("- 绝对不可以修改 core/ 目录下的任何文件")
        parts.append("")

        # 3. 可用能力
        parts.append("## 可用能力")
        parts.append("- 执行终端命令（sandbox 检查路径安全）")
        parts.append("- 读写文件（core/ 目录禁止写入）")
        parts.append("- 记忆系统（remember/recall/reflect）")
        parts.append("- 搜索互联网（需启用 web 工具）")
        parts.append("")

        # 4. 进化状态
        stats = self.evolution.get_evolution_stats()
        parts.append("## 进化状态")
        parts.append(f"- 总进化次数: {stats['total_evolutions']}")
        parts.append(f"- 各级进化数: {stats['by_level']}")
        task_stats = self.evolution.get_task_stats()
        parts.append(f"- 已完成任务: {task_stats['total']}")
        parts.append(f"- 成功率: {task_stats['success_rate']}%")
        parts.append("")

        # 5. 相关信息
        user_profile_path = ROOT_DIR / "memory" / "user_profile.json"
        if user_profile_path.exists():
            parts.append("## 关于用户")
            try:
                user_profile = json.loads(user_profile_path.read_text(encoding="utf-8"))
                pref = user_profile.get("preferences", {})
                if pref:
                    parts.append(f"用户偏好: {json.dumps(pref, ensure_ascii=False)}")
            except (json.JSONDecodeError, Exception):
                pass

        # 6. 当前模型信息
        parts.append("## 当前模型配置")
        parts.append(f"- 后端: {self.llm.backend}")
        parts.append(f"- 模型: {self.llm.model}")
        parts.append(f"- API URL: {self.llm.base_url}")
        parts.append(f"- max_tokens: {self.llm.max_tokens}")
        parts.append(f"- temperature: {self.llm.temperature}")
        parts.append("")

        # 7. 相关记忆（仅当有具体查询时召回）
        # 注：recall("") 在 file 模式下返回最新 N 条，但 Python 中 "" in text 恒 True
        # 导致返回的全是 P2 空闲决策垃圾。空查询时跳过，需要时由 LLM 决定具体查什么。
        # recent = self.memory.recall("", limit=10)

        return "\n".join(parts)

    def run(self, task: str, mode: str = "standard",
            resume_from: Optional[str] = None,
            resume_mode: str = "brief",
            resume_max_tokens: int = 4000) -> dict:
        """执行一次任务。
        
        支持两种模式：
        - standard: 标准 AgentLoop（常规对话 + 工具调用）
        - whiteboard: 白板模式（分解 → 逐步执行 → 汇总）

        如果用户输入的是问候/寒暄，直接回复，不进入 agent 循环。
        
        使用 AgentLoop 驱动完整的 LLM + 工具执行循环。

        Args:
            task: 用户任务
            mode: "standard" 或 "whiteboard"
            resume_from: 可选，从指定会话 ID 恢复
            resume_mode: "brief" / "fork" / "full"
            resume_max_tokens: 恢复数据最大 token 数

        Returns:
            {
                "success": bool,
                "result": str,
                "turns": int,
                "evolution": EvolutionEvent or None,
                "errors": list[str],
                "duration": float,
            }
        """
        start = time.time()
        self._task_count += 1

        # 先检测任务类型
        init_task_type = detect_task_type(task)

        # 记录任务开始
        self.memory.remember(
            key=f"task:{self._task_count}",
            content=f"任务 #{self._task_count}: {task[:200]}",
            tags=["task", init_task_type],
        )

        # 问候/寒暄检测 — 不进 agent 循环
        greeting_reply = self._detect_greeting(task)
        if greeting_reply:
            return {
                "success": True,
                "result": greeting_reply,
                "summary": greeting_reply,
                "turns": 0,
                "errors": [],
                "duration": 0.0,
                "task_type": "greeting",
            }

        # 模型切换/查询检测 — 不进 agent 循环
        model_switch_reply = self._detect_model_switch(task)
        if model_switch_reply:
            self._task_count -= 1  # 这不是真正的任务
            return {
                "success": True,
                "result": model_switch_reply,
                "summary": model_switch_reply,
                "turns": 0,
                "errors": [],
                "duration": 0.0,
                "task_type": "model_switch",
            }

        # 创建 AgentLoop 执行
        loop = AgentLoop(
            llm=self.llm,
            memory=self.memory,
            evolution=self.evolution,
            on_step=lambda msg: print(f"  {msg}", flush=True),
        )
        # 注入审批推送回调
        self._inject_approval_notifier(loop)

        # 根据 mode 选择执行路径
        if mode == "whiteboard":
            result = loop.run_whiteboard(task)
        else:
            result = loop.run(
                task,
                resume_from=resume_from,
                resume_mode=resume_mode,
                resume_max_tokens=resume_max_tokens,
            )

        # 补充元信息
        # 使用 agent_loop 内部检测的 task_type（更准确）
        detected_type = result.get("task_type", "generic")
        result["task_type"] = detected_type
        result["duration"] = round(time.time() - start, 3)

        # 回调：通知 evolution 已完成
        evolution_event = result.get("evolution")
        if evolution_event:
            if isinstance(evolution_event, dict):
                evo_level = evolution_event.get("evolution_mode", "info") or "info"
                evo_action = evolution_event.get("reason", "未知")
            else:
                evo_level = getattr(evolution_event, "level", "info")
                evo_action = getattr(evolution_event, "action", "未知")
            self.memory.remember(
                key=f"evolution:L{evo_level}:{self._task_count}",
                content=f"L{evo_level} 进化: {evo_action}",
                tags=["evolution", f"L{evo_level}"],
            )

        return result

    @staticmethod
    def _clean_input(text: str) -> str:
        """清理 input() 接收的原始输入，处理退格符等控制字符。

        Python 的 input() 函数原样保留终端控制字符（如退格符 \b），
        但终端显示时这些字符已被正确解释。本函数模拟退格语义，
        正确移除被退格删除的字符，只保留用户最终看到的输入内容。
        """
        result = []
        for ch in text:
            if ch == '\b':
                if result:
                    result.pop()
            elif ord(ch) < 0x20 and ch not in ('\t', '\n', '\r'):
                continue
            else:
                result.append(ch)
        return ''.join(result).strip()

    def converse(self, input_text: str) -> dict:
        """多轮对话 — 延续上下文。

        与 run() 的区别：
        - 保存 conversation messages 跨轮次传递
        - 用户可追问"再试一次"、"换个方式"、"修改刚才的代码"
        - 前 N 轮 conversation 消息作为上下文注入 AgentLoop
        - 首次调用 _tasks=1，后续调用是连续会话

        Args:
            input_text: 用户本轮输入

        Returns:
            同 run() 的返回结构
        """
        start = time.time()
        self._task_count += 1

        # 清理输入中的退格符等控制字符
        input_text = self._clean_input(input_text)

        # 问候检测
        greeting_reply = self._detect_greeting(input_text)
        if greeting_reply:
            return {
                "success": True,
                "result": greeting_reply,
                "summary": greeting_reply,
                "turns": 0,
                "errors": [],
                "duration": 0.0,
                "task_type": "greeting",
            }

        # 自主学习模式指令检测
        learn_mode_reply = self._detect_learning_mode(input_text)
        if learn_mode_reply:
            return {
                "success": True,
                "result": learn_mode_reply,
                "summary": learn_mode_reply,
                "turns": 0,
                "errors": [],
                "duration": 0.0,
                "task_type": "learning_mode",
            }

        # 模型切换/查询检测
        model_switch_reply = self._detect_model_switch(input_text)
        if model_switch_reply:
            return {
                "success": True,
                "result": model_switch_reply,
                "summary": model_switch_reply,
                "turns": 0,
                "errors": [],
                "duration": 0.0,
                "task_type": "model_switch",
            }

        # 是否为后续轮次
        is_followup = self._conversation is not None

        # 记录任务到记忆
        context_note = f"对话 #{self._task_count} ('{input_text[:80]}')"
        if is_followup:
            context_note = f"对话 #{self._task_count} (追问) '{input_text[:80]}'"
        self.memory.remember(
            key=f"converse:{self._task_count}",
            content=context_note,
            tags=["conversation", detect_task_type(input_text)],
        )

        # 构建 AgentLoop
        loop = AgentLoop(
            llm=self.llm,
            memory=self.memory,
            evolution=self.evolution,
            on_step=lambda msg: print(f"  {msg}", flush=True),
        )
        # 注入审批推送回调
        self._inject_approval_notifier(loop)

        # 传递历史上下文（最近的 5 轮）
        if is_followup:
            history_context = self._format_conversation_history()
            enriched_input = f"{history_context}\n\n[当前输入]\n{input_text}"
        else:
            enriched_input = input_text

        result = loop.run(enriched_input)

        # 补充元信息
        # 使用 agent_loop 内部检测的 task_type（更准确）
        result["task_type"] = result.get("task_type", "generic")
        result["duration"] = round(time.time() - start, 3)
        result["is_followup"] = is_followup

        # 保存本轮对话上下文
        self._conversation = {
            "turn": self._task_count,
            "input": input_text,
            "result": result.get("result", "")[:300],
            "success": result["success"],
            "turns": result.get("turns", 0),
            "time": time.time(),
        }

        # 保留 messages 引用（前 2 轮 user + assistant）
        self._conversation_messages.append({
            "role": "user",
            "content": input_text,
        })
        if result.get("result"):
            self._conversation_messages.append({
                "role": "assistant",
                "content": result["result"][:500],
            })
        # 对话上下文压缩：只保留最近 6 轮（12 条消息）
        if len(self._conversation_messages) > 12:
            self._conversation_messages = self._conversation_messages[-12:]

        # 进化回调
        evolution_event = result.get("evolution")
        if evolution_event:
            # evolution_event 可能是 dict（来自新管道 run_pipeline）或 EvolutionEvent 对象（旧路径）
            if isinstance(evolution_event, dict):
                level = evolution_event.get("evolution_mode", "info") or "info"
                action = evolution_event.get("reason", "未知")
                evo_level = "skill" if evolution_event.get("skill_written") else "info"
            else:
                level = getattr(evolution_event, "level", "info")
                action = getattr(evolution_event, "action", "未知")
                evo_level = level
            self.memory.remember(
                key=f"evolution:L{evo_level}:conv:{self._task_count}",
                content=f"L{evo_level} 进化: {action}",
                tags=["evolution", f"L{evo_level}"],
            )

        return result

    def _format_conversation_history(self) -> str:
        """将最近的对话历史格式化为 LLM 上下文。"""
        if not self._conversation_messages:
            return ""
        lines = ["[对话历史]"]
        for msg in self._conversation_messages[-6:]:  # 最近 3 轮
            role = "用户" if msg["role"] == "user" else "夸父"
            content = msg["content"][:200]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def reflect_on_task(self, task_result: dict) -> Optional[str]:
        """任务结束后反思。"""
        if task_result.get("errors"):
            return self.memory.reflect(f"如何避免 {task_result['errors'][0]}")
        return None

    def reset_conversation(self):
        """重置对话上下文，开始新会话。"""
        self._conversation = None
        self._conversation_messages = []

    def _sync_model_manager_with_llm(self):
        """以 LLMClient 实际状态为准，同步 ModelManager 配置。"""
        mm = self.model_manager.as_dict()
        if self.llm.backend != mm.get("backend") or self.llm.model != mm.get("model"):
            self.model_manager.apply({
                "backend": self.llm.backend,
                "model": self.llm.model,
                "base_url": self.llm.base_url,
                "max_tokens": getattr(self.llm, "max_tokens", 4096),
                "temperature": getattr(self.llm, "temperature", 0.7),
            })

    def _inject_approval_notifier(self, loop: AgentLoop) -> None:
        """注入审批通知回调到 AgentLoop。"""
        def _notify(tool_name: str, args: dict, req_id: str):
            # 终端醒目提示（无论飞书是否可用，都打印到终端日志）
            cmd_info = ""
            if tool_name == "terminal":
                cmd_info = f" 命令: {args.get('command', '')[:80]}"
            print(f"\n{'='*55}", flush=True)
            print(f"  🔐 审批请求已提交", flush=True)
            print(f"  工具: {tool_name}{cmd_info}", flush=True)
            print(f"  审批ID: {req_id}", flush=True)
            if self._feishu_bot is not None:
                print(f"  已推送到飞书，请查看审批", flush=True)
            else:
                print(f"  请在终端确认（回到终端窗口查看）", flush=True)
            print(f"{'='*55}\n", flush=True)
            # 飞书推送（如有配置）
            if self._feishu_bot is not None:
                msg = (
                    f"🔐 审批请求\n"
                    f"工具: {tool_name}\n"
                    f"参数: {json.dumps(args, ensure_ascii=False, indent=2)[:200]}\n"
                    f"审批ID: {req_id}\n"
                    f"---\n"
                    f"请回复同意或拒绝"
                )
                self._feishu_bot.send_text(msg)

        loop.on_approval_request = _notify

    # ---- 模型切换 ----

    def switch_model(self, target: str) -> str:
        """运行时切换模型。

        支持：
        - 模板 ID: 'cloud:deepseek', 'local:qwen', 'cloud:claude'
        - 别名: 'deepseek', 'claude', 'qwen', 'local', 'cloud'
        - 快速后端切换: 'local', 'cloud'
        - 自定义参数: '--backend local --model xxx'
        - 自定义模型名: 'gpt-4o-mini'

        Returns:
            人类可读的切换结果。
        """
        result = self.model_manager.switch(target)
        if result["success"]:
            # 应用到当前 LLMClient
            self.llm.switch(result["config"])
            msg = result["message"]
            self.memory.remember(
                key=f"model_switch:{int(time.time())}",
                content=msg,
                tags=["model_switch", self.llm.backend, self.llm.model],
            )
        else:
            msg = result["message"]
        return msg

    # ---- 状态查询 ----

    def get_status(self) -> dict:
        status = {
            "name": self.name,
            "version": self.version,
            "task_count": self._task_count,
            "llm_model": self.llm.model,
            "memory": self.memory.get_status(),
            "evolution": self.evolution.get_evolution_stats(),
            "task_stats": self.evolution.get_task_stats(),
        }
        # P2 状态
        if _HAS_PRIORITIZER and hasattr(self, '_prioritizer_thread'):
            status["prioritizer"] = {
                "alive": self._prioritizer_thread is not None and self._prioritizer_thread.is_alive(),
            }
        return status

    @staticmethod
    def _detect_greeting(text: str) -> str:

        # 纯问候/自我介绍/闲聊 — 不进 agent 循环
        greeting_patterns = [
            # 单纯问候你好
            r"^(你好|您好|hi|hello|hey|hi~|嗨|早|早上好|下午好|晚上好)[!！。.，,]*$",
            r"^你[叫是]谁$",
            r"^(你是谁|你叫什么|你叫什么名字)[?？]*$",
            r"^(你好吗|你怎么样|还好吗|怎么样)[?？]*$",
            r"^(夸父|你好夸父|夸父你好)[!！。.，,]*$",
            r"^(在吗|在不在|在不)[?？]*$",
            r"^(再见|bye|拜拜|88)[!！。.，,]*$",
            r"^(谢谢|多谢|感谢)[!！。.，,]*$",
        ]
        for pattern in greeting_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return "你好！我是夸父，一个自我进化的 AI agent。有问题尽管说，我来帮你搞定 💪"

        return ""

    def _detect_model_switch(self, text: str) -> Optional[str]:
        """检测模型切换意图。

        格式：
        - "切换模型 local" / "切到 deepseek" / "用 claude"
        - "模型列表" / "查看可用模型"
        - "当前模型" / "查看模型"

        Returns:
            如果检测到切换命令，返回切换目标字符串；如果是查询，返回已格式化的信息字符串；否则返回 None。
        """
        text = text.strip()

        # 查询：查看可用模型
        if re.match(r"^(查看|显示|列出|有哪些|看看|list)\s*(可用\s*)?模型[t]*(模板)?", text, re.IGNORECASE):
            return self._format_model_list()
        if re.match(r"^(模型列表|可用模型|模型模板|模型大全|model list|models)$", text, re.IGNORECASE):
            return self._format_model_list()

        # 查询：当前模型
        if re.match(r"^(当前模型|查看模型|模型状态|你.*(什么|当前|用).*模型|你.*模型.*什么|model\s*$)", text, re.IGNORECASE):
            cfg = self.model_manager.as_dict()
            active = f"**当前模型：** `{self.llm.model}`\n"
            active += f"**后端：** {self.llm.backend}\n"
            active += f"**API URL：** {self.llm.base_url}\n"
            active += f"**max_tokens：** {self.llm.max_tokens}\n"
            active += f"**temperature：** {self.llm.temperature}\n"
            active += f"**profile：** `{cfg.get('profile', '—')}`"
            return active

        # 切换命令
        m = re.match(r"^切换\s*(模型|后端|到)\s*(.+)$", text, re.IGNORECASE)
        if m:
            target = m.group(2).strip()
            return self.switch_model(target)

        m = re.match(r"^切(到|换)\s*(.+)$", text, re.IGNORECASE)
        if m:
            target = m.group(2).strip()
            return self.switch_model(target)

        m = re.match(r"^用\s*(.+)$", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            if target in ALIASES or target in MODEL_TEMPLATES:
                return self.switch_model(target)
            for alias in ALIASES:
                if target.startswith(alias):
                    return self.switch_model(alias)
        return None

    def _format_model_list(self) -> str:
        """格式化可用模型列表。"""
        templates = self.model_manager.list_templates()
        aliases = self.model_manager.list_aliases()
        lines = ["**可用模型模板：**"]
        for t in templates:
            marker = " ✅" if t["active"] else ""
            lines.append(f"  `{t['id']}` — {t['name']}{marker}")
        lines.append("")
        lines.append("**简写别名：**")
        for alias, target in sorted(aliases.items()):
            lines.append(f"  `{alias}` → `{target}`")
        lines.append("")
        lines.append("**使用：** `切换模型 <别名/模板ID>`")
        return "\n".join(lines)

    # ── 自主学习模式 ──────────────────────────────────────────────────

    def _detect_learning_mode(self, text: str) -> Optional[str]:
        """检测自主学习模式指令（start learn / stop learn）。

        进入学习模式时，WebLearner 进入高强度扫描状态（15 秒间隔），
        退出时汇总本轮学习成果。
        """
        text = text.strip().lower()

        # 开始自主学习
        start_patterns = [
            r"^(开始|启动|开启|进入)\s*(自主)?\s*(学习|自?学)\s*(模式)?$",
            r"^start\s+(learn|learning|auto)\s*(mode)?$",
            r"^go\s+(learn|learning)$",
            r"^(我休息了|我睡了|休息|学习模式)$",
            r"^(自主学习|自动学习|自学)$",
        ]
        is_start = any(re.match(p, text) for p in start_patterns)

        # 停止自主学习
        stop_patterns = [
            r"^(停止|结束|退出|关闭|退出)\s*(自主)?\s*(学习|自学)\s*(模式)?$",
            r"^stop\s+(learn|learning|auto)\s*(mode)?$",
            r"^(我回来了|我醒了|醒来|停止学习)$",
            r"^(退出学习模式|结束学习)$",
        ]
        is_stop = any(re.match(p, text) for p in stop_patterns)

        # 查询学习模式状态
        status_patterns = [
            r"^(学习状态|学习进展|学习进度|看看学了什么)$",
            r"^learning\s*(status|progress|state)$",
            r"^(学了什么|学了多少|学习报告)$",
        ]
        is_status = any(re.match(p, text) for p in status_patterns)

        # 如果没有 WebLearner，告知无法自主学习
        if not self._web_learner:
            if is_start or is_stop or is_status:
                return "⚠️ 主动网络学习引擎未加载，无法使用自主学习模式。"
            return None

        # —— 查询状态 ——
        if is_status:
            if not self._learning_auto_mode:
                s = self._web_learner.stats
                return (
                    f"📊 **当前学习状态〔非自主学习模式〕**\n"
                    f"- 已学累计: {s['learned_count']} 个\n"
                    f"- WebLearner {'🟢 运行中' if s['is_running'] else '🔴 未启动'}\n"
                    f"- 后台扫描间隔: {s['interval']}s\n\n"
                    f"输入 `start learn` 进入高强度自主学习模式"
                )
            elapsed = time.time() - self._learning_auto_start_time
            s = self._web_learner.stats
            session_learned = s["total_learned_since_start"] - self._learning_auto_learned_before
            return (
                f"📊 **自主学习进行中〔{int(elapsed // 60)}分{int(elapsed % 60)}秒〕**\n"
                f"- 本轮已学: {session_learned} 个项目\n"
                f"- 累计扫源: {s['source_index']} 轮\n"
                f"- 扫描间隔: {s['interval']}s\n\n"
                f"输入 `stop learn` 结束学习并查看总结"
            )

        # —— 开始学习 ——
        if is_start:
            if self._learning_auto_mode:
                return "🤖 已经处于自主学习模式了。输入 `stop learn` 停止。"

            self._learning_auto_mode = True
            self._learning_auto_start_time = time.time()
            self._learning_auto_learned_before = self._web_learner.stats["total_learned_since_start"]

            # 调整 WebLearner 为高强度模式
            self._web_learner.set_interval(15)  # 15 秒高强度扫描
            self._web_learner.set_max_per_cycle(10)  # 每轮最多学10个

            # 确保 WebLearner 在运行
            if not self._web_learner.stats["is_running"]:
                self._web_learner.start(daemon=True)

            self.memory.remember(
                key=f"learning_mode:start:{int(time.time())}",
                content="用户启动了高强度自主学习模式",
                tags=["learning_mode", "start"],
            )

            return (
                "🧠 **进入自主学习模式！**\n\n"
                "我将在后台持续扫描以下源：\n"
                "- GitHub Trending（热门仓库）\n"
                "- Hacker News（技术热点）\n"
                "- GitHub AI/LLM/RAG 相关项目\n\n"
                "高强度扫描已开启，学到实用项目会自动记录。\n"
                "你随时可以输入 `stop learn` 或 `我回来了` 结束学习，\n"
                "我会汇总汇报本轮学习成果。\n\n"
                "➡️ 夸父开始自主学习了，主公好好休息。"
            )

        # —— 停止学习 ——
        if is_stop:
            if not self._learning_auto_mode:
                return "🤖 当前不在自主学习模式。输入 `start learn` 开始。"

            self._learning_auto_mode = False
            elapsed = time.time() - self._learning_auto_start_time

            # 恢复默认设置
            self._web_learner.set_interval(600)  # 恢复 10 分钟
            self._web_learner.set_max_per_cycle(6)  # 恢复每轮6个

            # 计算本轮成果
            s = self._web_learner.stats
            session_learned = s["total_learned_since_start"] - self._learning_auto_learned_before
            total_count = s["learned_count"]

            self.memory.remember(
                key=f"learning_mode:stop:{int(time.time())}",
                content=(
                    f"自主学习模式结束，持续 {elapsed:.0f}s，本轮学习了 {session_learned} 个项目"
                ),
                tags=["learning_mode", "stop"],
            )

            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            time_str = f"{minutes}分{seconds}秒" if minutes else f"{seconds}秒"

            # 生成成果汇报
            lines = [
                "📚 **自主学习报告**\n",
                f"⏱ 学习时长: {time_str}",
                f"✅ 本轮学到: {session_learned} 个实用项目",
                f"📦 历史累计: {total_count} 个项目",
                "",
                "---",
                "好了，主公回来了～夸父切换回正常模式，随时听令。",
            ]
            return "\n".join(lines)

        return None

    def __repr__(self) -> str:
        return f"<KuafuAgent v{self.version} | {self._task_count} tasks | LLM: {self.llm.model}>"

    # ---- WebHook 服务器 ----

    def start_webhook(
        self,
        port: int = 8765,
        token: str = "",
    ) -> bool:
        """启动 WebHook 服务器（后台线程）。"""
        if self._webhook_server and self._webhook_server.is_running():
            return True

        try:
            from core.webhook_server import WebhookServer
            self._webhook_server = WebhookServer(port=port, token=token)
            self._webhook_server.set_handler(
                on_task=lambda payload, task_id: self._handle_webhook_task(payload, task_id)
            )
            return self._webhook_server.start()
        except Exception as e:
            import logging
            logging.error(f"WebHook 启动失败: {e}")
            return False

    def _handle_webhook_task(self, payload: dict, task_id: str):
        """处理 WebHook 任务（在线程中执行）。"""
        task = payload.get("task", payload.get("prompt", ""))
        if not task:
            return

        context = payload.get("context", {})
        mode = context.get("mode", "standard")
        print(f"\n[WebHook:{task_id}] 收到任务: {task[:80]}...")
        result = self.run(task, mode=mode)
        status = "✅" if result["success"] else "❌"
        print(f"[WebHook:{task_id}] {status} 完成 ({result['duration']}s)")

    def stop_webhook(self):
        """停止 WebHook 服务器。"""
        if self._webhook_server:
            self._webhook_server.stop()
            self._webhook_server = None


# ---- CLI 入口 ----

def main():
    """命令行入口。python -m kuafu"""
    agent = KuafuAgent()
    print(f"⚡ {agent.name} v{agent.version} — 自我进化的 AI Agent")
    print(f"   LLM: {agent.llm.model}")
    print("=" * 50)
    print()

    import argparse
    parser = argparse.ArgumentParser(description="夸父 Agent CLI")
    parser.add_argument("task", nargs="?", help="直接执行任务")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--whiteboard", action="store_true",
                        help="使用白板模式执行（复杂任务分解为多步，节省上下文）")
    parser.add_argument("--mode", choices=["standard", "whiteboard"], default=None,
                        help="执行模式（覆盖 --whiteboard 的简写）")
    parser.add_argument("--webhook-port", type=int, default=0,
                        help="启动 WebHook 服务器（指定端口，如 8765）")
    parser.add_argument("--webhook-token", default="",
                        help="WebHook 认证 Token")

    args = parser.parse_args()

    if args.webhook_port:
        agent.start_webhook(port=args.webhook_port, token=args.webhook_token)

    if args.status:
        status = agent.get_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    if args.task:
        # 确定执行模式
        mode = args.mode or ("whiteboard" if args.whiteboard else "standard")
        mode_label = "📋" if mode == "standard" else "🧩"
        print(f"{mode_label} 任务 ({mode}): {args.task}")
        result = agent.run(args.task, mode=mode)
        status = "✅" if result["success"] else "❌"
        print(f"\n{status} 结果:")
        print(result.get("result", "(无结果)"))
        if result.get("evolution"):
            evo = result["evolution"]
            if isinstance(evo, dict):
                evo_level = evo.get("evolution_mode", "info") or "info"
                evo_action = evo.get("reason", "未知")
            else:
                evo_level = getattr(evo, "level", "info")
                evo_action = getattr(evo, "action", "未知")
            print(f"\n🧬 进化: L{evo_level} — {evo_action}")
        quality = result.get("quality")
        if quality:
            bar = "🟩" * int(quality["score"]) + "⬜" * (10 - int(quality["score"]))
            print(f"📊 质量: {quality['score']}/10 {bar}")
        print(f"\n⏱ {result['duration']}s | 轮次: {result.get('turns', 0)} | 错误: {len(result.get('errors', []))}")
        return

    # 交互模式（多轮对话）— 使用 readline 支持行编辑
    import readline
    print("夸父交互模式 (输入 'exit' 退出，'new' 重置对话)")
    while True:
        try:
            task = input("\n> ").strip()
            if task.lower() in ("exit", "quit", "q"):
                break
            if task.lower() in ("new", "reset", "r"):
                agent.reset_conversation()
                print("🔄 对话已重置")
                continue
            if not task:
                continue
            result = agent.converse(task)
            status_icon = "✅" if result["success"] else "❌"
            if result["success"]:
                print(f"\n{status_icon} {result.get('result', '(无结果)')}")
            else:
                errs = result.get("errors", [])
                err_detail = f" — {'; '.join(errs[:3])}" if errs else ""
                print(f"\n{status_icon} 执行失败{err_detail}")
                if not errs:
                    print(f"   结果: {result.get('result', '(空)')[:200]}")
            if result.get("evolution"):
                evo = result["evolution"]
                if isinstance(evo, dict):
                    evo_level = evo.get("evolution_mode", "info") or "info"
                    evo_action = evo.get("reason", "未知")
                else:
                    evo_level = getattr(evo, "level", "info")
                    evo_action = getattr(evo, "action", "未知")
                print(f"   🧬 进化: L{evo_level} — {evo_action}")
            turn_label = "多轮" if result.get("is_followup") else "单次"
            print(f"   ⏱ {result['duration']}s | {turn_label} | {result.get('turns', 0)} turns")
            # 质量评分
            quality = result.get("quality")
            if quality:
                bar = "🟩" * int(quality["score"]) + "⬜" * (10 - int(quality["score"]))
                print(f"   📊 质量: {quality['score']}/10 {bar}")
                if quality.get("suggestions") and not result.get("success"):
                    for s in quality["suggestions"][:2]:
                        print(f"   💡 {s}")
        except KeyboardInterrupt:
            print("\n再见！")
            break


if __name__ == "__main__":
    main()
