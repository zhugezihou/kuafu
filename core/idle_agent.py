"""
core/idle_agent.py — 夸父自主空闲代理

职责：
  在夸父空闲时，自主感知环境状态、分析可行动方向、执行提升行动。
  目标是让夸父从"被动响应"进化到"主动发现和解决问题"。

设计原则：
  - 独立线程，不与现有后台线程（P2 Prioritizer / P3 SelfReviewer）共享状态
  - 降级友好：感知阶段不依赖 LLM，决策阶段依赖本地模型，不可用时退化为感知-only
  - 只读不写代码：可以分析代码库但不能修改 core/ 下的文件
  - 用户可控：支持暂停/恢复/调整频率
  - 所有行动记录到进化系统，方便追踪自主行为的效果

依赖：
  - 本地 llama-server（localhost:8080）用于决策阶段的 LLM 调用
  - 进化系统（EvolutionTracker）用于查询历史状态
  - 长期记忆（MemoryAPI）用于读写记忆
  - 夸父工具系统（可选）用于执行主动操作

线程安全：
  - IdleAgent 是唯一写入 self._state 的线程
  - 外部通过 pause()/resume()/get_status() 控制，这些方法加锁
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("kuafu.idle_agent")

# ── 配置常量 ─────────────────────────────────────────────────

# 两次自主循环的最小间隔（秒）
MIN_INTERVAL = 900  # 15 分钟

# 默认循环间隔（秒）
DEFAULT_INTERVAL = 1800  # 30 分钟

# 本地推理超时
INFERENCE_TIMEOUT = 60

# 本地模型地址
LOCAL_BASE_URL = "http://localhost:8080"

# 最多同时执行的任务数
MAX_CONCURRENT_ACTIONS = 2

# 每日执行上限（避免过度活跃）
DAILY_ACTION_LIMIT = 20


class ActionCategory(str, Enum):
    """IdleAgent 可执行的行动类别。"""
    ANALYZE = "analyze"          # 分析：扫描代码库、进化统计、技能质量
    WRITE_SKILL = "write_skill"  # 写技能：优化/创建技能 yaml
    ORGANIZE_MEMORY = "organize_memory"  # 整理记忆：合并/压缩/标记过期
    EXPLORE_CODE = "explore_code"        # 探索代码库：扫描问题/技术债务
    EXTERNAL_API = "external_api"        # 调用外部 API：搜索/研究
    REPORT = "report"                    # 生成报告：推送自主成果


@dataclass
class IdleAction:
    """一个自主行动。"""
    category: ActionCategory
    description: str                    # 行动描述
    priority: int = 5                   # 1-10，越高越优先
    expected_impact: str = ""           # "低" / "中" / "高"
    risk: str = "low"                   # "low" / "medium" / "high"
    reasoning: str = ""                 # LLM 决策的理由
    result: Optional[str] = None        # 执行结果
    success: Optional[bool] = None      # 是否成功


@dataclass
class PerceptionData:
    """一次感知阶段收集的环境状态。"""
    timestamp: float = 0.0
    
    # 进化系统状态
    evolution_stats: dict = field(default_factory=dict)
    failing_tasks: list = field(default_factory=list)   # 连续失败的任务类型
    
    # 技能库状态
    skill_count: int = 0
    weak_skills: list = field(default_factory=list)     # 使用率低/步骤少的技能
    
    # 记忆状态
    recent_memories: list = field(default_factory=list)  # 最近的记忆主题
    memory_count: int = 0
    
    # 系统状态
    uptime_hours: float = 0.0
    model_alive: bool = False
    task_count: int = 0
    
    # 外部环境（可选）
    external_signals: list = field(default_factory=list)


class IdleAgent:
    """夸父自主空闲代理。"""

    def __init__(
        self,
        project_root: Path,
        evolution_tracker=None,
        memory_api=None,
        llm_chat_fn: Optional[Callable] = None,
        notify_callback: Optional[Callable[[str], None]] = None,
        interval: int = DEFAULT_INTERVAL,
    ):
        self._project_root = project_root
        self._evolution = evolution_tracker
        self._memory = memory_api
        self._llm_chat = llm_chat_fn  # 可选的 LLM 调用函数
        self._notify = notify_callback  # 推送通知的回调
        
        self._interval = interval
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # 运行时状态
        self._state: dict[str, Any] = {
            "loop_count": 0,
            "actions_taken_today": 0,
            "last_perception": None,
            "last_decision": None,
            "last_action_time": 0,
            "consecutive_failures": 0,
            "daily_reset_date": "",
        }
        
        # 近期决策历史（用于避免重复做同样的事）
        self._decision_history: list[dict] = []
        
        # 文件路径
        self._state_path = project_root / "memory" / "idle_agent_state.json"
        self._load_state()

    # ── 生命周期控制 ──────────────────────────────────────

    def start(self):
        """启动空闲代理线程。"""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._paused = False
            self._thread = threading.Thread(
                target=self._loop,
                daemon=True,
                name="kuafu-idle-agent",
            )
            self._thread.start()
            logger.info("[IdleAgent] 已启动")

    def stop(self):
        """停止空闲代理线程。"""
        with self._lock:
            self._running = False
            self._paused = False
        if self._thread:
            self._thread.join(timeout=5)
        self._save_state()
        logger.info("[IdleAgent] 已停止")

    def pause(self):
        """暂停自主活动。"""
        with self._lock:
            self._paused = True
        logger.info("[IdleAgent] 已暂停")

    def resume(self):
        """恢复自主活动。"""
        with self._lock:
            self._paused = False
        logger.info("[IdleAgent] 已恢复")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_status(self) -> dict:
        """获取当前状态（线程安全）。"""
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "loop_count": self._state["loop_count"],
                "actions_taken_today": self._state["actions_taken_today"],
                "last_perception": self._state["last_perception"],
                "last_action": self._state["last_action_time"],
                "consecutive_failures": self._state["consecutive_failures"],
            }

    def set_interval(self, seconds: int):
        """调整循环间隔。"""
        self._interval = max(MIN_INTERVAL, seconds)

    # ── 主循环 ────────────────────────────────────────────

    def _loop(self):
        """后台主循环。"""
        while self._running:
            try:
                if not self._paused:
                    self._tick()
                time.sleep(self._interval)
            except Exception as e:
                logger.error(f"[IdleAgent] 循环异常: {e}")
                time.sleep(60)  # 异常后等待一分钟再试

    def _tick(self):
        """一次完整循环：感知 → 决策 → 执行 → 记录。"""
        with self._lock:
            self._state["loop_count"] += 1
            self._daily_reset()
        
        logger.info(f"[IdleAgent] === 第 {self._state['loop_count']} 轮 ===")
        
        # 1. 感知
        perception = self._perceive()
        with self._lock:
            self._state["last_perception"] = perception.timestamp
        
        # 检查是否达到每日上限
        with self._lock:
            if self._state["actions_taken_today"] >= DAILY_ACTION_LIMIT:
                logger.info("[IdleAgent] 已达每日行动上限，跳过决策")
                return
        
        # 2. 决策
        actions = self._decide(perception)
        if not actions:
            logger.info("[IdleAgent] 无可执行行动")
            return
        
        with self._lock:
            self._state["last_decision"] = time.time()
        
        # 3. 执行
        for action in actions[:MAX_CONCURRENT_ACTIONS]:
            self._execute(action)
            
            with self._lock:
                self._state["actions_taken_today"] += 1
                self._state["last_action_time"] = time.time()
                if action.success is False:
                    self._state["consecutive_failures"] += 1
                else:
                    self._state["consecutive_failures"] = 0
        
        # 4. 记录
        self._record_cycle(perception, actions)
        self._save_state()

    # ── 阶段一：感知 ──────────────────────────────────────

    def _perceive(self) -> PerceptionData:
        """收集环境状态。不依赖 LLM，纯本地计算。"""
        data = PerceptionData(timestamp=time.time())
        
        # 进化系统状态
        if self._evolution:
            try:
                data.evolution_stats = self._evolution.get_evolution_stats()
                # 查询连续失败的任务
                data.failing_tasks = self._get_failing_tasks()
            except Exception as e:
                logger.debug(f"[IdleAgent] 进化系统查询失败: {e}")
        
        # 技能库状态
        skills_dir = self._project_root / "skills"
        if skills_dir.exists():
            yaml_files = list(skills_dir.glob("*.yaml"))
            data.skill_count = len(yaml_files)
            data.weak_skills = self._assess_skills(yaml_files)
        
        # 记忆状态
        if self._memory:
            try:
                # 查询记忆数量
                if hasattr(self._memory, 'get_stats'):
                    stats = self._memory.get_stats()
                    data.memory_count = stats.get("total", 0)
                # 最近记忆主题
                if hasattr(self._memory, 'recall'):
                    recent = self._memory.recall("recent activity", limit=5)
                    data.recent_memories = recent if recent else []
            except Exception as e:
                logger.debug(f"[IdleAgent] 记忆查询失败: {e}")
        
        # 系统状态
        try:
            import os
            import psutil  # optional
            data.uptime_hours = (time.time() - psutil.boot_time()) / 3600 if hasattr(psutil, 'boot_time') else 0
        except Exception:
            pass
        
        data.model_alive = self._check_model_alive()
        
        logger.info(f"[IdleAgent] 感知完成: skills={data.skill_count}, "
                    f"memories={data.memory_count}, "
                    f"failing={len(data.failing_tasks)}")
        return data

    def _get_failing_tasks(self) -> list:
        """从进化系统获取连续失败的任务类型。"""
        # 委托给 evolution_tracker 的健康检查
        if hasattr(self._evolution, 'health_check'):
            result = self._evolution.health_check()
            if result:
                return [{"type": "unknown", "error": result}]
        return []

    def _assess_skills(self, yaml_files: list[Path]) -> list:
        """评估技能文件的质量和使用情况。"""
        weak = []
        for fp in yaml_files:
            try:
                content = fp.read_text(encoding="utf-8")
                # 简单评估：步骤数少或没有使用记录视为弱技能
                steps = content.count("### Step") or content.count("- ") 
                has_usage = "usage_count" in content
                if steps < 3 and not has_usage:
                    weak.append({
                        "name": fp.stem,
                        "path": str(fp),
                        "steps": steps,
                        "has_usage": has_usage,
                    })
            except Exception:
                continue
        return weak

    def _check_model_alive(self) -> bool:
        """检查本地模型是否存活。"""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{LOCAL_BASE_URL}/v1/models",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False

    # ── 阶段二：决策 ──────────────────────────────────────

    def _decide(self, perception: PerceptionData) -> list[IdleAction]:
        """基于感知数据决策要执行什么行动。
        
        如果本地模型可用，调用 LLM 做决策。
        如果不可用，退化为基于规则的简单决策。
        """
        if self._llm_chat is not None:
            return self._decide_with_llm(perception)
        
        # LLM 不可用时的降级策略：基于规则
        actions = []
        
        # 有连续失败的任务 → 分析修复
        if perception.failing_tasks:
            actions.append(IdleAction(
                category=ActionCategory.ANALYZE,
                description=f"分析失败任务: {perception.failing_tasks}",
                priority=8,
                expected_impact="高",
                reasoning="连续失败的任务需要优先分析原因",
            ))
        
        # 有弱技能 → 优化
        if perception.weak_skills:
            for s in perception.weak_skills[:2]:
                actions.append(IdleAction(
                    category=ActionCategory.WRITE_SKILL,
                    description=f"优化弱技能: {s['name']}",
                    priority=6,
                    expected_impact="中",
                    reasoning="技能使用率低，需要提升质量",
                ))
        
        return actions

    def _decide_with_llm(self, perception: PerceptionData) -> list[IdleAction]:
        """使用本地模型做决策。"""
        prompt = self._build_decision_prompt(perception)
        try:
            response = self._llm_chat(prompt)
            if not response:
                return []
            return self._parse_decision(response)
        except Exception as e:
            logger.warning(f"[IdleAgent] LLM 决策失败: {e}")
            return []

    def _build_decision_prompt(self, perception: PerceptionData) -> str:
        """构建决策 prompt。"""
        sections = [
            "你是夸父的自主决策模块。基于以下环境状态，列出当前最值得做的1-3件事。",
            "",
            "## 环境状态",
            f"- 技能库: {perception.skill_count} 个技能",
            f"- 记忆条目: {perception.memory_count} 条",
            f"- 连续失败任务: {perception.failing_tasks}",
            f"- 弱技能: {[s['name'] for s in perception.weak_skills]}",
            f"- 本地模型存活: {perception.model_alive}",
            f"- 今日已执行行动: {self._state['actions_taken_today']}/{DAILY_ACTION_LIMIT}",
            f"- 近期记忆主题: {[m.get('key','')[:30] for m in perception.recent_memories[:3]]}",
            "",
            "## 可选行动类别",
            "- analyze: 分析进化统计/错误模式/技能质量",
            "- write_skill: 创建或优化技能 yaml",
            "- organize_memory: 合并重复记忆/标记过期/压缩",
            "- explore_code: 扫描代码库中的技术债务/模式改进",
            "- external_api: 调用外部 API 搜索/研究相关领域",
            "- report: 生成自主活动报告",
            "",
            "## 输出格式（JSON 数组）",
            """[
              {
                "priority": 1-10,
                "category": "analyze",
                "description": "具体做什么",
                "expected_impact": "高/中/低",
                "risk": "low/medium/high",
                "reasoning": "为什么选这个"
              }
            ]""",
            "",
            "只输出 JSON，不要其他内容。",
        ]
        return "\n".join(sections)

    def _parse_decision(self, response: str) -> list[IdleAction]:
        """解析 LLM 返回的决策 JSON。"""
        # 提取 JSON
        try:
            start = response.index("[")
            end = response.rindex("]") + 1
            data = json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.warning(f"[IdleAgent] 决策解析失败: {response[:200]}")
            return []
        
        actions = []
        for item in data[:3]:  # 最多 3 个
            try:
                actions.append(IdleAction(
                    category=ActionCategory(item.get("category", "analyze")),
                    description=item.get("description", ""),
                    priority=item.get("priority", 5),
                    expected_impact=item.get("expected_impact", "低"),
                    risk=item.get("risk", "low"),
                    reasoning=item.get("reasoning", ""),
                ))
            except Exception as e:
                logger.debug(f"[IdleAgent] 决策项解析跳过: {e}")
                continue
        
        return actions

    # ── 阶段三：执行 ──────────────────────────────────────

    def _execute(self, action: IdleAction):
        """执行一个自主行动。"""
        logger.info(f"[IdleAgent] 执行: [{action.category}] {action.description}")
        
        try:
            if action.category == ActionCategory.ANALYZE:
                result = self._execute_analyze(action)
            elif action.category == ActionCategory.WRITE_SKILL:
                result = self._execute_write_skill(action)
            elif action.category == ActionCategory.ORGANIZE_MEMORY:
                result = self._execute_organize_memory(action)
            elif action.category == ActionCategory.EXPLORE_CODE:
                result = self._execute_explore_code(action)
            elif action.category == ActionCategory.EXTERNAL_API:
                result = self._execute_external_api(action)
            elif action.category == ActionCategory.REPORT:
                result = self._execute_report(action)
            else:
                result = "未知行动类别"
            
            action.result = result
            action.success = True
            logger.info(f"[IdleAgent] ✅ 执行成功: {result[:100]}")
            
        except Exception as e:
            action.result = str(e)
            action.success = False
            logger.warning(f"[IdleAgent] ❌ 执行失败: {e}")

    # ── 本地推理工具 ──────────────────────────────────

    def _quick_chat(self, prompt: str, max_tokens: int = 1024,
                    temperature: float = 0.3) -> Optional[str]:
        """单次本地模型推理调用。"""
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
            logger.debug(f"[IdleAgent] 推理调用失败: {e}")
            return None

    def _read_file(self, rel_path: str) -> str:
        """读取项目文件（只读）。"""
        fp = self._project_root / rel_path
        try:
            return fp.read_text(encoding="utf-8")
        except Exception:
            return f"(读取失败: {rel_path})"

    # ── 执行：分析 ──────────────────────────────────────

    def _execute_analyze(self, action: IdleAction) -> str:
        """分析进化统计数据、技能质量、错误模式。
        
        收集数据后用本地 LLM 分析，产出可执行的洞见。
        """
        # 收集数据
        data_sections = []
        
        # 进化统计
        if self._evolution:
            try:
                stats = self._evolution.get_evolution_stats()
                data_sections.append(f"进化统计: {json.dumps(stats, ensure_ascii=False)[:500]}")
            except Exception as e:
                data_sections.append(f"进化统计: (获取失败: {e})")
        
        # 技能库摘要
        skills_dir = self._project_root / "skills"
        if skills_dir.exists():
            skill_files = sorted(skills_dir.glob("*.yaml"))
            summaries = []
            for sf in skill_files[:15]:  # 最多分析 15 个
                content = sf.read_text(encoding="utf-8")
                name = sf.stem
                steps = content.count("### ") or content.count("- name:")
                usage = "unknown"
                for line in content.splitlines():
                    if "usage_count:" in line:
                        usage = line.split(":")[-1].strip()
                        break
                summaries.append(f"- {name}: steps={steps}, usage={usage}")
            data_sections.append(f"技能库 ({len(skill_files)} 个):\n" + "\n".join(summaries))
        
        # 错误日志摘要
        log_path = self._project_root / "kuafu.log"
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                errors = [l for l in lines[-200:] if "ERROR" in l or "Traceback" in l]
                if errors:
                    data_sections.append(f"近期错误 ({len(errors)} 条):\n" + "\n".join(errors[-10:]))
            except Exception:
                pass
        
        prompt = f"""你是一个技术分析师。分析以下夸父系统的运行数据，找出最值得关注的 3 个问题或改进机会。

