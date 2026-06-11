"""
core/agents_md.py — AGENTS.md 层次化发现与注入

源自 Codex CLI AgentsMdManager：
  - 从多个层级发现 AGENTS.md / CLAUDE.md 指令文件
  - 结构化解析：分段映射 + injection_points
  - 注入到 TurnContext

发现层级（从低到高）：
  1. ~/.config/kuafu/AGENTS.md（全局用户指令）
  2. <project_root>/.kuafu/AGENTS.md（项目指令）
  3. <project_root>/AGENTS.md（项目根指令，最高优先级）
"""

import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kuafu.agents_md")

# 搜索的文件名（按优先级）
AGENTS_FILENAMES = ["AGENTS.md", "CLAUDE.md", "CLAUDE.txt", "instructions.md"]

# 默认搜索路径（相对于项目根）
PROJECT_RELATIVE_PATHS = [
    ".kuafu/AGENTS.md",
    ".kuafu/CLAUDE.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/CLAUDE.md",
    ".cursorrules",
]


# =========================================================================
# 发现结果
# =========================================================================

@dataclass
class LoadedAgentsMd:
    """加载的 AGENTS.md 内容。"""
    content: str                     # 完整内容
    source_path: str                 # 来源文件路径
    source_type: str                 # "global" / "project" / "local"
    sections: dict = field(default_factory=dict)  # 分段解析结果

    def get_section(self, name: str) -> Optional[str]:
        """获取指定段的内容。"""
        return self.sections.get(name)


# =========================================================================
# 解析器
# =========================================================================

def parse_agents_md(content: str) -> dict[str, str]:
    """将 AGENTS.md 解析为分段字典。

    支持 Markdown 二级标题（## Section Name）作为分段标记。
    如果没有标题，整个内容作为 "default" 段。

    Returns:
        {section_name: section_content, ...}
    """
    sections = {}
    current_section = "default"
    current_lines = []

    for line in content.split("\n"):
        heading_match = re.match(r"^##\s+(.+)$", line)
        if heading_match:
            # 保存当前段
            text = "\n".join(current_lines).strip()
            if text:
                sections[current_section] = text
            current_section = heading_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 保存最后一段
    text = "\n".join(current_lines).strip()
    if text:
        sections[current_section] = text

    return sections


# =========================================================================
# AGENTS.md 管理器
# =========================================================================

class AgentsMdManager:
    """AGENTS.md 层次化发现与注入管理器。

    用法：
        mgr = AgentsMdManager()
        loaded = mgr.discover(cwd="/path/to/project")
        if loaded:
            user_instructions = loaded.content

        # 注入到 TurnContext
        turn_ctx = turn_ctx.with_user_instructions(loaded.content)
    """

    def __init__(self):
        self._cache: dict[str, LoadedAgentsMd] = {}
        self._mtime_cache: dict[str, float] = {}

    def discover(self, cwd: Optional[str] = None) -> Optional[LoadedAgentsMd]:
        """从所有层级发现 AGENTS.md，返回最高优先级的有效结果。

        层级（从低到高合并）：
          1. ~/.config/kuafu/AGENTS.md — 全局指令
          2. <project>/.kuafu/AGENTS.md — 项目指令
          3. <project>/AGENTS.md — 项目根指令

        Returns:
            合并后的 LoadedAgentsMd，或 None（未找到任何文件）
        """
        search_dir = Path(cwd or os.getcwd())
        found = []

        # Layer 1: 全局用户配置
        global_path = Path("~/.config/kuafu").expanduser() / "AGENTS.md"
        if global_path.exists():
            loaded = self._load(global_path, "global")
            if loaded:
                found.append(loaded)

        # Layer 2: 项目 .kuafu/AGENTS.md
        proj_config = search_dir / ".kuafu" / "AGENTS.md"
        if proj_config.exists():
            loaded = self._load(proj_config, "project")
            if loaded:
                found.append(loaded)

        # Layer 3: 项目根 AGENTS.md
        proj_root = search_dir / "AGENTS.md"
        if proj_root.exists():
            loaded = self._load(proj_root, "local")
            if loaded:
                found.append(loaded)

        # 其他文件名
        for name in ["CLAUDE.md", "CLAUDE.txt", ".cursorrules"]:
            path = search_dir / name
            if path.exists():
                loaded = self._load(path, "local")
                if loaded:
                    found.append(loaded)

        if not found:
            return None

        # 合并所有层级
        combined_parts = []
        last_source = ""

        for loaded in found:
            if loaded.content.strip():
                if last_source:
                    combined_parts.append(f"<!-- from {loaded.source_path} -->")
                combined_parts.append(loaded.content.strip())
                last_source = loaded.source_path

        combined_content = "\n\n".join(combined_parts)

        return LoadedAgentsMd(
            content=combined_content,
            source_path=" + ".join(l.source_path for l in found),
            source_type="merged",
            sections=parse_agents_md(combined_content),
        )

    def get_instructions(self, cwd: Optional[str] = None) -> Optional[str]:
        """快捷获取用户指令文本。"""
        loaded = self.discover(cwd)
        if loaded:
            return loaded.content
        return None

    def invalidate_cache(self):
        """清除缓存，下次 discover 重新加载。"""
        self._cache = {}
        self._mtime_cache = {}

    def _load(self, path: Path, source_type: str) -> Optional[LoadedAgentsMd]:
        """加载单个文件。"""
        cache_key = str(path)
        mtime = path.stat().st_mtime

        # 缓存命中且未变更
        if cache_key in self._mtime_cache and self._mtime_cache[cache_key] == mtime:
            return self._cache.get(cache_key)

        try:
            content = path.read_text(encoding="utf-8")
            loaded = LoadedAgentsMd(
                content=content,
                source_path=str(path),
                source_type=source_type,
                sections=parse_agents_md(content),
            )
            self._cache[cache_key] = loaded
            self._mtime_cache[cache_key] = mtime
            logger.info(f"📜 加载 {source_type} AGENTS.md: {path}")
            return loaded
        except Exception as e:
            logger.warning(f"加载 {path} 失败: {e}")
            return None
