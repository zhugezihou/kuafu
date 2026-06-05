"""
core/batch_engine.py — 夸父批量任务引擎

批量提交/管理/追踪多个独立任务的执行引擎。

能力：
1. 批量提交 — 一次提交多个任务（字符串列表或（ID, 任务）对）
2. 并发控制 — 最大 N 并发，队列等待
3. 结果收集 — 每个任务的结果独立存储于 SQLite
4. 进度追踪 — 完成/总数/成功/失败实时查询
5. 持久化 — 重启不丢任务状态
6. CLI + HTTP API — 双入口

用法：
    from core.batch_engine import BatchEngine

    engine = BatchEngine(agent=agent, max_concurrent=3)
    batch_id = engine.submit(["任务1", "任务2", "任务3"])
    # 异步执行，轮询进度
    status = engine.get_status(batch_id)
    # {"total": 3, "completed": 1, "running": 2, "results": [...]}
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("kuafu.batch")

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
BATCH_DB = MEMORY_DIR / "batch.db"


# ── SQLite Schema ───────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS batch_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL,           -- 批次 ID
    task_index  INTEGER NOT NULL,        -- 批次内序号
    task_text   TEXT NOT NULL,           -- 任务文本
    task_id     TEXT DEFAULT '',         -- 自定义任务 ID
    status      TEXT DEFAULT 'pending',  -- pending / running / completed / failed
    result      TEXT DEFAULT '',         -- 结果摘要
    duration    REAL DEFAULT 0.0,        -- 执行耗时（秒）
    error       TEXT DEFAULT '',         -- 错误信息
    created_at  REAL NOT NULL DEFAULT (unixepoch()),
    updated_at  REAL NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_batch_id ON batch_jobs(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_status ON batch_jobs(batch_id, status);
"""


# ── 数据结构 ─────────────────────────────────────────────────


@dataclass
class BatchTask:
    """单个批处理任务。"""
    task_text: str
    task_id: str = ""          # 可选的自定义 ID
    status: str = "pending"    # pending / running / completed / failed
    result: str = ""
    duration: float = 0.0
    error: str = ""


@dataclass
class Batch:
    """一个任务批次。"""
    batch_id: str
    total: int = 0
    completed: int = 0
    running: int = 0
    failed: int = 0
    pending: int = 0
    results: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


# ── 批量任务引擎 ─────────────────────────────────────────────


