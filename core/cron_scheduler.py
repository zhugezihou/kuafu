"""
夸父 Cron 定时任务调度器 — 基于 YAML 配置的轻量级任务调度。

设计原则：
- 零外部依赖（仅 urllib + json + threading）
- YAML 配置文件驱动（可选，无依赖时可接受纯 dict 配置）
- 支持一次性/周期性/定时任务
- 线程级调度（非进程级，保持简单）
- 持久化任务状态，重启后恢复
- 支持任务结果输出到文件或飞书

配置文件格式（cron/schedule.yaml）：
```yaml
tasks:
  - name: 每日新闻摘要
    schedule: "0 8 * * *"         # cron 表达式或 '30m'/'2h' 等
    task: "搜索今日科技新闻并总结"
    enabled: true
    output_mode: file              # file | callback | none

  - name: 健康检查
    schedule: "10m"                # 每 10 分钟
    task: "检查系统状态"
    enabled: true
    output_mode: file
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent


# ── 简易 cron 表达式解析（支持部分标准格式 + 简易间隔） ───────


def parse_schedule(expr: str) -> tuple[float, str]:
    """解析调度表达式，返回 (间隔秒数, 类型)。

    支持格式：
    - "30m" / "2h" / "10s" → 间隔
    - "0 8 * * *" → cron 每日 8:00
    - "*/15 * * * *" → cron 每 15 分钟
    - ISO 时间如 "2026-05-10T08:00:00" → 一次性定时

    Returns:
        (interval_seconds, schedule_type)
        schedule_type: 'interval' | 'cron' | 'once'
    """
    expr = expr.strip()

    # 简易间隔格式: 数字 + s/m/h/d
    m = re.match(r"^(\d+)\s*(s|m|h|d)$", expr, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return (val * multipliers[unit], "interval")

    # ISO 时间格式
    if 'T' in expr:
        try:
            dt = datetime.fromisoformat(expr)
            interval = (dt - datetime.now()).total_seconds()
            if interval < 0:
                interval = 0
            return (interval, "once")
        except ValueError:
            print(f"[CronScheduler] ⚠️ ISO时间解析失败: '{expr}', 回退到默认间隔", flush=True)
            pass

    # 简易 cron 表达式（只支持标准 5 字段）
    # 这里只解析小时和分钟
    parts = expr.split()
    if len(parts) == 5:
        minute, hour = parts[0], parts[1]
        if minute == "*" and hour == "*":
            return (60, "cron")  # 每分钟 → 当作 60s 间隔
        elif minute.isdigit() and hour.isdigit():
            h, m = int(hour), int(minute)
            now = datetime.now()
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            interval = (target - now).total_seconds()
            return (interval, "cron")

    # fallback: 当作 30 分钟
    return (1800, "interval")


def format_next_run(interval: float, schedule_type: str) -> str:
    """格式化为人类可读的下次运行时间。"""
    if schedule_type == "once":
        return "一次性"
    if schedule_type == "interval":
        if interval < 60:
            return f"每 {int(interval)} 秒"
        elif interval < 3600:
            return f"每 {int(interval / 60)} 分钟"
        else:
            return f"每 {interval / 3600:.1f} 小时"
    return f"每 {int(interval)} 秒"


# ── 任务定义 ──────────────────────────────────────────────────


class CronTask:
    """单个定时任务。"""

    def __init__(
        self,
        name: str,
        schedule: str,
        task_text: str,
        enabled: bool = True,
        output_mode: str = "file",
        source_channel: str = "",
        run_count: int = 0,
        last_run: Optional[str] = None,
        last_result: str = "",
    ):
        self.name = name
        self.schedule_raw = schedule
        self.task_text = task_text
        self.enabled = enabled
        self.output_mode = output_mode
        self.source_channel = source_channel
        self.run_count = run_count
        self.last_run = last_run
        self.last_result = last_result

        self.interval, self.schedule_type = parse_schedule(schedule)
        self.schedule_expr = schedule if self.schedule_type == "cron" else ""
        self.next_run = time.time() + self.interval

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "schedule": self.schedule_raw,
            "task": self.task_text,
            "enabled": self.enabled,
            "output_mode": self.output_mode,
            "source_channel": self.source_channel,
            "run_count": self.run_count,
            "last_run": self.last_run or "",
            "last_result": self.last_result[:200] if self.last_result else "",
        }

    def __repr__(self):
        return f"<CronTask '{self.name}' {self.schedule_raw} run={self.run_count}>"


# ── 调度器 ────────────────────────────────────────────────────


class CronScheduler:
    """定时任务调度器。"""

    def __init__(
        self,
        config_path: str = "",
        on_task_run: Optional[Callable[[CronTask], str]] = None,
        state_path: str = "",
    ):
        """
        Args:
            config_path: 任务配置文件路径（可选，YAML）
            on_task_run: 任务执行回调 (task) → result_str
            state_path: 运行状态持久化路径
        """
        self._tasks: list[CronTask] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.on_task_run = on_task_run
        self._state_path = Path(state_path or (ROOT_DIR / "memory" / "cron_state.json"))
        self._lock = threading.Lock()
        self._feishu_bot: Optional[Any] = None
        self._wechat_bot: Optional[Any] = None

        # 从配置加载
        config_path_obj = Path(config_path or (ROOT_DIR / "cron" / "schedule.yaml"))
        self._config_path = config_path_obj
        if config_path_obj.exists():
            self._load_config(config_path_obj)

        # 从状态文件恢复运行计数
        self._load_state()

    def _load_config(self, path: Path):
        """加载 YAML 配置文件。

        用纯 Python 的简单 YAML 解析（少量 YAML 子集），
        避免 pyyaml 依赖。
        """
        try:
            text = path.read_text(encoding="utf-8")

            # 使用内置 YAML 读取
            # 夸父允许 pyyaml 依赖，但这里用简单行解析保持零依赖
            import yaml
            config = yaml.safe_load(text)
            if not config or "tasks" not in config:
                print(f"[CronScheduler] 配置中无任务: {path}")
                return

            for item in config["tasks"]:
                task = CronTask(
                    name=item.get("name", f"task-{len(self._tasks)}"),
                    schedule=item.get("schedule", "30m"),
                    task_text=item.get("task", ""),
                    enabled=item.get("enabled", True),
                    output_mode=item.get("output_mode", "file"),
                    source_channel=item.get("source_channel", ""),
                )
                self._tasks.append(task)

            print(f"[CronScheduler] 已加载 {len(config['tasks'])} 个任务")

        except ImportError:
            # 没有 pyyaml 时用简单解析
            print("[CronScheduler] pyyaml 未安装，尝试简单解析...")
            self._parse_simple_yaml(text, path)

        except Exception as e:
            print(f"[CronScheduler] 配置加载失败: {e}")

    def _parse_simple_yaml(self, text: str, path: Path):
        """简易 YAML 解析（仅支持 task 数组的扁平格式）。"""
        current = {}
        in_tasks_block = False
        tasks_started = False

        for line in text.splitlines():
            stripped = line.strip()

            if stripped == "" or stripped.startswith("#"):
                continue

            if stripped == "tasks:":
                in_tasks_block = True
                continue

            if in_tasks_block and stripped.startswith("- "):
                # 前一个任务入列
                if current.get("name"):
                    self._tasks.append(CronTask(**current))
                    current = {}
                tasks_started = True
                continue

            if tasks_started and ":" in stripped:
                k, v = stripped.split(":", 1)
                key = k.strip()
                val = v.strip().strip('"').strip("'")
                current[key] = val

        # 最后一个任务
        if current.get("name"):
            self._tasks.append(CronTask(**current))

        print(f"[CronScheduler] 简易解析: {len(self._tasks)} 个任务")

    def _load_state(self):
        """从状态文件恢复运行计数。"""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                task_states: dict = data.get("tasks", {})
                for task in self._tasks:
                    if task.name in task_states:
                        ts = task_states[task.name]
                        task.run_count = ts.get("run_count", 0)
                        task.last_run = ts.get("last_run", "")
                        task.last_result = ts.get("last_result", "")
                print(f"[CronScheduler] 已恢复 {len(task_states)} 个任务状态")
        except Exception as e:
            print(f"[CronScheduler] 状态恢复失败: {e}")

    def _save_state(self):
        """持久化任务状态。"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            states = {}
            for task in self._tasks:
                states[task.name] = {
                    "run_count": task.run_count,
                    "last_run": task.last_run or "",
                    "last_result": task.last_result[:200],
                }
            self._state_path.write_text(
                json.dumps({"tasks": states, "updated_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[CronScheduler] 状态保存失败: {e}")

    # ── 任务管理 ────────────────────────────────────────────────

    def add_task(self, task: CronTask):
        """添加一个任务。"""
        with self._lock:
            self._tasks.append(task)
            print(f"[CronScheduler] 已添加任务: {task.name}")
            self._save_state()

    def remove_task(self, name: str) -> bool:
        """按名称删除任务。"""
        with self._lock:
            for i, t in enumerate(self._tasks):
                if t.name == name:
                    self._tasks.pop(i)
                    print(f"[CronScheduler] 已删除任务: {name}")
                    self._save_state()
                    return True
            return False

    def get_tasks(self) -> list[CronTask]:
        with self._lock:
            return list(self._tasks)

    def get_task(self, name: str) -> Optional[CronTask]:
        for t in self._tasks:
            if t.name == name:
                return t
        return None

    # ── 运行循环 ────────────────────────────────────────────────

    def _run_loop(self):
        """调度主循环。"""
        print(f"[CronScheduler] 🟢 启动，{len(self._tasks)} 个任务")
        while self._running:
            try:
                now = time.time()
                due_tasks: list[CronTask] = []

                with self._lock:
                    for task in self._tasks:
                        if task.enabled and now >= task.next_run:
                            due_tasks.append(task)
                            # Cron 表达式：每次执行后重新计算时隔（而非固定间隔）
                            if task.schedule_type == "cron" and task.schedule_expr:
                                task.interval, _ = parse_schedule(task.schedule_expr)
                            task.next_run = now + task.interval

                # 执行到期的任务
                for task in due_tasks:
                    if not self._running:
                        break
                    self._execute_task(task)

                # 状态持久化
                if due_tasks:
                    self._save_state()
            except Exception as e:
                print(f"[CronScheduler] ❌ 主循环异常: {e}", flush=True)
                import traceback
                traceback.print_exc()

            # 每秒检查一次
            time.sleep(1)

        print("[CronScheduler] 🔴 已停止")

    def _execute_task(self, task: CronTask):
        """执行一个任务。"""
        task.run_count += 1
        task.last_run = datetime.now().isoformat()
        print(f"[CronScheduler] ▶️ [{task.name}] 第 {task.run_count} 次运行")

        if self.on_task_run:
            try:
                result = self.on_task_run(task)
                task.last_result = result[:500] if result else ""
                lines = result.split("\n")
                preview = lines[0][:80] if lines else "(无输出)"
                print(f"[CronScheduler] ✅ [{task.name}] {preview}")
            except Exception as e:
                task.last_result = f"错误: {e}"
                print(f"[CronScheduler] ❌ [{task.name}] {e}")
        else:
            task.last_result = "(无回调)"

        # 输出模式: file / feishu / wechat / all
        # 优先按 source_channel 推送
        channel_bot = None
        if task.source_channel:
            if 'feishu' in task.source_channel.lower() and self._feishu_bot is not None:
                channel_bot = self._feishu_bot
            elif 'wechat' in task.source_channel.lower() and self._wechat_bot is not None:
                channel_bot = self._wechat_bot

        if channel_bot:
            try:
                if hasattr(channel_bot, 'send_text'):
                    channel_bot.send_text(
                        f"⏰ Cron 任务: {task.name}\n"
                        f"时间: {task.last_run}\n\n"
                        f"{task.last_result[:19000] if task.last_result else '(无输出)'}"
                    )
                elif hasattr(channel_bot, 'send'):
                    channel_bot.send(
                        f"⏰ Cron 任务: {task.name}\n"
                        f"时间: {task.last_run}\n\n"
                        f"{task.last_result[:19000] if task.last_result else '(无输出)'}"
                    )
            except Exception as e:
                print(f"[CronScheduler] ⚠️ 源通道推送失败 ({task.source_channel}): {e}")
            self._save_to_file(task)
        elif task.output_mode == "file":
            self._save_to_file(task)
        elif task.output_mode in ("feishu", "all") and self._feishu_bot is not None:
            try:
                msg = (
                    f"⏰ Cron 任务: {task.name}\n"
                    f"时间: {task.last_run}\n\n"
                    f"{task.last_result[:19000] if task.last_result else '(无输出)'}"
                )
                if hasattr(self._feishu_bot, 'send_text'):
                    self._feishu_bot.send_text(msg)
                elif hasattr(self._feishu_bot, 'send'):
                    self._feishu_bot.send(msg)
            except Exception as e:
                print(f"[CronScheduler] ⚠️ 飞书推送失败: {e}")
            self._save_to_file(task)
        elif task.output_mode in ("wechat", "all") and hasattr(self, '_wechat_bot'):
            try:
                msg = (
                    f"⏰ Cron 任务: {task.name}\n"
                    f"时间: {task.last_run}\n\n"
                    f"{task.last_result[:19000] if task.last_result else '(无输出)'}"
                )
                if hasattr(self._wechat_bot, 'send_text'):
                    self._wechat_bot.send_text(msg)
                elif hasattr(self._wechat_bot, 'send'):
                    self._wechat_bot.send(msg)
            except Exception as e:
                print(f"[CronScheduler] ⚠️ 微信推送失败: {e}")
            self._save_to_file(task)

    def _save_to_file(self, task: CronTask):
        """将任务结果保存到文件。"""
        out_dir = ROOT_DIR / "cron" / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", task.name)
        out_path = out_dir / f"{safe_name}_{task.run_count}.txt"
        content = (
            f"任务: {task.name}\n"
            f"时间: {task.last_run}\n"
            f"结果:\n{task.last_result}\n"
        )
        out_path.write_text(content, encoding="utf-8")

    # ── 飞书集成 ────────────────────────────────────────────────

    def set_feishu_bot(self, bot) -> None:
        """注入飞书 Bot 实例，使 feishu output_mode 生效。"""
        self._feishu_bot = bot
        print(f"[CronScheduler] 📱 已接入飞书 Bot")

    def set_wechat_bot(self, bot) -> None:
        """注入微信 Bot 实例，使 wechat output_mode 生效。"""
        self._wechat_bot = bot
        print(f"[CronScheduler] 💬 已接入微信 Bot")

    # ── 启动 / 停止 ─────────────────────────────────────────────

    def start(self):
        """启动调度器（异步线程）。"""
        if self._running:
            print("[CronScheduler] 已在运行中")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[CronScheduler] ✅ 已启动")

    def stop(self):
        """停止调度器。"""
        self._running = False
        self._save_state()
        print("[CronScheduler] 正在停止...")


# ── 简易测试 ──────────────────────────────────────────────────


if __name__ == "__main__":
    def on_run(task: CronTask) -> str:
        return f"执行任务: {task.task_text}"

    scheduler = CronScheduler(on_task_run=on_run)
    scheduler.add_task(CronTask(
        name="测试任务",
        schedule="10s",
        task_text="这是一个测试",
    ))

    scheduler.start()
    try:
        time.sleep(25)
    except KeyboardInterrupt:
        pass
    scheduler.stop()
    print("调度器已退出")
