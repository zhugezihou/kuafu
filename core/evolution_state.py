"""
core/evolution_state.py — 增量进化状态管理

职责：
维护 .evolution_state.json 文件，提供：
1. task_type 计数器（总次数、连续失败、最近 N 次成功/失败）
2. 已知错误库（模糊匹配去重）
3. 最近 N 次任务的简要记录
4. 判断接口：is_novel、is_repeated_failure、is_unknown_error
5. skill 版本链追踪（FIX/DERIVED/CAPTURED 记录）
6. 错误→skill 关联（错误归属哪个 skill）
7. skill 质量历史（每次执行后的评分）
8. 版本回滚（undo_last_evolution）

设计原则：
- 纯内存+JSON持久化，零 LLM 成本
- 增量更新，不扫描历史日志
- 原子写入避免损坏
"""

import json
import os
import shutil
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Optional


class EvolutionState:
    """增量进化状态管理。

    记录每个 task_type 的执行历史，用于 Observer 信号增强。

    字段结构（.evolution_state.json）：
    {
        "task_types": {
            "<task_type>": {
                "count": int,
                "last_n": [bool, ...],
                "consecutive_fail": int,
                "last_seen": float,
            }
        },
        "known_errors": ["error1", ...],
        "last_cleanup": float,
        "skills": {
            "<skill_name>": {
                "versions": [
                    {
                        "v": 1,
                        "file": "skills/<name>_v1.md",
                        "created": float,
                        "summary": "初始",
                        "mode": "CAPTURED",
                        "parent": null,
                        "quality": [0.8],
                    },
                    ...
                ],
                "current": 2,
            }
        },
        "error_to_skill": {
            "error_text_fragment": "skill_name",
            ...
        },
    }
    """

    STATE_FILE = "memory/.evolution_state.json"
    MAX_FAILURE_HISTORY = 20
    MAX_KNOWN_ERRORS = 200

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = (root_dir or Path(__file__).resolve().parent.parent)
        self.state_path = self.root_dir / self.STATE_FILE
        self._lock = threading.Lock()
        self._data: dict = self._load()

    # ── 公开接口 ──

    def record_result(self, task_type: str, success: bool):
        """记录一次任务结果。"""
        if not task_type:
            task_type = "generic"

        entry = self._data["task_types"].get(task_type, {
            "count": 0,
            "last_n": [],
            "consecutive_fail": 0,
            "last_seen": 0.0,
        })
        entry["count"] += 1
        entry["last_seen"] = time.time()

        entry["last_n"].append(success)
        if len(entry["last_n"]) > self.MAX_FAILURE_HISTORY:
            entry["last_n"] = entry["last_n"][-self.MAX_FAILURE_HISTORY:]

        if success:
            entry["consecutive_fail"] = 0
        else:
            entry["consecutive_fail"] = entry.get("consecutive_fail", 0) + 1

        self._data["task_types"][task_type] = entry
        self._save()

    def record_error(self, error_text: str):
        """记录一条错误到已知错误库（自动去重模糊匹配）。"""
        if self._is_known(error_text):
            return
        errors = self._data["known_errors"]
        errors.append(error_text)
        if len(errors) > self.MAX_KNOWN_ERRORS:
            errors[:] = errors[-self.MAX_KNOWN_ERRORS:]
        self._save()

    def is_novel(self, task_type: str) -> bool:
        """判断 task_type 是否首次出现。"""
        return task_type not in self._data["task_types"]

    def is_repeated_failure(self, task_type: str, threshold: int = 2) -> bool:
        """判断同类任务是否连续失败 threshold 次以上。"""
        entry = self._data["task_types"].get(task_type)
        if not entry:
            return False
        return entry.get("consecutive_fail", 0) >= threshold

    def is_unknown_error(self, error_text: str) -> bool:
        """判断是否为新错误。"""
        return not self._is_known(error_text)

    def get_task_type_count(self, task_type: str) -> int:
        """获取某 task_type 的出现次数。"""
        entry = self._data["task_types"].get(task_type, {})
        return entry.get("count", 0)

    def get_recent_failure_rate(self, task_type: str, n: int = 5) -> float:
        """最近 n 次任务中失败的比例 (0-1)。"""
        entry = self._data["task_types"].get(task_type)
        if not entry or not entry["last_n"]:
            return 0.0
        recent = entry["last_n"][-n:]
        if not recent:
            return 0.0
        return sum(1 for s in recent if not s) / len(recent)

    def get_stats(self) -> dict:
        """返回状态统计（纯数据，适合注入系统 prompt）。"""
        stats = {"total_types": len(self._data["task_types"]), "types": []}
        for tt, entry in self._data["task_types"].items():
            stats["types"].append({
                "name": tt,
                "count": entry["count"],
                "consecutive_fail": entry.get("consecutive_fail", 0),
                "last_seen": entry["last_seen"],
            })
        return stats

    def health_check(self) -> Optional[str]:
        """运行自检，返回问题描述或 None。"""
        warnings = []
        for tt, entry in self._data["task_types"].items():
            cf = entry.get("consecutive_fail", 0)
            if cf >= 3:
                warnings.append(f"[{tt}] 连续失败 {cf} 次")
        if warnings:
            return "; ".join(warnings)
        return None

    # ── 新增：skill 版本链 ────────────────────────────────────────

    def record_skill_evolution(
        self,
        skill_name: str,
        file_path: str,
        mode: str,
        summary: str,
        parent: Optional[str] = None,
        quality_score: Optional[float] = None,
    ) -> int:
        """记录一次 skill 进化。

        Args:
            skill_name: skill 唯一名称（如 'pip_install'）
            file_path: 当前版本的文件路径
            mode: 'CAPTURED' | 'FIX' | 'DERIVED'
            summary: 变更摘要说明
            parent: 父版本号（None 表示全新，数字字符串如 '1' 表示基于 v1）
            quality_score: 初始质量评分 (0-1)

        Returns:
            新版本号（v）
        """
        skills = self._data["skills"]
        if skill_name not in skills:
            skills[skill_name] = {"versions": [], "current": 0}

        skill_entry = skills[skill_name]
        versions = skill_entry["versions"]
        new_v = (versions[-1]["v"] + 1) if versions else 1

        version_record = {
            "v": new_v,
            "file": file_path,
            "created": time.time(),
            "summary": summary,
            "mode": mode,
            "parent": parent,
            "quality": [quality_score] if quality_score is not None else [],
        }
        versions.append(version_record)
        skill_entry["current"] = new_v
        self._save()
        return new_v

    def record_skill_quality(self, skill_name: str, score: float) -> bool:
        """追加一条 skill 质量评分。

        Args:
            skill_name: skill 名称
            score: 质量评分 (0-1)

        Returns:
            是否成功（False = skill 不存在）
        """
        skill_entry = self._data["skills"].get(skill_name)
        if not skill_entry:
            return False
        current_v = skill_entry["current"]
        for ver in skill_entry["versions"]:
            if ver["v"] == current_v:
                ver["quality"].append(score)
                self._save()
                return True
        return False

    def get_skill_quality(self, skill_name: str) -> Optional[list]:
        """获取当前版本的质量评分历史。

        Returns:
            list of float 或 None（skill 不存在）
        """
        skill_entry = self._data["skills"].get(skill_name)
        if not skill_entry:
            return None
        current_v = skill_entry["current"]
        for ver in skill_entry["versions"]:
            if ver["v"] == current_v:
                return ver.get("quality", [])
        return None

    def get_skill_degradation(self, skill_name: str, n: int = 5) -> Optional[float]:
        """检查 skill 是否退化。

        比较最近 n 次评分的均值 vs 历史均值。
        返回退化幅度（正数表示变差），None 表示数据不足。

        Args:
            skill_name: skill 名称
            n: 最近几次评分用于比较
        """
        quality = self.get_skill_quality(skill_name)
        if not quality or len(quality) < n * 2:
            return None
        recent = quality[-n:]
        historical = quality[:-n]
        if not historical:
            return None
        return (sum(recent) / len(recent)) - (sum(historical) / len(historical))

    def undo_last_evolution(self, skill_name: str) -> Optional[dict]:
        """回滚 skill 到上一个版本。

        会备份当前版本的 SKILL.md 文件到 .bak 后恢复父版本文件。
        返回被回滚的版本信息 dict，或 None（无版本可回滚）。

        Returns:
            {
                "rolled_back_v": int,
                "restored_to_v": int,
                "summary": str,
            }
            或 None
        """
        skill_entry = self._data["skills"].get(skill_name)
        if not skill_entry:
            return None
        versions = skill_entry["versions"]
        if len(versions) < 2:
            return None

        current_v = skill_entry["current"]
        current_idx = next((i for i, v in enumerate(versions) if v["v"] == current_v), -1)
        if current_idx < 1:
            return None

        rolled_ver = versions[current_idx]
        restored_ver = versions[current_idx - 1]

        # 备份当前文件
        current_file = self.root_dir / rolled_ver["file"]
        if current_file.exists():
            bak_file = current_file.with_suffix(f".bak.{rolled_ver['v']}")
            shutil.copy2(str(current_file), str(bak_file))

        # 恢复父版本
        # 注意：FIX 时 rolling_ver 和 restored_ver 可能指向同文件（覆盖写入）
        # 此时父版本的内容已被覆盖，需要从 .bak.* 备份或走其他恢复策略
        restored_file = self.root_dir / restored_ver["file"]
        if restored_file.exists() and restored_file != current_file:
            # 不同文件 → 直接复制（DERIVED 场景）
            shutil.copy2(str(restored_file), str(current_file))
        elif restored_file == current_file:
            # 同文件 → FIX 覆盖场景，尝试从 .bak. 备份恢复
            # 查找最后一个非当前版本的备份
            current_dir = current_file.parent
            stem = current_file.stem
            exclude = f".bak.{rolled_ver['v']}"
            baks = sorted(
                (
                    p for p in current_dir.glob(f"{stem}.bak.*")
                    if not str(p).endswith(exclude)
                ),
                key=lambda p: p.stat().st_mtime,
            )
            if baks:
                shutil.copy2(str(baks[-1]), str(current_file))
            else:
                # 没有备份也返回成功（至少版本号回去了）
                logger.warning(
                    f"回滚 {skill_name}: 无备份文件可恢复，仅更新版本号"
                )
        else:
            raise FileNotFoundError(
                f"父版本文件 {restored_ver['file']} 不存在，无法回滚"
            )

        skill_entry["current"] = restored_ver["v"]
        self._save()

        return {
            "rolled_back_v": rolled_ver["v"],
            "restored_to_v": restored_ver["v"],
            "summary": f"回滚 {skill_name} v{rolled_ver['v']} → v{restored_ver['v']}: "
                       f"{rolled_ver.get('summary', '')}",
        }

    def get_evolution_history(self, skill_name: str) -> Optional[list]:
        """获取 skill 的完整进化历史。

        Returns:
            list of dict（按版本排序），或 None（skill 不存在）
        """
        skill_entry = self._data["skills"].get(skill_name)
        if not skill_entry:
            return None
        return list(skill_entry["versions"])

    def get_all_skills(self) -> dict:
        """返回所有 skill 的名称和当前版本号。"""
        skills = self._data.get("skills", {})
        return {name: entry["current"] for name, entry in skills.items()}

    # ── 新增：错误→skill 关联 ───────────────────────────────────

    def associate_error_with_skill(self, error_fragment: str, skill_name: str):
        """将一段错误文本与 skill 关联。

        使用模糊匹配核心词（错误类型的关键片段，如 'Connection refused'）
        而不是完整错误文本，确保泛化。
        """
        fragment = error_fragment.strip().lower()
        if not fragment:
            return
        self._data["error_to_skill"][fragment] = skill_name
        self._save()

    def get_skill_for_error(self, error_text: str) -> Optional[str]:
        """根据错误文本查找关联的 skill 名称。

        精确匹配 → 子串匹配 → 词重叠匹配 → None
        """
        err_lower = error_text.lower()
        mapping = self._data.get("error_to_skill", {})

        # 精确匹配
        if err_lower in mapping:
            return mapping[err_lower]

        # 子串匹配（错误文本包含关联的片段）
        for fragment, skill in mapping.items():
            if fragment in err_lower:
                return skill

        # 词重叠匹配
        err_words = set(err_lower.split())
        best_overlap = 0
        best_skill = None
        for fragment, skill in mapping.items():
            frag_words = set(fragment.lower().split())
            overlap = len(err_words.intersection(frag_words))
            if overlap > best_overlap:
                best_overlap = overlap
                best_skill = skill

        if best_overlap >= 2:
            return best_skill
        return None

    def get_all_skill_errors(self) -> dict:
        """返回 skill → 关联错误列表 的映射。"""
        mapping = self._data.get("error_to_skill", {})
        result: dict[str, list[str]] = {}
        for fragment, skill in mapping.items():
            result.setdefault(skill, []).append(fragment)
        return result

    # ── 内部方法 ──

    def _load(self) -> dict:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if "task_types" not in data:
                    data["task_types"] = {}
                if "known_errors" not in data:
                    data["known_errors"] = []
                if "last_cleanup" not in data:
                    data["last_cleanup"] = time.time()
                if "skills" not in data:
                    data["skills"] = {}
                if "error_to_skill" not in data:
                    data["error_to_skill"] = {}
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return {
            "task_types": {},
            "known_errors": [],
            "last_cleanup": time.time(),
            "skills": {},
            "error_to_skill": {},
            "_schema": "v2",
        }

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            tmp = self.state_path.with_suffix(f".tmp.{os.getpid()}")
            try:
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(self.state_path)
            except OSError:
                if tmp.exists():
                    tmp.unlink()

    def _is_known(self, error_text: str) -> bool:
        if not error_text:
            return False
        words_err = set(error_text.lower().split())
        for known in self._data["known_errors"]:
            words_known = set(known.lower().split())
            if len(words_known) > 3:
                overlap = words_err.intersection(words_known)
                if len(overlap) >= min(3, len(words_known) // 2):
                    return True
        return False
