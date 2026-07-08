"""
core/daily_learner.py — 夸父自主学习日报

职责：
  1. 生成日报：每天收集夸父当天的进化事件、技能变更、Session 摘要，产出日报
  2. 学习日报：每天读取前一天的日报，用 LLM 提取可复用的技能/模式/最佳实践

夸父自己产出 → 夸父自己学，不依赖外部系统。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("kuafu.daily_learner")

# 日报存放目录
DAILY_REPORT_DIR = "memory/daily_reports"

# ── 日报生成 ──────────────────────────────────────


def generate_daily_report(
    root_dir: Path,
    evolution_stats_fn: Optional[Callable[[], dict]] = None,
    session_store: Any = None,
    skills_dir: Optional[Path] = None,
) -> str:
    """生成今日夸父日报。

    收集：
    - 进化事件：今天新学的技能、修复的错误
    - 技能变更：skills/ 目录新增/修改的文件
    - 最近 Session：今天处理的任务摘要
    """
    today = datetime.now().strftime("%Y-%m-%d")
    sections = [f"# 夸父日报 {today}\n"]

    # 1. 进化事件
    if evolution_stats_fn:
        try:
            stats = evolution_stats_fn()
            events = stats.get("recent_events", [])
            # 筛选今天的（24小时内）
            cutoff = time.time() - 86400
            today_events = [e for e in events if e.get("timestamp", 0) >= cutoff]
            skill_events = [e for e in today_events if e.get("level") == "skill"]
            error_events = [e for e in today_events if e.get("level") == "error"]

            if skill_events:
                sections.append("## 今日新学技能\n")
                for e in skill_events:
                    sections.append(f"- {e.get('action', '?')} → {e.get('target', '?')}")
                sections.append("")

            if error_events:
                sections.append("## 今日修复错误\n")
                for e in error_events:
                    sections.append(f"- {e.get('target', '?')}: {e.get('payload', '')[:200]}")
                sections.append("")

            sections.append(f"## 进化统计\n")
            sections.append(f"- 总进化次数: {stats.get('total_evolutions', 0)}")
            sections.append(f"- 今日事件数: {len(today_events)}")
            sections.append("")

        except Exception as e:
            sections.append(f"<!-- 进化事件获取失败: {e} -->\n")

    # 2. 技能变更
    if skills_dir and skills_dir.exists():
        try:
            files = sorted(skills_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            cutoff_ts = time.time() - 86400
            recent = [f for f in files if f.is_file() and f.stat().st_mtime >= cutoff_ts]
            if recent:
                sections.append("## 今日技能变更\n")
                for f in recent[:10]:
                    sections.append(f"- {f.name} (更新于 {datetime.fromtimestamp(f.stat().st_mtime).strftime('%H:%M')})")
                sections.append("")
        except Exception as e:
            sections.append(f"<!-- 技能目录读取失败: {e} -->\n")

    # 3. 最近 Session
    if session_store:
        try:
            sessions = session_store.list_sessions(limit=20)
            cutoff_ts = time.time() - 86400
            today_sessions = [s for s in sessions if s.updated_at and _parse_ts(s.updated_at) >= cutoff_ts]
            if today_sessions:
                sections.append("## 今日对话摘要\n")
                for s in today_sessions[:5]:
                    title = s.title or "(无标题)"
                    msg_count = getattr(s, "message_count", "?")
                    sections.append(f"- [{title}]({s.id}) ({msg_count} 条消息)")
                sections.append("")
        except Exception as e:
            sections.append(f"<!-- Session 获取失败: {e} -->\n")

    # 4. 写入日报
    report_dir = root_dir / DAILY_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today}.md"
    content = "\n".join(sections)
    report_path.write_text(content, encoding="utf-8")
    logger.info(f"[DailyLearner] 日报已生成: {report_path}")
    return content


# ── 自主学习 ──────────────────────────────────────


def learn_from_daily(
    root_dir: Path,
    llm_chat_fn: Callable[[str], Optional[str]],
    skills_dir: Optional[Path] = None,
) -> str:
    """读取前一天的日报，用 LLM 提取可复用技能。

    流程：
    1. 读昨天的日报文件
    2. 用 LLM 判断是否有值得学习的内容
    3. 若有，提取为技能 YAML 写入 skills/
    """
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")

    report_path = root_dir / DAILY_REPORT_DIR / f"{date_str}.md"
    if not report_path.exists():
        return f"未找到 {date_str} 的日报，跳过学习"

    content = report_path.read_text(encoding="utf-8")

    # 去除空/无实质内容的日报
    lines = [l for l in content.strip().split("\n") if l.strip() and not l.startswith("<!--")]
    if len(lines) < 3:
        return f"{date_str} 日报内容过少，跳过学习"

    if not llm_chat_fn:
        return "LLM 不可用，跳过学习"

    # 构建学习 prompt
    learn_prompt = f"""你是夸父的自主学习模块。阅读以下日报内容，提取出**值得写入技能库的知识**。

