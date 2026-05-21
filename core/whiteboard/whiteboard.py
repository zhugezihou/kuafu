"""白板 (Whiteboard) — Agent 外部推理状态存储。

分区说明：
  - current_state:   当前正在执行的步骤状态
  - completed:       已完成的任务/步骤列表
  - next_plan:       下一步执行计划
  - intermediate:    中间结果库（代码输出、搜索结果等）
  - excluded_paths:  已排除的路径/方法
  - hypotheses:      待验证假设
"""

import json
import time
from pathlib import Path
from typing import Any, Optional


class Whiteboard:
    """外部白板 — 不消耗 LLM 上下文的信息存储。"""

    def __init__(self, work_dir: Optional[Path] = None):
        self.work_dir = (work_dir or Path.cwd()) / ".whiteboard"
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # 分区文件名映射
        self._partitions = {
            "current_state": "current_state.json",
            "completed": "completed.json",
            "next_plan": "next_plan.json",
            "intermediate": "intermediate.json",
            "excluded_paths": "excluded_paths.json",
            "hypotheses": "hypotheses.json",
        }

        # 内存缓存（减少磁盘 I/O）
        self._cache: dict[str, Any] = {}

        # 初始化所有分区
        for name, filename in self._partitions.items():
            path = self.work_dir / filename
            if not path.exists():
                self._write_partition(name, [])

    # ── 核心读写接口 ───────────────────────────────────────────────

    def read(self, partition: str) -> list[dict]:
        """读取一个分区的全部内容。"""
        return list(self._read_partition(partition))

    def write(self, partition: str, data: list[dict]) -> None:
        """覆写一个分区的全部内容。"""
        self._write_partition(partition, data)

    def append(self, partition: str, entry: dict) -> None:
        """追加一条记录到分区。"""
        data = self._read_partition(partition)
        entry["_timestamp"] = time.time()
        entry["_id"] = f"{partition}_{len(data)}_{int(time.time() * 1000)}"
        data.append(entry)
        self._write_partition(partition, data)

    def get(self, partition: str, entry_id: str) -> Optional[dict]:
        """按 ID 获取某条记录。"""
        for entry in self._read_partition(partition):
            if entry.get("_id") == entry_id:
                return entry
        return None

    def update(self, partition: str, entry_id: str, updates: dict) -> bool:
        """更新某条记录的字段。"""
        data = self._read_partition(partition)
        for entry in data:
            if entry.get("_id") == entry_id:
                entry.update(updates)
                entry["_updated"] = time.time()
                self._write_partition(partition, data)
                return True
        return False

    def remove(self, partition: str, entry_id: str) -> bool:
        """删除某条记录。"""
        data = self._read_partition(partition)
        new_data = [e for e in data if e.get("_id") != entry_id]
        if len(new_data) != len(data):
            self._write_partition(partition, new_data)
            return True
        return False

    def clear(self, partition: Optional[str] = None) -> None:
        """清空一个或全部分区。"""
        if partition:
            self._write_partition(partition, [])
        else:
            for name in self._partitions:
                self._write_partition(name, [])

    # ── 便捷查询 ──────────────────────────────────────────────────

    def summary(self) -> dict:
        """返回各分区摘要（条目数），用于 LLM 快速了解当前状态。"""
        result = {}
        for name in self._partitions:
            data = self._read_partition(name)
            result[name] = {
                "count": len(data),
                "recent": data[-3:] if data else [],
            }
        return result

    def recent_steps(self, n: int = 3) -> list[dict]:
        """获取最近 n 步（从 completed 分区）。"""
        completed = self._read_partition("completed")
        return completed[-n:] if completed else []

    def current_task(self) -> Optional[dict]:
        """获取当前正在执行的任务（current_state 分区）。"""
        state = self._read_partition("current_state")
        return state[-1] if state else None

    def plan_summary(self) -> str:
        """生成下一步计划的人类可读摘要（短文本，适合嵌入 system prompt）。"""
        plan = self._read_partition("next_plan")
        if not plan:
            return ""
        lines = ["## 下一步计划"]
        for i, step in enumerate(plan[:5], 1):
            desc = step.get("description", str(step))
            status = step.get("status", "pending")
            lines.append(f"  {i}. [{status}] {desc[:120]}")
        if len(plan) > 5:
            lines.append(f"  ... 还有 {len(plan)-5} 步")
        return "\n".join(lines)

    # ── 持久化 ─────────────────────────────────────────────────────

    def checkpoint(self) -> dict:
        """保存检查点，返回快照元数据。"""
        snapshot = {}
        for name in self._partitions:
            snapshot[name] = self._read_partition(name)

        cp_id = f"cp_{int(time.time())}"
        cp_path = self.work_dir / f"checkpoint_{cp_id}.json"
        cp_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 更新最新检查点指针
        latest_path = self.work_dir / "latest_checkpoint.json"
        latest_path.write_text(json.dumps({"id": cp_id, "timestamp": time.time()}))

        return {
            "checkpoint_id": cp_id,
            "path": str(cp_path),
            "partitions": {k: len(v) for k, v in snapshot.items()},
        }

    def restore(self, checkpoint_id: str) -> bool:
        """从检查点恢复。"""
        cp_path = self.work_dir / f"checkpoint_{checkpoint_id}.json"
        if not cp_path.exists():
            return False

        snapshot = json.loads(cp_path.read_text(encoding="utf-8"))
        for name, data in snapshot.items():
            if name in self._partitions:
                self._write_partition(name, data)
        return True

    @property
    def total_entries(self) -> int:
        """白板中所有条目的总数。"""
        return sum(len(self._read_partition(n)) for n in self._partitions)

    # ── 内部方法 ──────────────────────────────────────────────────

    def _partition_path(self, name: str) -> Path:
        return self.work_dir / self._partitions[name]

    def _read_partition(self, name: str) -> list[dict]:
        """读取分区（优先走缓存）。"""
        if name in self._cache:
            return list(self._cache[name])

        path = self._partition_path(name)
        if not path.exists():
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._cache[name] = list(data)
            return data
        except (json.JSONDecodeError, OSError):
            return []

    def _write_partition(self, name: str, data: list[dict]) -> None:
        """写入分区并更新缓存。"""
        self._cache[name] = list(data)
        path = self._partition_path(name)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def sync(self) -> None:
        """强制刷新缓存到磁盘（所有分区）。"""
        for name in self._partitions:
            if name in self._cache:
                self._write_partition(name, self._cache[name])
