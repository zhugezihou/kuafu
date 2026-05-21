"""
夸父交互 UI — 固定底部输入框，任务输出在上方。

使用 threading 实现：
  - 后台线程：读取用户输入，推入队列
  - 主线程：从队列取任务，执行 agent，输出结果

输出在终端自由滚动，输入框在 agent 空闲时显示 "> " 提示符。
agent 忙碌时输入不会丢失——内容排入队列，等当前任务完成再处理。
"""
import sys
import threading
import queue
import time
from typing import Optional, Callable


class AsyncAgentUI:
    """异步 Agent 交互。Agent 在主线程执行，后台线程处理输入。

    用户在 agent 执行期间可以随时输入新任务（排队），
    agent 完成当前任务后自动从队列取下一个执行。
    所有输出打印在终端，输入框固定使用 "> " 提示符。

    使用方式：
        ui = AsyncAgentUI()
        ui.process_tasks(lambda task: agent.converse(task, on_step=ui.on_step))
    """

    def __init__(self, prompt: str = "> "):
        self.prompt = prompt
        self._task_queue: queue.Queue = queue.Queue()
        self._input_thread: Optional[threading.Thread] = None
        self._running = True

    def start(self):
        """启动后台输入线程。"""
        self._input_thread = threading.Thread(
            target=self._input_loop, daemon=True, name="ui-input"
        )
        self._input_thread.start()

    def stop(self):
        """停止输入线程。"""
        self._running = False

    def _input_loop(self):
        """后台线程：逐行读取 stdin，推入队列。"""
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    # EOF (pipe 关闭或 Ctrl+D)
                    self._task_queue.put("exit")
                    break
                text = line.rstrip("\n")
                self._task_queue.put(text)
            except (EOFError, KeyboardInterrupt):
                self._task_queue.put("exit")
                break
            except Exception:
                time.sleep(0.1)

    def on_step(self, msg: str):
        """Agent 步骤回调 — 实时显示进度。

        用于传入 AgentLoop 的 on_step 参数。agent 执行期间，
        每步状态会打印到终端上方（滚动区域）。
        """
        print(f"\r\033[K  {msg}", flush=True)

    def get_next_task(self) -> Optional[str]:
        """阻塞获取下一个用户任务。

        Returns:
            任务文本，或 "exit"（退出信号）
        """
        try:
            return self._task_queue.get()
        except Exception:
            return None

    def process_tasks(self, agent_executor: Callable[[str], dict]):
        """主循环：处理排队任务。

        流程：
        1. 启动输入线程
        2. 等待输入
        3. 取出任务 → 执行 agent → 输出结果 → 回到 2

        Args:
            agent_executor: 接收任务文本，返回结果 dict
        """
        print(f"\r\033[K⚡ 夸父交互模式 (输入 exit 退出，new 重置对话)", flush=True)
        print(f"\r\033[K{self.prompt}", end="", flush=True)
        self.start()

        while self._running:
            task = self.get_next_task()
            if task is None:
                continue

            task = task.strip()
            if task.lower() in ("exit", "quit", "q"):
                print("")
                break
            if task.lower() in ("new", "reset", "r"):
                print(f"\r\033[K🔄 对话已重置", flush=True)
                print(f"\r\033[K{self.prompt}", end="", flush=True)
                continue
            if not task:
                print(f"\r\033[K{self.prompt}", end="", flush=True)
                continue

            # 执行 agent 任务
            print(f"\r\033[K📋 任务: {task}", flush=True)
            try:
                result = agent_executor(task)
                self._print_result(result)
            except Exception as e:
                print(f"\r\033[K❌ 异常: {e}", flush=True)

            # 任务完成，恢复输入框
            print(f"\r\033[K{self.prompt}", end="", flush=True)

        self.stop()

    def _print_result(self, result: dict):
        """格式化打印 agent 结果。"""
        # 结果文本
        result_text = result.get("result", "")
        if result_text:
            lines = result_text.split("\n")
            status_icon = "✅ " if result.get("success") else "❌ "
            for i, part in enumerate(lines):
                prefix = status_icon if i == 0 else "   "
                print(f"\r\033[K{prefix}{part}", flush=True)
        else:
            errs = result.get("errors", [])
            if errs:
                for err in errs[:3]:
                    print(f"\r\033[K❌ {err}", flush=True)
            else:
                print(f"\r\033[K❌ 执行失败 (无结果)", flush=True)

        # 元信息
        meta_parts = []
        duration = result.get("duration", 0)
        turns = result.get("turns", 0)
        meta_parts.append(f"⏱ {duration}s | {turns} turns")
        if result.get("is_followup"):
            meta_parts.append("多轮")
        if result.get("evolution"):
            evo = result["evolution"]
            meta_parts.append(f"🧬 L{evo.level}")
        quality = result.get("quality")
        if quality:
            score = int(quality["score"])
            bar = "🟩" * score + "⬜" * (10 - score)
            meta_parts.append(f"📊 {quality['score']}/10 {bar}")
        if meta_parts:
            print(f"\r\033[K   {' | '.join(meta_parts)}", flush=True)
        print("", flush=True)  # 空行分隔
