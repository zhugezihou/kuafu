"""
autonomous/self_health.py — P4 自检优化程序

职责（"夸父体检"）：
定期检查夸父自身，找出"臃肿""过时""垃圾"的部分并优化。

体检维度：
1. **memory 垃圾** — 旧记忆文件（mem_*.json）是否长期未被访问？删除无用记忆
2. **quality.yaml 重复** — 重复的 rule 条目，清理
3. **strategy 垃圾** — 不再使用的 strategy 文件，标记
4. **skills 未引用** — skills/ 目录中未被任何代码引用的遗留文件
5. **代码腐化** — import 缺失/路径错误的模块引用
6. **进化日志膨胀** — evolution_log.json 中的测试/重复数据，归档
7. **index.txt 脏数据** — memory/index.json 中指向已删除 mem_*.json 的条目

运行方式：
后台 daemon 线程，由 main.py 的 KuafuAgent 启动。
不修改 core/ 目录任何文件，只读/清理 memory/ strategy/ skills/。
"""

import json
import os
import re
import time
import glob
import shutil
import logging
import threading
from pathlib import Path
from typing import Optional

# 审批机制
try:
    from core.approval import ApprovalManager
except ImportError:
    ApprovalManager = None  # type: ignore

# ── 配置 ──────────────────────────────────────────────────────────────

HEALTH_CHECK_INTERVAL = 3600 * 4  # 4 小时执行一次

ROOT_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT_DIR / "memory"
STRATEGY_DIR = ROOT_DIR / "strategy"
SKILLS_DIR = ROOT_DIR / "skills"
BACKUP_DIR = MEMORY_DIR / "_health_backup"

logger = logging.getLogger("kuafu.self_health")


