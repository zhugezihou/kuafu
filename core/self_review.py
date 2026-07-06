"""
core/self_review.py — 本地模型驱动的自主学习（自我审查）

职责：
  周期性地扫描夸父的内建数据（技能库 / 进化统计 / 已知错误 / 长期记忆），
  用本地 4B 模型分析出薄弱点和可提升方向，将分析结果记入记忆，
  供夸父后续对话时自然使用。

设计原则：
  - 只读不写：不修改任何技能/配置/代码
  - 全本地：全部走 llama-server（localhost:8080），零 API 成本
  - 降级友好：本地模型不可用时静默跳过
  - 轻量：单次分析 ≤ 3 次本地推理调用，≤ 15 秒
  - 独立：不依赖 autonomous/ 目录，不与后台线程共享状态
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("kuafu.self_review")

ROOT_DIR = Path(__file__).resolve().parent.parent
LOCAL_BASE_URL = "http://localhost:8080"
LOCAL_TIMEOUT = 8
INFERENCE_TIMEOUT = 120

# ── 发现级别 ──
SEVERITY_LABELS = {0: "info", 1: "low", 2: "medium", 3: "high"}


def _quick_chat(prompt: str, max_tokens: int = 512,
                temperature: float = 0.1) -> Optional[str]:
    """单次本地推理调用。超时/失败返回 None。"""
    import urllib.request
    import urllib.error
    try:
        payload = json.dumps({
            "model": "",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{LOCAL_BASE_URL}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=INFERENCE_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            msg = result.get("choices", [{}])[0].get("message", {})
            content = msg.get("content", "").strip()
            if not content:
                content = msg.get("reasoning_content", "").strip()
            return content if content else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError,
            KeyError, IndexError) as e:
        logger.debug(f"[SelfReview] 推理调用失败: {e}")
        return None


def _is_local_available() -> bool:
    """检查本地模型是否在线。"""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(f"{LOCAL_BASE_URL}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ── 数据采集 ─────────────────────────────────────────────


def _collect_skills_summary() -> str:
    """扫描 skills/*.yaml，返回技能列表摘要。"""
    skills_dir = ROOT_DIR / "skills"
    if not skills_dir.exists():
        return "(无技能目录)"

    lines = []
    for f in sorted(skills_dir.iterdir()):
        if f.suffix in (".yaml", ".yml"):
            try:
                text = f.read_text(encoding="utf-8")
                # 提取 name 和 description
                name = ""
                desc = ""
                for line in text.splitlines():
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("\"'")
                    elif line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip("\"'")
                    elif line.startswith("usage_count:"):
                        usage = line.split(":", 1)[1].strip()
                        desc += f" (使用{usage}次)"
                if name:
                    lines.append(f"  - {name}: {desc[:100]}")
            except Exception:
                continue
    if not lines:
        return "(无技能)"
    return "\n".join(lines)


def _collect_evolution_stats() -> str:
    """从 evolution.db 采集进化统计摘要。"""
    try:
        import sqlite3
        db_path = ROOT_DIR / "memory" / "evolution.db"
        if not db_path.exists():
            return "(evolution.db 不存在)"
        db = sqlite3.connect(str(db_path))
        cur = db.execute(
            "SELECT task_type, count, consecutive_fail FROM evolution_task_types "
            "ORDER BY count DESC LIMIT 20"
        )
        rows = cur.fetchall()
        db.close()

        if not rows:
            return "(无进化统计数据)"

        lines = ["任务类型统计（前20）:"]
        for name, cnt, fail in rows:
            fail_mark = f" ⚠️连续失败{fail}次" if fail > 2 else ""
            lines.append(f"  - {name}: {cnt}次{fail_mark}")

        # 技能统计
        try:
            db2 = sqlite3.connect(str(db_path))
            cur2 = db2.execute(
                "SELECT name, version, mode, summary FROM evolution_skills "
                "ORDER BY id DESC LIMIT 15"
            )
            skill_rows = cur2.fetchall()
            db2.close()
            if skill_rows:
                lines.append("")
                lines.append("最近进化技能:")
                for name, ver, mode, summary in skill_rows:
                    s = (summary or "")[:60]
                    lines.append(f"  - {name} v{ver} [{mode}]: {s}")
        except Exception:
            pass

        return "\n".join(lines)
    except Exception as e:
        return f"(采集进化统计失败: {e})"


def _collect_known_errors() -> str:
    """从 evolution.db 采集已知错误摘要。"""
    try:
        import sqlite3
        db_path = ROOT_DIR / "memory" / "evolution.db"
        if not db_path.exists():
            return "(evolution.db 不存在)"
        db = sqlite3.connect(str(db_path))
        cur = db.execute(
            "SELECT error_text, count, skill_name FROM evolution_errors "
            "ORDER BY count DESC LIMIT 15"
        )
        rows = cur.fetchall()
        db.close()

        if not rows:
            return "(无错误记录)"

        lines = ["高频错误（前15）:"]
        for text, cnt, skill in rows:
            skill_info = f" → 关联技能: {skill}" if skill else ""
            lines.append(f"  - [{cnt}次] {text[:80]}{skill_info}")
        return "\n".join(lines)
    except Exception as e:
        return f"(采集错误统计失败: {e})"


def _collect_recent_memories() -> str:
    """采集最新的记忆条目摘要。"""
    try:
        import glob
        mem_dir = ROOT_DIR / "memory"
        files = sorted(glob.glob(str(mem_dir / "mem_*.json")),
                       key=lambda x: int(x.split("_")[-1].split(".")[0]))
        if not files:
            return "(无记忆)"

        recent = files[-10:]  # 最近 10 条
        lines = ["最近记忆:"]
        for f in recent:
            try:
                data = json.loads(open(f, encoding="utf-8").read())
                content = (data.get("content", "") or "")[:80]
                tags = data.get("context", "")
                lines.append(f"  - {tags}: {content}")
            except Exception:
                continue
        return "\n".join(lines)
    except Exception as e:
        return f"(采集记忆失败: {e})"


# ── 分析引擎 ─────────────────────────────────────────────


ANALYSIS_PROMPT = """你是一个 AI Agent 的「自我审查员」。直接输出 JSON，不要思考过程，不要markdown代码块。

分析以下数据，找出薄弱点和可提升方向：

【技能库】
{skills}

【进化统计】
{evolution}

【已知错误】
{errors}

【最近记忆】
{memories}

输出 JSON 数组，每条包含 area/severity/finding/evidence/suggestion。
severity 只取 high/medium/low/info 之一。
如果都不值得学，只输出 []。"""


def analyze(skills_summary: str, evolution_stats: str,
            known_errors: str, recent_memories: str) -> list[dict]:
    """用本地模型分析数据，返回发现列表。"""
    # 本地模型有 reasoning 过程会占用大量 token，截短输入
    prompt = ANALYSIS_PROMPT.format(
        skills=skills_summary[:200],
        evolution=evolution_stats[:200],
        errors=known_errors[:200],
        memories=recent_memories[:200],
    )
    result = _quick_chat(prompt, max_tokens=4096, temperature=0.2)
    if not result:
        return []

    # 提取 JSON 数组
    try:
        # 去掉 markdown 代码块包裹
        cleaned = result.strip()
        if cleaned.startswith("```"):
            # 去掉开头的 ```json 或 ``` 和结尾的 ```
            cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        # 找到第一个 [ 和最后一个 ]
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1:
            logger.warning(f"[SelfReview] LLM 输出无 JSON 数组: {result[:100]}")
            return []
        json_str = cleaned[start:end + 1]
        findings = json.loads(json_str)
        if not isinstance(findings, list):
            return []
        return findings
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"[SelfReview] JSON 解析失败: {e}")
        return []


# ── 发现去重 ─────────────────────────────────────────────


def _deduplicate_findings(new_findings: list[dict],
                          existing: list[dict]) -> list[dict]:
    """过滤掉已存在的发现，基于 finding 文本的简单匹配。"""
    existing_texts = {f.get("finding", "") for f in existing}
    return [f for f in new_findings if f.get("finding", "") not in existing_texts]


# ── 主入口 ───────────────────────────────────────────────


def run_one_cycle() -> list[dict]:
    """执行一轮自我审查。返回新发现的列表。

    不抛异常——所有失败静默处理。
    """
    if not _is_local_available():
        logger.info("[SelfReview] 本地模型不可用，跳过本轮审查")
        return []

    try:
        # 1. 采集数据
        skills = _collect_skills_summary()
        evolution = _collect_evolution_stats()
        errors = _collect_known_errors()
        memories = _collect_recent_memories()

        # 2. 本地模型分析
        findings = analyze(skills, evolution, errors, memories)
        if not findings:
            logger.info("[SelfReview] 本轮无新发现")
            return []

        logger.info(f"[SelfReview] 发现 {len(findings)} 项: "
                     f"{', '.join(f.get('finding','')[:30] for f in findings[:3])}")
        return findings

    except Exception as e:
        logger.warning(f"[SelfReview] 审查异常: {e}")
        return []


# ── 调度器 ───────────────────────────────────────────────


class SelfReviewer:
    """自我审查调度器。后台线程，定期执行一轮分析。

    用法：
        reviewer = SelfReviewer(memory_api, interval=3600)
        reviewer.start()  # 启动后台线程
        reviewer.stop()   # 停止
    """

    def __init__(self, memory_api: Any, interval: int = 3600,
                 notify_callback: Optional[Callable[[str], None]] = None):
        """
        Args:
            memory_api: KuafuAgent.memory (MemoryAPI 实例)，分析结果写入此处
            interval: 审查间隔（秒），默认 1 小时
            notify_callback: 可选回调，收到新的 high/medium 发现时调用（用于主动推送）
        """
        self._memory = memory_api
        self._interval = max(60, interval)
        self._notify = notify_callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # 历史发现（用于去重）
        self._previous_findings: list[dict] = []

    # ── 生命周期 ──

    def start(self, daemon: bool = True):
        """启动后台审查线程。"""
        if self._thread and self._thread.is_alive():
            logger.info("[SelfReview] 已在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=daemon,
            name="kuafu-self-review",
        )
        self._thread.start()
        logger.info(f"[SelfReview] 后台线程已启动（间隔 {self._interval}s）")

    def stop(self):
        """停止后台线程。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("[SelfReview] 已停止")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── 内部 ──

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                findings = run_one_cycle()
                if findings:
                    new_ones = _deduplicate_findings(
                        findings, self._previous_findings
                    )
                    if new_ones:
                        self._save_findings(new_ones)
                        self._notify_new_findings(new_ones)
                        self._previous_findings.extend(new_ones)
                    else:
                        logger.info("[SelfReview] 发现与历史重复，跳过存储")
                else:
                    logger.info("[SelfReview] 本轮无新发现")
            except Exception:
                pass

            # 等待间隔（可被 stop 打断）
            self._stop_event.wait(self._interval)

    def _save_findings(self, findings: list[dict]):
        """将新发现写入记忆系统。"""
        for f in findings:
            finding = f.get("finding", "?")
            severity = f.get("severity", "info")
            evidence = f.get("evidence", "")
            suggestion = f.get("suggestion", "")
            area = f.get("area", "unknown")

            content = (
                f"[{severity.upper()}] [{area}] {finding}\n"
                f"依据: {evidence}\n"
                f"建议: {suggestion}"
            )
            try:
                self._memory.remember(
                    key=f"self_review:{area}:{int(time.time())}",
                    content=content,
                    tags=["self_review", area, severity],
                )
            except Exception:
                pass

    def _notify_new_findings(self, findings: list[dict]):
        """主动推送 medium/high 发现。"""
        if not self._notify:
            return
        important = [f for f in findings if f.get("severity") in ("high", "medium")]
        if not important:
            return
        lines = ["🔍 **夸父自我审查发现**\n"]
        for f in important:
            icon = "🔴" if f.get("severity") == "high" else "🟡"
            finding = f.get("finding", "?")
            area = f.get("area", "unknown")
            suggestion = f.get("suggestion", "")
            lines.append(f"{icon} [{area}] {finding}")
            if suggestion:
                lines.append(f"   💡 {suggestion}")
        try:
            self._notify("\n".join(lines))
        except Exception:
            pass

    def run_now(self) -> list[dict]:
        """立即执行一轮审查（同步），供手动触发。"""
        findings = run_one_cycle()
        if findings:
            new_ones = _deduplicate_findings(findings, self._previous_findings)
            if new_ones:
                self._save_findings(new_ones)
                self._notify_new_findings(new_ones)
                self._previous_findings.extend(new_ones)
                return new_ones
        return []


# 延迟导入，避免模块初始化时加载 threading（不影响纯函数测试）
import threading  # noqa: E402