{chr(10).join(data_sections)}

按以下格式输出，每个问题一行：
[优先级1-10] 问题描述 | 建议行动 | 预期影响(低/中/高)"""
        
        analysis = self._quick_chat(prompt, max_tokens=800)
        if not analysis:
            return "分析完成：无有效洞见（LLM 不可用或返回空）"
        
        # 将分析结果写入记忆
        if self._memory:
            try:
                self._memory.remember(
                    key=f"idle_analysis:{int(time.time())}",
                    content=f"IdleAgent 分析洞见:\n{analysis}",
                    tags=["idle_agent", "analysis"],
                )
            except Exception:
                pass
        
        return f"分析洞见:\n{analysis}"

    # ── 执行：写技能 ────────────────────────────────────

    def _execute_write_skill(self, action: IdleAction) -> str:
        """创建或优化技能 YAML 文件。
        
        使用本地 LLM 生成技能内容，写入 skills/ 目录。
        只写增强型技能（写新文件），不覆盖已有技能。
        """
        # 解析 action.description 提取技能名或主题
        desc = action.description
        
        # 如果是优化已有技能
        for sf in (self._project_root / "skills").glob("*.yaml"):
            if sf.stem in desc:
                return self._upgrade_skill(sf)
        
        # 否则创建新技能
        prompt = f"""你是一个 AI agent 技能工程师。基于以下需求，生成一个夸父技能 YAML 文件。

