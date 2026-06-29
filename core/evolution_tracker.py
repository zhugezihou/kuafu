"""
core/evolution_tracker.py — SQLite 进化追踪器

将进化追踪数据从 JSON 持久化迁移到 SQLite，提供：
1. 结构化查询：按时间、类型、技能名聚合统计
2. 批量写入：原子事务，WAL 模式避免写冲突
3. 兼容旧接口：保持 EvolutionState 的公开 API 签名

Schema:
  evolution_skills      — 技能版本链 + 适应度历史
  evolution_task_types  — 任务类型执行统计
  evolution_fitness_log — 每次适应度评估的详细记录
  evolution_events      — 进化事件日志
  evolution_errors      — 已知错误库
  evolution_meta        — 键值元数据（兼容 JSON 字段）
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kuafu.evolution_tracker")

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
TRACKER_DB = MEMORY_DIR / "evolution.db"

# ── SQLite Schema ────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 技能版本链
CREATE TABLE IF NOT EXISTS evolution_skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,           -- 技能名（e.g. "pip-install"）
    version     INTEGER NOT NULL DEFAULT 1,
    file_path   TEXT DEFAULT '',
    mode        TEXT DEFAULT 'CAPTURED', -- CAPTURED / FIX / DERIVED
    summary     TEXT DEFAULT '',
    parent      TEXT,                    -- 父版本号或 null
    created_at  REAL NOT NULL DEFAULT (unixepoch()),
    UNIQUE(name, version)
);

-- 技能适应度质量评分历史
CREATE TABLE IF NOT EXISTS evolution_skill_quality (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    score       REAL NOT NULL,           -- 0-1 适应度评分
    metrics     TEXT DEFAULT '{}',       -- JSON 详细指标
    created_at  REAL NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (skill_name, version) REFERENCES evolution_skills(name, version)
);

-- 任务类型执行统计
CREATE TABLE IF NOT EXISTS evolution_task_types (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT NOT NULL UNIQUE,
    count           INTEGER NOT NULL DEFAULT 0,
    consecutive_fail INTEGER NOT NULL DEFAULT 0,
    last_seen       REAL NOT NULL DEFAULT (unixepoch()),
    last_n          TEXT DEFAULT '[]'    -- JSON list of bool
);

-- 每次适应度评估的详细日志
CREATE TABLE IF NOT EXISTS evolution_fitness_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL,
    version     INTEGER DEFAULT 1,
    score       REAL NOT NULL,
    metrics     TEXT DEFAULT '{}',       -- JSON 详细 6 维指标
    success_rate REAL,
    usage_count INTEGER DEFAULT 0,
    step_count  INTEGER DEFAULT 0,
    last_used_days REAL,
    quality_score REAL,
    created_at  REAL NOT NULL DEFAULT (unixepoch())
);

-- 进化事件日志
CREATE TABLE IF NOT EXISTS evolution_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       TEXT DEFAULT 'info',     -- info / skill / warning / error
    action      TEXT NOT NULL,
    target      TEXT DEFAULT '',
    payload     TEXT DEFAULT '',
    success     INTEGER DEFAULT 1,
    created_at  REAL NOT NULL DEFAULT (unixepoch())
);

-- 已知错误库
CREATE TABLE IF NOT EXISTS evolution_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    error_text  TEXT NOT NULL UNIQUE,
    skill_name  TEXT DEFAULT '',         -- 关联的技能名
    count       INTEGER DEFAULT 1,
    first_seen  REAL NOT NULL DEFAULT (unixepoch()),
    last_seen   REAL NOT NULL DEFAULT (unixepoch())
);

-- 键值元数据（存储 schema 版本等兼容字段）
CREATE TABLE IF NOT EXISTS evolution_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_fitness_log_skill ON evolution_fitness_log(skill_name, created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON evolution_events(created_at);
CREATE INDEX IF NOT EXISTS idx_skill_quality_name ON evolution_skill_quality(skill_name, created_at);
CREATE INDEX IF NOT EXISTS idx_skills_name ON evolution_skills(name, version);

-- 技能文件内容快照（用于差异检测和回滚恢复）
CREATE TABLE IF NOT EXISTS evolution_skill_content (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    content     TEXT NOT NULL,           -- 完整文件内容
    content_hash TEXT NOT NULL,          -- SHA256 内容哈希
    file_path   TEXT NOT NULL,           -- 对应的文件路径
    created_at  REAL NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (skill_name, version) REFERENCES evolution_skills(name, version)
);
CREATE INDEX IF NOT EXISTS idx_skill_content_hash ON evolution_skill_content(content_hash);
"""

# ── SQLite 进化追踪器 ───────────────────────────────────────


