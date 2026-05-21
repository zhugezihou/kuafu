"""
固定底部输入框 — 输入框永远在终端最底端，输出在上面滚动。

用法：
    from core.input_bottom import FixedBottomInput
    ui = FixedBottomInput()
    ui.run(lambda task: agent.converse(task))
"""
import sys
import threading
import queue
from typing import Callable, Optional


class FixedBottomInput:
    """固定底部输入框。

    原理：
    - 后台线程读取输入，放入队列
    - 主线程从队列取任务，执行 agent，打印结果
    - 每行输出前用 \033[A 回到输入框上一行再打印
    - 输入框始终在终端最底行
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._thread: Optional[threading.Thread] = None

    def _input_reader(self):
        """后台线程：读取 stdin，每行推入队列。"""
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    self._queue.put("__EOF__")
                    break
                self._queue.put(line.rstrip("\n"))
            except (EOFError, KeyboardInterrupt):
                self._queue.put("__EOF__")
                break
            except Exception:
                pass

    def print_above(self, text: str):
        """在输入框上方打印一行。

        先用 \033[A 向上移动一行（覆盖输入框行），
        打印内容，再重新显示输入框。
        """
        # 上移一行 + 清除当前行 + 打印内容
        sys.stdout.write(f"\033[A\033[K{text}\n")
        # 重新显示输入框（保证它在最底行）
        sys.stdout.write("> ")
        sys.stdout.flush()

    def print_above_multi(self, lines: list):
        """在输入框上方打印多行。"""
        if not lines:
            return
        for line in lines:
            sys.stdout.write(f"\033[A\033[K{line}\n")
        sys.stdout.write("> ")
        sys.stdout.flush()

    def separator(self):
        """打印分割线。"""
        sys.stdout.write("\033[A\033[K" + "─" * 40 + "\n")
        sys.stdout.write("> ")
        sys.stdout.flush()

    def run(self, executor: Callable[[str], dict]):
        """主循环。

        Args:
            executor: 接收任务文本，返回结果 dict
        """
        # 启动输入线程
        self._thread = threading.Thread(target=self._input_reader, daemon=True)
        self._thread.start()

        print("⚡ 夸父交互模式 (exit 退出, new 重置)", flush=True)
        sys.stdout.write("> ")
        sys.stdout.flush()

        while self._running:
            task = self._queue.get()
            if task in ("__EOF__", "exit", "quit", "q"):
                break
            if task in ("new", "reset", "r"):
                self.separator()
                self.print_above("🔄 对话已重置")
                continue
            if not task:
                continue

            self.separator()
            self.print_above(f"📋 {task}")

            try:
                result = executor(task)
                self._format_result(result)
            except Exception as e:
                self.print_above(f"❌ 异常: {e}")

        # 退出前清除最后一行输入框
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _format_result(self, result: dict):
        """格式化结果输出到输入框上方。"""
        lines = []

        # 结果文本
        text = result.get("result", "")
        if text:
            icon = "✅" if result.get("success") else "❌"
            for line in text.split("\n"):
                lines.append(f"{icon} {line}")
                icon = "  "
        else:
            errs = result.get("errors", [])
            if errs:
                for e in errs[:3]:
                    lines.append(f"❌ {e}")
            else:
                lines.append("❌ 执行失败")

        # 元信息
        meta = []
        duration = result.get("duration", 0)
        turns = result.get("turns", 0)
        meta.append(f"⏱ {duration}s | {turns}turns")
        if result.get("evolution"):
            evo = result["evolution"]
            meta.append(f"🧬 L{evo.level}")
        quality = result.get("quality")
        if quality:
            meta.append(f"📊 {quality['score']}/10")
        if meta:
            lines.append("  " + " | ".join(meta))

        self.print_above_multi(lines)
