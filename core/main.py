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
from core.agent_loop import AgentLoop
from autonomous.reviewer import ReviewerThread

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
        self.version = "0.2.0"
        self.memory = MemoryAPI()
        self.evolution = EvolutionEngine(memory=self.memory)
        self.llm = llm_client or LLMClient()
        self._task_count = 0
        # 多轮对话上下文
        self._conversation: Optional[dict] = None
        self._conversation_messages: list = []
        self._setup()
        # P0: 启动后台复盘线程（daemon=True，自动随主进程退出）
        self._reviewer_thread = ReviewerThread(
            llm_chat_fn=self.llm.chat,
            memory_remember_fn=lambda key, content, tags: self.memory.remember(
                key=key, content=content, tags=tags
            ),
        )
        self._reviewer_thread.start()

    def _setup(self):
        """首次启动设置。"""
        for d in ["strategy", "skills", "memory", "logs", "tests"]:
            (ROOT_DIR / d).mkdir(parents=True, exist_ok=True)
        self.memory.remember(
            key="system:startup",
            content=f"夸父 v{self.version} 启动，LLM: {self.llm.model}",
            tags=["system", "startup"],
        )

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

        # 6. 相关记忆
        recent = self.memory.recall("", limit=10)
        if recent:
            parts.append("## 相关记忆")
            for m in recent[-5:]:
                parts.append(f"- {m.get('key', '?')}: {m.get('content', '')[:100]}")
            parts.append("")

        return "\n".join(parts)

    def run(self, task: str, task_type: str = "generic") -> dict:
        """执行一次任务。
        
        如果用户输入的是问候/寒暄，直接回复，不进入 agent 循环。
        
        使用 AgentLoop 驱动完整的 LLM + 工具执行循环。

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

        # 记录任务开始
        self.memory.remember(
            key=f"task:{self._task_count}",
            content=f"任务 #{self._task_count}: {task[:200]}",
            tags=["task", task_type],
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

        # 创建 AgentLoop 执行
        loop = AgentLoop(
            llm=self.llm,
            memory=self.memory,
            evolution=self.evolution,
            on_step=lambda msg: print(f"  {msg}", flush=True),
        )
        result = loop.run(task)

        # 补充元信息
        result["task_type"] = task_type
        result["duration"] = round(time.time() - start, 3)

        # 回调：通知 evolution 已完成
        evolution_event = result.get("evolution")
        if evolution_event:
            self.memory.remember(
                key=f"evolution:L{evolution_event.level}:{self._task_count}",
                content=f"L{evolution_event.level} 进化: {evolution_event.action}",
                tags=["evolution", f"L{evolution_event.level}"],
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

    def converse(self, input_text: str, task_type: str = "generic") -> dict:
        """多轮对话 — 延续上下文。

        与 run() 的区别：
        - 保存 conversation messages 跨轮次传递
        - 用户可追问"再试一次"、"换个方式"、"修改刚才的代码"
        - 前 N 轮 conversation 消息作为上下文注入 AgentLoop
        - 首次调用 _tasks=1，后续调用是连续会话

        Args:
            input_text: 用户本轮输入
            task_type: 任务类型（默认 generic）

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

        # 是否为后续轮次
        is_followup = self._conversation is not None

        # 记录任务到记忆
        context_note = f"对话 #{self._task_count} ('{input_text[:80]}')"
        if is_followup:
            context_note = f"对话 #{self._task_count} (追问) '{input_text[:80]}'"
        self.memory.remember(
            key=f"converse:{self._task_count}",
            content=context_note,
            tags=["conversation", task_type],
        )

        # 构建 AgentLoop
        loop = AgentLoop(
            llm=self.llm,
            memory=self.memory,
            evolution=self.evolution,
            on_step=lambda msg: print(f"  {msg}", flush=True),
        )

        # 传递历史上下文（最近的 5 轮）
        if is_followup:
            history_context = self._format_conversation_history()
            enriched_input = f"{history_context}\n\n[当前输入]\n{input_text}"
        else:
            enriched_input = input_text

        result = loop.run(enriched_input)

        # 补充元信息
        result["task_type"] = task_type
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
            self.memory.remember(
                key=f"evolution:L{evolution_event.level}:conv:{self._task_count}",
                content=f"L{evolution_event.level} 进化: {evolution_event.action}",
                tags=["evolution", f"L{evolution_event.level}"],
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

    # ---- 状态查询 ----

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "task_count": self._task_count,
            "llm_model": self.llm.model,
            "memory": self.memory.get_status(),
            "evolution": self.evolution.get_evolution_stats(),
            "task_stats": self.evolution.get_task_stats(),
        }

    @staticmethod
    def _detect_greeting(text: str) -> str:
        """检测问候/寒暄，匹配则返回回复，否则返回空字符串。"""
        text = text.strip()

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

    def __repr__(self) -> str:
        return f"<KuafuAgent v{self.version} | {self._task_count} tasks | LLM: {self.llm.model}>"


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

    args = parser.parse_args()

    if args.status:
        status = agent.get_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    if args.task:
        print(f"📋 任务: {args.task}")
        result = agent.run(args.task)
        status = "✅" if result["success"] else "❌"
        print(f"\n{status} 结果:")
        print(result.get("result", "(无结果)"))
        if result.get("evolution"):
            evo = result["evolution"]
            print(f"\n🧬 进化: L{evo.level} — {evo.action}")
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
                print(f"   🧬 进化: L{evo.level} — {evo.action}")
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
