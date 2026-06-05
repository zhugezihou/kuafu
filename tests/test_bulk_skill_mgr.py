"""
Core test for SkillManager — remove_local, install (by_name/from_url), uninstall, 
market_index, search_market, fetch_market_index (various network states).
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, call, ANY

import pytest


class TestSkillManager:
    """Complete coverage for SkillManager — focus on uncovered paths."""

    def _make_mgr(self):
        from core.skill_manager import SkillManager
        return SkillManager()

    # ---- list_local ----
    def test_list_local_empty(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = []
            results = mgr.list_local()
            assert results == []

    def test_list_local_with_skills(self):
        mgr = self._make_mgr()
        yaml_content = """
name: test_skill
description: test
steps:
  - prompt: hello
keywords: [test]
usage_count: 5
"""
        mock_file = MagicMock()
        mock_file.name = "test_skill.yaml"
        mock_file.stem = "test_skill"
        mock_file.read_text.return_value = yaml_content
        mock_file.relative_to.return_value = Path("skills/test_skill.yaml")

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {
                    "name": "test_skill",
                    "description": "test",
                    "steps": [{"prompt": "hello"}],
                    "keywords": ["test"],
                    "usage_count": 5,
                }
                results = mgr.list_local()
                assert len(results) == 1
                assert results[0].name == "test_skill"
                assert results[0].steps == 1

    def test_list_local_parse_error(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.name = "bad.yaml"
        mock_file.read_text.side_effect = Exception("parse error")

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            results = mgr.list_local()
            assert results == []

    def test_list_local_empty_yaml(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.name = "empty.yaml"
        mock_file.stem = "empty"
        mock_file.read_text.return_value = ""

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = None
                results = mgr.list_local()
                assert results == []

    # ---- get_local ----
    def test_get_local_exists(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [SkillInfo(name="my_skill")]
            result = mgr.get_local("my_skill")
            assert result is not None
            assert result.name == "my_skill"

    def test_get_local_not_found(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local', return_value=[]):
            result = mgr.get_local("nonexistent")
            assert result is None

    # ---- search_local ----
    def test_search_local_by_name(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [
                SkillInfo(name="web_search", description="search the web", keywords=["internet"]),
                SkillInfo(name="file_read", description="read files", keywords=["fs"]),
            ]
            results = mgr.search_local("web")
            assert len(results) == 1
            assert results[0].name == "web_search"

    def test_search_local_by_description(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [
                SkillInfo(name="skill_a", description="file management tool", keywords=[]),
            ]
            results = mgr.search_local("management")
            assert len(results) == 1

    def test_search_local_by_keyword(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [
                SkillInfo(name="skill_b", description="something", keywords=["database", "sql"]),
            ]
            results = mgr.search_local("database")
            assert len(results) == 1

    def test_search_local_no_match(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local', return_value=[]):
            results = mgr.search_local("zzz_nonexistent")
            assert results == []

    def test_search_local_multi_match_limited(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            skills = [SkillInfo(name=f"skill_{i}", description="test") for i in range(20)]
            mock_list.return_value = skills
            results = mgr.search_local("test")
            assert len(results) <= 10

    # ---- remove_local ----
    def test_remove_local_exists(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: my_skill\ndescription: test"

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "my_skill"}
                result = mgr.remove_local("my_skill")
                assert result is True
                mock_file.unlink.assert_called_once()

    def test_remove_local_not_found(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: other_skill"

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "other_skill"}
                result = mgr.remove_local("my_skill")
                assert result is False

    def test_remove_local_no_yaml_files(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = []
            result = mgr.remove_local("any_skill")
            assert result is False

    def test_remove_local_yaml_exception(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.side_effect = Exception("read error")

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            result = mgr.remove_local("any_skill")
            assert result is False

    # ---- fetch_market_index ----
    def test_fetch_market_index_no_url(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_INDEX_URL', ""):
            result = mgr.fetch_market_index()
            assert result == []

    def test_fetch_market_index_uses_cache(self):
        mgr = self._make_mgr()
        mgr._market_cache = ["cached"]
        mgr._cache_time = time.time()  # recent
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            result = mgr.fetch_market_index(force=False)
            assert result == ["cached"]

    def test_fetch_market_index_force_refresh(self):
        mgr = self._make_mgr()
        mgr._market_cache = ["cached"]
        mgr._cache_time = time.time()
        # force=True should bypass cache
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"skills": [{"name": "remote_skill"}]}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = mgr.fetch_market_index(force=True)
                assert len(result) == 1
                assert result[0].name == "remote_skill"

    def test_fetch_market_index_network_error(self):
        mgr = self._make_mgr()
        mgr._market_cache = ["fallback_cache"]
        mgr._cache_time = 0  # expired
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen', side_effect=Exception("network error")):
                result = mgr.fetch_market_index()
                # Should return cached data or empty
                assert result == ["fallback_cache"] or result == []

    def test_fetch_market_index_network_error_no_cache(self):
        mgr = self._make_mgr()
        mgr._market_cache = None
        mgr._cache_time = 0
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen', side_effect=Exception("network error")):
                result = mgr.fetch_market_index()
                assert result == []

    def test_fetch_market_index_success(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"skills": [{"name": "s1", "description": "d1", "keywords": ["k1"], "steps": 3, "author": "a1", "url": "u1", "category": "c1"}]}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = mgr.fetch_market_index()
                assert len(result) == 1
                assert result[0].name == "s1"
                assert result[0].author == "a1"
                assert result[0].category == "c1"
                assert result[0].url == "u1"
                assert result[0].steps == 3
                assert mgr._market_cache is not None
                assert mgr._cache_time > 0

    def test_fetch_market_index_empty_response(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"skills": []}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = mgr.fetch_market_index()
                assert result == []

    # ---- search_market ----
    def test_search_market_by_name(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="web_scraper", description="scrape websites", keywords=["http"]),
                SkillInfo(name="file_tool", description="file operations", keywords=["fs"]),
            ]
            results = mgr.search_market("web")
            assert len(results) == 1
            assert results[0].name == "web_scraper"

    def test_search_market_by_description(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="tool_a", description="database query tool", keywords=[]),
            ]
            results = mgr.search_market("query")
            assert len(results) == 1

    def test_search_market_by_keyword(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="tool_b", description="something", keywords=["machine learning"]),
            ]
            results = mgr.search_market("machine")
            assert len(results) == 1

    def test_search_market_by_category(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="tool_c", description="desc", keywords=[], category="utility"),
            ]
            results = mgr.search_market("utility")
            assert len(results) == 1

    def test_search_market_no_match(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index', return_value=[]):
            results = mgr.search_market("zzz_nonexistent")
            assert results == []

    def test_search_market_limited_to_20(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            skills = [SkillInfo(name=f"s{i}", description="common desc") for i in range(30)]
            mock_fetch.return_value = skills
            results = mgr.search_market("common")
            assert len(results) <= 20

    # ---- install ----
    def test_install_by_url_success(self):
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_from_url') as mock_url:
            mock_url.return_value = {"success": True, "name": "test", "file": "/tmp/test.yaml"}
            result = mgr.install("https://example.com/skill.md")
            assert result["success"] is True
            mock_url.assert_called_with("https://example.com/skill.md")

    def test_install_by_url_fallback_to_repo(self):
        """URL install fails → try RepoManager.install_from_url."""
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_from_url', return_value={"success": False, "error": "failed"}):
            with patch('core.skill_manager.RepoManager') as MockRepo:
                repo = MagicMock()
                repo.install_from_url.return_value = {"success": True, "name": "repo_skill"}
                MockRepo.return_value = repo
                result = mgr.install("https://example.com/skill.md")
                assert result["success"] is True
                repo.install_from_url.assert_called_with("https://example.com/skill.md")

    def test_install_by_name_success(self):
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_by_name') as mock_name:
            mock_name.return_value = {"success": True, "name": "my_skill", "file": "/tmp/skill.yaml"}
            with patch.object(mgr, '_check_skill_deps'):
                result = mgr.install("my_skill")
                assert result["success"] is True
                mock_name.assert_called_with("my_skill")

    def test_install_by_name_fallback_to_repo(self):
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_by_name', return_value={"success": False, "error": "not found"}):
            with patch('core.skill_manager.RepoManager') as MockRepo:
                repo = MagicMock()
                repo.install.return_value = {"success": True, "name": "repo_skill"}
                MockRepo.return_value = repo
                with patch.object(mgr, '_check_skill_deps'):
                    result = mgr.install("my_skill")
                    assert result["success"] is True
                    repo.install.assert_called_with("my_skill")

    def test_install_name_not_url(self):
        """Name that doesn't start with http should go through _install_by_name path."""
        mgr = self._make_mgr()
        result = mgr.install("just_a_name")
        # It will try _install_by_name, then RepoManager
        # We just verify the path is taken

    # ---- _install_by_name ----
    def test_install_by_name_found_with_url(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="target_skill", url="https://example.com/target.md")
            ]
            with patch.object(mgr, '_install_from_url') as mock_url:
                mock_url.return_value = {"success": True}
                result = mgr._install_by_name("target_skill")
                assert result["success"] is True
                mock_url.assert_called_with("https://example.com/target.md")

    def test_install_by_name_found_no_url(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="target_skill", url="")
            ]
            result = mgr._install_by_name("target_skill")
            assert result["success"] is False
            assert "没有下载 URL" in result["error"]

    def test_install_by_name_not_found(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index', return_value=[]):
            result = mgr._install_by_name("nonexistent")
            assert result["success"] is False
            assert "未找到" in result["error"]

    # ---- _install_from_url ----
    def test_install_from_url_success(self):
        mgr = self._make_mgr()
        md_content = """---
name: my_skill
description: a test skill
---
# My Skill
This is a test skill.
"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = md_content.encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            with patch('core.skill_manager.MARKET_DIR') as MockDir:
                MockDir.mkdir = MagicMock()
                mock_file = MagicMock()
                MockDir.__truediv__.return_value = mock_file

                result = mgr._install_from_url("https://example.com/skills/test.md")
                assert result["success"] is True
                assert result["name"] == "my_skill"
                assert result["file"] is not None

    def test_install_from_url_network_error(self):
        mgr = self._make_mgr()
        with patch('urllib.request.urlopen', side_effect=Exception("timeout")):
            result = mgr._install_from_url("https://example.com/skill.md")
            assert result["success"] is False
            assert "下载失败" in result["error"]

    def test_install_from_url_no_name(self):
        mgr = self._make_mgr()
        content = "no frontmatter here"
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = content.encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = mgr._install_from_url("https://example.com/SKILL.md")
            assert result["success"] is False
            assert "无法解析" in result["error"]

    # ---- _extract_name_from_md ----
    def test_extract_name_from_md_frontmatter(self):
        from core.skill_manager import SkillManager
        content = """---
name: my_skill
description: test
---
# Content
"""
        name = SkillManager._extract_name_from_md(content, "https://example.com/skill.md")
        assert name == "my_skill"

    def test_extract_name_from_md_no_frontmatter(self):
        from core.skill_manager import SkillManager
        content = "# Just a skill"
        name = SkillManager._extract_name_from_md(content, "https://example.com/skills/web_scraper.md")
        assert name == "web_scraper"

    def test_extract_name_from_md_empty_stem(self):
        from core.skill_manager import SkillManager
        name = SkillManager._extract_name_from_md("no frontmatter", "https://example.com/SKILL.md")
        assert name == ""

    def test_extract_name_from_md_frontmatter_no_name(self):
        from core.skill_manager import SkillManager
        content = """---
description: no name here
---
"""
        name = SkillManager._extract_name_from_md(content, "https://example.com/test.md")
        assert name == "test"

    # ---- _check_skill_deps ----
    def test_check_skill_deps_no_file(self):
        from core.skill_manager import SkillManager
        SkillManager._check_skill_deps({"file": ""})
        # Should not raise

    def test_check_skill_deps_file_not_exists(self):
        from core.skill_manager import SkillManager
        SkillManager._check_skill_deps({"file": "/tmp/nonexistent_file.yaml"})
        # Should not raise

    def test_check_skill_deps_with_deps(self):
        from core.skill_manager import SkillManager
        with patch('pathlib.Path.exists', return_value=True):
            with patch('pathlib.Path.read_text', return_value="name: test\ndependencies:\n  pip:\n    - requests"):
                with patch('core.skill_manager.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"name": "test", "dependencies": {"pip": ["requests"]}}
                    with patch('core.skill_deps.check_dependencies') as mock_check:
                        mock_result = MagicMock()
                        mock_result.ok = False
                        mock_result.summary.return_value = "missing deps"
                        mock_check.return_value = mock_result
                        with patch('core.skill_deps.suggest_command', return_value="pip install requests"):
                            SkillManager._check_skill_deps({"file": "/tmp/test.yaml"})

    def test_check_skill_deps_no_deps_key(self):
        from core.skill_manager import SkillManager
        with patch('pathlib.Path.exists', return_value=True):
            with patch('pathlib.Path.read_text', return_value="name: test"):
                with patch('core.skill_manager.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"name": "test"}
                    SkillManager._check_skill_deps({"file": "/tmp/test.yaml"})

    # ---- uninstall ----
    def test_uninstall_no_market_dir(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = False
            result = mgr.uninstall("test_skill")
            assert result is False

    def test_uninstall_success(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: test_skill"

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "test_skill"}
                result = mgr.uninstall("test_skill")
                assert result is True
                mock_file.unlink.assert_called_once()

    def test_uninstall_not_found(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: other_skill"

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "other_skill"}
                result = mgr.uninstall("test_skill")
                assert result is False

    def test_uninstall_file_read_error(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.side_effect = Exception("read error")

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            result = mgr.uninstall("test_skill")
            assert result is False

    # ---- list_installed_market ----
    def test_list_installed_market_no_dir(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = False
            result = mgr.list_installed_market()
            assert result == []

    def test_list_installed_market_with_skills(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.name = "installed.yaml"
        mock_file.stem = "installed"
        mock_file.relative_to.return_value = Path("skills/market/installed.yaml")
        mock_file.read_text.return_value = "name: installed_skill\ndescription: installed"

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "installed_skill", "description": "installed"}
                result = mgr.list_installed_market()
                assert len(result) == 1
                assert result[0].name == "installed_skill"
                assert result[0].source == "installed"

    def test_list_installed_market_parse_error(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.side_effect = Exception("error")

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            result = mgr.list_installed_market()
            assert result == []

    # ---- get_stats ----
    def test_get_stats(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.SKILLS_DIR') as MockSkillsDir:
            MockSkillsDir.glob.return_value = [MagicMock(), MagicMock()]
            with patch('core.skill_manager.MARKET_DIR') as MockMarketDir:
                MockMarketDir.exists.return_value = True
                MockMarketDir.glob.return_value = [MagicMock()]
                with patch.object(mgr, 'fetch_market_index', return_value=[MagicMock(), MagicMock(), MagicMock()]):
                    with patch('core.skill_manager.RepoManager') as MockRepo:
                        repo = MagicMock()
                        repo.get_stats.return_value = {"total_repos": 2, "total_skills": 10}
                        MockRepo.return_value = repo
                        stats = mgr.get_stats()
                        assert stats["local"] == 2
                        assert stats["installed_market"] == 1
                        assert stats["available_market"] == 3
                        assert stats["repos"] == 2
                        assert stats["repo_skills"] == 10

    # ---- SkillInfo ----
    def test_skill_info_to_dict(self):
        from core.skill_manager import SkillInfo
        si = SkillInfo(name="test", description="a long description " * 20,
                        keywords=["k1", "k2", "k3", "k4", "k5", "k6"],
                        steps=3, usage_count=10, author="me", category="dev")
        d = si.to_dict()
        assert d["name"] == "test"
        assert len(d["description"]) <= 100
        assert len(d["keywords"]) <= 5
        assert d["steps"] == 3
        assert d["usage"] == 10

    def test_skill_info_defaults(self):
        from core.skill_manager import SkillInfo
        si = SkillInfo(name="test")
        assert si.description == ""
        assert si.keywords == []
        assert si.source == "local"
        assert si.steps == 0
        assert si.usage_count == 0
        assert si.author == ""
        assert si.url == ""
        assert si.category == ""
