"""
core/workflow/persistence.py — 工作流持久化层（SQLite）

职责：
  1. 保存工作流运行时状态
  2. 恢复崩溃/重启后的工作流
  3. 历史查询

设计参考: Temporal 的 Event History 概念
  - 每条状态变更附加到 Event Log
  - 可通过 Event Log 完全重建运行时状态

零外部依赖，使用内置 sqlite3。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from core.workflow.models import (
    NodeRuntime,
    NodeStatus,
    WorkflowDef,
    WorkflowRuntime,
    WorkflowStatus,
)


class WorkflowStore:
    """工作流持久化存储。

    SQLite 后端，线程安全。
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent.parent / "memory" / "workflow.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id   TEXT PRIMARY KEY,
                workflow_def  TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                input_data    TEXT DEFAULT '{}',
                output_data   TEXT DEFAULT '{}',
                error         TEXT DEFAULT '',
                created_at    REAL NOT NULL,
                started_at    REAL DEFAULT 0,
                completed_at  REAL DEFAULT 0,
                updated_at    REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS workflow_nodes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id   TEXT NOT NULL,
                node_id       TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                result        TEXT DEFAULT '',
                error         TEXT DEFAULT '',
                started_at    REAL DEFAULT 0,
                completed_at  REAL DEFAULT 0,
                attempts      INTEGER DEFAULT 0,
                output        TEXT DEFAULT '{}',
                FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id),
                UNIQUE(workflow_id, node_id)
            );

            CREATE TABLE IF NOT EXISTS workflow_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id   TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                node_id       TEXT DEFAULT '',
                data          TEXT DEFAULT '{}',
                created_at    REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id)
            );

            CREATE INDEX IF NOT EXISTS idx_wn_wfid ON workflow_nodes(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_we_wfid ON workflow_events(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_we_type ON workflow_events(event_type);
        """)
        conn.commit()

    # ═══════════════════════════════════════════════════════════
    # 保存
    # ═══════════════════════════════════════════════════════════

    def save_workflow(self, rt: WorkflowRuntime):
        """保存/更新工作流运行时。"""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO workflows
               (workflow_id, workflow_def, status, input_data, output_data, error,
                created_at, started_at, completed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rt.workflow_id,
                json.dumps(rt.workflow_def.to_dict(), ensure_ascii=False),
                rt.status.value,
                json.dumps(rt.input_data, ensure_ascii=False),
                json.dumps(rt.output_data, ensure_ascii=False),
                rt.error,
                rt.created_at,
                rt.started_at,
                rt.completed_at,
                time.time(),
            ),
        )

        for node_id, nr in rt.nodes.items():
            conn.execute(
                """INSERT OR REPLACE INTO workflow_nodes
                   (workflow_id, node_id, status, result, error,
                    started_at, completed_at, attempts, output)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rt.workflow_id,
                    node_id,
                    nr.status.value,
                    json.dumps(nr.result) if nr.result is not None else "",
                    nr.error,
                    nr.started_at,
                    nr.completed_at,
                    nr.attempts,
                    json.dumps(nr.output, ensure_ascii=False),
                ),
            )

        conn.commit()

    def log_event(
        self,
        workflow_id: str,
        event_type: str,
        node_id: str = "",
        data: dict | None = None,
    ):
        """记录事件到 Event Log。"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO workflow_events (workflow_id, event_type, node_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                workflow_id,
                event_type,
                node_id,
                json.dumps(data or {}, ensure_ascii=False),
                time.time(),
            ),
        )
        conn.commit()

    # ═══════════════════════════════════════════════════════════
    # 读取
    # ═══════════════════════════════════════════════════════════

    def load_workflow(self, workflow_id: str) -> Optional[WorkflowRuntime]:
        """从 SQLite 恢复工作流运行时。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()

        if not row:
            return None

        wf_def = WorkflowDef.from_dict(json.loads(row["workflow_def"]))
        rt = WorkflowRuntime(
            workflow_id=row["workflow_id"],
            workflow_def=wf_def,
            status=WorkflowStatus(row["status"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            input_data=json.loads(row["input_data"] or "{}"),
            output_data=json.loads(row["output_data"] or "{}"),
            error=row["error"] or "",
        )

        # 恢复节点状态
        node_rows = conn.execute(
            "SELECT * FROM workflow_nodes WHERE workflow_id = ?", (workflow_id,)
        ).fetchall()

        for nr in node_rows:
            rt.nodes[nr["node_id"]] = NodeRuntime(
                node_id=nr["node_id"],
                status=NodeStatus(nr["status"]),
                result=json.loads(nr["result"]) if nr["result"] else None,
                error=nr["error"] or "",
                started_at=nr["started_at"],
                completed_at=nr["completed_at"],
                attempts=nr["attempts"],
                output=json.loads(nr["output"] or "{}"),
            )

        return rt

    def list_workflows(
        self,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> list[dict]:
        """列出工作流记录。"""
        conn = self._get_conn()
        if status:
            rows = conn.execute(
                "SELECT workflow_id, status, created_at, completed_at, error FROM workflows "
                "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT workflow_id, status, created_at, completed_at, error FROM workflows "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [dict(r) for r in rows]

    def get_events(
        self,
        workflow_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """获取工作流的事件日志。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY id ASC LIMIT ?",
            (workflow_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