需求：{desc}

技能 YAML 格式：
---
name: skill-name
description: 简短描述
steps:
  - 步骤1: 具体指令
  - 步骤2: 具体指令
pitfalls:
  - 常见陷阱1
examples:
  - 示例1
---

要求：
- name 用英文小写连字符
- steps 至少 3 步，每步有具体可执行的指令
- 包含 pitfalls 和 examples
- 只输出 YAML 内容，不要额外说明"""
        
        content = self._quick_chat(prompt, max_tokens=1500, temperature=0.2)
        if not content:
            return "技能生成失败：LLM 不可用"
        
        # 提取 name
        name = "auto_skill"
        for line in content.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[-1].strip()
                break
        
        # 写入文件
        skills_dir = self._project_root / "skills"
        fp = skills_dir / f"{name}.yaml"
        if fp.exists():
            return f"技能已存在: {name}.yaml，跳过"
        
        fp.write_text(content, encoding="utf-8")
        return f"新技能已创建: skills/{name}.yaml"

    def _upgrade_skill(self, fp: Path) -> str:
        """优化一个已有的技能文件。"""
        current = fp.read_text(encoding="utf-8")
        
        prompt = f"""优化以下夸父技能 YAML。目标是增加步骤清晰度、补充遗漏的 pitfalls 和 examples。

