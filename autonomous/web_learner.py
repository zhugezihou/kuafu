"""
autonomous/web_learner.py — P3 主动网络学习引擎

夸父在空闲时主动上网学习，而不是等任务来了才被动学习。

工作原理：
1. 后台线程（daemon）每隔 N 小时自动唤醒
2. 从多个源抓取最新内容：GitHub Trending / Hacker News / 技术资讯
3. 用 LLM 评估每条内容的价值（与自己 skill 的关联？解决了什么问题？）
4. 有价值的 → 生成长期知识笔记 → 写入 memory
5. 特别有价值的 → 尝试生成可复用的 skill
6. 自身也进化：记录所学过的主题，避免重复学习同一个项目

学习源：
- GitHub Trending（今日热门仓库）
- Hacker News 首页（技术讨论热点）

原则：
- 不阻塞主流程：异常时静默失败
- 增量学习：记录已学过的项目，跳过重复
- 轻量级：单次学习循环不超过 3-5 次 LLM 调用
"""

import json
import time
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("kuafu.web_learner")

ROOT_DIR = Path(__file__).resolve().parent.parent
LEARNED_INDEX = ROOT_DIR / "memory" / "web_learned_index.json"  # 已学项目索引，防重复


class WebLearner:
    """P3 主动网络学习引擎。

    启动后台线程后，周期性抓取网络资讯，筛选值得学习的内容，
    将知识沉淀到夸父的记忆和技能体系中。

    用法：
        learner = WebLearner(llm_chat_fn=..., memory_remember_fn=...)
        learner.start(daemon=True)  # 启动后台线程
    """

    def __init__(
        self,
        llm_chat_fn: Callable,                    # callable(messages) -> dict
        memory_remember_fn: Callable,             # callable(key, content, tags)
        memory_recall_fn: Optional[Callable] = None,  # callable(query, limit) -> list
        evolution_emit_fn: Optional[Callable] = None, # callable(level, action, target, payload)
        learn_interval: int = 21600,               # 默认 6 小时
        max_per_cycle: int = 8,                    # 每轮最多学几个项目
    ):
        self._llm_chat = llm_chat_fn
        self._remember = memory_remember_fn
        self._recall = memory_recall_fn
        self._emit_evolution = evolution_emit_fn
        self._interval = learn_interval
        self._max_per_cycle = max_per_cycle

        # 已学项目索引（防止重复学习）
        self._learned: dict = self._load_learned_index()

        # 线程控制
        self._thread: Optional[__import__('threading').Thread] = None
        self._stop_event = __import__('threading').Event()

    # ── 公开接口 ─────────────────────────────────────────────────────

    def start(self, daemon: bool = True):
        """启动后台学习线程。"""
        import threading
        if self._thread and self._thread.is_alive():
            logger.info("[WebLearner] 已在运行，跳过启动")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=daemon,
            name="kuafu-web-learner",
        )
        self._thread.start()
        logger.info(f"[WebLearner] 后台线程已启动（间隔 {self._interval//3600}h，最多 {self._max_per_cycle} 项/轮）")

    def stop(self):
        """停止后台线程。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def stats(self) -> dict:
        return {
            "learned_count": len(self._learned),
            "interval_hours": self._interval // 3600,
            "is_running": self._thread is not None and self._thread.is_alive(),
        }

    # ── 主循环 ───────────────────────────────────────────────────────

    def _run_loop(self):
        """后台主循环：每隔 interval 秒执行一次学习循环。"""
        while not self._stop_event.is_set():
            try:
                self._learn_cycle()
            except Exception as e:
                logger.warning(f"[WebLearner] 学习循环异常: {e}")
            # 等待 interval，但可被 stop() 打断
            self._stop_event.wait(self._interval)
        logger.info("[WebLearner] 后台线程已停止")

    def _learn_cycle(self):
        """一轮完整的学习循环。

        1. 从各个源抓取内容
        2. LLM 评估价值并筛选
        3. 学习有价值的内容 → 写入 memory
        4. 更新已学索引
        """
        logger.info("[WebLearner] 开始新一轮学习...")

        # Step 1: 收集候选学习素材
        candidates = self._fetch_all_sources()
        if not candidates:
            logger.info("[WebLearner] 本轮未获取到候选素材")
            return

        # Step 2: 过滤已学过的 + 用 LLM 评估价值
        new_items = [c for c in candidates if c["id"] not in self._learned]
        if not new_items:
            logger.info("[WebLearner] 所有候选都已学过，跳过")
            return

        # 按推荐度排序（如果来源自带分数）
        new_items.sort(key=lambda x: x.get("score", 0), reverse=True)

        # 最多取 self._max_per_cycle 项进行评估
        to_evaluate = new_items[:self._max_per_cycle * 2]  # 给 LLM 筛选空间

        # Step 3: LLM 评估哪些值得学
        valuable = self._evaluate_with_llm(to_evaluate)
        if not valuable:
            logger.info("[WebLearner] LLM 评估后无值得学习的内容")
            # 但还是把已评估过的标记为已学，避免重复评估
            for item in to_evaluate:
                self._mark_learned(item["id"], item.get("title", ""), status="skipped")
            return

        # 限制真正学习的数量
        to_learn = valuable[:self._max_per_cycle]

        # Step 4: 学习并写入记忆
        learned_topics = []
        for item in to_learn:
            try:
                self._learn_one(item)
                learned_topics.append(item.get("title", item["id"]))
            except Exception as e:
                logger.warning(f"[WebLearner] 学习「{item.get('title', '?')}」失败: {e}")

        # Step 5: 生成学习摘要并触发进化
        if learned_topics:
            summary = self._generate_learning_summary(learned_topics, valuable)
            self._remember(
                key=f"web_learner:cycle:{int(time.time())}",
                content=summary,
                tags=["web_learner", "learning_summary"],
            )
            # 触发 L1 进化（例行学习事件）
            if self._emit_evolution:
                try:
                    self._emit_evolution(
                        level=1,
                        action=f"主动学习: {', '.join(learned_topics[:3])}",
                        target="web_learner",
                        payload={"learned": learned_topics, "count": len(learned_topics)},
                    )
                except Exception:
                    pass
            logger.info(f"[WebLearner] 本轮学习了 {len(learned_topics)} 个项目: {', '.join(learned_topics)}")
        else:
            logger.info("[WebLearner] 本轮无可学习内容")

    # ── 数据源抓取 ──────────────────────────────────────────────────

    def _fetch_all_sources(self) -> list[dict]:
        """从所有源抓取候选列表。"""
        candidates = []
        candidates.extend(self._fetch_github_trending() or [])
        candidates.extend(self._fetch_hackernews() or [])
        return candidates

    def _fetch_github_trending(self) -> Optional[list[dict]]:
        """抓取 GitHub Trending 今日仓库。"""
        try:
            url = "https://api.github.com/search/repositories?q=created:>%s&sort=stars&order=desc&per_page=15" % (
                time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 7))
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Kuafu/0.2.0",
                "Accept": "application/vnd.github.v3+json",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("items", [])
            results = []
            for repo in items[:15]:
                results.append({
                    "id": f"github:{repo['full_name']}",
                    "source": "github_trending",
                    "title": repo["full_name"],
                    "description": repo.get("description", "") or "",
                    "url": repo["html_url"],
                    "score": repo.get("stargazers_count", 0),
                    "extra": {
                        "language": repo.get("language", ""),
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "topics": repo.get("topics", []),
                    },
                })
            logger.info(f"[WebLearner] GitHub Trending: 获取 {len(results)} 个仓库")
            return results
        except Exception as e:
            logger.warning(f"[WebLearner] GitHub Trending 抓取失败: {e}")
            return None

    def _fetch_hackernews(self) -> Optional[list[dict]]:
        """抓取 Hacker News 首页故事。"""
        try:
            # 1. 获取首页故事 ID 列表
            req = urllib.request.Request(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                headers={"User-Agent": "Kuafu/0.2.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                story_ids = json.loads(resp.read().decode("utf-8"))

            # 2. 取前 20 条详情
            results = []
            for sid in story_ids[:20]:
                try:
                    item_req = urllib.request.Request(
                        f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                        headers={"User-Agent": "Kuafu/0.2.0"},
                    )
                    with urllib.request.urlopen(item_req, timeout=10) as item_resp:
                        item = json.loads(item_resp.read().decode("utf-8"))
                    if not item or item.get("type") != "story":
                        continue
                    title = (item.get("title", "") or "")[:200]
                    url = item.get("url", f"https://news.ycombinator.com/item?id={sid}")
                    results.append({
                        "id": f"hn:{sid}",
                        "source": "hackernews",
                        "title": title,
                        "description": (item.get("text", "") or "")[:300],
                        "url": url,
                        "score": item.get("score", 0),
                        "extra": {
                            "by": item.get("by", ""),
                            "comments": item.get("descendants", 0),
                        },
                    })
                except Exception:
                    continue

            logger.info(f"[WebLearner] Hacker News: 获取 {len(results)} 个故事")
            return results
        except Exception as e:
            logger.warning(f"[WebLearner] Hacker News 抓取失败: {e}")
            return None

    # ── LLM 评估 ────────────────────────────────────────────────────

    def _evaluate_with_llm(self, items: list[dict]) -> list[dict]:
        """用 LLM 评估候选列表，返回值得学习的项目（按价值排序）。"""
        if not items:
            return []

        # 把候选摘要给 LLM
        items_summary = []
        for item in items:
            desc = (item.get("description", "") or "")[:150]
            items_summary.append(
                f"- [{item['source']}] {item['title']}: {desc}"
            )

        prompt = (
            "你是一个 AI 技术顾问，帮夸父（一个自我进化的 AI Agent）筛选值得学习的内容。\n\n"
            f"候选项目（共 {len(items)} 项）：\n"
            + "\n".join(items_summary[:30]) +
            "\n\n请评估每项内容对夸父的「学习价值」。有价值的标准：\n"
            "1. 开源项目/工具，可能被夸父集成或使用\n"
            "2. AI / Agent 领域的新发现、新论文、新方法\n"
            "3. 编程技巧、架构模式、最佳实践\n"
            "4. 能解决夸父实际开发中遇到的痛点\n\n"
            "按以下 JSON 格式输出，仅输出数组，不要多余文字：\n"
            "[\n"
            '  {\n'
            '    "index": 0,           // 在候选列表中的序号(从0开始)\n'
            '    "value_score": 8,      // 1-10 分，<6 表示不值得学\n'
            '    "reason": "为什么值得学（15字内）",\n'
            '    "learning_type": "skill | knowledge | reference",  // 学完后变成什么\n'
            '    "key_takeaway": "最值得记住的一句话" \n'
            "  },\n"
            "  ...\n"
            "]\n"
            "只保留 value_score >= 6 的项目。如果都不值得，输出 []。"
        )

        try:
            result = self._llm_chat([
                {"role": "system", "content": "你是夸父的学习筛选器，输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ])
            content = self._parse_llm_output(result)
            if not content:
                return []

            evaluations = json.loads(content)
            if not isinstance(evaluations, list):
                return []

            valuable = []
            for ev in evaluations:
                idx = ev.get("index", -1)
                score = ev.get("value_score", 0)
                if 0 <= idx < len(items) and score >= 6:
                    item = dict(items[idx])
                    item["value_score"] = score
                    item["reason"] = ev.get("reason", "")
                    item["learning_type"] = ev.get("learning_type", "knowledge")
                    item["key_takeaway"] = ev.get("key_takeaway", "")
                    valuable.append(item)

            valuable.sort(key=lambda x: x["value_score"], reverse=True)
            return valuable
        except Exception as e:
            logger.warning(f"[WebLearner] LLM 评估失败: {e}")
            return []

    # ── 学习过程 ────────────────────────────────────────────────────

    def _learn_one(self, item: dict):
        """学习一个项目：深入理解 + 写入记忆。"""
        title = item.get("title", "?")
        source = item.get("source", "")
        url = item.get("url", "")
        value_score = item.get("value_score", 5)
        learning_type = item.get("learning_type", "knowledge")
        reason = item.get("reason", "")
        extra = item.get("extra", {})
        description = (item.get("description", "") or "")[:500]

        # 对 GitHub 项目，额外抓取 README 摘要
        deeper_info = self._fetch_deeper_info(item)

        # 构建知识笔记
        note_parts = [
            f"【主动学习】{title}",
            f"来源: {source.upper()} | 价值评分: {value_score}/10",
            f"URL: {url}",
        ]
        if reason:
            note_parts.append(f"为什么学: {reason}")
        if description:
            note_parts.append(f"简介: {description[:300]}")
        if deeper_info:
            note_parts.append(f"详情: {deeper_info[:500]}")
        if item.get("key_takeaway"):
            note_parts.append(f"要点: {item['key_takeaway']}")

        knowledge_note = "\n".join(note_parts)

        # 写入记忆
        self._remember(
            key=f"web_learn:{learning_type}:{int(time.time())}",
            content=knowledge_note,
            tags=[
                "web_learner", "learned", source, learning_type,
                f"score_{value_score}",
            ],
        )

        # 标记已学
        self._mark_learned(
            item["id"],
            title=title,
            status="learned",
            learning_type=learning_type,
            value_score=value_score,
            note_preview=knowledge_note[:200],
        )

        logger.info(f"[WebLearner] ✅ 已学习: {title} ({value_score}/10)")

    def _fetch_deeper_info(self, item: dict) -> str:
        """对 GitHub 项目，抓取 README 前几行作为更深了解。"""
        if item.get("source") != "github_trending":
            return ""
        full_name = item.get("title", "")
        if "/" not in full_name:
            return ""
        try:
            readme_url = f"https://api.github.com/repos/{full_name}/readme"
            req = urllib.request.Request(readme_url, headers={
                "User-Agent": "Kuafu/0.2.0",
                "Accept": "application/vnd.github.v3.raw",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="replace")
            # 取前 20 行（约 1000 字符）
            lines = content.splitlines()
            return "\n".join(lines[:20])[:800]
        except Exception:
            return ""

    # ── 摘要生成 ────────────────────────────────────────────────────

    def _generate_learning_summary(self, learned: list[str], all_valuable: list[dict]) -> str:
        """生成本轮学习摘要。"""
        lines = [
            f"## 主动学习报告 ({time.strftime('%Y-%m-%d %H:%M')})",
            "",
            f"本学习了 {len(learned)} 个项目：",
        ]
        for title in learned:
            lines.append(f"- {title}")

        # 列出被标记为有价值但本轮未深入学的
        skipped = [v for v in all_valuable if v.get("title") not in learned]
        if skipped:
            lines.append("")
            lines.append(f"另有 {len(skipped)} 个候选未深入学：")
            for s in skipped[:5]:
                lines.append(f"- {s.get('title', '?')} ({s.get('value_score', 0)}分, {s.get('reason', '')})")

        return "\n".join(lines)

    # ── 已学索引管理 ────────────────────────────────────────────────

    def _mark_learned(self, item_id: str, title: str = "", status: str = "learned",
                      learning_type: str = "", value_score: int = 0,
                      note_preview: str = ""):
        """标记一个项目为已学，避免重复。"""
        self._learned[item_id] = {
            "title": title,
            "status": status,
            "learning_type": learning_type,
            "value_score": value_score,
            "learned_at": time.time(),
            "note_preview": note_preview[:200],
        }
        self._save_learned_index()

    def _load_learned_index(self) -> dict:
        """加载已学索引。"""
        if LEARNED_INDEX.exists():
            try:
                data = json.loads(LEARNED_INDEX.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_learned_index(self):
        """保存已学索引（最多保留 500 条）。"""
        try:
            LEARNED_INDEX.parent.mkdir(parents=True, exist_ok=True)
            # 只保留最近 500 条
            items = sorted(self._learned.items(), key=lambda x: x[1].get("learned_at", 0), reverse=True)
            trimmed = dict(items[:500])
            LEARNED_INDEX.write_text(
                json.dumps(trimmed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ── 工具方法 ────────────────────────────────────────────────────

    @staticmethod
    def _parse_llm_output(result) -> str:
        """从 LLM 响应中提取文本内容。"""
        if isinstance(result, dict):
            choices = result.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                if content:
                    # 尝试提取 JSON 块
                    import re as _re
                    json_match = _re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
                    if json_match:
                        return json_match.group(1).strip()
                    return content.strip()
        return ""
