"""
autonomous/web_learner.py — P3 主动网络学习引擎 v2 (持续学习模式)

夸父持续学习，不依赖间隔轮询。学完一批立即抓下一批。

工作原理：
1. 多源循环抓取池（GitHub Trending / HN / GitHub 主题搜索）
2. 每轮从不同源抓取，学完立即换下一个源
3. 源池学完后短暂休息 10 分钟，然后重新开始
4. 被跳过的项目 30 分钟后重新进入候选池（时间敏感内容再次评估）
5. 真正学过的项目标记为永久已学

学习源（轮换制）：
- GitHub Trending（7天内热门仓库）
- Hacker News 前20
- GitHub 主题搜索（topic:ai-agent, topic:python, topic:llm）
- GitHub 主题搜索（topic:autonomous-agents, topic:rag, topic:vector-database）

原则：
- 不阻塞主流程：异常时静默失败
- 持续学习：学完一批立即抓新源
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
LEARNED_INDEX = ROOT_DIR / "memory" / "web_learned_index.json"
SKIPPED_EXPIRE = 1800  # 被跳过的项目 30 分钟后重新评估

# 学习源池（轮换制）
SOURCES = [
    "github_trending",
    "hackernews",
    "github_search_ai_agent",
    "github_search_llm_python",
    "github_search_rag_vector",
    "github_search_autonomous",
]


class WebLearner:
    """P3 主动网络学习引擎 v2 — 持续学习模式。

    后台线程启动后，持续从多个源抓取内容、评估、学习。
    学完一个源立即切换到下一个源。
    所有源学完之后休息 10 分钟再重新开始。
    """

    def __init__(
        self,
        llm_chat_fn: Callable,
        memory_remember_fn: Callable,
        memory_recall_fn: Optional[Callable] = None,
        evolution_emit_fn: Optional[Callable] = None,
        learn_interval: int = 600,         # 默认 10 分钟（源学完后的休息间隔）
        max_per_cycle: int = 6,             # 每轮最多学几个项目
    ):
        self._llm_chat = llm_chat_fn
        self._remember = memory_remember_fn
        self._recall = memory_recall_fn
        self._emit_evolution = evolution_emit_fn
        self._interval = learn_interval
        self._max_per_cycle = max_per_cycle

        # 已学项目索引
        self._learned: dict = self._load_learned_index()

        # 当前源游标
        self._source_index = 0

        # 自主学习模式计数
        self._total_learned_since_start = 0

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
        logger.info("[WebLearner] 后台线程已启动（持续学习模式）")

    def stop(self):
        """停止后台线程。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def set_interval(self, seconds: int):
        """动态调整学习间隔（所有源学完后的休息时间）。"""
        self._interval = max(10, seconds)
        logger.info(f"[WebLearner] 学习间隔调整为 {self._interval}s")

    def set_max_per_cycle(self, count: int):
        """动态调整每轮最大学习项目数。"""
        self._max_per_cycle = max(1, min(count, 20))
        logger.info(f"[WebLearner] 每轮学习上限调整为 {self._max_per_cycle}")

    @property
    def stats(self) -> dict:
        return {
            "learned_count": len(self._learned),
            "total_learned_since_start": self._total_learned_since_start,
            "source_index": self._source_index,
            "is_running": self._thread is not None and self._thread.is_alive(),
            "interval": self._interval,
        }

    # ── 主循环（持续模式）───────────────────────────────────────────

    def _run_loop(self):
        """持续学习主循环：学完一个源立即切换下一个，全部学完休息后重来。"""
        rounds_since_idle = 0
        while not self._stop_event.is_set():
            try:
                source = SOURCES[self._source_index % len(SOURCES)]
                logger.info(f"[WebLearner] 切换到源: {source}（第 {rounds_since_idle + 1} 轮）")

                # 清理过期的 skipped 项目
                self._cleanup_expired_skipped()

                # 执行一轮学习
                learned = self._learn_from_source(source)

                if learned:
                    rounds_since_idle = 0  # 有收获，重置空闲计数
                    # 学完立即切下一个源（不等待）
                    self._source_index += 1
                    continue
                else:
                    # 当前源无新内容，切下一个
                    self._source_index += 1
                    rounds_since_idle += 1

                # 如果所有源都轮过一遍且没学到东西，休息 interval
                if rounds_since_idle >= len(SOURCES):
                    logger.info(f"[WebLearner] 所有源学完，休息 {self._interval // 60} 分钟后重新开始")
                    self._stop_event.wait(self._interval)
                    rounds_since_idle = 0
                    self._source_index = 0

            except Exception as e:
                logger.warning(f"[WebLearner] 学习循环异常: {e}")
                self._stop_event.wait(60)  # 出错后等 1 分钟重试

        logger.info("[WebLearner] 后台线程已停止")

    def _learn_from_source(self, source: str) -> bool:
        """从指定源学习，返回是否学到了内容。"""
        # Step 1: 抓取
        candidates = self._fetch_source(source)
        if not candidates:
            logger.info(f"[WebLearner] 源「{source}」无候选素材")
            return False

        # Step 2: 过滤已学（包括真正的 learned + 还未过期的 skipped）
        new_items = [c for c in candidates if self._is_effectively_new(c["id"])]
        if not new_items:
            logger.info(f"[WebLearner] 源「{source}」所有候选已学或过期前跳过")
            return False

        logger.info(f"[WebLearner] 源「{source}」: {len(new_items)}/{len(candidates)} 新候选项")

        # Step 3: 用 LLM 评估
        valuable = self._evaluate_with_llm(new_items)
        if not valuable:
            logger.info(f"[WebLearner] LLM 评估「{source}」无值得学习的内容")
            # skip 标记（但保留给30分钟后重评）
            for item in new_items:
                self._mark_learned(
                    item["id"], item.get("title", ""), status="skipped",
                    value_score=0,
                )
            return False

        # Step 4: 学习有价值的内容
        to_learn = valuable[:self._max_per_cycle]
        learned_topics = []
        for item in to_learn:
            try:
                self._learn_one(item)
                learned_topics.append(item.get("title", item["id"]))
            except Exception as e:
                logger.warning(f"[WebLearner] 学习「{item.get('title', '?')}」失败: {e}")
                self._mark_learned(
                    item["id"], item.get("title", ""), status="failed",
                )

        # 标记剩余未学的为 skipped
        learned_ids = {item["id"] for item in to_learn}
        for item in valuable[self._max_per_cycle:]:
            if item["id"] not in learned_ids:
                self._mark_learned(
                    item["id"], item.get("title", ""), status="skipped",
                    value_score=item.get("value_score", 0),
                )

        # Step 5: 生成摘要并触发进化
        if learned_topics:
            summary = self._generate_learning_summary(learned_topics, source)
            self._remember(
                key=f"web_learner:cycle:{int(time.time())}",
                content=summary,
                tags=["web_learner", "learning_summary"],
            )
            if self._emit_evolution:
                try:
                    self._emit_evolution(
                        level=1,
                        action=f"主动学习({source}): {', '.join(learned_topics[:3])}",
                        target="web_learner",
                        payload={"learned": learned_topics, "source": source, "count": len(learned_topics)},
                    )
                except Exception:
                    pass
            logger.info(f"[WebLearner] ✅ 源「{source}」学习了 {len(learned_topics)} 项: {', '.join(learned_topics)}")
            return True
        else:
            logger.info(f"[WebLearner] 源「{source}」无可学习内容")
            return False

    # ── 数据源抓取 ──────────────────────────────────────────────────

    def _fetch_source(self, source: str) -> Optional[list[dict]]:
        """根据源名称抓取数据。"""
        fetchers = {
            "github_trending": self._fetch_github_trending,
            "hackernews": self._fetch_hackernews,
            "github_search_ai_agent": lambda: self._fetch_github_search(
                "topic:ai-agent topic:autonomous", "ai-agent 相关仓库", 10
            ),
            "github_search_llm_python": lambda: self._fetch_github_search(
                "topic:llm topic:python stars:>500", "LLM Python 项目", 10
            ),
            "github_search_rag_vector": lambda: self._fetch_github_search(
                "topic:rag topic:vector-database stars:>200", "RAG/向量库项目", 10
            ),
            "github_search_autonomous": lambda: self._fetch_github_search(
                "topic:autonomous-agents topic:ai-agent stars:>300",
                "自主 Agent 项目", 10,
            ),
        }
        fetcher = fetchers.get(source)
        if not fetcher:
            logger.warning(f"[WebLearner] 未知源: {source}")
            return None
        return fetcher()

    def _fetch_github_trending(self) -> Optional[list[dict]]:
        """抓取 GitHub Trending 本周热门仓库。"""
        try:
            url = (
                "https://api.github.com/search/repositories"
                "?q=created:>%s&sort=stars&order=desc&per_page=15"
                % time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 7))
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Kuafu/0.3.0",
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
                    "description": (repo.get("description", "") or "")[:300],
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
            req = urllib.request.Request(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                headers={"User-Agent": "Kuafu/0.3.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                story_ids = json.loads(resp.read().decode("utf-8"))

            results = []
            for sid in story_ids[:20]:
                try:
                    item_req = urllib.request.Request(
                        f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                        headers={"User-Agent": "Kuafu/0.3.0"},
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

    def _fetch_github_search(self, query: str, label: str, per_page: int = 10) -> Optional[list[dict]]:
        """按特定 GitHub 搜索查询抓取仓库。"""
        try:
            import urllib.parse
            encoded = urllib.parse.quote(query)
            url = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&order=desc&per_page={per_page}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Kuafu/0.3.0",
                "Accept": "application/vnd.github.v3+json",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("items", [])
            results = []
            for repo in items[:per_page]:
                results.append({
                    "id": f"github:{repo['full_name']}",
                    "source": f"github_search:{label}",
                    "title": repo["full_name"],
                    "description": (repo.get("description", "") or "")[:300],
                    "url": repo["html_url"],
                    "score": repo.get("stargazers_count", 0),
                    "extra": {
                        "language": repo.get("language", ""),
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "topics": repo.get("topics", []),
                    },
                })
            logger.info(f"[WebLearner] GitHub 搜索({label}): 获取 {len(results)} 个仓库")
            return results
        except Exception as e:
            logger.warning(f"[WebLearner] GitHub 搜索({label}) 失败: {e}")
            return None

    # ── 去重逻辑 ────────────────────────────────────────────────────

    def _is_effectively_new(self, item_id: str) -> bool:
        """判断一个项目是否「有效新」——没学过 或 被跳过但已过期。"""
        if item_id not in self._learned:
            return True
        meta = self._learned[item_id]
        status = meta.get("status", "")
        if status == "learned":
            return False  # 真正学过的永不再学
        if status in ("skipped", "failed"):
            # 检查是否过期
            learned_at = meta.get("learned_at", 0)
            if time.time() - learned_at >= SKIPPED_EXPIRE:
                return True  # 过期了，允许重新评估
        return False

    def _cleanup_expired_skipped(self):
        """清理过期 skipped 项目（从索引中删除，允许重新抓取）。"""
        now = time.time()
        expired = []
        for item_id, meta in self._learned.items():
            status = meta.get("status", "")
            if status in ("skipped", "failed"):
                learned_at = meta.get("learned_at", 0)
                if now - learned_at >= SKIPPED_EXPIRE:
                    expired.append(item_id)
        for item_id in expired:
            del self._learned[item_id]
        if expired:
            logger.debug(f"[WebLearner] 清理了 {len(expired)} 个过期的 skipped 条目")
            self._save_learned_index()

    # ── LLM 评估 ────────────────────────────────────────────────────

    def _evaluate_with_llm(self, items: list[dict]) -> list[dict]:
        """用 LLM 评估候选列表，返回值得学习的项目（按价值排序）。"""
        if not items:
            return []

        items_summary = []
        for i, item in enumerate(items):
            desc = (item.get("description", "") or "")[:150]
            extra = item.get("extra", {})
            lang = extra.get("language", "")
            lang_info = f" [{lang}]" if lang else ""
            items_summary.append(
                f"[{i}] [{item['source']}]{lang_info} {item['title']}: {desc}"
            )

        prompt = (
            "你是一个 AI 技术顾问，帮夸父（一个自我进化的 AI Agent）筛选值得学习的内容。\n\n"
            "夸父用 Python 开发，擅长 AI Agent、LLM 集成、工具开发。\n\n"
            f"候选项目（共 {len(items)} 项）：\n"
            + "\n".join(items_summary) +
            "\n\n请评估每项内容对夸父的「学习价值」。有价值的标准（满足任一即可）：\n"
            "1. Python / AI / LLM / Agent 相关的开源项目或工具\n"
            "2. Web 开发、API 集成相关的技术\n"
            "3. 编程技巧、架构模式、最佳实践\n"
            "4. 能解决夸父实际开发中遇到的痛点（错误处理、性能优化、测试等）\n\n"
            "按以下 JSON 格式输出，仅输出数组，不要多余文字：\n"
            "[\n"
            '  {\n'
            '    "index": 0,           // 在候选列表中的序号\n'
            '    "value_score": 8,      // 1-10 分，>=6 表示值得学\n'
            '    "reason": "为什么值得学（15字内）",\n'
            '    "learning_type": "skill | knowledge | reference",\n'
            '    "key_takeaway": "最值得记住的一句话"\n'
            "  },\n"
            "  ...\n"
            "]\n"
            "只保留 value_score >= 6 的项目。如果都不值得，输出 []。\n"
            "宽松评估：不确定时给 >=6，让学习过程来验证。宁可误学不可漏学。"
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
        source = item.get("source", "unknown")
        url = item.get("url", "")
        description = item.get("description", "") or ""
        value_score = item.get("value_score", 0)
        reason = item.get("reason", "")
        learning_type = item.get("learning_type", "knowledge")
        key_takeaway = item.get("key_takeaway", "")

        # 按照学习类型，生成不同的知识笔记
        if learning_type == "skill":
            note = self._format_skill_note(title, url, description, key_takeaway, source)
        else:
            note = self._format_knowledge_note(title, url, description, key_takeaway, source, value_score)

        # 写入 memory
        self._remember(
            key=f"web_learner:{source.replace(':', '_')}:{int(time.time())}",
            content=note,
            tags=["web_learner", "learned", source, learning_type],
        )

        # 标记为已学
        self._mark_learned(
            item["id"], title, status="learned",
            learning_type=learning_type, value_score=value_score,
            note_preview=note[:100],
        )
        self._total_learned_since_start += 1

        logger.info(f"[WebLearner] ✅ 已学习: {title} ({value_score}/10, {reason})")

    @staticmethod
    def _format_skill_note(title: str, url: str, description: str,
                            takeaway: str, source: str) -> str:
        return (
            f"## 🎯 技能: {title}\n"
            f"- 来源: {source}\n"
            f"- 链接: {url}\n"
            f"- 简介: {description[:300]}\n"
            f"- 要点: {takeaway}\n"
            f"- 学习时间: {time.strftime('%Y-%m-%d %H:%M')}\n"
        )

    @staticmethod
    def _format_knowledge_note(title: str, url: str, description: str,
                                takeaway: str, source: str, score: int) -> str:
        return (
            f"## 📚 {title}\n"
            f"- 来源: {source}\n"
            f"- 链接: {url}\n"
            f"- 简介: {description[:300]}\n"
            f"- 要点: {takeaway}\n"
            f"- 价值评分: {score}/10\n"
            f"- 学习时间: {time.strftime('%Y-%m-%d %H:%M')}\n"
        )

    # ── 摘要生成 ────────────────────────────────────────────────────

    def _generate_learning_summary(self, learned: list[str], source: str) -> str:
        lines = [
            f"## 主动学习报告 ({time.strftime('%Y-%m-%d %H:%M')})",
            "",
            f"源: {source}",
            f"学习了 {len(learned)} 个项目：",
        ]
        for title in learned:
            lines.append(f"- {title}")
        return "\n".join(lines)

    # ── 已学索引管理 ────────────────────────────────────────────────

    def _mark_learned(self, item_id: str, title: str = "", status: str = "learned",
                      learning_type: str = "", value_score: int = 0,
                      note_preview: str = ""):
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
        if LEARNED_INDEX.exists():
            try:
                data = json.loads(LEARNED_INDEX.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_learned_index(self):
        try:
            LEARNED_INDEX.parent.mkdir(parents=True, exist_ok=True)
            items = sorted(
                self._learned.items(),
                key=lambda x: x[1].get("learned_at", 0), reverse=True,
            )
            trimmed = dict(items[:2000])  # 保留 2000 条
            LEARNED_INDEX.write_text(
                json.dumps(trimmed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ── 工具方法 ────────────────────────────────────────────────────

    @staticmethod
    def _parse_llm_output(result) -> str:
        if isinstance(result, dict):
            # 兼容 llm.chat() 包装格式：{"success": True, "content": "..."}
            content = result.get("content", "")
            if not content:
                # 也兼容原始 API 响应格式：{"choices": [{"message": {"content": "..."}}]}
                choices = result.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")
            if content:
                import re as _re
                json_match = _re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
                if json_match:
                    return json_match.group(1).strip()
                return content.strip()
        return ""