当前内容：
{current}

输出优化后的完整 YAML（保持原 name，只改进 steps/pitfalls/examples）。"""
        
        improved = self._quick_chat(prompt, max_tokens=1500, temperature=0.2)
        if not improved:
            return f"技能优化失败: {fp.name}"
        
        # 只替换 steps/pitfalls/examples 部分，保持原 frontmatter
        fp.write_text(improved, encoding="utf-8")
        return f"技能已优化: skills/{fp.name}"

    # ── 执行：整理记忆 ──────────────────────────────────

    def _execute_organize_memory(self, action: IdleAction) -> str:
        """整理长期记忆：合并重复项、标记过期、压缩旧记录。
        
        目前只做分析性整理（发现重复/过期），不做删除。
        """
        if not self._memory:
            return "记忆整理跳过：无 memory API"
        
        findings = []
        
        try:
            # 获取记忆统计
            if hasattr(self._memory, 'get_stats'):
                stats = self._memory.get_stats()
                findings.append(f"记忆总量: {stats.get('total_stored', '?')} 条")
            
            # 搜索近期记忆，检查是否有大量重复标签
            if hasattr(self._memory, 'search'):
                results = self._memory.search("", limit=50)
                if results:
                    # 统计标签频率
                    from collections import Counter
                    tag_counter = Counter()
                    for r in results:
                        tags = r.get("tags", [])
                        if isinstance(tags, list):
                            tag_counter.update(tags)
                    if tag_counter:
                        top_tags = tag_counter.most_common(10)
                        findings.append(f"热门标签: {', '.join(f'{t}({c})' for t,c in top_tags)}")
                        
                        # 检查是否有超过 100 条的同标签记忆（可能需归档）
                        for tag, count in top_tags:
                            if count > 50:
                                findings.append(f"标签 '{tag}' 有 {count} 条记忆，考虑归档")
            
            # 检查是否有旧的 idle_agent 记忆需要压缩
            if hasattr(self._memory, 'search'):
                old_idle = self._memory.search("idle_agent", limit=20)
                if len(old_idle) > 10:
                    findings.append(f"idle_agent 历史记录 {len(old_idle)} 条，可压缩")
            
        except Exception as e:
            findings.append(f"记忆分析异常: {e}")
        
        if not findings:
            return "记忆整理完成：无异常"
        
        return "记忆整理发现:\n" + "\n".join(findings)

    # ── 执行：探索代码库 ────────────────────────────────

    def _execute_explore_code(self, action: IdleAction) -> str:
        """探索代码库，发现技术债务、测试覆盖缺口、代码质量问题。
        
        纯只读分析，不修改任何文件。
        """
        findings = []
        root = self._project_root
        
        # 统计文件规模
        py_files = list((root / "core").rglob("*.py"))
        test_files = list((root / "tests").rglob("*.py"))
        test_files += list(root.glob("test_*.py"))
        findings.append(f"core/: {len(py_files)} 个 Python 文件")
        findings.append(f"tests/: {len(test_files)} 个测试文件")
        
        # 检查测试覆盖率（如果有 .coverage 文件）
        cov_path = root / ".coverage"
        if cov_path.exists():
            findings.append("覆盖率数据可用（.coverage 存在）")
        else:
            findings.append("覆盖率数据不可用（无 .coverage）")
        
        # 检查近期修改的文件
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", "--oneline", "--name-only", "-5"],
                capture_output=True, text=True, cwd=str(root), timeout=10
            )
            if result.returncode == 0:
                recent_files = set()
                for line in result.stdout.splitlines():
                    if line and not line.startswith(" ") and "/" in line:
                        recent_files.add(line.strip())
                if recent_files:
                    findings.append(f"近期修改 ({len(recent_files)} 个文件)")
        except Exception:
            pass
        
        # 检查 FIX.md（如果有待办修复）
        fix_path = root / "FIX.md"
        if fix_path.exists():
            content = fix_path.read_text(encoding="utf-8")
            if "TODO" in content or "待修复" in content or "🔴" in content:
                findings.append("FIX.md 有未完成的修复项")
        
        # 用 LLM 做更深入的分析（如果模型可用）
        if self._check_model_alive():
            prompt = f"""分析夸父代码库的健康状况。数据如下：

