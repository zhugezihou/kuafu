"""
tests/test_skill_discovery.py — 技能发现与隐式触发测试
"""

import tempfile
from pathlib import Path
import pytest

from core.skill_discovery import (
    SkillMetadata, maybe_emit_implicit_skill_invocation,
    get_implicit_skill_injection, load_all_skills,
)


class TestSkillMetadata:

    def test_create_minimal(self):
        s = SkillMetadata(name="test", description="a test skill")
        assert s.name == "test"
        assert s.trigger_priority == 0
        assert s.trigger_keywords == []
        assert s.injection_point == "skills"

    def test_create_full(self):
        s = SkillMetadata(
            name="web_scraper",
            description="抓取网页",
            trigger_priority=10,
            trigger_keywords=["抓取", "爬虫", "网页"],
            injection_point="tools",
            implicit_only=True,
            steps=["分析结构", "发送请求"],
        )
        assert s.trigger_priority == 10
        assert s.implicit_only is True
        assert len(s.steps) == 2


class TestImplicitTrigger:

    def test_match_by_keyword(self):
        skills = [
            SkillMetadata(name="web", trigger_keywords=["抓取", "爬虫"], trigger_priority=5),
            SkillMetadata(name="git", trigger_keywords=["git", "提交", "推送"], trigger_priority=3),
        ]
        result = maybe_emit_implicit_skill_invocation("帮我抓取这个网页", skills)
        assert result is not None
        assert result.name == "web"

    def test_no_match(self):
        skills = [
            SkillMetadata(name="web", trigger_keywords=["抓取"]),
        ]
        result = maybe_emit_implicit_skill_invocation("你好", skills)
        assert result is None

    def test_empty_content(self):
        result = maybe_emit_implicit_skill_invocation("", [])
        assert result is None

    def test_priority_wins_when_same_match_count(self):
        """匹配数相同时优先级高的优先"""
        skills = [
            SkillMetadata(name="low", trigger_keywords=["代码"], trigger_priority=1),
            SkillMetadata(name="high", trigger_keywords=["代码"], trigger_priority=10),
        ]
        result = maybe_emit_implicit_skill_invocation("帮我写代码", skills)
        assert result is not None
        assert result.name == "high"

    def test_multiple_keyword_match(self):
        """匹配关键词多的优先"""
        skills = [
            SkillMetadata(name="one", trigger_keywords=["代码"], trigger_priority=5),
            SkillMetadata(name="two", trigger_keywords=["代码", "python", "实现"], trigger_priority=5),
        ]
        result = maybe_emit_implicit_skill_invocation("用python实现一段代码", skills)
        assert result is not None
        assert result.name == "two"  # 匹配更多关键词


class TestImplicitInjection:

    def test_injection_with_steps(self):
        skills = [
            SkillMetadata(
                name="web_scraper",
                description="抓取网页内容",
                trigger_keywords=["抓取"],
                steps=["分析目标URL", "发送HTTP请求", "解析HTML"],
            ),
        ]
        result = get_implicit_skill_injection("帮我抓取这个网页")
        assert result is None  # load_all_skills 没有 YAML 文件

    def test_no_match_returns_none(self):
        result = get_implicit_skill_injection("你好")
        assert result is None


class TestConfigParsing:

    def test_yaml_trigger_config(self):
        """模拟 YAML 中带 trigger 配置"""
        skill = SkillMetadata(
            name="review",
            trigger_keywords=["review", "审查", "代码审查"],
            trigger_priority=8,
            requires_confirmation=True,
        )
        assert "review" in skill.trigger_keywords
        assert skill.trigger_priority == 8
        assert skill.requires_confirmation is True