def _safe_json_load(path: Path) -> Optional[dict]:
    """安全加载 JSON，失败返回 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None


def _read_mem_file(mem_id: int) -> Optional[str]:
    """读取 mem_{id}.json 的内容（纯文本摘要）。"""
    path = MEMORY_DIR / f"mem_{mem_id}.json"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


# ── 体检器 ────────────────────────────────────────────────────────────


class HealthChecker:
    """P4 自检优化程序。

    每个 check_* 方法返回 (issues: list[str], fixed: list[str])。
    """

    def __init__(self):
        self.dry_run = False  # True=只报告不修改

    # ── 检查 1: memory 垃圾 ────────────────────────────────────────

    def check_memory_garbage(self) -> tuple[list, list]:
        """检查 memory/ 目录：
        - mem_*.json 是否在 index.json 中注册
        - 未注册的孤立文件 → 删除
        - 已注册但文件丢失 → 清理 index 条目
        """
        issues, fixed = [], []

        # 收集所有 mem_*.json 文件
        mem_files = {}
        for fpath in MEMORY_DIR.glob("mem_*.json"):
            try:
                mem_id = int(fpath.stem.replace("mem_", ""))
                mem_files[mem_id] = fpath
            except ValueError:
                continue

        # 读 index.json
        index_path = MEMORY_DIR / "index.json"
        index = _safe_json_load(index_path)
        if index is None:
            issues.append("index.json 无法读取，跳过 memory 检查")
            return issues, fixed

        # index 中的 mem_* 引用可能在不同层级：有的直接在 keys 层，有的在记录里
        # 先收集 index 中引用的所有 mem_id
        indexed_ids: set[int] = set()
        if isinstance(index, dict):
            # 尝试展开所有值中的 mem_id 引用
            for key, val in index.items():
                if isinstance(val, str) and val.startswith("mem_"):
                    try:
                        indexed_ids.add(int(val.replace("mem_", "")))
                    except ValueError:
                        pass
                elif isinstance(val, dict):
                    # 有些嵌套在 tags -> {...} 结构里
                    for sub_key, sub_val in val.items():
                        if isinstance(sub_val, str) and sub_val.startswith("mem_"):
                            try:
                                indexed_ids.add(int(sub_val.replace("mem_", "")))
                            except ValueError:
                                pass
                        elif sub_key == "mem_id":
                            indexed_ids.add(int(sub_val))  # type: ignore
                elif key == "mem_id":
                    indexed_ids.add(int(val))  # type: ignore

        # 检查 1: 孤立 mem_*.json（不在 index 中）
        for mem_id, fpath in sorted(mem_files.items()):
            if mem_id not in indexed_ids:
                issues.append(f"孤立记忆文件: {fpath.name} (不在 index.json 中)")
                if not self.dry_run:
                    fpath.unlink()
                    fixed.append(f"已删除: {fpath.name}")

        # 检查 2: index 中引用了但文件不存在的条目
        # 用字符串扫描 index 避免结构假设
        index_text = index_path.read_text(encoding="utf-8")
        all_refs = re.findall(r'(?:mem_|"mem_id":\s*")(\d+)', index_text)
        for ref_id in all_refs:
            ref_path = MEMORY_DIR / f"mem_{ref_id}.json"
            if not ref_path.exists():
                issues.append(f"index.json 引用了不存在的 mem_{ref_id}.json")
                # 注意：不自动修改 index.json，太危险

        return issues, fixed

    # ── 检查 2: quality.yaml 重复 ──────────────────────────────────

    def check_quality_duplicates(self) -> tuple[list, list]:
        """检查 strategy/quality.yaml 中重复的 rule 条目。"""
        issues, fixed = [], []
        qpath = STRATEGY_DIR / "quality.yaml"
        if not qpath.exists():
            return issues, fixed

        try:
            content = qpath.read_text(encoding="utf-8")
        except Exception as e:
            issues.append(f"读取 quality.yaml 失败: {e}")
            return issues, fixed

        # 提取所有 rule 行
        rules = re.findall(r"rule:\s*'([^']+)'", content)
        seen: dict[str, list[int]] = {}
        for i, rule in enumerate(rules):
            if rule not in seen:
                seen[rule] = []
            seen[rule].append(i)

        # 报告重复
        for rule, positions in seen.items():
            if len(positions) > 1:
                issues.append(f"quality.yaml 重复规则 (x{len(positions)}): {rule}")
                # 保留第一个出现，删除后面的
                if not self.dry_run:
                    # 从后往前删除，避免行号偏移
                    lines = content.splitlines()
                    occurrences_found = 0
                    new_lines = []
                    for line in lines:
                        m = re.match(r"(.*rule:\s*')" + re.escape(rule) + r"('.*)", line)
                        if m and occurrences_found > 0:
                            # 跳过这一行（不保留重复）
                            occurrences_found += 1
                            fixed.append(f"已删除重复: {rule}")
                            continue
                        if m:
                            occurrences_found += 1
                        new_lines.append(line)

                    qpath.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        return issues, fixed

    # ── 检查 3: skills/ 未引用文件 ──────────────────────────────────

    def check_skills_orphans(self) -> tuple[list, list]:
        """检查 skills/ 目录中的文件是否被任何 Python 代码引用。"""
        issues, fixed = [], []
        if not SKILLS_DIR.exists():
            return issues, fixed

        # 收集所有 Python 代码文件
        all_py_files = sorted(ROOT_DIR.rglob("*.py"))
        all_py_source = ""
        for pf in all_py_files:
            try:
                all_py_source += pf.read_text(encoding="utf-8", errors="ignore") + "\n"
            except Exception:
                pass

        for fpath in sorted(SKILLS_DIR.glob("*")):
            if not fpath.is_file() or fpath.name == ".gitkeep":
                continue
            fname = fpath.name
            # 检查是否被任何 .py 文件 import / open / Path 引用
            if fname not in all_py_source:
                # 也要检查 strategy/ 目录中的 yaml
                strategy_text = ""
                for sf in STRATEGY_DIR.glob("*"):
                    try:
                        strategy_text += sf.read_text(encoding="utf-8") + "\n"
                    except Exception:
                        pass
                if fname not in strategy_text:
                    issues.append(f"skills/ 未引用文件: {fname}")

        return issues, fixed

    # ── 检查 4: 进化日志膨胀 ───────────────────────────────────────

    def check_evolution_log_bloat(self) -> tuple[list, list]:
        """检查 evolution_log.json 是否过大，清理测试数据。"""
        issues, fixed = [], []
        elog_path = MEMORY_DIR / "evolution_log.json"
        if not elog_path.exists():
            return issues, fixed

        try:
            data = json.loads(elog_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, PermissionError) as e:
            issues.append(f"evolution_log.json 读取失败: {e}")
            return issues, fixed

        if not isinstance(data, list):
            return issues, fixed

        size_kb = elog_path.stat().st_size / 1024
        if size_kb < 50:
            return issues, fixed  # 没到清理阈值

        issues.append(f"evolution_log.json 较大 ({size_kb:.0f}KB, {len(data)} 条)")

        # 检测并标记测试数据（trigger 包含 test 或 level=0）
        test_entries = []
        real_entries = []
        for entry in data:
            trigger = entry.get("trigger", "")
            level = entry.get("level", 0)
            if "test" in trigger.lower() or level == 0:
                test_entries.append(entry)
            else:
                real_entries.append(entry)

        if len(test_entries) > len(data) * 0.3:  # 测试数据超过 30%
            issues.append(
                f"evolution_log.json 测试数据过多 ({len(test_entries)}/{len(data)} 条)"
            )

        return issues, fixed

    # ── 检查 5: 文件系统垃圾 ───────────────────────────────────────

    def check_filesystem_garbage(self) -> tuple[list, list]:
        """检查项目根目录的垃圾文件：
        - 孤立的 .py、.txt、.md 等（非 git tracked）
        """
        issues, fixed = [], []
        root_files = [f for f in ROOT_DIR.iterdir() if f.is_file()]

        # 检查 git tracked files
        try:
            import subprocess
            result = subprocess.run(
                ["git", "ls-files"],
                capture_output=True, text=True, cwd=ROOT_DIR, timeout=5
            )
            tracked = set(result.stdout.splitlines())
        except Exception:
            return issues, fixed  # git 不可用时跳过

        garbage_patterns = [
            r"^test_.*\.py$",        # 测试文件（除了 tests/ 下的）
            r"^.*\.bak$",
            r"^.*\.tmp$",
            r"^.*~$",
            r"^core/.*\.pyc$",
            r"^__pycache__",
        ]

        for fpath in root_files:
            rel = str(fpath.relative_to(ROOT_DIR))
            if rel in tracked:
                continue
            for pat in garbage_patterns:
                if re.match(pat, fpath.name):
                    issues.append(f"疑似垃圾文件 (未跟踪): {fpath.name}")
                    break

        # 检查 __pycache__（排除 venv/）
        for pycache in ROOT_DIR.rglob("__pycache__"):
            if "venv" in pycache.parts or ".venv" in pycache.parts:
                continue
            if pycache.is_dir():
                size = sum(f.stat().st_size for f in pycache.rglob("*") if f.is_file())
                issues.append(f"__pycache__ 目录: {pycache} ({size/1024:.0f}KB)")

        return issues, fixed

    # ── 检查 6: 代码中的腐化 import ────────────────────────────────

    def check_dead_imports(self) -> tuple[list, list]:
        """检查 Python 文件中 import 的模块是否存在。"""
        issues, fixed = [], []

        for py_file in sorted(ROOT_DIR.rglob("*.py")):
            # 跳过 __pycache__
            if "__pycache__" in str(py_file):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # 找 from X import Y 和 import X
            imports = re.findall(
                r"^(?:from\s+(\S+)\s+import|\s*import\s+(\S+))",
                text, re.MULTILINE
            )
            for from_mod, import_mod in imports:
                mod = from_mod or import_mod
                # 只检查项目内部的模块
                if not mod.startswith("autonomous.") and not mod.startswith("core."):
                    continue
                if "try" in text or "ImportError" in text:
                    continue  # 有 try/except 保护的跳过
                mod_path = mod.replace(".", "/") + ".py"
                if not (ROOT_DIR / mod_path).exists():
                    issues.append(f"{py_file.name}: import '{mod}' 但 {mod_path} 不存在")

        return issues, fixed

    # ── 综合运行 ────────────────────────────────────────────────────

    def run_all(self, dry_run: bool = False) -> dict:
        """运行全部检查，返回报告。"""
        self.dry_run = dry_run
        results = {}

        checks = [
            ("memory 垃圾文件", self.check_memory_garbage),
            ("quality.yaml 重复", self.check_quality_duplicates),
            ("skills 未引用文件", self.check_skills_orphans),
            ("进化日志膨胀", self.check_evolution_log_bloat),
            ("文件系统垃圾", self.check_filesystem_garbage),
            ("腐化 import", self.check_dead_imports),
        ]

        total_issues = 0
        total_fixes = 0

        for name, check_fn in checks:
            try:
                issues, fixed = check_fn()
                results[name] = {
                    "issues": issues,
                    "fixed": fixed,
                    "dry_run": dry_run,
                }
                total_issues += len(issues)
                total_fixes += len(fixed)
            except Exception as e:
                results[name] = {"issues": [f"检查异常: {e}"], "fixed": [], "dry_run": dry_run}
                total_issues += 1

        results["_summary"] = {
            "issues_count": total_issues,
            "fixed_count": total_fixes,
            "dry_run": dry_run,
        }

        return results

    def format_report(self, results: dict) -> str:
        """将检查结果格式化为可读报告。"""
        lines = ["━━ 夸父自检报告 ━━", ""]
        summary = results.pop("_summary", {})

        for name, data in sorted(results.items()):
            issues = data.get("issues", [])
            fixed = data.get("fixed", [])
            dry_run = data.get("dry_run", False)

            if not issues:
                continue

            status = "✅" if not issues else ("🔧" if fixed else "⚠️")
            lines.append(f"{status} {name}")

            if dry_run:
                for issue in issues[:10]:
                    lines.append(f"  · {issue}")
                if len(issues) > 10:
                    lines.append(f"  … 还有 {len(issues)-10} 项")
            else:
                for issue in issues:
                    lines.append(f"  · {issue}")
                for fix in fixed[:10]:
                    lines.append(f"  ✓ {fix}")

            lines.append("")

        # 恢复 summary
        isc = summary.get("issues_count", 0)
        ifc = summary.get("fixed_count", 0)
        dr = summary.get("dry_run", False)

        if dr:
            lines.append(f"总计发现 {isc} 个问题（dry-run 模式，未修改）")
        else:
            lines.append(f"总计发现 {isc} 个问题，修复 {ifc} 项")

        lines.append("")
        return "\n".join(lines)


# ── 后台线程 ──────────────────────────────────────────────────────────


class HealthCheckerThread(threading.Thread):
    """P4 自检优化后台线程。

    每 4 小时自动运行一次 HealthChecker，结果写入 memory。
    **不自作主张清理** — 发现问题后在 memory 中留下待审批标记，
    等待用户通过对话界面下令执行清理。
    """

    def __init__(
        self,
        memory_remember_fn,
        interval: int = HEALTH_CHECK_INTERVAL,
    ):
        super().__init__(daemon=True)
        self._remember = memory_remember_fn
        self._interval = interval
        self._checker = HealthChecker()
        self._last_check_time = 0.0
        self._logger = logger
        # 存储最近一次体检结果，供用户审批时查看
        self._last_results: Optional[dict] = None
        self._last_report: str = ""

    def run(self):
        """后台循环：每 4 小时一次自我检查。"""
        self._logger.info("P4 自检优化线程已启动")
        while True:
            try:
                time.sleep(self._interval)
                self._do_check()
            except Exception as e:
                self._logger.error(f"自检异常: {e}")

    def _do_check(self):
        """执行一次体检。始终 dry_run，只报告不修改。

        发现问题后通过 approval 机制发起审批请求，
        不等用户（后台线程不能阻塞），等用户通过对话主动批准。
        """
        results = self._checker.run_all(dry_run=True)
        report = self._checker.format_report(results)
        self._last_results = results
        self._last_report = report
        self._logger.info(f"自检完成:\n{report}")

        summary = results.get("_summary", {})
        issues = summary.get("issues_count", 0)
        detail = "\n".join(
            f"  · {issue}"
            for name, data in results.items()
            if name != "_summary"
            for issue in data.get("issues", [])[:3]
        )

        # 将报告摘要存入 memory
        try:
            report_text = f"P4 自检: 发现 {issues} 个问题"
            if detail:
                report_text += "\n" + detail
            else:
                report_text += "\n一切正常"

            tags = ["self_health", "auto"]
            if issues > 0:
                tags.append("needs_approval")

            self._remember(
                key=f"health_check:{int(time.time())}",
                content=report_text,
                tags=tags,
            )
        except Exception as e:
            self._logger.error(f"写入自检报告到 memory 失败: {e}")

        # 发现问题 → 发起审批请求（非阻塞，写入 approval 文件后立即返回）
        if issues > 0 and ApprovalManager is not None:
            try:
                ApprovalManager.submit(
                    title="⛑️ 夸父体检发现需要优化项",
                    detail=(
                        f"夸父自检发现 {issues} 个问题，可清理操作包括：\n\n"
                        f"{detail}\n\n"
                        f"输入「批准清理」或「夸父执行清理」来执行。"
                    ),
                    risk="medium",
                    timeout=86400,  # 24 小时有效期
                )
                self._logger.info(f"已发起审批请求，等待用户决策")
            except Exception as e:
                self._logger.error(f"发起审批请求失败: {e}")

    def run_once(self, dry_run: bool = True) -> str:
        """主动触发一次体检，返回报告文本。
        
        默认 dry_run=True，传入 False 直接执行清理（由用户下令时调用）。
        """
        results = self._checker.run_all(dry_run=dry_run)
        report = self._checker.format_report(results)
        # 更新缓存
        self._last_results = results
        self._last_report = report
        return report

    @property
    def last_report(self) -> str:
        """最近一次体检报告。"""
        return self._last_report
