"""
夸父远程技能仓库 (Remote Skill Repository)

架构：
- 支持多个远程仓库源（类似 apt 的 sources.list）
- 每个仓库是一个符合 kfskill 市场索引规范的 JSON 端点
- 本地缓存仓库索引，支持定时刷新
- 跨仓库搜索、按名称安装

仓库索引 JSON 格式：
    {
        "name": "kuafu-community",
        "description": "夸父官方技能市场",
        "homepage": "https://skills.kuafu.dev",
        "skills": [
            {
                "name": "web-scraper",
                "description": "网页抓取工具",
                "version": "1.2.0",
                "author": "kuafu",
                "category": "web",
                "keywords": ["scraping", "web"],
                "steps": 4,
                "url": "https://skills.kuafu.dev/skills/web-scraper.yaml",
                "checksum": "sha256:abc123..."
            }
        ]
    }

设计原则：
- 零新增依赖（仅标准库）
- 本地文件系统 caching
- 自动 fallback（仓库不可用不阻塞整个系统）
- 与现有 skill_manager.py 无缝集成
"""

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kuafu.skill_repo")

ROOT_DIR = Path(__file__).resolve().parent.parent
REPOS_DIR = ROOT_DIR / "skills" / "repos"
REPOS_DIR.mkdir(parents=True, exist_ok=True)

# 仓库缓存 TTL 配置
DEFAULT_CACHE_TTL = 3600     # 1 小时
SHORT_CACHE_TTL = 300        # 5 分钟（强制刷新时使用）

# 默认仓库列表（用户可通过 KUAFU_SKILL_REPOS 环境变量覆盖）
DEFAULT_REPOS = [
    {
        "name": "kuafu-official",
        "url": "https://raw.githubusercontent.com/zhugezihou/kuafu-skill-market/main/index.json",
        "description": "夸父官方技能仓库",
        "enabled": True,
    },
]


# ── 仓库配置管理 ──────────────────────────────────────────────

class RepoConfig:
    """单个仓库的配置。"""

    def __init__(self, name: str, url: str, description: str = "",
                 enabled: bool = True):
        self.name = name
        self.url = url
        self.description = description
        self.enabled = enabled
        self._cache: Optional[dict] = None
        self._cache_time: float = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "description": self.description[:60],
            "enabled": self.enabled,
        }

    @property
    def cache_path(self) -> Path:
        """本地缓存文件路径。"""
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", self.name)
        return REPOS_DIR / f"{safe_name}.cache.json"

    def is_cache_fresh(self, ttl: int = DEFAULT_CACHE_TTL) -> bool:
        """检查缓存是否在 TTL 内。"""
        if not self._cache:
            return False
        return (time.time() - self._cache_time) < ttl

    def load_cache(self) -> Optional[dict]:
        """从磁盘加载缓存。"""
        cp = self.cache_path
        if not cp.exists():
            return None
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            self._cache = data
            self._cache_time = cp.stat().st_mtime
            return data
        except Exception:
            return None

    def save_cache(self, data: dict):
        """保存缓存到磁盘。"""
        cp = self.cache_path
        cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self._cache = data
        self._cache_time = time.time()

    def fetch(self, force: bool = False) -> dict:
        """获取仓库索引（优先缓存，必要时远程拉取）。

        Returns:
            {"success": True, "data": {...}} 或 {"success": False, "error": "..."}
        """
        if not self.url:
            return {"success": False, "error": "未配置仓库 URL"}

        # 缓存命中
        if not force and self.load_cache() is not None:
            if self.is_cache_fresh():
                return {"success": True, "data": self._cache, "source": "cache"}

        # 远程拉取
        try:
            req = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": "KuafuSkillRepo/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except json.JSONDecodeError as e:
            # 尝试加载缓存
            if self._cache:
                return {"success": True, "data": self._cache, "source": "cache (fallback)"}
            return {"success": False, "error": f"JSON 解析失败: {e}"}
        except Exception as e:
            # 网络失败 → 回退到缓存
            if self._cache:
                cache_age = time.time() - self._cache_time
                return {
                    "success": True,
                    "data": self._cache,
                    "source": f"cache ({int(cache_age / 60)}min old, fallback)",
                }
            return {"success": False, "error": f"拉取失败: {e}"}

        # 验证数据格式
        if not isinstance(data, dict) or "skills" not in data:
            if self._cache:
                return {"success": True, "data": self._cache, "source": "cache (invalid remote)"}
            return {"success": False, "error": "仓库索引缺少 'skills' 字段"}

        self.save_cache(data)
        return {"success": True, "data": data, "source": "remote"}

    def get_skills(self, force: bool = False) -> list[dict]:
        """获取仓库中的技能列表。"""
        result = self.fetch(force=force)
        if not result["success"]:
            return []
        return result["data"].get("skills", [])


