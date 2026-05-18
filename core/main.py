"""
夸父 (Kuafu) — 自我进化的 AI Agent 入口。

用法:
    # Python 调用
    from core.main import KuafuAgent
    
    agent = KuafuAgent()
    result = agent.run("帮我写一个 Python 脚本读取 CSV")
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Any, Optional

# 核心模块
from core.identity import load_identity_statement, detect_identity_impersonation
from core.sandbox import is_path_allowed_for_write, validate_command
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine, EvolutionEvent

ROOT_DIR = Path(__file__).resolve().parent.parent


class KuafuAgent:
    """夸父 Agent。

    组装：
    - 身份声明 → 系统 prompt
    - 沙盒 → 安全执行
    - 记忆 → 长期记忆
    - 进化 → 自我改进
    """

    def __init__(self):
        self.name = "夸父"
        self.version = "0.1.0"
        self.memory = MemoryAPI()
        self.evolution = EvolutionEngine()
        self._task_count = 0
        self._setup()

    def _setup(self):
        """首次启动设置。"""
        # 确保目录存在
        for d in ["strategy", "skills", "memory", "logs", "tests"]:
            (ROOT_DIR / d).mkdir(parents=True, exist_ok=True)
        # 记录启动
        self.memory.remember(
            key="system:startup",
            content=f"夸父 v{self.version} 启动",
            tags=["system", "startup"],
        )

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
        parts.append("")

        # 3. 可用工具
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
        parts.append("## 关于用户")
        user_profile = json.loads(
            (ROOT_DIR / "memory" / "user_profile.json").read_text(encoding="utf-8")
        )
        pref = user_profile.get("preferences", {})
        if pref:
            parts.append(f"用户偏好: {json.dumps(pref, ensure_ascii=False)}")

        return "\n".join(parts)

    def run(self, task: str, task_type: str = "generic") -> dict:
        """执行一次任务。

        Returns:
            {
                "success": bool,
                "result": str,
                "task_type": str,
                "errors": list[str],
                "evolution": EvolutionEvent or None,
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

        result = {
            "success": True,
            "result": "",
            "task_type": task_type,
            "errors": [],
            "user_correction": None,
            "tool_calls": 0,
            "duration": 0.0,
            "evolution": None,
        }

        try:
            # 安全检查
            safe, risk, reason = validate_command(task)
            if not safe:
                result["errors"].append(reason)
                result["success"] = False
                result["result"] = f"安全拦截: {reason}"
                return result

            # TODO: 实际执行任务逻辑
            # 这里由外部 LLM 循环驱动
            result["result"] = f"任务「{task}」已接收，等待执行..."

        except Exception as e:
            result["errors"].append(str(e))
            result["success"] = False
            result["result"] = f"执行异常: {e}"

        finally:
            result["duration"] = round(time.time() - start, 3)

        # 重要: 每次任务完成后触发进化评估
        evolution_event = self.evolution.evaluate_and_evolve(result)
        if evolution_event:
            result["evolution"] = evolution_event
            self.memory.remember(
                key=f"evolution:L{evolution_event.level}:{self._task_count}",
                content=f"L{evolution_event.level} 进化: {evolution_event.action}",
                tags=["evolution", f"L{evolution_event.level}"],
            )

        # evolution.evaluate_and_evolve 内部已记录任务历史
        return result

    def reflect_on_task(self, task_result: dict) -> Optional[str]:
        """任务结束后反思。"""
        if task_result.get("errors"):
            return self.memory.reflect(f"如何避免 {task_result['errors'][0]}")
        return None

    # ---- 状态查询 ----

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "task_count": self._task_count,
            "memory": self.memory.get_status(),
            "evolution": self.evolution.get_evolution_stats(),
            "task_stats": self.evolution.get_task_stats(),
        }

    def __repr__(self) -> str:
        return f"<KuafuAgent v{self.version} | {self._task_count} tasks>"


# ---- CLI 入口 ----

def main():
    """命令行入口。python -m kuafu"""
    agent = KuafuAgent()
    print(f"⚡ {agent.name} v{agent.version} — 自我进化的 AI Agent")
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
        result = agent.run(args.task)
        print(f"\n结果: {'✅' if result['success'] else '❌'}")
        print(result["result"])
        if result.get("evolution"):
            evo = result["evolution"]
            print(f"\n🧬 进化: L{evo.level} — {evo.action}")
        return

    # 交互模式
    print("夸父交互模式 (输入 'exit' 退出)")
    while True:
        try:
            task = input("\n> ").strip()
            if task.lower() in ("exit", "quit", "q"):
                break
            if not task:
                continue
            result = agent.run(task)
            status_icon = "✅" if result["success"] else "❌"
            print(f"\n{status_icon} {result['result']}")
            if result.get("evolution"):
                evo = result["evolution"]
                print(f"🧬 进化: L{evo.level} — {evo.action}")
        except KeyboardInterrupt:
            print("\n再见！")
            break


if __name__ == "__main__":
    main()