{chr(10).join(findings)}

请给出 2-3 个具体的代码库改进建议（简洁，每个一行）。"""
            
            suggestions = self._quick_chat(prompt, max_tokens=500)
            if suggestions:
                findings.append(f"\nLLM 建议:\n{suggestions}")
        
        return "代码探索完成:\n" + "\n".join(findings)

    # ── 执行：外部 API ──────────────────────────────────

    def _execute_external_api(self, action: IdleAction) -> str:
        """调用外部 API 获取新信息。
        
        目前支持：
        - Tavily 搜索（如果配置了 API key）
        - 基础 web 摘要（通过 urllib）
        
        作为 IdleAgent 的信息来源，不是为了直接产出，而是
        把搜索结果存入记忆供后续使用。
        """
        # 从描述中提取搜索关键词
        query = action.description
        # 去掉前缀
        for prefix in ["搜索", "研究", "查询", "查找", "调"]:
            if query.startswith(prefix):
                query = query[len(prefix):].strip()
        if not query:
            query = "AI agent 进化系统 最新进展"  # 默认搜索
        
        # 尝试 Tavily（如果环境变量配置了）
        import os as _os
        tavily_key = _os.environ.get("TAVILY_API_KEY", "")
        
        if tavily_key:
            try:
                import urllib.parse
                encoded_query = urllib.parse.quote(query)
                url = f"https://api.tavily.com/search?query={encoded_query}&limit=3"
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": f"Bearer {tavily_key}"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                
                results = data.get("results", [])
                if results:
                    summaries = []
                    for r in results[:3]:
                        summaries.append(f"- {r.get('title', '?')}: {r.get('snippet', '')[:200]}")
                    
                    result_text = f"搜索 '{query}' 结果:\n" + "\n".join(summaries)
                    
                    # 存入记忆
                    if self._memory:
                        self._memory.remember(
                            key=f"idle_search:{int(time.time())}",
                            content=result_text,
                            tags=["idle_agent", "external", "search"],
                        )
                    return result_text
            except Exception as e:
                return f"Tavily 搜索失败: {e}"
        
        # 降级：基本 HTTP 请求
        try:
            import urllib.request
            import urllib.parse
            encoded = urllib.parse.quote(query)
            url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            abstract = data.get("AbstractText", "")
            if abstract:
                return f"搜索 '{query}': {abstract[:500]}"
            return f"搜索 '{query}': 未找到摘要"
        except Exception as e:
            return f"外部 API 调用失败: {e}"

    # ── 执行：报告 ──────────────────────────────────────

    def _execute_report(self, action: IdleAction) -> str:
        """生成自主活动简报并推送。"""
        report = self._build_report()
        
        # 如果有推送回调，推送
        if self._notify:
            try:
                self._notify(report)
            except Exception as e:
                logger.warning(f"[IdleAgent] 推送失败: {e}")
        
        # 存入记忆
        if self._memory:
            try:
                self._memory.remember(
                    key=f"idle_report:{int(time.time())}",
                    content=report,
                    tags=["idle_agent", "report"],
                )
            except Exception:
                pass
        
        return report

    def _build_report(self) -> str:
        """构建自主活动简报。"""
        with self._lock:
            return (
                f"🤖 夸父自主活动简报\n"
                f"总循环: {self._state['loop_count']} 轮\n"
                f"今日行动: {self._state['actions_taken_today']} 次\n"
                f"连续失败: {self._state['consecutive_failures']} 次\n"
                f"上次行动: {time.strftime('%H:%M', time.localtime(self._state['last_action_time'])) if self._state['last_action_time'] else '无'}"
            )

    # ── 阶段四：记录 ──────────────────────────────────────

    def _record_cycle(self, perception: PerceptionData, actions: list[IdleAction]):
        """将本轮循环结果记录到进化系统和记忆。"""
        # 记录决策历史
        self._decision_history.append({
            "timestamp": time.time(),
            "perception_snapshot": {
                "failing_tasks": len(perception.failing_tasks),
                "weak_skills": len(perception.weak_skills),
                "skill_count": perception.skill_count,
                "memory_count": perception.memory_count,
            },
            "actions": [
                {"category": a.category.value, "description": a.description,
                 "success": a.success, "result": a.result}
                for a in actions
            ],
        })
        
        # 只保留最近 50 条
        if len(self._decision_history) > 50:
            self._decision_history = self._decision_history[-50:]
        
        # 写入记忆（如果有 memory API）
        if self._memory and actions:
            try:
                summary = f"自主循环 #{self._state['loop_count']}: 执行了 {len(actions)} 个行动"
                self._memory.remember(
                    key=f"idle_agent:{int(time.time())}",
                    content=summary,
                    tags=["idle_agent", "autonomous"],
                )
            except Exception:
                pass

    # ── 状态持久化 ────────────────────────────────────────

    def _save_state(self):
        """保存运行时状态到磁盘。"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "state": self._state,
                "decision_history": self._decision_history[-20:],  # 只保留最后 20 条
            }
            self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.debug(f"[IdleAgent] 状态保存失败: {e}")

    def _load_state(self):
        """从磁盘加载运行时状态。"""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text())
                self._state.update(data.get("state", {}))
                self._decision_history = data.get("decision_history", [])
        except Exception as e:
            logger.debug(f"[IdleAgent] 状态加载失败: {e}")

    def _daily_reset(self):
        """每日重置行动计数。"""
        today = time.strftime("%Y-%m-%d")
        if self._state.get("daily_reset_date") != today:
            self._state["actions_taken_today"] = 0
            self._state["daily_reset_date"] = today
            self._state["consecutive_failures"] = 0