class EvolutionTracker:
    """SQLite 进化追踪器。

    替代 EvolutionState 的 JSON 持久化，提供结构化查询能力。

    用法：
        tracker = EvolutionTracker()
        tracker.record_result("coding", True)
        tracker.record_skill_evolution("pip-install", "skills/pip.yaml", "CAPTURED", "初始")
        fitness = tracker.evaluate_and_log("pip-install", score=0.82, ...)
    """

    _shared_conn: Optional[sqlite3.Connection] = None
    _shared_db_path: Optional[Path] = None
    _lock = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None, reuse_conn: bool = True):
        self.db_path = db_path or TRACKER_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if reuse_conn and EvolutionTracker._shared_conn is not None:
            if EvolutionTracker._shared_db_path == self.db_path:
                self.conn = EvolutionTracker._shared_conn
                self._owns_conn = False
                return

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._owns_conn = True

        if reuse_conn:
            EvolutionTracker._shared_conn = self.conn
            EvolutionTracker._shared_db_path = self.db_path

        self._init_db()

    def _init_db(self):
        """初始化数据库表结构。"""
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()

    def close(self):
        """关闭连接。"""
        if self._owns_conn and self.conn:
            self.conn.close()

    # ── 事务助手 ──

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def _execute_many(self, sql: str, params_list: list[tuple]):
        self.conn.executemany(sql, params_list)

    # ── 技能版本链 ──

    def record_skill_evolution(
        self,
        skill_name: str,
        file_path: str,
        mode: str = "CAPTURED",
        summary: str = "",
        parent: Optional[str] = None,
    ) -> int:
        """记录一次 skill 进化。

        Returns:
            新版本号
        """
        # 查当前最高版本
        row = self._execute(
            "SELECT COALESCE(MAX(version), 0) as max_v FROM evolution_skills WHERE name = ?",
            (skill_name,),
        ).fetchone()
        new_v = (row["max_v"] if row else 0) + 1

        self._execute(
            """INSERT OR REPLACE INTO evolution_skills
               (name, version, file_path, mode, summary, parent, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (skill_name, new_v, file_path, mode, summary, parent, time.time()),
        )
        self.conn.commit()
        return new_v

    def get_evolution_history(self, skill_name: str) -> list[dict]:
        """获取技能的版本链历史。"""
        rows = self._execute(
            "SELECT * FROM evolution_skills WHERE name = ? ORDER BY version ASC",
            (skill_name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_skills(self) -> dict[str, int]:
        """返回所有 skill 的最新版本。"""
        rows = self._execute(
            """SELECT name, MAX(version) as current
               FROM evolution_skills GROUP BY name"""
        ).fetchall()
        return {r["name"]: r["current"] for r in rows}

    # ── 技能适应度质量 ──

    def record_skill_quality(self, skill_name: str, score: float, metrics: Optional[dict] = None):
        """记录一次技能适应度质量评分。"""
        # 查当前版本
        row = self._execute(
            "SELECT COALESCE(MAX(version), 1) as v FROM evolution_skills WHERE name = ?",
            (skill_name,),
        ).fetchone()
        version = row["v"] if row else 1

        self._execute(
            """INSERT INTO evolution_skill_quality (skill_name, version, score, metrics, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (skill_name, version, score, json.dumps(metrics or {}, ensure_ascii=False), time.time()),
        )
        self.conn.commit()

    def get_skill_quality(self, skill_name: str) -> Optional[list[float]]:
        """获取技能当前版本的质量评分历史。"""
        # 查当前版本
        row = self._execute(
            "SELECT COALESCE(MAX(version), 1) as v FROM evolution_skills WHERE name = ?",
            (skill_name,),
        ).fetchone()
        version = row["v"] if row else 1

        rows = self._execute(
            "SELECT score FROM evolution_skill_quality WHERE skill_name = ? AND version = ? ORDER BY created_at",
            (skill_name, version),
        ).fetchall()
        return [r["score"] for r in rows] if rows else None

    def get_skill_degradation(self, skill_name: str, n: int = 5) -> Optional[float]:
        """检查技能是否退化。"""
        scores = self.get_skill_quality(skill_name)
        if not scores or len(scores) < n * 2:
            return None
        recent = scores[-n:]
        historical = scores[:-n]
        if not historical:
            return None
        return (sum(recent) / len(recent)) - (sum(historical) / len(historical))

    # ── 任务类型统计 ──

    def record_result(self, task_type: str, success: bool):
        """记录一次任务执行结果。"""
        entry = self._execute(
            "SELECT * FROM evolution_task_types WHERE task_type = ?", (task_type,)
        ).fetchone()

        if entry:
            last_n = json.loads(entry["last_n"] or "[]")
            last_n.append(success)
            if len(last_n) > 20:
                last_n = last_n[-20:]
            new_count = entry["count"] + 1
            new_cf = 0 if success else entry["consecutive_fail"] + 1
            self._execute(
                """UPDATE evolution_task_types SET
                   count = ?, consecutive_fail = ?, last_seen = ?, last_n = ?
                   WHERE task_type = ?""",
                (new_count, new_cf, time.time(), json.dumps(last_n, ensure_ascii=False), task_type),
            )
        else:
            self._execute(
                """INSERT INTO evolution_task_types (task_type, count, consecutive_fail, last_seen, last_n)
                   VALUES (?, 1, 0, ?, ?)""",
                (task_type, time.time(), json.dumps([success], ensure_ascii=False)),
            )
        self.conn.commit()

    def is_novel(self, task_type: str) -> bool:
        """判断 task_type 是否首次出现。"""
        row = self._execute(
            "SELECT 1 FROM evolution_task_types WHERE task_type = ?", (task_type,)
        ).fetchone()
        return row is None

    def is_repeated_failure(self, task_type: str, threshold: int = 2) -> bool:
        """判断同类任务是否连续失败 threshold 次以上。"""
        row = self._execute(
            "SELECT consecutive_fail FROM evolution_task_types WHERE task_type = ?",
            (task_type,),
        ).fetchone()
        if not row:
            return False
        return row["consecutive_fail"] >= threshold

    def get_task_type_count(self, task_type: str) -> int:
        """获取某 task_type 的出现次数。"""
        row = self._execute(
            "SELECT count FROM evolution_task_types WHERE task_type = ?",
            (task_type,),
        ).fetchone()
        return row["count"] if row else 0

    def get_recent_failure_rate(self, task_type: str, n: int = 5) -> float:
        """最近 n 次任务中失败的比例 (0-1)。"""
        row = self._execute(
            "SELECT last_n FROM evolution_task_types WHERE task_type = ?",
            (task_type,),
        ).fetchone()
        if not row:
            return 0.0
        last_n = json.loads(row["last_n"] or "[]")[-n:]
        if not last_n:
            return 0.0
        return sum(1 for s in last_n if not s) / len(last_n)

    def get_task_type_stats(self) -> list[dict]:
        """返回所有 task_type 的统计（用于系统 prompt 注入）。"""
        rows = self._execute(
            "SELECT task_type, count, consecutive_fail, last_seen FROM evolution_task_types ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 适应度日志 ──

    def log_fitness(
        self,
        skill_name: str,
        score: float,
        metrics: Optional[dict] = None,
        success_rate: Optional[float] = None,
        usage_count: int = 0,
        step_count: int = 0,
        last_used_days: Optional[float] = None,
        quality_score: Optional[float] = None,
    ):
        """记录一次适应度评估。"""
        # 查当前版本
        row = self._execute(
            "SELECT COALESCE(MAX(version), 1) as v FROM evolution_skills WHERE name = ?",
            (skill_name,),
        ).fetchone()
        version = row["v"] if row else 1

        self._execute(
            """INSERT INTO evolution_fitness_log
               (skill_name, version, score, metrics, success_rate, usage_count,
                step_count, last_used_days, quality_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill_name, version, score, json.dumps(metrics or {}, ensure_ascii=False),
                success_rate, usage_count, step_count, last_used_days, quality_score, time.time(),
            ),
        )
        self.conn.commit()

    def get_fitness_history(self, skill_name: str, limit: int = 50) -> list[dict]:
        """获取技能的适应度历史。"""
        rows = self._execute(
            """SELECT * FROM evolution_fitness_log
               WHERE skill_name = ? ORDER BY created_at DESC LIMIT ?""",
            (skill_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fitness_trend(self, skill_name: str, n: int = 10) -> Optional[dict]:
        """获取适应度趋势（最近 n 次的均值 vs 全部均值）。"""
        rows = self._execute(
            "SELECT score FROM evolution_fitness_log WHERE skill_name = ? ORDER BY created_at",
            (skill_name,),
        ).fetchall()
        if len(rows) < n:
            return None
        scores = [r["score"] for r in rows]
        recent = scores[-n:]
        overall = scores
        return {
            "recent_avg": round(sum(recent) / len(recent), 4),
            "overall_avg": round(sum(overall) / len(overall), 4),
            "trend": "up" if sum(recent) / len(recent) > sum(overall) / len(overall) else "down",
            "samples": len(scores),
        }

    # ── 进化事件 ──

    def record_event(
        self,
        level: str = "info",
        action: str = "",
        target: str = "",
        payload: str = "",
        success: bool = True,
    ):
        """记录一条进化事件。"""
        self._execute(
            """INSERT INTO evolution_events (level, action, target, payload, success, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (level, action, target, str(payload)[:2000], 1 if success else 0, time.time()),
        )
        self.conn.commit()

    def get_recent_events(self, limit: int = 20) -> list[dict]:
        """获取最近的进化事件。"""
        rows = self._execute(
            "SELECT * FROM evolution_events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["success"] = bool(d["success"])
            result.append(d)
        return result

    def get_event_stats(self) -> dict:
        """获取事件统计摘要。"""
        total = self._execute("SELECT COUNT(*) as c FROM evolution_events").fetchone()["c"]
        skill_events = self._execute(
            "SELECT COUNT(*) as c FROM evolution_events WHERE level='skill'"
        ).fetchone()["c"]
        return {"total_events": total, "skill_events": skill_events}

    # ── 错误管理 ──

    def record_error(self, error_text: str, skill_name: str = ""):
        """记录一条错误。"""
        now = time.time()
        try:
            self._execute(
                """INSERT INTO evolution_errors (error_text, skill_name, count, first_seen, last_seen)
                   VALUES (?, ?, 1, ?, ?)
                   ON CONFLICT(error_text) DO UPDATE SET
                       count = count + 1,
                       skill_name = CASE WHEN ? != '' THEN ? ELSE skill_name END,
                       last_seen = ?""",
                (error_text, skill_name, now, now, skill_name, skill_name, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            # 并发写入冲突，安全忽略
            pass

    def is_known_error(self, error_text: str) -> bool:
        """判断是否已知错误（模糊匹配）。"""
        if not error_text:
            return False
        words_err = set(error_text.lower().split())
        rows = self._execute(
            "SELECT error_text FROM evolution_errors"
        ).fetchall()
        for r in rows:
            words_known = set(r["error_text"].lower().split())
            if len(words_known) > 3:
                overlap = words_err.intersection(words_known)
                if len(overlap) >= min(3, len(words_known) // 2):
                    return True
        return False

    def get_error_count(self) -> int:
        """获取已知错误总数。"""
        row = self._execute("SELECT COUNT(*) as c FROM evolution_errors").fetchone()
        return row["c"] if row else 0

    def get_top_errors(self, limit: int = 10) -> list[dict]:
        """获取出现次数最多的错误。"""
        rows = self._execute(
            "SELECT error_text, count, skill_name FROM evolution_errors ORDER BY count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 元数据 ──

    def set_meta(self, key: str, value: str):
        self._execute(
            "INSERT OR REPLACE INTO evolution_meta (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self._execute("SELECT value FROM evolution_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # ── 综合统计 ──

    def get_stats(self, include_recent_events: bool = True) -> dict:
        """获取统计摘要。"""
        skills = self.get_all_skills()
        task_types = self._execute("SELECT COUNT(*) as c FROM evolution_task_types").fetchone()["c"]

        # 近 24 小时活动
        day_ago = time.time() - 86400
        recent_fitness = self._execute(
            "SELECT COUNT(*) as c FROM evolution_fitness_log WHERE created_at >= ?", (day_ago,)
        ).fetchone()["c"]
        recent_events = self._execute(
            "SELECT COUNT(*) as c FROM evolution_events WHERE created_at >= ?", (day_ago,)
        ).fetchone()["c"]

        event_stats = self.get_event_stats()
        error_count = self.get_error_count()

        result = {
            "total_skills": len(skills),
            "total_task_types": task_types,
            "known_errors": error_count,
            "total_events": event_stats["total_events"],
            "skill_events": event_stats["skill_events"],
            "recent_24h": {
                "fitness_evals": recent_fitness,
                "events": recent_events,
            },
        }

        if include_recent_events:
            result["recent_events"] = self.get_recent_events(limit=10)

        return result

    def undo_last_skill_evolution(self, skill_name: str) -> Optional[dict]:
        """回滚 skill 的最新版本。

        从 evolution_skill_content 表取上一版本内容回写文件，
        然后清理版本记录。

        Returns:
            {"rolled_back_v": int, "restored_to_v": int,
             "file_restored": str or None} 或 None
        """
        versions = self._execute(
            "SELECT * FROM evolution_skills WHERE name = ? ORDER BY version DESC LIMIT 2",
            (skill_name,),
        ).fetchall()
        if len(versions) < 2:
            return None

        latest = versions[0]
        previous = versions[1]

        # 尝试从 evolution_skill_content 表恢复上一版本的文件内容
        file_restored = None
        try:
            content_row = self._execute(
                "SELECT content, file_path FROM evolution_skill_content "
                "WHERE skill_name = ? AND version = ? ORDER BY created_at DESC LIMIT 1",
                (skill_name, previous["version"]),
            ).fetchone()
            if content_row:
                file_path = Path(self.db_path.parent.parent / content_row["file_path"])
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content_row["content"], encoding="utf-8")
                file_restored = str(file_path)
            else:
                # 没有内容快照，尝试检查文件系统上是否还保留着旧文件
                old_path = Path(self.db_path.parent.parent / previous["file_path"])
                if old_path.exists():
                    file_restored = str(old_path)
        except Exception as e:
            logger.warning(f"技能回滚: 文件恢复失败 ({e})，仅清理版本记录")

        # 删除最新版本
        self._execute(
            "DELETE FROM evolution_skills WHERE name = ? AND version = ?",
            (skill_name, latest["version"]),
        )
        # 删除对应的 fitness 日志
        self._execute(
            "DELETE FROM evolution_fitness_log WHERE skill_name = ? AND version = ?",
            (skill_name, latest["version"]),
        )
        self.conn.commit()

        return {
            "rolled_back_v": latest["version"],
            "restored_to_v": previous["version"],
            "file_restored": file_restored,
        }

    # ── 退化检测 ────────────────────────────────────────────

    DEGRADATION_WARNING_WINDOW = 5       # 最近几次评估的窗口
    DEGRADATION_THRESHOLD = -0.10        # 退化幅度超过 -0.10 触发 warning
    CRITICAL_THRESHOLD = -0.25           # 超过 -0.25 触发 critical
    LOW_USAGE_THRESHOLD = 3              # 连续 3 次评分 <= 上一版均值视为使用率退化
    TASK_FAILURE_RATE_THRESHOLD = 0.4    # 任务失败率超过 40% 视为退化信号

    def detect_degradation(self, skill_name: str,
                           recent_task_failures: Optional[list[bool]] = None) -> Optional[dict]:
        """检测技能是否退化。

        多信号综合判定：
        1. fitness 趋势 — 最近 N 次评分 vs 历史均值的下降幅度
        2. quality 评分趋势 — skill_quality 表的评分均值变化
        3. 使用率退化 — 高频使用技能突然使用率下降
        4. 任务失败率 — 使用该技能的任务失败率是否升高

        Args:
            skill_name: 技能名
            recent_task_failures: 可选，最近使用该技能的任务成功/失败列表

        Returns:
            {
                "degraded": bool,
                "severity": "none" | "warning" | "critical",
                "signals": [str, ...],           # 触发的信号描述
                "fitness_drop": float,            # 适应度下降幅度
                "current_version": int,
                "best_version": Optional[int],     # 历史最佳版本
                "suggested_action": str,           # 建议操作
            } 或 None（无退化或数据不足）
        """
        signals = []
        max_drop = 0.0
        current_version = self._get_current_version(skill_name)

        # ── 信号 1: fitness 趋势退化 ──
        trend = self.get_fitness_trend(skill_name, n=self.DEGRADATION_WARNING_WINDOW)
        if trend is not None:
            drop = trend["overall_avg"] - trend["recent_avg"]
            max_drop = max(max_drop, drop)
            if drop >= abs(self.CRITICAL_THRESHOLD):
                signals.append(f"fitness 严重下降 ({drop:.3f})")
            elif drop >= abs(self.DEGRADATION_THRESHOLD):
                signals.append(f"fitness 下降 ({drop:.3f})")

        # ── 信号 2: quality 评分退化 ──
        quality_drop = self.get_skill_degradation(skill_name, n=self.DEGRADATION_WARNING_WINDOW)
        if quality_drop is not None and quality_drop < 0:
            max_drop = max(max_drop, abs(quality_drop))
            if abs(quality_drop) >= abs(self.CRITICAL_THRESHOLD):
                signals.append(f"quality 严重退化 ({quality_drop:.3f})")
            elif abs(quality_drop) >= abs(self.DEGRADATION_THRESHOLD):
                signals.append(f"quality 退化 ({quality_drop:.3f})")

        # ── 信号 3: 任务失败率升高 ──
        if recent_task_failures and len(recent_task_failures) >= 3:
            fail_rate = sum(1 for s in recent_task_failures if not s) / len(recent_task_failures)
            if fail_rate >= self.TASK_FAILURE_RATE_THRESHOLD:
                signals.append(f"任务失败率 {fail_rate:.0%} 超过阈值")

        # ── 确定严重度 ──
        if not signals:
            return None

        if max_drop >= abs(self.CRITICAL_THRESHOLD):
            severity = "critical"
        elif max_drop >= abs(self.DEGRADATION_THRESHOLD):
            severity = "warning"
        else:
            severity = "warning"

        # ── 找历史最佳版本 ──
        best_version = self._find_best_version(skill_name)

        # ── 建议操作 ──
        suggested_action = self._suggest_action(severity, skill_name, current_version, best_version)

        return {
            "degraded": True,
            "severity": severity,
            "signals": signals,
            "fitness_drop": round(max_drop, 4),
            "current_version": current_version,
            "best_version": best_version,
            "suggested_action": suggested_action,
        }

    def detect_all_degradations(self) -> list[dict]:
        """扫描所有技能，返回有退化信号的列表。"""
        results = []
        for name in self.get_all_skills():
            result = self.detect_degradation(name)
            if result:
                results.append({
                    "skill_name": name,
                    **result,
                })
        return results

    def _get_current_version(self, skill_name: str) -> int:
        """获取技能当前版本号。"""
        row = self._execute(
            "SELECT COALESCE(MAX(version), 0) as v FROM evolution_skills WHERE name = ?",
            (skill_name,),
        ).fetchone()
        return row["v"] if row else 0

    def _find_best_version(self, skill_name: str) -> Optional[int]:
        """从 fitness 日志中找历史最优版本。"""
        rows = self._execute(
            """SELECT fl.version, AVG(fl.score) as avg_score
               FROM evolution_fitness_log fl
               WHERE fl.skill_name = ?
               GROUP BY fl.version
               ORDER BY avg_score DESC LIMIT 1""",
            (skill_name,),
        ).fetchall()
        if rows and rows[0]["version"]:
            return rows[0]["version"]
        return None

    def _suggest_action(self, severity: str, skill_name: str,
                        current_version: int, best_version: Optional[int]) -> str:
        """根据严重度建议操作。"""
        if severity == "critical" and best_version and best_version < current_version:
            return (f"建议立即回滚到 v{best_version}（kuafu skill restore {skill_name} {best_version}），"
                    f"同时执行 kuafu skill scan 更新版本链")
        elif severity == "warning":
            if best_version and best_version < current_version:
                return f"建议评估后手动回滚到 v{best_version}"
            return "建议监控后续评分趋势"
        return ""

    # ── 退化回滚 ──

    def auto_rollback(self, skill_name: str,
                      skills_dir: Optional[Path] = None) -> Optional[dict]:
        """自动回滚技能到历史最佳版本。

        流程：
        1. 检测退化
        2. 如果 severity >= warning：
           a. 找到历史最佳版本
           b. 备份当前文件
           c. 从 SQLite 恢复最佳版本到磁盘
           d. 记录回滚事件

        Args:
            skill_name: 技能名
            skills_dir: skills 目录

        Returns:
            {
                "rolled_back": bool,
                "from_version": int,
                "to_version": int,
                "severity": str,
                "backup_file": str,        # 当前文件的备份路径
                "signals": [str, ...],
            } 或 None（无需回滚）
        """
        degradation = self.detect_degradation(skill_name)
        if not degradation or degradation["severity"] == "none":
            return None

        severity = degradation["severity"]
        best_version = degradation.get("best_version")
        current_version = degradation["current_version"]

        if best_version is None or best_version >= current_version:
            return None

        if skills_dir is None:
            skills_dir = self._get_project_root() / "skills"

        # 备份当前文件
        current_file = skills_dir / f"{skill_name}.yaml"
        backup_path = None
        if current_file.exists():
            import shutil
            backup_path = str(current_file) + f".rollback.bak.v{current_version}"
            shutil.copy2(str(current_file), backup_path)

        # 恢复最佳版本
        restored = self.restore_skill_file(skill_name, best_version, skills_dir)
        if not restored:
            return None

        # 记录回滚事件
        self.record_event(
            level="warning" if severity == "warning" else "critical",
            action=f"自动回滚 {skill_name} v{current_version} → v{best_version}（{severity}）",
            target=skill_name,
            payload=f"signals: {'; '.join(degradation['signals'])}",
        )

        return {
            "rolled_back": True,
            "from_version": current_version,
            "to_version": best_version,
            "severity": severity,
            "backup_file": backup_path,
            "signals": degradation["signals"],
        }

    def auto_rollback_all(self, skills_dir: Optional[Path] = None) -> list[dict]:
        """扫描所有技能并执行自动回滚。"""
        results = []
        for name in self.get_all_skills():
            result = self.auto_rollback(name, skills_dir)
            if result:
                results.append({"skill_name": name, **result})
        return results

    # ── 技能文件内容管理 ─────────────────────────────────

    def record_skill_content(
        self,
        skill_name: str,
        content: str,
        file_path: str,
        version: Optional[int] = None,
    ) -> int:
        """记录技能文件的内容快照。

        自动计算 SHA256 哈希，检测是否已有相同内容的版本。

        Args:
            skill_name: 技能名
            content: 完整文件内容
            file_path: 文件路径（相对于项目根目录）
            version: 指定版本号，None 自动取当前最新 +1

        Returns:
            记录成功的版本号，或 -1（内容重复，无变更）
        """
        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # 检查是否已有相同哈希的内容
        existing = self._execute(
            "SELECT version FROM evolution_skill_content WHERE skill_name = ? AND content_hash = ?",
            (skill_name, content_hash),
        ).fetchone()
        if existing:
            return -1  # 内容相同，跳过

        if version is None:
            row = self._execute(
                "SELECT COALESCE(MAX(version), 0) as max_v FROM evolution_skills WHERE name = ?",
                (skill_name,),
            ).fetchone()
            version = (row["max_v"] if row else 0) + 1

        self._execute(
            """INSERT INTO evolution_skill_content
               (skill_name, version, content, content_hash, file_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (skill_name, version, content, content_hash, file_path, time.time()),
        )
        self.conn.commit()
        return version

    def get_skill_content(self, skill_name: str, version: Optional[int] = None) -> Optional[str]:
        """获取技能文件的内容。

        Args:
            skill_name: 技能名
            version: 指定版本，None 取最新版本

        Returns:
            文件内容字符串，或 None（不存在）
        """
        if version is not None:
            row = self._execute(
                "SELECT content FROM evolution_skill_content WHERE skill_name = ? AND version = ?",
                (skill_name, version),
            ).fetchone()
        else:
            row = self._execute(
                """SELECT sc.content FROM evolution_skill_content sc
                   JOIN evolution_skills s ON s.name = sc.skill_name AND s.version = sc.version
                   WHERE sc.skill_name = ?
                   ORDER BY sc.version DESC LIMIT 1""",
                (skill_name,),
            ).fetchone()
        return row["content"] if row else None

    def diff_skill_versions(self, skill_name: str, v1: int, v2: int) -> Optional[str]:
        """比较技能两个版本的内容差异。"""
        c1 = self.get_skill_content(skill_name, v1)
        c2 = self.get_skill_content(skill_name, v2)
        if c1 is None or c2 is None:
            return None
        if c1 == c2:
            return "(内容相同)"

        # 简单行差异比较
        lines1 = c1.splitlines()
        lines2 = c2.splitlines()
        diff_lines = []
        max_len = max(len(lines1), len(lines2))

        for i in range(max_len):
            l1 = lines1[i] if i < len(lines1) else ""
            l2 = lines2[i] if i < len(lines2) else ""
            if l1 != l2:
                if l1 and not l2:
                    diff_lines.append(f"- [{i+1}] {l1}")
                elif not l1 and l2:
                    diff_lines.append(f"+ [{i+1}] {l2}")
                else:
                    diff_lines.append(f"- [{i+1}] {l1}")
                    diff_lines.append(f"+ [{i+1}] {l2}")

        return "\n".join(diff_lines[:30]) if diff_lines else "(无差异)"

    # ── 技能文件扫描 ─────────────────────────────────────

    def _get_project_root(self) -> Path:
        """获取项目根目录。"""
        return Path(__file__).resolve().parent.parent

    def scan_skills_directory(self, skills_dir: Optional[Path] = None) -> dict:
        """扫描 skills/ 目录，自动建立版本链。

        遍历所有 *.yaml 文件，为每个技能：
        1. 如果文件内容与 SQLite 中最新内容一致 → 跳过
        2. 如果文件内容有变更 → 记录新版本 + 内容快照
        3. 如果是新技能（SQLite 中不存在）→ 创建初始版本

        Args:
            skills_dir: skills 目录，默认项目根下的 skills/

        Returns:
            {
                "scanned": int,        # 扫描的文件数
                "new": int,            # 新增技能数
                "updated": int,        # 更新技能数
                "unchanged": int,      # 未变更技能数
                "details": [...],      # 每个文件的扫描结果
            }
        """
        if skills_dir is None:
            skills_dir = self._get_project_root() / "skills"

        if not skills_dir.exists():
            return {"scanned": 0, "new": 0, "updated": 0, "unchanged": 0, "details": []}

        result = {"scanned": 0, "new": 0, "updated": 0, "unchanged": 0, "details": []}
        import hashlib
        import yaml

        for fp in sorted(skills_dir.glob("*.yaml")):
            result["scanned"] += 1

            try:
                content = fp.read_text(encoding="utf-8")
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                root = self._get_project_root()
                try:
                    relative_path = str(fp.relative_to(root))
                except ValueError:
                    relative_path = fp.name

                # 从 yaml 提取技能名
                data = yaml.safe_load(content)
                if not data:
                    continue
                skill_name = data.get("name", "") or fp.stem

                # 检查 SQLite 中最新内容是否匹配
                latest_row = self._execute(
                    """SELECT sc.content_hash, sc.version FROM evolution_skill_content sc
                       JOIN evolution_skills s ON s.name = sc.skill_name AND s.version = sc.version
                       WHERE sc.skill_name = ? ORDER BY sc.version DESC LIMIT 1""",
                    (skill_name,),
                ).fetchone()

                if latest_row and latest_row["content_hash"] == content_hash:
                    # 内容未变更
                    result["unchanged"] += 1
                    result["details"].append({
                        "name": skill_name, "file": relative_path,
                        "status": "unchanged", "version": latest_row["version"],
                    })
                    continue

                if latest_row:
                    # 内容有变更 → 新版本
                    new_v = latest_row["version"] + 1
                    self._execute(
                        """INSERT INTO evolution_skills
                           (name, version, file_path, mode, summary, parent, created_at)
                           VALUES (?, ?, 'SCAN', 'DERIVED', ?, ?, ?)""",
                        (skill_name, new_v, f"内容变更（自动检测）", str(latest_row["version"]), time.time()),
                    )
                    self.record_skill_content(skill_name, content, relative_path, version=new_v)
                    result["updated"] += 1
                    result["details"].append({
                        "name": skill_name, "file": relative_path,
                        "status": "updated", "version": new_v,
                    })
                else:
                    # 新技能
                    self._execute(
                        """INSERT INTO evolution_skills
                           (name, version, file_path, mode, summary, parent, created_at)
                           VALUES (?, 1, ?, 'CAPTURED', '初始扫描', NULL, ?)""",
                        (skill_name, relative_path, time.time()),
                    )
                    self.record_skill_content(skill_name, content, relative_path, version=1)
                    result["new"] += 1
                    result["details"].append({
                        "name": skill_name, "file": relative_path,
                        "status": "new", "version": 1,
                    })

                self.conn.commit()

            except Exception as e:
                result["details"].append({
                    "file": fp.name, "status": "error", "error": str(e),
                })

        return result

    def restore_skill_file(self, skill_name: str, version: int,
                           skills_dir: Optional[Path] = None) -> bool:
        """从 SQLite 恢复技能文件的指定版本。

        Args:
            skill_name: 技能名
            version: 要恢复的版本号
            skills_dir: skills 目录

        Returns:
            是否成功
        """
        content = self.get_skill_content(skill_name, version)
        if content is None:
            return False

        if skills_dir is None:
            skills_dir = self._get_project_root() / "skills"

        skills_dir.mkdir(parents=True, exist_ok=True)
        fp = skills_dir / f"{skill_name}.yaml"
        fp.write_text(content, encoding="utf-8")
        return True


# ── JSON 兼容桥 ────────────────────────────────────────────


class JSONCompatibleTracker(EvolutionTracker):
    """JSON 兼容的追踪器封装，提供与旧 EvolutionState 相同的接口。

    所有接口保持与 EvolutionState 相同的签名和行为，
    内部使用 SQLite 存储。
    """

    MAX_FAILURE_HISTORY = 20
    MAX_KNOWN_ERRORS = 200

    def health_check(self) -> Optional[str]:
        """运行自检，返回问题描述或 None。
        
        过滤掉 generic 类型（兜底分类，大量非失败场景也会归入）。
        """
        warnings = []
        rows = self._execute(
            "SELECT task_type, consecutive_fail FROM evolution_task_types WHERE consecutive_fail >= 3"
        ).fetchall()
        for r in rows:
            if r["task_type"] == "generic":
                continue
            warnings.append(f"[{r['task_type']}] 连续失败 {r['consecutive_fail']} 次")
        return "; ".join(warnings) if warnings else None

    def get_recent_failure_rate(self, task_type: str, n: int = 5) -> float:
        return super().get_recent_failure_rate(task_type, n)

    def associate_error_with_skill(self, error_fragment: str, skill_name: str):
        """弃用：错误关联已内置于 evolution_errors 表。"""
        pass

    def get_skill_for_error(self, error_text: str) -> Optional[str]:
        """根据错误文本查找关联的 skill。"""
        rows = self._execute(
            "SELECT skill_name, error_text FROM evolution_errors"
        ).fetchall()
        err_lower = error_text.lower()
        best_overlap = 0
        best_skill = None
        for r in rows:
            fragment = r["error_text"].lower()
            if fragment in err_lower:
                return r["skill_name"] or None
            frag_words = set(fragment.split())
            err_words = set(err_lower.split())
            overlap = len(err_words.intersection(frag_words))
            if overlap > best_overlap:
                best_overlap = overlap
                best_skill = r["skill_name"] or None
        if best_overlap >= 2:
            return best_skill
        return None

    def get_all_skill_errors(self) -> dict[str, list[str]]:
        """返回 skill → 关联错误列表。"""
        result: dict[str, list[str]] = {}
        rows = self._execute(
            "SELECT error_text, skill_name FROM evolution_errors WHERE skill_name != ''"
        ).fetchall()
        for r in rows:
            result.setdefault(r["skill_name"], []).append(r["error_text"])
        return result
