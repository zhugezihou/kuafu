"""
core/skill_manager.py — 夸父技能管理器

职责：
1. 本地技能（skills/*.yaml）的 CRUD
2. 远程技能市场的搜索和安装
3. 技能启用/禁用管理

远程技能市场规范：
  - 每个技能是一个 SKILL.md 文件（Markdown + YAML frontmatter）
  - 市场索引是一个 JSON 文件，描述所有可用技能
  - 通过 URL 或本地文件系统加载

设计原则：
  - 不修改现有 skill_resolver.py（进化系统仍在用）
  - 新技能管理器是上层抽象
  - 远程技能通过 URL 下载，存入 skills/market/ 目录
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kuafu.skill_manager")

ROOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT_DIR / "skills"
MARKET_DIR = SKILLS_DIR / "market"
MARKET_INDEX_URL = os.environ.get("KUAFU_SKILL_MARKET_URL", "")
CACHE_TTL = 3600


class SkillInfo:
    """单个技能的信息。"""
    def __init__(self, name: str, description: str = "",
                 file_path: str = "", source: str = "local",
                 keywords: list[str] = None, steps: int = 0,
                 usage_count: int = 0, author: str = "",
                 url: str = "", category: str = ""):
        self.name = name
        self.description = description
        self.file_path = file_path
        self.source = source
        self.keywords = keywords or []
        self.steps = steps
        self.usage_count = usage_count
        self.author = author
        self.url = url
        self.category = category

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description[:100],
            "source": self.source,
            "keywords": self.keywords[:5],
            "steps": self.steps,
            "usage": self.usage_count,
            "author": self.author,
            "category": self.category,
        }


class SkillManager:
    """夸父技能管理器。"""

    def __init__(self):
        self._market_cache: Optional[list[SkillInfo]] = None
        self._cache_time: float = 0

    # ── 本地技能 ────────────────────────────────────────────

    def list_local(self) -> list[SkillInfo]:
        """列出本地所有技能。"""
        results = []
        for yaml_file in sorted(SKILLS_DIR.glob("*.yaml")):
            try:
                import yaml
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not data:
                    continue
                steps = len(data.get("steps", []))
                results.append(SkillInfo(
                    name=data.get("name", yaml_file.stem),
                    description=data.get("description", ""),
                    file_path=str(yaml_file.relative_to(ROOT_DIR)),
                    source="local",
                    keywords=data.get("keywords", []),
                    steps=steps,
                    usage_count=data.get("usage_count", 0),
                ))
            except Exception as e:
                logger.warning(f"加载技能 {yaml_file.name} 失败: {e}")
        return results

    def get_local(self, name: str) -> Optional[SkillInfo]:
        for skill in self.list_local():
            if skill.name == name:
                return skill
        return None

    def search_local(self, query: str) -> list[SkillInfo]:
        q = query.lower()
        results = []
        for skill in self.list_local():
            if q in skill.name.lower() or q in skill.description.lower():
                results.append(skill)
                continue
            for kw in skill.keywords:
                if q in kw.lower():
                    results.append(skill)
                    break
        return results[:10]

    def remove_local(self, name: str) -> bool:
        for f in SKILLS_DIR.glob("*.yaml"):
            try:
                import yaml
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and data.get("name") == name:
                    f.unlink()
                    return True
            except Exception:
                continue
        return False

    # ── 远程市场 ────────────────────────────────────────────

    def fetch_market_index(self, force: bool = False) -> list[SkillInfo]:
        """从远程获取技能市场索引。

        市场索引格式:
            {"skills": [{"name": "...", "url": "...", ...}]}
        """
        if not MARKET_INDEX_URL:
            return []

        now = time.time()
        if not force and self._market_cache and (now - self._cache_time) < CACHE_TTL:
            return self._market_cache

        try:
            req = urllib.request.Request(
                MARKET_INDEX_URL,
                headers={"User-Agent": "Kuafu/0.4"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"获取技能市场索引失败: {e}")
            return self._market_cache or []

        skills = []
        for item in data.get("skills", []):
            skills.append(SkillInfo(
                name=item.get("name", ""),
                description=item.get("description", ""),
                source="market",
                keywords=item.get("keywords", []),
                steps=item.get("steps", 0),
                author=item.get("author", ""),
                url=item.get("url", ""),
                category=item.get("category", ""),
            ))
        self._market_cache = skills
        self._cache_time = now
        return skills

    @staticmethod
    def _check_skill_deps(install_result: dict):
        """安装后检查技能依赖并打印信息。"""
        file_path = install_result.get("file", "")
        if not file_path or not Path(file_path).exists():
            return
        try:
            import yaml
            data = yaml.safe_load(Path(file_path).read_text(encoding="utf-8"))
            if not data or "dependencies" not in data:
                return
            from core.skill_deps import check_dependencies, suggest_command
            result = check_dependencies(data)
            if not result.ok:
                print(f"   📦 检测到依赖缺失:")
                print(f"      {result.summary()}")
                cmd = suggest_command(data)
                if cmd:
                    print(f"      💡 {cmd}")
        except Exception:
            pass

    def search_market(self, query: str) -> list[SkillInfo]:
        q = query.lower()
        all_skills = self.fetch_market_index()
        results = []
        for skill in all_skills:
            if q in skill.name.lower() or q in skill.description.lower():
                results.append(skill)
                continue
            for kw in skill.keywords:
                if q in kw.lower():
                    results.append(skill)
                    break
            if skill.category and q in skill.category.lower():
                results.append(skill)
        return results[:20]

    # ── 技能安装 ────────────────────────────────────────────

    def install(self, name_or_url: str) -> dict:
        if name_or_url.startswith("http://") or name_or_url.startswith("https://"):
            # 先尝试直接下载（原有逻辑）
            result = self._install_from_url(name_or_url)
            if result["success"]:
                return result
            # 下载失败时，尝试通过仓库解析
            from core.skill_repo import RepoManager
            repo = RepoManager()
            return repo.install_from_url(name_or_url)
        # 先查本地市场
        result = self._install_by_name(name_or_url)
        if result["success"]:
            # 安装成功后检查依赖
            self._check_skill_deps(result)
            return result
        # 再查远程仓库
        from core.skill_repo import RepoManager
        repo = RepoManager()
        result = repo.install(name_or_url)
        if result["success"]:
            self._check_skill_deps(result)
        return result

    def _install_by_name(self, name: str) -> dict:
        market = self.fetch_market_index()
        for skill in market:
            if skill.name == name:
                if skill.url:
                    return self._install_from_url(skill.url)
                return {"success": False, "error": f"技能 {name} 没有下载 URL"}
        return {"success": False, "error": f"市场未找到技能 {name}"}

    def _install_from_url(self, url: str) -> dict:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Kuafu/0.4"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
        except Exception as e:
            return {"success": False, "error": f"下载失败: {e}"}

        name = self._extract_name_from_md(content, url)
        if not name:
            return {"success": False, "error": "无法解析技能名称"}

        MARKET_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name)
        file_path = MARKET_DIR / f"{safe_name}.yaml"
        file_path.write_text(content, encoding="utf-8")
        return {"success": True, "name": name, "file": str(file_path)}

    @staticmethod
    def _extract_name_from_md(content: str, fallback_url: str) -> str:
        m = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if m:
            front = m.group(1)
            for line in front.split("\n"):
                if line.strip().startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if name:
                        return name
        import urllib.parse
        parsed = urllib.parse.urlparse(fallback_url)
        stem = Path(parsed.path).stem
        if stem and stem != "SKILL":
            return stem
        return ""

    # ── 技能卸载 ────────────────────────────────────────────

    def uninstall(self, name: str) -> bool:
        if not MARKET_DIR.exists():
            return False
        for f in MARKET_DIR.glob("*.yaml"):
            try:
                import yaml
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and data.get("name") == name:
                    f.unlink()
                    return True
            except Exception:
                continue
        return False

    def list_installed_market(self) -> list[SkillInfo]:
        if not MARKET_DIR.exists():
            return []
        results = []
        for yaml_file in sorted(MARKET_DIR.glob("*.yaml")):
            try:
                import yaml
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not data:
                    continue
                results.append(SkillInfo(
                    name=data.get("name", yaml_file.stem),
                    description=data.get("description", ""),
                    file_path=str(yaml_file.relative_to(ROOT_DIR)),
                    source="installed",
                    keywords=data.get("keywords", []),
                    steps=len(data.get("steps", [])),
                    usage_count=data.get("usage_count", 0),
                ))
            except Exception:
                continue
        return results

    def get_stats(self) -> dict:
        local_count = len(list(SKILLS_DIR.glob("*.yaml")))
        market_count = len(list(MARKET_DIR.glob("*.yaml"))) if MARKET_DIR.exists() else 0
        remote_count = len(self.fetch_market_index())

        # 远程仓库统计
        from core.skill_repo import RepoManager
        repo_stats = RepoManager().get_stats()

        return {
            "local": local_count,
            "installed_market": market_count,
            "available_market": remote_count,
            "repos": repo_stats["total_repos"],
            "repo_skills": repo_stats["total_skills"],
        }
