"""
core/evolution_viz.py — 夸父进化系统终端可视化

ASCII 图表渲染，零外部依赖（纯 Python 标准库）。
直接从 SQLite evolution_tracker 读取数据渲染。

支持的图表类型：
- bar: 横向条形图（任务类型分布、错误频率）
- line: 折线图（fitness 趋势、事件时间序列）
- timeline: 版本链时间线
- heat: 退化热图摘要

设计原则：
- 纯纯纯文本，零外部依赖
- 终端宽度自适应（默认 80 字符）
- 数据不足时优雅降级
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kuafu.evolution_viz")


class EvolutionVisualizer:
    """进化系统终端可视化器。

    Usage:
        viz = EvolutionVisualizer(tracker)
        viz.fitness_trend()       # 适应度趋势
        viz.task_type_chart()     # 任务类型分布
        viz.skill_timeline()      # 技能版本链时间线
        viz.degradation_summary() # 退化摘要
        viz.full_report()         # 全部
    """

    # 颜色符号（终端兼容的纯 ASCII）
    BAR_CHARS = "█▇▆▅▄▃▂▁"
    LINE_CHARS = "╱╲─│"
    SPARK_CHARS = "▁▂▃▄▅▆▇█"  # 8 级柱状 sparkline

    def __init__(self, tracker):
        self.tracker = tracker
        self.width = 72  # 终端宽度，可以被覆盖

    # ═══════════════════════════════════════════════════════════
    # 适应度趋势图
    # ═══════════════════════════════════════════════════════════

    def fitness_trend(self, skill_name: Optional[str] = None,
                      limit_days: int = 30) -> str:
        """渲染适应度趋势折线图。

        Args:
            skill_name: 指定技能名，None 则显示所有技能汇总
            limit_days: 最近 N 天的数据

        Returns:
            纯文本 ASCII 图表
        """
        lines = ["📈 适应度趋势", "=" * self.width]

        if skill_name:
            data = self.tracker._execute(
                """SELECT date(created_at, 'unixepoch') as day,
                          ROUND(AVG(score), 3) as avg_score,
                          COUNT(*) as count
                   FROM evolution_fitness_log
                   WHERE skill_name = ? AND created_at >= ?
                   GROUP BY day ORDER BY day""",
                (skill_name, time.time() - limit_days * 86400),
            ).fetchall()
            title = f"  技能: {skill_name}"
        else:
            data = self.tracker._execute(
                """SELECT date(created_at, 'unixepoch') as day,
                          ROUND(AVG(score), 3) as avg_score,
                          COUNT(*) as count
                   FROM evolution_fitness_log
                   WHERE created_at >= ?
                   GROUP BY day ORDER BY day""",
                (time.time() - limit_days * 86400,),
            ).fetchall()
            title = "  所有技能汇总"

        if not data:
            return lines[0] + "\n" + "  (暂无适应度评估数据)\n"

        lines.append(title)
        lines.append("")

        # 提取数据
        days = [r["day"] for r in data]
        scores = [r["avg_score"] for r in data]
        counts = [r["count"] for r in data]

        min_s = min(scores)
        max_s = max(scores)
        span = max_s - min_s if max_s > min_s else 0.1

        # 绘图区域宽度
        chart_w = self.width - 12  # 留出刻度标签

        # 折线图：每行一个刻度
        steps = min(len(data), 8)  # 最多 8 行
        for i in range(steps):
            idx = int(i * (len(data) - 1) / (steps - 1)) if steps > 1 else 0
            score = scores[idx]
            day = days[idx]
            # 归一化到 [0, chart_w-1]
            norm = (score - min_s) / span
            pos = int(norm * (chart_w - 1))
            marker = "●" if counts[idx] > 1 else "○"
            line = " " * pos + marker
            # 标注
            label = f"{score:.2f}"
            lines.append(f"  {label:<6} {line}  {day}")
        lines.append(f"  {'     '} {'─' * chart_w}")
        lines.append(f"  {min_s:.2f}{' ' * (chart_w - 10)}{max_s:.2f}")
        lines.append("")

        # 汇总信息
        avg = sum(scores) / len(scores)
        lines.append(f"  共 {len(data)} 天, 总计 {sum(counts)} 次评估, 平均 {avg:.3f}")
        if len(scores) >= 2:
            trend = scores[-1] - scores[0]
            trend_symbol = "↑" if trend > 0 else "↓" if trend < 0 else "→"
            lines.append(f"  趋势: {trend_symbol} {abs(trend):.3f} (首日→最新)")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 适应性 sparkline（微型趋势线）
    # ═══════════════════════════════════════════════════════════

    def _sparkline(self, values: list[float], width: int = 20) -> str:
        """渲染 sparkline 微型趋势线。"""
        if not values:
            return ""
        if len(values) == 1:
            return self.SPARK_CHARS[-1]

        # 采样到 width 个点
        n = len(values)
        if n > width:
            indices = [int(i * (n - 1) / (width - 1)) for i in range(width)]
            sampled = [values[i] for i in indices]
        else:
            sampled = values

        min_v = min(sampled)
        max_v = max(sampled)
        span_v = max_v - min_v if max_v > min_v else 1

        chars = []
        for v in sampled:
            level = int((v - min_v) / span_v * (len(self.SPARK_CHARS) - 1))
            level = min(level, len(self.SPARK_CHARS) - 1)
            chars.append(self.SPARK_CHARS[level])

        return "".join(chars)

    # ═══════════════════════════════════════════════════════════
    # 任务类型分布条形图
    # ═══════════════════════════════════════════════════════════

    def task_type_chart(self, top_n: int = 10) -> str:
        """渲染任务类型分布条形图。"""
        lines = ["📊 任务类型分布 (Top {})".format(top_n), "=" * self.width]

        rows = self.tracker._execute(
            "SELECT task_type, count, consecutive_fail, last_seen "
            "FROM evolution_task_types ORDER BY count DESC LIMIT ?",
            (top_n,),
        ).fetchall()

        if not rows:
            lines.append("  (暂无任务记录)")
            return "\n".join(lines)

        max_count = max(r["count"] for r in rows)
        bar_max = self.width - 35  # 条形宽度

        lines.append("")
        for r in rows:
            count = r["count"]
            name = (r["task_type"] or "")[:20]
            bar_len = int(count / max_count * bar_max) if max_count > 0 else 0
            bar = self.BAR_CHARS[0] * bar_len if bar_len > 0 else "▏"
            fail_mark = " ⚠️" if r["consecutive_fail"] >= 3 else ""
            lines.append(f"  {name:<20} {count:>8} {bar}{fail_mark}")

        lines.append("")
        # 汇总
        total = sum(r["count"] for r in rows)
        with_fail = sum(1 for r in rows if r["consecutive_fail"] >= 3)
        if with_fail:
            lines.append(f"  ⚠️  {with_fail} 个任务类型有连续失败")
        lines.append(f"  总计: {total} 次执行, {len(rows)} 种类型")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 技能版本链时间线
    # ═══════════════════════════════════════════════════════════

    def skill_timeline(self, skill_name: Optional[str] = None,
                       top_n: int = 15) -> str:
        """渲染技能版本链时间线。"""
        lines = ["📋 技能版本链时间线", "=" * self.width]

        if skill_name:
            skills = [skill_name]
        else:
            all_skills = self.tracker.get_all_skills()
            # 按最新版本时间排序（从 evolution_skills 的 created_at）
            if not all_skills:
                lines.append("  (暂无技能版本记录)")
                return "\n".join(lines)

            rows = self.tracker._execute(
                """SELECT s.name, MAX(s.created_at) as latest
                   FROM evolution_skills s
                   GROUP BY s.name
                   ORDER BY latest DESC LIMIT ?""",
                (top_n,),
            ).fetchall()
            skills = [r["name"] for r in rows]

        lines.append("")
        for name in skills:
            history = self.tracker.get_evolution_history(name)
            if not history:
                continue

            # 当前版本号
            current = history[-1]["version"]
            version_count = len(history)

            # 获取最近 fitness 评分（如果有的话）
            fitness_row = self.tracker._execute(
                "SELECT ROUND(AVG(score), 3) as avg_score FROM evolution_fitness_log "
                "WHERE skill_name = ? AND version = ?",
                (name, current),
            ).fetchone()
            fitness = fitness_row["avg_score"] if fitness_row and fitness_row["avg_score"] else None

            # 退化检测
            degradation = self.tracker.detect_degradation(name)

            # 格式时间
            last_time = ""
            if history[-1].get("created_at"):
                ts = datetime.fromtimestamp(history[-1]["created_at"])
                last_time = ts.strftime("%m-%d")

            # 版本链图示
            versions = []
            for h in history:
                if h["version"] == current:
                    versions.append("●")  # 当前
                else:
                    versions.append("○")  # 历史

            ver_chain = "─".join(versions)

            # 退化标记
            deg_mark = ""
            if degradation:
                if degradation["severity"] == "critical":
                    deg_mark = " 🔴退化"
                elif degradation["severity"] == "warning":
                    deg_mark = " 🟡退化"

            # fitness sparkline
            fitness_scores = [
                r["score"]
                for r in self.tracker._execute(
                    "SELECT score FROM evolution_fitness_log WHERE skill_name = ? ORDER BY created_at",
                    (name,),
                ).fetchall()
            ]

            fit_str = ""
            if fitness_scores:
                spark = self._sparkline(fitness_scores, width=12)
                fit_str = f" [{spark}]"

            lines.append(
                f"  {name:<25} v{current:<2} {ver_chain:<30} "
                f"f={fitness or 0:.2f}{fit_str}{deg_mark}"
            )

        lines.append("")
        lines.append(f"  ● = 当前版本  ○ = 历史版本")
        lines.append(f"  ▁▂▃▄▅▆▇█ = fitness 微型趋势线")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 退化摘要
    # ═══════════════════════════════════════════════════════════

    def degradation_summary(self) -> str:
        """渲染退化检测摘要。"""
        lines = ["⚠️  退化检测摘要", "=" * self.width]

        results = self.tracker.detect_all_degradations()

        if not results:
            lines.append("  ✅ 未检测到任何技能退化")
            return "\n".join(lines)

        # 分级
        critical = [r for r in results if r["severity"] == "critical"]
        warning = [r for r in results if r["severity"] == "warning"]

        lines.append("")
        if critical:
            lines.append(f"  🔴 严重退化 ({len(critical)}):")
            for r in critical:
                lines.append(f"     {r['skill_name']}: {'; '.join(r['signals'])}")
                lines.append(f"     💡 {r.get('suggested_action', '')}")
            lines.append("")

        if warning:
            lines.append(f"  🟡 警告 ({len(warning)}):")
            for r in warning:
                lines.append(f"     {r['skill_name']}: {'; '.join(r['signals'])}")
                if r.get("suggested_action"):
                    lines.append(f"     💡 {r['suggested_action']}")
            lines.append("")

        # 汇总
        lines.append(f"  已扫描: {self.tracker.get_all_skills().__len__()} 个技能")
        lines.append(f"  健康: {self.tracker.get_all_skills().__len__() - len(results)} 个正常")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 综合仪表盘
    # ═══════════════════════════════════════════════════════════

    def dashboard(self) -> str:
        """渲染进化系统综合仪表盘。"""
        parts = []

        # 标题
        parts.append("┌" + "─" * (self.width - 2) + "┐")
        parts.append("│" + "夸父进化系统 ─ 终端仪表盘".center(self.width - 2) + "│")
        parts.append("└" + "─" * (self.width - 2) + "┘")
        parts.append("")

        # 系统概览
        stats = self.tracker.get_stats(include_recent_events=False)
        parts.append("📊 系统概览")
        parts.append("  " + "─" * (self.width - 4))
        parts.append(f"  技能版本链: {stats.get('total_skills', 0)} 个")
        parts.append(f"  任务类型:   {stats.get('total_task_types', 0)} 种")
        parts.append(f"  已知错误:   {stats.get('known_errors', 0)} 个")
        parts.append(f"  进化事件:   {stats.get('total_events', 0)} 条")
        recent = stats.get("recent_24h", {})
        parts.append(f"  近24h:      {recent.get('fitness_evals', 0)} 次评估, "
                     f"{recent.get('events', 0)} 条事件")
        parts.append("")

        # 任务类型 Top 5（横条）
        rows = self.tracker._execute(
            "SELECT task_type, count FROM evolution_task_types "
            "ORDER BY count DESC LIMIT 5"
        ).fetchall()
        if rows:
            parts.append("📊 高频任务类型 (Top 5)")
            parts.append("  " + "─" * (self.width - 4))
            max_c = max(r["count"] for r in rows)
            bar_w = self.width - 40
            for r in rows:
                name = (r["task_type"] or "")[:18]
                bar_len = int(r["count"] / max_c * bar_w) if max_c > 0 else 0
                bar = self.BAR_CHARS[0] * bar_len if bar_len > 0 else "▏"
                parts.append(f"  {name:<18} {r['count']:>8} {bar}")
            parts.append("")

        # 技能版本链（sparkline 版）
        all_skills = self.tracker.get_all_skills()
        if all_skills:
            parts.append("📋 技能版本链")
            parts.append("  " + "─" * (self.width - 4))

            skill_rows = self.tracker._execute(
                """SELECT s.name, MAX(s.version) as ver, MAX(s.created_at) as latest
                   FROM evolution_skills s GROUP BY s.name
                   ORDER BY latest DESC LIMIT 8"""
            ).fetchall()

            for r in skill_rows:
                name = r["name"]
                ver = r["ver"]
                # fitness sparkline
                scores = [
                    row["score"]
                    for row in self.tracker._execute(
                        "SELECT score FROM evolution_fitness_log WHERE skill_name = ? ORDER BY created_at",
                        (name,),
                    ).fetchall()
                ]
                if scores:
                    spark = self._sparkline(scores, width=10)
                    avg = sum(scores[-3:]) / min(3, len(scores))
                    parts.append(f"  {name:<22} v{ver:<2} {spark:<12} f={avg:.2f}")
                else:
                    parts.append(f"  {name:<22} v{ver:<2} (无评估)")
            parts.append("")

        # 退化摘要
        degradations = self.tracker.detect_all_degradations()
        if degradations:
            parts.append("⚠️  退化检测")
            parts.append("  " + "─" * (self.width - 4))
            for d in degradations[:5]:
                mark = "🔴" if d["severity"] == "critical" else "🟡"
                parts.append(f"  {mark} {d['skill_name']}: {'; '.join(d['signals'][:2])}")
            if len(degradations) > 5:
                parts.append(f"  ... 还有 {len(degradations) - 5} 个")
            parts.append("")

        # 最近事件
        events = self.tracker.get_recent_events(limit=6)
        if events:
            parts.append("📝 最近进化事件")
            parts.append("  " + "─" * (self.width - 4))
            for e in events:
                ts = datetime.fromtimestamp(e["created_at"]).strftime("%m-%d %H:%M")
                action = (e.get("action") or "")[:50]
                parts.append(f"  [{e.get('level','?')}] {action:<50} {ts}")
            parts.append("")

        return "\n".join(parts)

    # ═══════════════════════════════════════════════════════════
    # 全量报告
    # ═══════════════════════════════════════════════════════════

    def full_report(self) -> str:
        """生成全量报告。"""
        parts = [self.dashboard()]

        parts.append("")
        parts.append("─" * self.width)
        parts.append("")

        parts.append(self.fitness_trend())

        parts.append("")
        parts.append("─" * self.width)
        parts.append("")

        parts.append(self.task_type_chart(top_n=15))

        parts.append("")
        parts.append("─" * self.width)
        parts.append("")

        parts.append(self.skill_timeline(top_n=20))

        parts.append("")
        parts.append("─" * self.width)
        parts.append("")

        parts.append(self.degradation_summary())

        return "\n".join(parts)
