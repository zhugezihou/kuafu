"""
tests/test_agents_md.py — AGENTS.md 层次化发现测试
"""

import tempfile
from pathlib import Path
import pytest

from core.agents_md import (
    AgentsMdManager, LoadedAgentsMd, parse_agents_md,
)


class TestParseAgentsMd:

    def test_plain_content(self):
        """无标题时整个内容为 default 段"""
        sections = parse_agents_md("这是一段普通文本\n第二行")
        assert "default" in sections
        assert "普通文本" in sections["default"]

    def test_with_sections(self):
        content = """## 核心规则
规则1：不要修改 core/ 目录
规则2：完成任务后调用 finish()

## 沟通风格
直接简洁
"""
        sections = parse_agents_md(content)
        assert "核心规则" in sections
        assert "规则1" in sections["核心规则"]
        assert "沟通风格" in sections
        assert "直接简洁" in sections["沟通风格"]

    def test_multiple_headings(self):
        content = """## 第一部分
内容A

## 第二部分
内容B
"""
        sections = parse_agents_md(content)
        assert len(sections) == 2
        assert "内容A" in sections["第一部分"]
        assert "内容B" in sections["第二部分"]


class TestAgentsMdManager:

    def test_discover_global_only(self):
        """只有全局文件"""
        with tempfile.TemporaryDirectory() as d:
            # 模拟 ~/.kuafu/AGENTS.md
            global_dir = Path(d) / ".kuafu"
            global_dir.mkdir(parents=True)
            (global_dir / "AGENTS.md").write_text("## 全局指令\n全局规则")

            # mock home
            old_home = Path.home()
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(Path, "home", lambda: Path(d))

                mgr = AgentsMdManager()
                loaded = mgr.discover(cwd=d)
                assert loaded is not None
                assert "全局规则" in loaded.content
                assert loaded.source_type == "merged"

    def test_discover_project_overrides_global(self):
        """项目文件覆盖全局文件"""
        with tempfile.TemporaryDirectory() as d:
            # 全局
            global_dir = Path(d) / ".kuafu"
            global_dir.mkdir(parents=True)
            (global_dir / "AGENTS.md").write_text("## 全局指令\n全局规则")

            # 项目
            proj_dir = Path(d) / "project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "AGENTS.md").write_text("## 项目指令\n项目规则")

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(Path, "home", lambda: Path(d))
                mgr = AgentsMdManager()
                loaded = mgr.discover(cwd=str(proj_dir))
                assert loaded is not None
                # 两者都包含（全局 + 项目）
                assert "全局规则" in loaded.content
                assert "项目规则" in loaded.content

    def test_no_files(self):
        """无文件时返回 None"""
        with tempfile.TemporaryDirectory() as d:
            mgr = AgentsMdManager()
            loaded = mgr.discover(cwd=d)
            assert loaded is None

    def test_get_instructions(self):
        """快捷获取指令文本"""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "AGENTS.md").write_text("测试指令")
            mgr = AgentsMdManager()
            instructions = mgr.get_instructions(cwd=d)
            assert instructions is not None
            assert "测试指令" in instructions

    def test_cache_invalidation(self):
        """清除缓存后重新加载"""
        with tempfile.TemporaryDirectory() as d:
            md_path = Path(d) / "AGENTS.md"
            md_path.write_text("v1")

            mgr = AgentsMdManager()
            v1 = mgr.get_instructions(cwd=d)
            assert "v1" in v1

            mgr.invalidate_cache()
            md_path.write_text("v2")
            v2 = mgr.get_instructions(cwd=d)
            assert "v2" in v2