# ── 仓库管理器 ─────────────────────────────────────────────────

class RepoManager:
    """管理所有远程技能仓库。"""

    def __init__(self):
        self._repos: list[RepoConfig] = []
        self._load_repos()

    def _load_repos(self):
        """从环境变量和默认配置加载仓库列表。"""
        repos = []

        # 1. 从环境变量 KUAFU_SKILL_REPOS 加载（JSON 格式，支持多仓库）
        env_repos = os.environ.get("KUAFU_SKILL_REPOS", "")
        if env_repos:
            try:
                items = json.loads(env_repos)
                if isinstance(items, list):
                    for item in items:
                        repos.append(RepoConfig(
                            name=item.get("name", "unknown"),
                            url=item.get("url", ""),
                            description=item.get("description", ""),
                            enabled=item.get("enabled", True),
                        ))
                elif isinstance(items, dict):
                    # 单仓库
                    repos.append(RepoConfig(
                        name=items.get("name", "custom"),
                        url=items.get("url", ""),
                        description=items.get("description", ""),
                        enabled=items.get("enabled", True),
                    ))
            except (json.JSONDecodeError, AttributeError):
                pass

        # 2. 仅当环境变量未设置时使用默认仓库
        if not repos:
            for r in DEFAULT_REPOS:
                repos.append(RepoConfig(
                    name=r["name"],
                    url=r["url"],
                    description=r.get("description", ""),
                    enabled=r.get("enabled", True),
                ))

        # 3. 也支持 KUAFU_SKILL_MARKET_URL 旧格式（向下兼容）
        legacy_url = os.environ.get("KUAFU_SKILL_MARKET_URL", "")
        if legacy_url and not any(r.url == legacy_url for r in repos):
            repos.append(RepoConfig(
                name="legacy-market",
                url=legacy_url,
                description="遗留技能市场（从 KUAFU_SKILL_MARKET_URL 加载）",
                enabled=True,
            ))

        self._repos = repos

    # ── 仓库管理 ────────────────────────────────────────────

    def list_repos(self) -> list[dict]:
        """列出所有配置的仓库（带超时保护）。"""
        return [r.to_dict() for r in self._repos]

    def add_repo(self, name: str, url: str, description: str = "") -> dict:
        """添加一个远程仓库。"""
        if any(r.name == name for r in self._repos):
            return {"success": False, "error": f"仓库已存在: {name}"}

        self._repos.append(RepoConfig(
            name=name, url=url, description=description, enabled=True,
        ))
        # 立即尝试拉取一次（验证 URL 有效性）
        result = self._repos[-1].fetch(force=True)
        return {
            "success": True,
            "name": name,
            "url": url,
            "reachable": result["success"],
            "skills_count": len(self._repos[-1].get_skills()),
        }

    def remove_repo(self, name: str) -> bool:
        """移除一个仓库。"""
        for i, r in enumerate(self._repos):
            if r.name == name:
                # 清理缓存文件
                cp = r.cache_path
                if cp.exists():
                    try:
                        cp.unlink()
                    except Exception:
                        pass
                self._repos.pop(i)
                return True
        return False

    def get_repo(self, name: str) -> Optional[RepoConfig]:
        """获取指定仓库。"""
        for r in self._repos:
            if r.name == name:
                return r
        return None

    # ── 搜索 ────────────────────────────────────────────────

    def search(self, query: str, force: bool = False,
               max_per_repo: int = 10) -> list[dict]:
        """跨仓库搜索技能。

        Args:
            query: 搜索关键词
            force: 是否强制刷新远程
            max_per_repo: 每个仓库最大返回数

        Returns:
            [{"name": str, "description": str, "repo": str, ...}, ...]
        """
        q = query.lower()
        all_results = []

        for repo in self._repos:
            if not repo.enabled:
                continue

            skills = repo.get_skills(force=force)
            matched = []

            for skill in skills:
                score = 0
                name = skill.get("name", "")
                description = skill.get("description", "")
                keywords = skill.get("keywords", [])
                category = skill.get("category", "")

                # 精确匹配
                if q == name.lower():
                    score += 100
                elif q in name.lower():
                    score += 50

                # 描述匹配
                if q in description.lower():
                    score += 10

                # 关键词匹配
                for kw in keywords:
                    if q in kw.lower():
                        score += 20
                        break

                # 分类匹配
                if category and q in category.lower():
                    score += 5

                if score > 0:
                    matched.append({
                        "name": name,
                        "description": description[:100],
                        "version": skill.get("version", "?"),
                        "author": skill.get("author", ""),
                        "category": skill.get("category", ""),
                        "keywords": keywords[:5],
                        "steps": skill.get("steps", 0),
                        "url": skill.get("url", ""),
                        "repo": repo.name,
                        "score": score,
                    })

            matched.sort(key=lambda x: x["score"], reverse=True)
            all_results.extend(matched[:max_per_repo])

        # 全局排序
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:50]

    def list_all_skills(self, force: bool = False) -> list[dict]:
        """列出所有仓库的全部技能。"""
        all_skills = []
        for repo in self._repos:
            if not repo.enabled:
                continue
            for skill in repo.get_skills(force=force):
                all_skills.append({
                    "name": skill.get("name", "?"),
                    "description": skill.get("description", "")[:100],
                    "version": skill.get("version", "?"),
                    "author": skill.get("author", ""),
                    "category": skill.get("category", ""),
                    "url": skill.get("url", ""),
                    "repo": repo.name,
                })
        return all_skills

    # ── 安装 ────────────────────────────────────────────────

    def install(self, name: str, force_refresh: bool = False) -> dict:
        """从仓库安装指定名称的技能。

        遍历所有仓库，找到匹配的技能并下载安装。

        Returns:
            {"success": True, "name": "...", "file": "...", "repo": "..."}
            或 {"success": False, "error": "..."}
        """
        for repo in self._repos:
            if not repo.enabled:
                continue

            skills = repo.get_skills(force=force_refresh)
            for skill in skills:
                if skill.get("name") == name:
                    skill_url = skill.get("url", "")
                    if not skill_url:
                        return {"success": False, "error": f"技能 {name} 在仓库 {repo.name} 中没有下载 URL"}

                    # 下载技能文件
                    return self._download_and_install(skill_url, name, repo.name)

        return {"success": False, "error": f"在所有仓库中未找到技能: {name}"}

    def install_from_url(self, url: str) -> dict:
        """从任意 URL 下载并安装技能（不依赖仓库）。"""
        return self._download_and_install(url, "", "direct")

    def _download_and_install(self, url: str, expected_name: str,
                               repo_name: str) -> dict:
        """下载技能文件并安装到 skills/market/ 目录。"""
        from core.kfskill import load_skill

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Kuafu/0.4"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
        except Exception as e:
            return {"success": False, "error": f"下载失败: {e}"}

        # 先写入临时文件，用 kfskill 的 load 解析
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        try:
            tmp.write(content)
            tmp.close()

            result = load_skill(tmp.name)
            if result["success"]:
                skill_name = result["data"]["name"]
            else:
                # fallback: 从文件名推断
                skill_name = expected_name or Path(urllib.parse.urlparse(url).path).stem
                if not skill_name or skill_name in ("SKILL", "skill"):
                    skill_name = f"remote_{int(time.time())}"

        except Exception as e:
            skill_name = expected_name or f"remote_{int(time.time())}"
        finally:
            Path(tmp.name).unlink(missing_ok=True)

        # 安装到 market 目录
        from core.skill_manager import MARKET_DIR
        MARKET_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", skill_name)
        file_path = MARKET_DIR / f"{safe_name}.yaml"

        # 安全检查：如果内容包含 frontmatter 头（---），直接保存
        # 否则可能带有额外 meta，但 yaml 解析器会处理
        file_path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "name": skill_name,
            "file": str(file_path),
            "repo": repo_name,
        }

    # ── 仓库缓存管理 ────────────────────────────────────────

    def refresh_all(self) -> list[dict]:
        """强制刷新所有仓库的缓存。"""
        results = []
        for repo in self._repos:
            if not repo.enabled:
                continue
            result = repo.fetch(force=True)
            results.append({
                "name": repo.name,
                "success": result["success"],
                "source": result.get("source", "?"),
                "skills_count": len(repo.get_skills()),
            })
        return results

    def clear_cache(self, name: Optional[str] = None) -> int:
        """清理缓存文件。"""
        cleared = 0
        if name:
            repo = self.get_repo(name)
            if repo and repo.cache_path.exists():
                try:
                    repo.cache_path.unlink()
                    cleared += 1
                except Exception:
                    pass
        else:
            for f in REPOS_DIR.glob("*.cache.json"):
                try:
                    f.unlink()
                    cleared += 1
                except Exception:
                    pass
        return cleared

    def get_stats(self) -> dict:
        """获取所有仓库的状态统计。"""
        total_skills = 0
        repos_stats = []

        for repo in self._repos:
            skills = repo.get_skills()
            status = "ok" if repo._cache else "unreachable"
            total_skills += len(skills)
            cache_age = int(time.time() - repo._cache_time) if repo._cache_time else -1
            repos_stats.append({
                "name": repo.name,
                "enabled": repo.enabled,
                "url": repo.url,
                "status": status,
                "skills": len(skills),
                "cache_age_sec": cache_age,
            })

        return {
            "total_repos": len(self._repos),
            "total_skills": total_skills,
            "repos": repos_stats,
        }