class BatchEngine:
    """批量任务引擎。

    Usage:
        engine = BatchEngine(agent=agent)
        batch_id = engine.submit(["任务1", "任务2"], mode="standard")
        status = engine.get_status(batch_id)
        print(status.completed, "/", status.total)
    """

    _shared_conn: Optional[sqlite3.Connection] = None
    _shared_db_path: Optional[Path] = None
    _lock = threading.Lock()
    _global_semaphore: Optional[threading.Semaphore] = None

    def __init__(
        self,
        agent: Any,
        max_concurrent: int = 3,
        db_path: Optional[Path] = None,
        auto_start: bool = True,
    ):
        self.agent = agent
        self.max_concurrent = max_concurrent
        self.db_path = db_path or BATCH_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # SQLite 连接
        if BatchEngine._shared_conn is not None and BatchEngine._shared_db_path == self.db_path:
            self.conn = BatchEngine._shared_conn
        else:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()
            BatchEngine._shared_conn = self.conn
            BatchEngine._shared_db_path = self.db_path

        # 全局信号量（跨批次控制总并发）
        if BatchEngine._global_semaphore is None or max_concurrent != 3:
            BatchEngine._global_semaphore = threading.Semaphore(max_concurrent)

        self._semaphore = BatchEngine._global_semaphore

        # 后台调度线程
        self._running = False
        self._scheduler_thread: Optional[threading.Thread] = None

        if auto_start:
            self.start()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def start(self):
        """启动调度器线程。"""
        if self._running:
            return
        self._running = True
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="batch-scheduler",
        )
        self._scheduler_thread.start()
        logger.info(f"🟢 批量任务引擎已启动 (max_concurrent={self.max_concurrent})")

    def stop(self):
        """停止调度器。"""
        self._running = False

    # ── 提交接口 ───────────────────────────────────────────

    def submit(
        self,
        tasks: list[str | tuple[str, str]],
        mode: str = "standard",
        batch_id: Optional[str] = None,
    ) -> str:
        """提交一批任务。

        Args:
            tasks: 任务列表。每个元素可以是：
                   - str: 任务文本
                   - (task_id, task_text): 自定义 ID + 任务文本
            mode: 执行模式（standard / whiteboard）
            batch_id: 自定义批次 ID（可选，自动生成）

        Returns:
            批次 ID
        """
        if batch_id is None:
            batch_id = f"batch_{uuid.uuid4().hex[:12]}"

        now = time.time()
        for i, item in enumerate(tasks):
            if isinstance(item, tuple):
                task_id, task_text = item
            else:
                task_id, task_text = "", str(item)

            self._execute(
                """INSERT INTO batch_jobs
                   (batch_id, task_index, task_text, task_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (batch_id, i, task_text, task_id, now, now),
            )

        self.conn.commit()

        total = len(tasks)
        logger.info(f"📦 批次 {batch_id}: {total} 个任务已提交")
        return batch_id

    def submit_tasks(
        self,
        tasks: list[str | tuple[str, str]],
        mode: str = "standard",
        batch_id: Optional[str] = None,
    ) -> str:
        """submit 的别名，语义更清晰。"""
        return self.submit(tasks, mode=mode, batch_id=batch_id)

    # ── 状态查询 ───────────────────────────────────────────

    def get_status(self, batch_id: str) -> Batch:
        """获取批次状态。"""
        rows = self._execute(
            "SELECT * FROM batch_jobs WHERE batch_id = ? ORDER BY task_index",
            (batch_id,),
        ).fetchall()

        if not rows:
            return Batch(batch_id=batch_id, total=0)

        result_rows = []
        total = len(rows)
        completed = running = failed = pending = 0

        for r in rows:
            status = r["status"]
            if status == "completed":
                completed += 1
            elif status == "running":
                running += 1
            elif status == "failed":
                failed += 1
            else:
                pending += 1

            result_rows.append({
                "task_index": r["task_index"],
                "task_id": r["task_id"],
                "task_text": r["task_text"][:120],
                "status": status,
                "result": (r["result"] or "")[:200],
                "duration": r["duration"],
                "error": (r["error"] or "")[:200],
            })

        created_at = rows[0]["created_at"] if rows else 0.0

        return Batch(
            batch_id=batch_id,
            total=total,
            completed=completed,
            running=running,
            failed=failed,
            pending=pending,
            results=result_rows,
            created_at=created_at,
        )

    def get_all_batches(self, limit: int = 20) -> list[dict]:
        """获取所有批次摘要。"""
        rows = self._execute(
            """SELECT batch_id,
                      COUNT(*) as total,
                      SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                      SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) as running,
                      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                      MAX(created_at) as created_at
               FROM batch_jobs GROUP BY batch_id
               ORDER BY MAX(created_at) DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        return [dict(r) for r in rows]

    def get_running_batches(self) -> list[dict]:
        """获取正在运行中的批次。"""
        return [
            b for b in self.get_all_batches(limit=50)
            if b["running"] > 0 or b["pending"] > 0
        ]

    # ── 调度循环 ───────────────────────────────────────────

    def _scheduler_loop(self):
        """调度器主循环。

        周期扫描 pending 任务，按信号量限制并发执行。
        """
        while self._running:
            try:
                # 取一个 pending 任务
                row = self._execute(
                    """SELECT id, batch_id, task_index, task_text, task_id
                       FROM batch_jobs WHERE status = 'pending'
                       ORDER BY created_at ASC LIMIT 1"""
                ).fetchone()

                if row:
                    # 标记为 running
                    self._execute(
                        "UPDATE batch_jobs SET status = 'running', updated_at = ? WHERE id = ?",
                        (time.time(), row["id"]),
                    )
                    self.conn.commit()

                    # 在独立线程执行（信号量控制并发）
                    task_data = dict(row)
                    thread = threading.Thread(
                        target=self._execute_task,
                        args=(task_data,),
                        daemon=True,
                        name=f"batch-{task_data['batch_id'][:8]}-{task_data['task_index']}",
                    )
                    thread.start()
                else:
                    # 无待处理任务，休息一会
                    for _ in range(25):
                        if not self._running:
                            return
                        time.sleep(0.2)

            except Exception as e:
                logger.error(f"调度器异常: {e}")
                time.sleep(1)

    def _execute_task(self, task_data: dict):
        """执行单个任务（受信号量保护）。"""
        if not self._semaphore.acquire(timeout=300):
            # 超时获取不到信号量
            self._update_task_result(
                task_data["id"], "failed",
                error="等待执行超时（300秒），系统繁忙",
            )
            return

        try:
            batch_id = task_data["batch_id"]
            task_index = task_data["task_index"]
            task_text = task_data["task_text"]

            logger.info(f"▶️  [{batch_id[:12]}][{task_index}] {task_text[:60]}...")

            start = time.time()
            result = self.agent.run(task_text)
            elapsed = time.time() - start

            success = result.get("success", False)
            output = result.get("result", "") or result.get("summary", "")

            if success:
                self._update_task_result(
                    task_data["id"], "completed",
                    result=output[:1000],
                    duration=elapsed,
                )
                logger.info(f"✅  [{batch_id[:12]}][{task_index}] 完成 ({elapsed:.1f}s)")
            else:
                errors = result.get("errors", [])
                error_msg = "; ".join(errors) if errors else "执行失败"
                self._update_task_result(
                    task_data["id"], "failed",
                    result=output[:500],
                    error=error_msg,
                    duration=elapsed,
                )
                logger.warning(f"❌  [{batch_id[:12]}][{task_index}] 失败: {error_msg}")

        except Exception as e:
            self._update_task_result(
                task_data["id"], "failed",
                error=str(e),
            )
            logger.error(f"❌  [{task_data['batch_id'][:12]}][{task_data['task_index']}] 异常: {e}")

        finally:
            self._semaphore.release()

    def _update_task_result(
        self,
        job_id: int,
        status: str,
        result: str = "",
        error: str = "",
        duration: float = 0.0,
    ):
        self._execute(
            """UPDATE batch_jobs SET
               status = ?, result = ?, error = ?, duration = ?, updated_at = ?
               WHERE id = ?""",
            (status, result[:2000], error[:500], duration, time.time(), job_id),
        )
        self.conn.commit()

    # ── 取消/重试 ──────────────────────────────────────────

    def cancel_batch(self, batch_id: str) -> int:
        """取消批次中所有 pending 任务。"""
        self._execute(
            "UPDATE batch_jobs SET status = 'failed', error = '已取消', updated_at = ? "
            "WHERE batch_id = ? AND status = 'pending'",
            (time.time(), batch_id),
        )
        self.conn.commit()
        return self._execute(
            "SELECT changes()"
        ).fetchone()[0]

    def retry_failed(self, batch_id: str) -> int:
        """重试批次中所有失败任务。"""
        self._execute(
            "UPDATE batch_jobs SET status = 'pending', error = '', result = '', updated_at = ? "
            "WHERE batch_id = ? AND status = 'failed'",
            (time.time(), batch_id),
        )
        self.conn.commit()
        return self._execute(
            "SELECT changes()"
        ).fetchone()[0]

    def clear_batch(self, batch_id: str) -> int:
        """清理批次的所有记录。"""
        self._execute("DELETE FROM batch_jobs WHERE batch_id = ?", (batch_id,))
        self.conn.commit()
        return self._execute("SELECT changes()").fetchone()[0]
