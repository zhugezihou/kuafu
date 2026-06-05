"""
core/evolution_state.py — 增量进化状态管理（SQLite 后端）

职责：
通过 EvolutionTracker（SQLite）提供：
1. task_type 计数器（总次数、连续失败、最近 N 次成功/失败）
2. 已知错误库（模糊匹配去重）
3. 最近 N 次任务的简要记录
4. 判断接口：is_novel、is_repeated_failure、is_unknown_error
5. skill 版本链追踪（FIX/DERIVED/CAPTURED 记录）
6. 错误→skill 关联（错误归属哪个 skill）
7. skill 质量历史（每次执行后的评分）
8. 版本回滚（undo_last_evolution）

设计原则：
- SQLite WAL 模式，原子写入，零数据损坏风险
- 完全兼容旧接口签名
- 结构化查询替代 JSON 文件扫描
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from core.evolution_tracker import JSONCompatibleTracker

logger = __import__("logging").getLogger("kuafu.evolution_state")

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = "memory/.evolution_state.json"


class EvolutionState:
    """增量进化状态管理（SQLite 后端）。

    兼容旧接口：所有方法签名与之前一致。
    """

    MAX_FAILURE_HISTORY = 20
    MAX_KNOWN_ERRORS = 200

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = root_dir or ROOT_DIR
        self.state_path = self.root_dir / STATE_FILE

        # SQLite 后端（兼容旧接口）
        db_path = self.root_dir / "memory" / "evolution.db"
        self._db = JSONCompatibleTracker(db_path=db_path)

        # 从旧 JSON 迁移数据（仅首次）
        self._maybe_migrate_from_json()

    def _maybe_migrate_from_json(self):
        """（首次启动时）将旧 JSON 数据迁移到 SQLite。"""
        if not self.state_path.exists():
            return
        # 检查是否已迁移过
        if self._db.get_meta("migrated_from_json", "") == "true":
            return

        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._migrate_data(data)
            self._db.set_meta("migrated_from_json", "true")
            # 重命名旧文件
            import os
            os.rename(str(self.state_path), str(self.state_path) + ".bak")
            logger.info(f"进化数据已从 JSON 迁移到 SQLite, 旧文件已备份为 {self.state_path}.bak")
        except Exception as e:  # pragma: no cover
            logger.warning(f"JSON 迁移失败（跳过）: {e}")  # pragma: no cover

    def _migrate_data(self, data: dict):
        """将 JSON 数据写入 SQLite。"""
        # task_types
        for tt, entry in data.get("task_types", {}).items():
            for _ in range(entry.get("count", 0)):
                # 只记录最后 N 条
                pass
            self._db._execute(
                """INSERT OR REPLACE INTO evolution_task_types
                   (task_type, count, consecutive_fail, last_seen, last_n)
                   VALUES (?, ?, ?, ?, ?)""",
                (tt, entry.get("count", 0), entry.get("consecutive_fail", 0),
                 entry.get("last_seen", time.time()),
                 json.dumps(entry.get("last_n", []), ensure_ascii=False)),
            )

        # errors
        for err in data.get("known_errors", []):
            self._db._execute(
                """INSERT OR IGNORE INTO evolution_errors
                   (error_text, count, first_seen, last_seen)
                   VALUES (?, 1, ?, ?)""",
                (err, time.time(), time.time()),
            )

        # skills
        for name, entry in data.get("skills", {}).items():
            for ver in entry.get("versions", []):
                self._db._execute(
                    """INSERT OR IGNORE INTO evolution_skills
                       (name, version, file_path, mode, summary, parent, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, ver.get("v", 1), ver.get("file", ""),
                     ver.get("mode", "CAPTURED"), ver.get("summary", ""),
                     ver.get("parent"), ver.get("created", time.time())),
                )
                quality_scores = ver.get("quality", [])
                for score in quality_scores:
                    self._db._execute(
                        """INSERT INTO evolution_skill_quality
                           (skill_name, version, score, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (name, ver.get("v", 1), score, time.time()),
                    )

        # error_to_skill（写入 errors 表的 skill_name 字段）
        for fragment, skill_name in data.get("error_to_skill", {}).items():
            self._db._execute(
                """UPDATE evolution_errors SET skill_name = ?
                   WHERE error_text = ?""",
                (skill_name, fragment),
            )

        self._db.conn.commit()

    # ── 公开接口（全部兼容旧签名） ──

    def record_result(self, task_type: str, success: bool):
        self._db.record_result(task_type, success)

    def record_error(self, error_text: str):
        self._db.record_error(error_text)

    def is_novel(self, task_type: str) -> bool:
        return self._db.is_novel(task_type)

    def is_repeated_failure(self, task_type: str, threshold: int = 2) -> bool:
        return self._db.is_repeated_failure(task_type, threshold)

    def is_unknown_error(self, error_text: str) -> bool:
        return not self._db.is_known_error(error_text)

    def get_task_type_count(self, task_type: str) -> int:
        return self._db.get_task_type_count(task_type)

    def get_recent_failure_rate(self, task_type: str, n: int = 5) -> float:
        return self._db.get_recent_failure_rate(task_type, n)

    def get_stats(self) -> dict:
        db_stats = self._db.get_stats(include_recent_events=False)
        # 组装为 JSON 风格的 stats
        return {
            "total_types": db_stats["total_task_types"],
            "types": [
                dict(r) for r in self._db._execute(
                    "SELECT task_type as name, count, consecutive_fail, last_seen "
                    "FROM evolution_task_types ORDER BY count DESC"
                ).fetchall()
            ],
        }

    def health_check(self) -> Optional[str]:
        return self._db.health_check()

    # ── skill 管理（兼容旧接口） ──

    def record_skill_evolution(
        self,
        skill_name: str,
        file_path: str,
        mode: str,
        summary: str,
        parent: Optional[str] = None,
        quality_score: Optional[float] = None,
    ) -> int:
        return self._db.record_skill_evolution(
            skill_name, file_path, mode, summary, parent
        )

    def record_skill_quality(self, skill_name: str, score: float) -> bool:
        """记录技能质量评分。兼容旧接口返回 bool。"""
        try:
            self._db.record_skill_quality(skill_name, score)
            return True
        except Exception:
            return False

    def get_skill_quality(self, skill_name: str) -> Optional[list]:
        return self._db.get_skill_quality(skill_name)

    def get_skill_degradation(self, skill_name: str, n: int = 5) -> Optional[float]:
        return self._db.get_skill_degradation(skill_name, n)

    def undo_last_evolution(self, skill_name: str) -> Optional[dict]:
        return self._db.undo_last_skill_evolution(skill_name)

    def get_evolution_history(self, skill_name: str) -> Optional[list]:
        return self._db.get_evolution_history(skill_name)

    def get_all_skills(self) -> dict:
        return self._db.get_all_skills()

    # ── 错误关联（兼容旧接口） ──

    def associate_error_with_skill(self, error_fragment: str, skill_name: str):
        self._db.associate_error_with_skill(error_fragment, skill_name)

    def get_skill_for_error(self, error_text: str) -> Optional[str]:
        return self._db.get_skill_for_error(error_text)

    def get_all_skill_errors(self) -> dict[str, list[str]]:
        return self._db.get_all_skill_errors()
