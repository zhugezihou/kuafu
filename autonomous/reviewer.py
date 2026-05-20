"""
autonomous/reviewer.py — P0 自我复盘模块

职责：
周期性地检查进化日志和记忆，自动生成结构化复盘总结。
复盘结果写入记忆，供夸父了解自己的进化轨迹。

运行方式：
后台线程（daemon=True），由 main.py 的 KuafuAgent 启动。
不修改 core/ 目录任何文件，只读 memory/，写入 memory/。

复盘周期：3600 秒（1 小时）
每次复盘最多调用 1 次 LLM、读 2 个文件。
"""

import json
import time
import threading
import logging
from pathlib import Path
from typing import Optional

# ── 配置 ──────────────────────────────────────────────────────────────

REVIEW_INTERVAL = 3600  # 秒（1 小时）

ROOT_DIR = Path(__file__).resolve().parent.parent
EVOLUTION_LOG = ROOT_DIR / "memory" / "evolution_log.json"
MEMORY_DIR = ROOT_DIR / "memory"

logger = logging.getLogger("kuafu.reviewer")


# ── 复盘器 ────────────────────────────────────────────────────────────


class Reviewer:
    """P0 自我复盘器。

    生命周期由 ReviewerThread 管理，每个 KuafuAgent 启动一个。
    不持有 LLM 引用 — 通过回调函数让外部注入 LLM 调用能力。
    """

    def __init__(self, llm_chat_fn, memory_remember_fn, interval: int = REVIEW_INTERVAL):
        """
        Args:
            llm_chat_fn: callable(messages: list[dict]) -> str
                用来调用 LLM 生成复盘内容。由调用方注入。
            memory_remember_fn: callable(key: str, content: str, tags: list)
                用来写入记忆。由调用方注入。
            interval: 复盘检查间隔（秒）
        """
        self._llm_chat = llm_chat_fn
        self._remember = memory_remember_fn
        self._interval = interval

        # 上次复盘时已处理的进化日志条目数
        # 用于增量：只处理上次复盘之后新增的日志
        self._last_reviewed_count = 0
        self._load_last_reviewed_count()

    # ── 公开接口 ────────────────────────────────────────────────────

    def review(self) -> bool:
        """执行一次复盘检查。有新的进化事件则生成复盘总结。

        Returns:
            True 如果生成了复盘总结，False 如果没有新内容。
        """
        logs = self._load_evolution_logs()
        if logs is None:
            return False

        current_count = len(logs)
        new_events = logs[self._last_reviewed_count:]

        if not new_events:
            logger.debug(f"[Reviewer] 无新进化事件（已处理 {self._last_reviewed_count} 条）")
            return False

        logger.info(
            f"[Reviewer] 发现 {len(new_events)} 条新进化事件"
            f"（已有 {self._last_reviewed_count} → 共 {current_count} 条）"
        )

        # 生成复盘总结
        summary = self._generate_summary(new_events, logs)

        if summary:
            # 写入记忆
            self._remember(
                key=f"review:{int(time.time())}",
                content=summary,
                tags=["review", "P0", "self-review"],
            )
            self._last_reviewed_count = current_count
            self._save_last_reviewed_count()
            logger.info("[Reviewer] ✅ 复盘总结已写入记忆")
            return True

        return False

    # ── 内部方法 ────────────────────────────────────────────────────

    def _load_evolution_logs(self) -> Optional[list]:
        """读取进化日志。"""
        if not EVOLUTION_LOG.exists():
            return []
        try:
            return json.loads(EVOLUTION_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reviewer] 读取进化日志失败: {e}")
            return None

    def _load_last_reviewed_count(self):
        """从记忆目录读取上次已处理的日志条目数。"""
        marker = MEMORY_DIR / ".reviewer_marker"
        if marker.exists():
            try:
                self._last_reviewed_count = int(marker.read_text().strip())
            except (ValueError, OSError):
                self._last_reviewed_count = 0

    def _save_last_reviewed_count(self):
        """保存当前已处理的日志条目数。"""
        marker = MEMORY_DIR / ".reviewer_marker"
        try:
            marker.write_text(str(self._last_reviewed_count))
        except OSError:
            pass

    def _generate_summary(self, new_events: list, all_logs: list) -> Optional[str]:
        """用 LLM 把所有新进化事件→一篇结构化复盘总结。

        控制成本：每次最多传 10 条事件给 LLM。
        """
        events_to_summarize = new_events[-10:]  # 最多取最近 10 条
        total_events = len(all_logs)

        # 统计各级别进化次数
        level_counts = {}
        for e in all_logs:
            lv = e.get("level", 0)
            level_counts[lv] = level_counts.get(lv, 0) + 1

        # 统计失败/成功的趋势
        recent_30 = all_logs[-30:]
        failures_count = sum(
            1 for e in recent_30
            if "失败" in e.get("trigger", "") or "错误" in e.get("trigger", "")
        )

        # 构建事件内容
        events_text = ""
        for e in events_to_summarize:
            lv = e.get("level", 0)
            trigger = e.get("trigger", "?")
            action = e.get("action", "?")
            target = e.get("target", "?")
            events_text += f"- L{lv}: 「{trigger}」→ {action} → {target}\n"

        prompt = (
            "你是一个 AI Agent 的自我复盘系统。\n"
            "请根据以下进化日志，生成一段简洁的结构化复盘总结。\n\n"
            f"## 总览\n"
            f"- 总进化事件数: {total_events}\n"
            f"- 各级别分布: {json.dumps(level_counts, ensure_ascii=False)}\n"
            f"- 最近 30 条中失败相关: {failures_count} 条\n\n"
            f"## 本次新增事件（{len(events_to_summarize)} 条）\n"
            f"{events_text}\n\n"
            "## 要求\n"
            "用中文输出，输出格式如下（不要多余内容）：\n"
            "## 自我复盘\n"
            "- 趋势: 整体进化趋势是进步还是原地踏步？\n"
            "- 亮点: 哪些进化体现了有效的学习？\n"
            "- 问题: 是否存在重复出现的失败模式？\n"
            "- 建议: 接下来应该关注什么方向？\n"
        )

        try:
            result = self._llm_chat([
                {"role": "system", "content": "你是一个善于总结的 AI 自我复盘系统。"},
                {"role": "user", "content": prompt},
            ])
            # llm.chat() 返回 dict，兼容 str 返回值
            if isinstance(result, dict):
                content = result.get("content", "")
            elif isinstance(result, str):
                content = result
            else:
                content = ""
            if content.strip():
                return content.strip()
            return None
        except Exception as e:
            logger.warning(f"[Reviewer] LLM 复盘生成失败: {e}")
            return None


# ── 后台线程 ──────────────────────────────────────────────────────────


class ReviewerThread(threading.Thread):
    """管理 Reviewer 的后台线程。daemon=True，主进程退出时自动终止。"""

    def __init__(self, llm_chat_fn, memory_remember_fn, interval: int = REVIEW_INTERVAL):
        super().__init__(daemon=True, name="kuafu-reviewer")
        self._reviewer = Reviewer(
            llm_chat_fn=llm_chat_fn,
            memory_remember_fn=memory_remember_fn,
            interval=interval,
        )
        self._stop_event = threading.Event()

    def run(self):
        logger.info("[ReviewerThread] 启动，复盘间隔 %ds", self._reviewer._interval)
        while not self._stop_event.is_set():
            try:
                self._reviewer.review()
            except Exception as e:
                logger.error(f"[ReviewerThread] 复盘异常: {e}")
            # 等待 interval 或被 stop 唤醒
            self._stop_event.wait(self._reviewer._interval)

    def stop(self):
        """优雅停止。"""
        self._stop_event.set()
        logger.info("[ReviewerThread] 已停止")
