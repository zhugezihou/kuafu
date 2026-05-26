"""
autonomous/learner.py — [已废弃] 旧 P1 主动学习信号检测模块

⚠️ 自 v0.4.0 起，本模块已被 core/ 中的三阶段进化管道取代：
  Observer → EvolutionState → Judge → SkillWriter

功能已迁移至：
  - 运行时追踪 → core/observer.py
  - 状态管理 → core/evolution_state.py
  - LLM 判断+提取 → core/judge.py
  - 技能写入 → core/evolution.py (EvolutionEngine.run_pipeline)

本文件保留仅为兼容旧代码引用，新代码应直接使用三阶段管道。
如需恢复旧行为，删除本注释块即可。
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("kuafu.learner")

ROOT_DIR = Path(__file__).resolve().parent.parent
EVOLUTION_LOG = ROOT_DIR / "memory" / "evolution_log.json"
KNOWN_ERRORS_FILE = ROOT_DIR / "memory" / "known_errors.json"

# ── 信号严重级别 ──────────────────────────────────────────────────────
# S = 安全（自动学，不通知）
# A = 重要（写入记忆 + 通知用户）
# B = 常规（仅写入记忆）

SIGNAL_PRIORITY = {
    "user_correction": "A",   # 用户直接纠正，最重要
    "repeat_failure": "A",    # 重复失败，需要关注
    "unknown_error": "B",     # 新错误，记录但不打扰
    "knowledge_gap": "B",     # 知识不足，记录
    "new_pattern": "S",       # 好的新方法，自动学
}


class Learner:
    """P1 主动学习信号检测器。

    在每轮任务完成后调用 detect()，自动分析进化日志和任务结果，
    发现有价值的学习信号后写入记忆。

    不持有 LLM 引用——通过回调函数依赖注入。
    """

    def __init__(
        self,
        llm_chat_fn: Callable,
        memory_remember_fn: Callable,
        memory_recall_fn: Optional[Callable] = None,
    ):
        """
        Args:
            llm_chat_fn: callable(messages: list[dict]) -> dict
                调用 LLM 分析任务结果。由调用方注入。
            memory_remember_fn: callable(key: str, content: str, tags: list)
                写入记忆。由调用方注入。
            memory_recall_fn: Optional callable(query: str, limit: int) -> list[dict]
                从记忆检索历史信息。由调用方注入。
        """
        self._llm_chat = llm_chat_fn
        self._remember = memory_remember_fn
        self._recall = memory_recall_fn

        # 已知错误库（避免重复报告相同错误）
        self._known_errors = self._load_known_errors()

    # ── 公开接口 ────────────────────────────────────────────────────

    def detect(self, task_result: dict, task: str, messages: list) -> list[dict]:
        """检测一个任务完成后的学习信号。

        Args:
            task_result: AgentLoop.run() 返回的结果 dict
            task: 原始用户任务描述
            messages: 本轮对话消息列表

        Returns:
            list[dict] 检测到的信号列表，每个信号格式:
            {
                "type": str,         # 信号类型
                "priority": str,     # S/A/B
                "title": str,        # 可读标题
                "detail": str,       # 具体内容
                "severity": int,     # 0-5
            }
        """
        signals = []

        # 0. 日常经验提取（不依赖任何触发条件，每次任务后都尝试）
        daily = self._extract_daily_lesson(task_result, task)
        if daily:
            signals.append(daily)

        # 1. 用户纠正信号（最高优先级）
        correction = self._detect_user_correction(task_result, task)
        if correction:
            signals.append(correction)

        # 2. 重复失败信号
        repeat = self._detect_repeat_failure(task_result)
        if repeat:
            signals.append(repeat)

        # 3. 未知错误信号
        unknown = self._detect_unknown_error(task_result)
        if unknown:
            signals.append(unknown)

        # 4. 知识缺口信号
        gap = self._detect_knowledge_gap(task_result)
        if gap:
            signals.append(gap)

        # 5. 新模式发现信号
        pattern = self._detect_new_pattern(task_result, messages)
        if pattern:
            signals.append(pattern)

        # 将信号写入记忆
        for signal in signals:
            self._persist_signal(signal)

        if signals:
            logger.info(
                f"[Learner] 检测到 {len(signals)} 个学习信号: "
                + ", ".join(s["type"] for s in signals)
            )

        return signals

    # ── 信号检测方法 ────────────────────────────────────────────────

    def _detect_user_correction(self, task_result: dict, task: str) -> Optional[dict]:
        """检测用户纠正信号。

        触发条件：
        - 用户输入中包含明确的纠正/指导语言
        - 且任务整体是成功的（用户纠正后按正确方法执行）

        检查用户输入的 Task 描述：
        - 「不要」「别」「应该」「记住」「注意」等纠正信号
        - 「用 XX 方法」「换成」「改用」等方法变更信号
        """
        if not task:
            return None

        correction_signals = [
            "不要", "别用", "不要用", "不可以", "不对", "错了",
            "应该用", "请用", "用这个", "换成", "改用",
            "注意", "记住", "以后", "建议",
        ]

        found_signals = []
        for signal in correction_signals:
            if signal in task:
                found_signals.append(signal)

        if not found_signals:
            return None

        # 用 LLM 提取具体的纠正内容
        prompt = (
            "用户刚刚给你下达了一个包含纠正/指导的任务。\n\n"
            f"用户输入:\n{task[:500]}\n\n"
            "请分析用户的具体纠正意图，按以下 JSON 输出（不要多余文字）：\n"
            "{\n"
            '  "what_was_wrong": "用户指出之前哪里不对",\n'
            '  "what_to_do": "用户要求的正确做法",\n'
            '  "actionable_lesson": "把这个写成一条可执行的教训，下次遇到类似情况能直接使用"\n'
            "}\n"
        )

        try:
            result = self._llm_chat([
                {"role": "system", "content": "你是夸父的纠错分析模块。输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ])
            content = self._parse_llm_output(result)

            parsed = json.loads(content) if content else {}
            lesson = parsed.get("actionable_lesson", "")
            if lesson:
                return {
                    "type": "user_correction",
                    "priority": "A",
                    "title": f"用户纠正: {parsed.get('what_was_wrong', '?')[:60]}",
                    "detail": lesson[:300],
                    "severity": 5,
                    "raw": {
                        "what_was_wrong": parsed.get("what_was_wrong", ""),
                        "what_to_do": parsed.get("what_to_do", ""),
                        "signals_found": found_signals,
                    },
                }
        except Exception as e:
            # 降级：不依赖 LLM，直接提取关键信息
            task_words = task[:200]
            return {
                "type": "user_correction",
                "priority": "A",
                "title": f"用户包含纠正信号: {found_signals[0]}",
                "detail": f"用户在任务中说: {task_words}",
                "severity": 4,
            }

        return None

    def _detect_repeat_failure(self, task_result: dict) -> Optional[dict]:
        """检测重复失败信号。

        触发条件：
        - 当前任务失败
        - 同类型任务在 evolution_log 中连续失败 >= 2 次
        """
        if task_result.get("success", True):
            return None

        task_type = task_result.get("task_type", "generic")

        # 读取进化日志
        logs = self._load_evolution_logs()
        if not logs:
            return None

        # 寻找同类型的连续失败
        recent_same_type = [
            e for e in logs[-20:]
            if e.get("target", "") == task_type
            or e.get("trigger", "").find(task_type) >= 0
            or e.get("action", "").find("失败") >= 0
        ]

        # 计算该类型任务失败次数（最近 10 条中）
        failures = sum(1 for e in recent_same_type[-10:]
                       if not e.get("success", True)
                       or "失败" in e.get("trigger", "")
                       or "错误" in e.get("trigger", ""))

        if failures < 2:
            return None

        # 获取具体错误信息
        errors = task_result.get("errors", [])
        error_detail = "; ".join(errors[:3]) if errors else "未知错误"

        consecutive_failures = self._count_consecutive_failures(logs, task_type)

        return {
            "type": "repeat_failure",
            "priority": "A",
            "title": f"任务类型「{task_type}」连续失败 {consecutive_failures} 次",
            "detail": (
                f"最近 {failures}/{len(recent_same_type)} 次同类型任务失败。"
                f"最新错误: {error_detail[:200]}"
            ),
            "severity": min(4, consecutive_failures),
            "raw": {
                "task_type": task_type,
                "consecutive_failures": consecutive_failures,
                "total_failures": failures,
                "errors": errors,
            },
        }

    def _detect_unknown_error(self, task_result: dict) -> Optional[dict]:
        """检测未知错误信号。

        触发条件：
        - 当前任务出错
        - 错误信息不在已知错误库中
        """
        if task_result.get("success", True):
            return None

        errors = task_result.get("errors", [])
        if not errors:
            return None

        new_errors = []
        for err in errors:
            err_short = err[:120]
            if not self._is_known_error(err_short):
                self._known_errors.append(err_short)
                new_errors.append(err_short)

        if not new_errors:
            return None

        # 保存更新的已知错误库
        self._save_known_errors()

        return {
            "type": "unknown_error",
            "priority": "B",
            "title": f"遇到新错误模式: {new_errors[0][:60]}",
            "detail": "; ".join(new_errors)[:300],
            "severity": 3,
            "raw": {"new_errors": new_errors},
        }

    def _detect_knowledge_gap(self, task_result: dict) -> Optional[dict]:
        """检测知识缺口信号。

        触发条件：
        - 任务中某个工具频繁出错（重试 >= 2 次）
        - 或任务类型是陌生的（非 generic 且之前很少做过）
        """
        errors = task_result.get("errors", [])
        if not errors:
            return None

        task_type = task_result.get("task_type", "generic")

        # 如果连续失败且错误与特定工具/操作相关
        tool_specific_failures = [
            e for e in errors
            if any(t in e.lower() for t in ["tool", "工具", "超时", "timeout",
                                             "not found", "权限", "permission",
                                             "连接", "connection", "格式", "format"])
        ]

        if not tool_specific_failures:
            return None

        return {
            "type": "knowledge_gap",
            "priority": "B",
            "title": f"对 {task_type} 任务的知识不足",
            "detail": f"工具相关错误: {'; '.join(tool_specific_failures[:2])}",
            "severity": 2,
            "raw": {
                "task_type": task_type,
                "tool_errors": tool_specific_failures,
            },
        }

    def _detect_new_pattern(self, task_result: dict, messages: list) -> Optional[dict]:
        """检测新模式发现信号。

        触发条件：
        - 任务成功
        - 工具调用 > 3 次（说明做了实际工作）
        - 之前没有类似的成功记录
        """
        success = task_result.get("success", False)
        if not success:
            return None

        # 检查是否做了真正的工作
        tool_count = 0
        tools_used = set()
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tool_count += 1
                    fn = tc.get("function", {}).get("name", "")
                    if fn:
                        tools_used.add(fn)

        if tool_count < 3:
            return None

        task_type = task_result.get("task_type", "generic")
        result_text = task_result.get("result", "")

        # 用 LLM 判断是否发现了新方法
        prompt = (
            "分析以下任务，判断夸父是否发现了一种新的有效工作模式。\n\n"
            f"任务类型: {task_type}\n"
            f"成功: 是\n"
            f"工具调用次数: {tool_count}\n"
            f"使用工具: {', '.join(sorted(tools_used))}\n"
            f"结果摘要: {result_text[:500]}\n\n"
            "如果这看起来是夸父第一次成功解决这类任务，"
            "请用以下 JSON 格式描述这个新模式（不要多余文字）。\n"
            "如果不算新模式（例如只是简单的文件操作），输出 null。\n"
            "{\n"
            '  "pattern_name": "给这个模式取个简短的名字（10字内）",\n'
            '  "when_to_use": "什么场景下使用这个模式",\n'
            '  "steps": ["步骤1", "步骤2", "步骤3"],\n'
            '  "key_tools": ["工具1", "工具2"]\n'
            "}"
        )

        try:
            result = self._llm_chat([
                {"role": "system", "content": "你是夸父的模式识别模块。输出严格 JSON 或 null。"},
                {"role": "user", "content": prompt},
            ])
            content = self._parse_llm_output(result)
            if not content or content.strip() == "null":
                return None

            parsed = json.loads(content)
            pattern_name = parsed.get("pattern_name", "")
            if not pattern_name:
                return None

            return {
                "type": "new_pattern",
                "priority": "S",
                "title": f"发现新模式: {pattern_name}",
                "detail": (
                    f"场景: {parsed.get('when_to_use', '')}\n"
                    f"步骤: {json.dumps(parsed.get('steps', []), ensure_ascii=False)}"
                )[:300],
                "severity": 1,
                "raw": parsed,
            }
        except Exception:
            return None

    # ── 辅助方法 ────────────────────────────────────────────────────

    def _persist_signal(self, signal: dict):
        """将学习信号写入记忆。"""
        tags = ["learning", "signal", signal["type"], signal["priority"]]
        key = f"learn:{signal['type']}:{int(time.time())}"

        # 高优先级的信号写入更完整的描述
        if signal["priority"] in ("A",):
            content = (
                f"[{signal['priority']}] {signal['title']}\n"
                f"详情: {signal['detail']}"
            )
        else:
            content = signal["title"]

        try:
            self._remember(key=key, content=content, tags=tags)
            if signal["priority"] == "A":
                logger.info(f"[Learner] 🔴 重要信号已保存: {signal['title']}")
            else:
                logger.debug(f"[Learner] 信号已保存: {signal['title']}")
        except Exception as e:
            logger.warning(f"[Learner] 保存信号失败: {e}")

    def _load_evolution_logs(self) -> list:
        """读取进化日志。"""
        if not EVOLUTION_LOG.exists():
            return []
        try:
            return json.loads(EVOLUTION_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Learner] 读取进化日志失败: {e}")
            return []

    def _load_known_errors(self) -> list:
        """加载已知错误库。"""
        if KNOWN_ERRORS_FILE.exists():
            try:
                data = json.loads(KNOWN_ERRORS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save_known_errors(self):
        """保存已知错误库。"""
        try:
            KNOWN_ERRORS_FILE.parent.mkdir(parents=True, exist_ok=True)
            KNOWN_ERRORS_FILE.write_text(
                json.dumps(self._known_errors[-200:], ensure_ascii=False, indent=2)
            )
        except OSError:
            pass

    def _is_known_error(self, error_text: str) -> bool:
        """判断错误是否已记录过（模糊匹配）。"""
        if not self._known_errors:
            return False
        # 检查是否与已知错误相似（共享关键短语）
        for known in self._known_errors:
            # 计算共同的单词/短语
            words_known = set(known.lower().split())
            words_err = set(error_text.lower().split())
            if len(words_known) > 3 and words_err.intersection(words_known):
                # 有重叠
                overlap = words_err.intersection(words_known)
                if len(overlap) >= min(3, len(words_known) // 2):
                    return True
        return False

    @staticmethod
    def _count_consecutive_failures(logs: list, task_type: str) -> int:
        """计算同类型任务的连续失败次数。"""
        count = 0
        for e in reversed(logs):
            target = e.get("target", "")
            if not e.get("success", True):
                if target == task_type or "失败" in e.get("trigger", ""):
                    count += 1
                else:
                    break
            else:
                break
        return count

    # ── 日常学习提取（新增） ─────────────────────────────────────────

    def _extract_daily_lesson(self, task_result: dict, task: str) -> Optional[dict]:
        """每次任务执行后提取日常学习内容。
        
        不依赖失败/纠正等特殊条件，每次 after_task 都会尝试运行。
        用极简方式把任务中有价值的经验提取出来，让夸父持续积累。
        """
        if not task:
            return None

        task_type = task_result.get("task_type", "generic")
        result_preview = (task_result.get("result", "") or "")[:200]
        success = task_result.get("success", False)

        # 成功任务：提取做了什么
        if success:
            # 简单任务（打开文件、查看状态等）不提取，太 trivial
            trivial_keywords = ["查看", "打开", "读取", "显示", "列出", "检查", "status", "ls", "cat"]
            if any(kw in task.lower() for kw in trivial_keywords):
                task_len = len(task)
                if task_len < 50 and len(result_preview) < 100:
                    return None

            return {
                "type": "daily_lesson",
                "priority": "S",
                "title": f"完成: {task[:60]}",
                "detail": (
                    f"任务类型: {task_type}\n"
                    f"结果: {result_preview[:200]}"
                )[:300],
                "severity": 1,
            }

        # 失败任务：提取教训
        errors = task_result.get("errors", [])
        error_text = "; ".join(errors[:3])[:200] if errors else "未知原因"

        return {
            "type": "daily_lesson",
            "priority": "B",
            "title": f"失败: {task[:60]}",
            "detail": f"错误: {error_text}",
            "severity": 2,
        }

    @staticmethod
    def _parse_llm_output(result) -> str:
        """从 LLM 返回值中提取文本内容。"""
        if isinstance(result, dict):
            content = result.get("content", "")
            if isinstance(content, str):
                return content
        elif isinstance(result, str):
            return result
        return ""