提取标准：
1. 新的错误修复模式（错误 → 原因 → 修复方法）→ 适合写成 skill
2. 新的工作流或流程（多个步骤的组合）→ 适合写成 skill
3. 用户明确纠正的偏好 → 适合写成 skill
4. 值得记住的工具使用技巧 → 适合写成 skill

如果没有任何值得学习的内容，输出空 JSON 数组。
如果有一项或多项值得学习，每项输出一个 JSON 对象。

## 日报内容
{content}

## 输出格式
只输出 JSON 数组，不要思考过程不要解释：
[
  {{
    "worth_learning": true,
    "skill_name": "英文小写连字符的技能名",
    "category": "error_fix | workflow | preference | tip",
    "trigger": "什么情况下会用到这个技能",
    "steps": ["步骤1", "步骤2", ...],
    "reason": "为什么提取这个"
  }}
]

如果没有值得学习的：
[]
"""

    try:
        response = llm_chat_fn(learn_prompt)
        if not response:
            return "LLM 返回为空，跳过学习"
    except Exception as e:
        return f"LLM 调用失败: {e}"

    # 解析 JSON
    text = response.strip()
    # 剥离可能的 markdown 代码块
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        items = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"[DailyLearner] 解析 LLM 返回失败: {e}\n{response[:300]}")
        return "学习结果解析失败"

    if not items:
        return f"{date_str} 日报无可学习内容"

    skills_dir = skills_dir or root_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for item in items:
        if not isinstance(item, dict) or not item.get("worth_learning"):
            continue

        name = item.get("skill_name", "").strip()
        trigger = item.get("trigger", "")
        steps = item.get("steps", [])
        reason = item.get("reason", "")

        if not name or not steps:
            continue

        # 从日报日期提取，避免覆盖已有同名 skill
        if (skills_dir / f"{name}.yaml").exists():
            name = f"{name}-from-{date_str}"

        yaml_content = f"""name: {name}
source: daily_learn
date: {date_str}
reason: {reason}

trigger:
  - {trigger}

steps:
"""
        for step in steps:
            yaml_content += f"  - {step}\n"

        yaml_content += f"""
pitfalls:
  - 来自日报学习，尚未经过验证

examples:
  - 见 {date_str} 日报
"""

        skill_path = skills_dir / f"{name}.yaml"
        skill_path.write_text(yaml_content, encoding="utf-8")
        written.append(name)
        logger.info(f"[DailyLearner] 从日报学习: {name}.yaml")

    if written:
        return f"从 {date_str} 日报学到 {len(written)} 项技能: {', '.join(written)}"
    return f"{date_str} 日报无可学习内容"


def _parse_ts(ts_str: str) -> float:
    """解析时间戳字符串为 float。"""
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.timestamp()
    except Exception:
        return 0.0
