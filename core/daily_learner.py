"""
core/daily_learner.py — 夸父自主学习日报（从已有 AI 日报学习）

职责：
  1. 每天 07:00 读取昨天生成的 AI 早报+晚报
  2. 从日报末尾的「夸父学习进化建议」提取可复用技能
  3. 写入 skills/ 目录，实现夸父自主进化

已有日报文件（Hermes 的 cron 任务产出）：
  - daily_reports/ai_daily_morning_{date}.md  ← 早报
  - daily_reports/ai_daily_evening_{date}.md   ← 晚报
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("kuafu.daily_learner")

# 日报存放目录（跟 AI 日报一致）
DAILY_REPORT_DIR = "daily_reports"


def learn_from_daily(
    root_dir: Path,
    llm_chat_fn: Callable[[str], Optional[str]],
    skills_dir: Optional[Path] = None,
) -> str:
    """读取昨天的 AI 早报和晚报，提取「夸父学习进化建议」为 skill。

    流程：
    1. 读昨天 ai_daily_morning_{date}.md 和 ai_daily_evening_{date}.md
    2. 用 LLM 判断是否有值得提取的技能（关注"进化建议"部分）
    3. 若有，提取为技能 YAML 写入 skills/
    """
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    date_str = yesterday.strftime("%Y%m%d")
    date_label = yesterday.strftime("%Y-%m-%d")

    report_dir = root_dir / DAILY_REPORT_DIR

    # 读取两份日报
    morning_path = report_dir / f"ai_daily_morning_{date_str}.md"
    evening_path = report_dir / f"ai_daily_evening_{date_str}.md"

    contents = []
    if morning_path.exists():
        contents.append(f"## 早报\n{morning_path.read_text(encoding='utf-8')}")
    if evening_path.exists():
        contents.append(f"## 晚报\n{evening_path.read_text(encoding='utf-8')}")

    if not contents:
        return f"未找到 {date_str} 的日报（早报/晚报），跳过学习"

    combined = "\n\n---\n\n".join(contents)

    if not llm_chat_fn:
        return "LLM 不可用，跳过学习"

    # 构建学习 prompt
    learn_prompt = f"""你是夸父的自主学习模块。阅读以下日报，提取出**值得写入技能库的知识**。

提取重点：
1. **「夸父学习进化建议」部分的内容** —— 日报已为你分析好方向，直接提取可执行项
2. 日报中提到的**开源项目/工具** —— 如果值得集成到夸父能力中
3. 日报中提到的**行业趋势** —— 如果对夸父的发展方向有指导意义

提取标准：
- 只提取有**具体行动方案**的建议（不要笼统的方向）
- 每项技能必须有可执行的 steps
- 如果只有新闻信息没有学习价值，输出空数组

## 日报内容
{combined}

## 输出格式
只输出 JSON 数组，不要思考过程不要解释：
[
  {{
    "worth_learning": true,
    "skill_name": "英文小写连字符的技能名",
    "category": "integration | workflow | direction | security",
    "trigger": "什么情况下会用到这个技能",
    "steps": ["步骤1", "步骤2", ...],
    "reason": "为什么提取这个（引用日报中具体建议）",
    "source_report": "morning | evening | both"
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
        source = item.get("source_report", "both")

        if not name or not steps:
            continue

        # 避免覆盖已有同名 skill
        skill_path = skills_dir / f"{name}.yaml"
        if skill_path.exists():
            name = f"{name}-from-{date_str}"

        yaml_content = f"""name: {name}
source: daily_learn
date: {date_str}
reason: {reason}
source_report: {source}

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
  - 见 ai_daily_{source}_{date_str}.md
"""

        skill_path = skills_dir / f"{name}.yaml"
        skill_path.write_text(yaml_content, encoding="utf-8")
        written.append(name)
        logger.info(f"[DailyLearner] 从日报学习: {name}.yaml")

    if written:
        return f"从 {date_str} 日报学到 {len(written)} 项技能: {', '.join(written)}"
    return f"{date_str} 日报无可学习内容"
