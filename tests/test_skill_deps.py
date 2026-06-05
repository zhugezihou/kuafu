"""测试 core/skill_deps.py — 技能依赖管理器。"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock, mock_open


class TestDependencyCheckResult:
    """DependencyCheckResult 测试。"""

    def test_init_ok(self):
        from core.skill_deps import DependencyCheckResult
        r = DependencyCheckResult()
        assert r.ok is True
        assert bool(r) is True

    def test_with_missing(self):
        from core.skill_deps import DependencyCheckResult
        r = DependencyCheckResult()
        r.ok = False
        r.missing_tools.append("git")
        r.missing_packages.append("requests")
        r.missing_env.append("API_KEY")
        assert bool(r) is False

    def test_to_dict(self):
        from core.skill_deps import DependencyCheckResult
        r = DependencyCheckResult()
        r.ok = False
        r.missing_tools.append("git")
        d = r.to_dict()
        assert d["ok"] is False
        assert "git" in d["missing_tools"]

    def test_summary_ok(self):
        from core.skill_deps import DependencyCheckResult
        r = DependencyCheckResult()
        assert "✅" in r.summary()

    def test_summary_missing(self):
        from core.skill_deps import DependencyCheckResult
        r = DependencyCheckResult()
        r.ok = False
        r.missing_tools.append("git")
        r.missing_packages.append("requests")
        assert "⚠️" in r.summary()

    def test_summary_missing_env(self):
        from core.skill_deps import DependencyCheckResult
        r = DependencyCheckResult()
        r.ok = False
        r.missing_env.append("API_KEY")
        assert "环境变量" in r.summary()


class TestCheckDependencies:
    """check_dependencies 测试。"""

    def test_no_deps(self):
        """无依赖声明。"""
        from core.skill_deps import check_dependencies
        result = check_dependencies({})
        assert result.ok is True

    def test_tool_missing(self):
        """缺失系统工具。"""
        with patch("shutil.which", return_value=None):
            from core.skill_deps import check_dependencies
            result = check_dependencies({"dependencies": {"tools": ["git"]}})
            assert result.ok is False
            assert "git" in result.missing_tools

    def test_tool_found(self):
        """系统工具存在。"""
        with patch("shutil.which", return_value="/usr/bin/git"):
            from core.skill_deps import check_dependencies
            result = check_dependencies({"dependencies": {"tools": ["git"]}})
            assert result.ok is True

    def test_package_installed(self):
        """Python 包已安装。"""
        with patch("core.skill_deps._check_package", return_value=True):
            from core.skill_deps import check_dependencies
            result = check_dependencies({"dependencies": {"packages": ["requests"]}})
            assert result.ok is True

    def test_package_missing(self):
        """Python 包缺失。"""
        with patch("core.skill_deps._check_package", return_value=False):
            from core.skill_deps import check_dependencies
            result = check_dependencies({"dependencies": {"packages": ["nope"]}})
            assert result.ok is False
            assert "nope" in result.missing_packages

    def test_env_missing(self):
        """环境变量缺失。"""
        with patch.dict("os.environ", {}, clear=True):
            from core.skill_deps import check_dependencies
            result = check_dependencies({"dependencies": {"env": ["MY_KEY"]}})
            assert result.ok is False
            assert "MY_KEY" in result.missing_env

    def test_env_found(self):
        """环境变量存在。"""
        with patch.dict("os.environ", {"MY_KEY": "value"}):
            from core.skill_deps import check_dependencies
            result = check_dependencies({"dependencies": {"env": ["MY_KEY"]}})
            assert result.ok is True


class TestInstallDependencies:
    """install_dependencies 测试。"""

    def test_no_deps(self):
        from core.skill_deps import install_dependencies
        result = install_dependencies({})
        assert result["installed"] == []
        assert result["failed"] == []

    def test_stdlib_skipped(self):
        """标准库包被跳过。"""
        with patch("core.skill_deps._is_stdlib", return_value=True):
            from core.skill_deps import install_dependencies
            result = install_dependencies({"dependencies": {"packages": ["os"]}})
            assert "os" in result["skipped"][0]

    def test_already_installed_skipped(self):
        """已安装包被跳过。"""
        with patch("core.skill_deps._is_stdlib", return_value=False):
            with patch("core.skill_deps._check_package", return_value=True):
                from core.skill_deps import install_dependencies
                result = install_dependencies({"dependencies": {"packages": ["requests"]}})
                assert len(result["installed"]) == 0

    def test_install_success(self):
        """安装成功。"""
        with patch("core.skill_deps._is_stdlib", return_value=False):
            with patch("core.skill_deps._check_package", return_value=False):
                with patch("core.skill_deps._pip_install",
                           return_value={"success": True}):
                    from core.skill_deps import install_dependencies
                    result = install_dependencies({"dependencies": {"packages": ["requests"]}})
                    assert "requests" in result["installed"]

    def test_install_failure(self):
        """安装失败。"""
        with patch("core.skill_deps._is_stdlib", return_value=False):
            with patch("core.skill_deps._check_package", return_value=False):
                with patch("core.skill_deps._pip_install",
                           return_value={"success": False, "error": "network error"}):
                    from core.skill_deps import install_dependencies
                    result = install_dependencies({"dependencies": {"packages": ["bad-pkg"]}})
                    assert len(result["failed"]) == 1

    def test_missing_tools_warning(self):
        """缺失系统工具产生 warning。"""
        with patch("shutil.which", return_value=None):
            from core.skill_deps import install_dependencies
            result = install_dependencies({"dependencies": {"tools": ["git"]}})
            assert len(result["warnings"]) >= 1

    def test_missing_env_warning(self):
        """缺失环境变量产生 warning。"""
        with patch.dict("os.environ", {}, clear=True):
            from core.skill_deps import install_dependencies
            result = install_dependencies({"dependencies": {"env": ["API_KEY"]}})
            assert len(result["warnings"]) >= 1
            assert "环境变量" in result["warnings"][0]

    def test_install_with_upgrade(self):
        """install_dependencies 带 upgrade=True。"""
        with patch("core.skill_deps._is_stdlib", return_value=False):
            with patch("core.skill_deps._check_package", return_value=False):
                with patch("core.skill_deps._pip_install",
                           return_value={"success": True}) as mock_pip:
                    from core.skill_deps import install_dependencies
                    result = install_dependencies(
                        {"dependencies": {"packages": ["requests"]}},
                        upgrade=True,
                    )
                    assert "requests" in result["installed"]
                    # Verify --upgrade flag was passed to _pip_install
                    mock_pip.assert_called_once()
                    assert mock_pip.call_args[1].get("upgrade") is True


class TestVerifyInstallation:
    """verify_installation 测试。"""

    def test_verify_ok(self):
        with patch("core.skill_deps.check_dependencies") as mock_check:
            mock_check.return_value.ok = True
            mock_check.return_value.to_dict.return_value = {}
            from core.skill_deps import verify_installation
            result = verify_installation({"dependencies": {}})
            assert result["ready"] is True


class TestSuggestCommand:
    """suggest_command 测试。"""

    def test_no_deps(self):
        from core.skill_deps import suggest_command
        assert suggest_command({}) == ""

    def test_with_packages(self):
        from core.skill_deps import suggest_command
        result = suggest_command({"dependencies": {"packages": ["requests"]}})
        assert "pip install" in result

    def test_with_tools(self):
        with patch("shutil.which", return_value=None):
            from core.skill_deps import suggest_command
            result = suggest_command({"dependencies": {"tools": ["git"]}})
            assert "安装" in result

    def test_missing_env(self):
        with patch.dict("os.environ", {}, clear=True):
            from core.skill_deps import suggest_command
            result = suggest_command({"dependencies": {"env": ["API_KEY"]}})
            assert "环境变量" in result

    def test_all_deps_satisfied_returns_empty_string(self):
        """所有依赖满足时返回空字符串。"""
        with patch("shutil.which", return_value="/usr/bin/git"):
            with patch.dict("os.environ", {"MY_KEY": "val"}):
                from core.skill_deps import suggest_command
                result = suggest_command({
                    "dependencies": {
                        "packages": [],
                        "tools": ["git"],
                        "env": ["MY_KEY"],
                    }
                })
                assert result == ""


class TestParsePackageSpec:
    """_parse_package_spec 测试。"""

    def test_no_version(self):
        from core.skill_deps import _parse_package_spec
        name, ver = _parse_package_spec("requests")
        assert name == "requests"
        assert ver == ""

    def test_with_version(self):
        from core.skill_deps import _parse_package_spec
        name, ver = _parse_package_spec("requests>=2.25")
        assert name == "requests"
        assert ">=" in ver

    def test_spaces(self):
        from core.skill_deps import _parse_package_spec
        name, ver = _parse_package_spec("  pandas >= 1.0  ")
        assert name == "pandas"
        assert ">=" in ver

    def test_no_regex_match_returns_raw(self):
        """正则不匹配时返回原字符串。"""
        from core.skill_deps import _parse_package_spec
        name, ver = _parse_package_spec("")
        assert name == ""
        assert ver == ""


class TestIsStdlib:
    """_is_stdlib 测试。"""

    def test_stdlib_returns_true(self):
        from core.skill_deps import _is_stdlib
        assert _is_stdlib("os") is True

    def test_non_stdlib(self):
        from core.skill_deps import _is_stdlib
        assert _is_stdlib("requests") is False


class TestCheckVersion:
    """_check_version 测试。"""

    def test_empty_spec(self):
        from core.skill_deps import _check_version
        assert _check_version("1.0", "") is True

    def test_ge_ok(self):
        from core.skill_deps import _check_version
        assert _check_version("2.0", ">=1.0") is True

    def test_ge_fail(self):
        from core.skill_deps import _check_version
        assert _check_version("0.5", ">=1.0") is False

    def test_eq_ok(self):
        from core.skill_deps import _check_version
        assert _check_version("1.5", "==1.5") is True

    def test_eq_fail(self):
        from core.skill_deps import _check_version
        assert _check_version("2.0", "==1.5") is False

    def test_gt_ok(self):
        from core.skill_deps import _check_version
        assert _check_version("2.0", ">1.0") is True

    def test_lt_ok(self):
        from core.skill_deps import _check_version
        assert _check_version("0.5", "<1.0") is True

    def test_invalid_spec(self):
        from core.skill_deps import _check_version
        assert _check_version("1.0", "???") is True  # defaults to pass

    def test_different_lengths(self):
        from core.skill_deps import _check_version
        # 补齐后相等 → True
        assert _check_version("1.0", ">=1.0.0") is True

    def test_le_ok(self):
        from core.skill_deps import _check_version
        assert _check_version("0.5", "<=1.0") is True

    def test_le_fail(self):
        from core.skill_deps import _check_version
        assert _check_version("2.0", "<=1.0") is False

    def test_ne_ok(self):
        from core.skill_deps import _check_version
        assert _check_version("2.0", "!=1.0") is True

    def test_ne_fail(self):
        from core.skill_deps import _check_version
        assert _check_version("1.0", "!=1.0") is False

    def test_unknown_op(self):
        """未知操作符返回 True。"""
        from core.skill_deps import _check_version
        assert _check_version("1.0", "~=1.0") is True

    def test_version_value_error_exception(self):
        """版本解析异常时返回 True。"""
        from core.skill_deps import _check_version
        assert _check_version("abc", ">=1.0") is True

    def test_fallthrough_return_true(self):
        """未识别的操作符（如 =>）走 fallthrough 返回 True。"""
        from core.skill_deps import _check_version
        # '=>' matches regex [><=!]+ as op '=>' which is not handled
        assert _check_version("1.0", "=>1.0") is True


class TestCheckPackage:
    """_check_package 测试。"""

    def test_stdlib_bypass(self):
        with patch("core.skill_deps._is_stdlib", return_value=True):
            from core.skill_deps import _check_package
            assert _check_package("os") is True

    def test_import_success(self):
        with patch("core.skill_deps._is_stdlib", return_value=False):
            mock_mod = MagicMock()
            # Mock at module attribute level
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: mock_mod if name == "requests" else orig(name)
            from core.skill_deps import _check_package
            try:
                assert _check_package("requests") is True
            finally:
                core.skill_deps.importlib.import_module = orig

    def test_import_fallback_to_pip_list(self):
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: (_ for _ in ()).throw(ImportError("not found")) if name == "requests" else orig(name)
            with patch("core.skill_deps._check_by_pip_list", return_value=True):
                from core.skill_deps import _check_package
                try:
                    assert _check_package("requests") is True
                finally:
                    core.skill_deps.importlib.import_module = orig

    def test_import_and_pip_list_fail(self):
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: (_ for _ in ()).throw(ImportError()) if name == "nonexistent" else orig(name)
            with patch("core.skill_deps._check_by_pip_list", return_value=False):
                from core.skill_deps import _check_package
                try:
                    assert _check_package("nonexistent") is False
                finally:
                    core.skill_deps.importlib.import_module = orig

    def test_import_exception_fallback(self):
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: (_ for _ in ()).throw(ValueError("corrupt")) if name == "broken" else orig(name)
            with patch("core.skill_deps._check_by_pip_list", return_value=True):
                from core.skill_deps import _check_package
                try:
                    assert _check_package("broken") is True
                finally:
                    core.skill_deps.importlib.import_module = orig

    def test_with_version_check(self):
        mock_mod = MagicMock()
        mock_mod.__version__ = "2.25.0"
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: mock_mod if name == "requests" else orig(name)
            from core.skill_deps import _check_package
            try:
                assert _check_package("requests", ">=2.0") is True
            finally:
                core.skill_deps.importlib.import_module = orig

    def test_version_check_fail(self):
        mock_mod = MagicMock()
        mock_mod.__version__ = "1.0.0"
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: mock_mod if name == "requests" else orig(name)
            from core.skill_deps import _check_package
            try:
                assert _check_package("requests", ">=2.0") is False
            finally:
                core.skill_deps.importlib.import_module = orig

    def test_version_check_no_version_attr_uses_metadata(self):
        """模块没有 __version__ 属性，回退到 importlib.metadata.version。"""
        mock_mod = MagicMock(spec=[])  # no __version__ attribute
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: mock_mod if name == "mypkg" else orig(name)
            with patch("core.skill_deps.importlib.metadata.version", return_value="2.0.0"):
                from core.skill_deps import _check_package
                try:
                    assert _check_package("mypkg", ">=1.0") is True
                finally:
                    core.skill_deps.importlib.import_module = orig

    def test_version_check_no_version_attr_metadata_exception(self):
        """模块没有 __version__ 且 metadata.version 也抛异常。"""
        mock_mod = MagicMock(spec=[])
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: mock_mod if name == "mypkg" else orig(name)
            with patch("core.skill_deps.importlib.metadata.version", side_effect=Exception("no metadata")):
                from core.skill_deps import _check_package
                try:
                    # No version info found, should return True
                    assert _check_package("mypkg", ">=1.0") is True
                finally:
                    core.skill_deps.importlib.import_module = orig

    def test_version_check_exception_in_check_version(self):
        """版本检查过程中抛异常，返回 True。"""
        mock_mod = MagicMock()
        mock_mod.__version__ = "1.0"
        with patch("core.skill_deps._is_stdlib", return_value=False):
            import core.skill_deps
            orig = core.skill_deps.importlib.import_module
            core.skill_deps.importlib.import_module = lambda name: mock_mod if name == "mypkg" else orig(name)
            with patch("core.skill_deps._check_version", side_effect=Exception("boom")):
                from core.skill_deps import _check_package
                try:
                    assert _check_package("mypkg", ">=1.0") is True
                finally:
                    core.skill_deps.importlib.import_module = orig


class TestPipInstall:
    """_pip_install 测试。"""

    def test_no_confirm(self):
        from core.skill_deps import _pip_install
        result = _pip_install("pkg", auto_confirm=False)
        assert result["success"] is False

    def test_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            from core.skill_deps import _pip_install
            result = _pip_install("requests", auto_confirm=True)
            assert result["success"] is True

    def test_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "error msg"
            from core.skill_deps import _pip_install
            result = _pip_install("bad-pkg", auto_confirm=True)
            assert result["success"] is False

    def test_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            from core.skill_deps import _pip_install
            result = _pip_install("pkg", auto_confirm=True)
            assert result["success"] is False

    def test_exception(self):
        with patch("subprocess.run", side_effect=Exception("crash")):
            from core.skill_deps import _pip_install
            result = _pip_install("pkg", auto_confirm=True)
            assert result["success"] is False

    def test_with_upgrade_flag(self):
        """upgrade=True 时传递 --upgrade 参数。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            from core.skill_deps import _pip_install
            result = _pip_install("pkg", auto_confirm=True, upgrade=True)
            assert result["success"] is True
            # Verify --upgrade was in the command
            call_args = mock_run.call_args[0][0]
            assert "--upgrade" in call_args


class TestCheckByPipList:
    """_check_by_pip_list 测试。"""

    def test_package_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Package Version\n------ -------\nrequests 2.25.0\n"
            from core.skill_deps import _check_by_pip_list
            assert _check_by_pip_list("requests") is True

    def test_package_not_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Package Version\n------ -------\n"
            from core.skill_deps import _check_by_pip_list
            assert _check_by_pip_list("nonexistent") is False

    def test_exception_safe(self):
        with patch("subprocess.run", side_effect=Exception("fail")):
            from core.skill_deps import _check_by_pip_list
            assert _check_by_pip_list("anything") is False


class TestGetDepsFromSkill:
    """get_deps_from_skill 测试。"""

    def test_skill_found_local(self):
        """本地找到技能，读取 YAML 返回依赖。"""
        from core.skill_manager import SkillInfo

        mock_skill = SkillInfo(
            name="test_skill",
            file_path="skills/test_skill.yaml",
            source="local",
        )

        fake_yaml = b"""
name: test_skill
dependencies:
  packages:
    - requests
  tools:
    - git
"""

        with patch("core.skill_manager.SkillManager.list_local",
                   return_value=[mock_skill]):
            with patch("core.skill_manager.SkillManager.list_installed_market",
                       return_value=[]):
                with patch("pathlib.Path.is_absolute", return_value=False):
                    with patch("pathlib.Path.read_text",
                              return_value=fake_yaml):
                        from core.skill_deps import get_deps_from_skill
                        result = get_deps_from_skill("test_skill")
                        assert result["exists"] is True
                        assert result["source"] == "local"
                        assert "requests" in result["dependencies"]["packages"]
                        assert "git" in result["dependencies"]["tools"]

    def test_skill_found_absolute_path(self):
        """技能文件路径是绝对路径时，不拼装 ROOT_DIR。"""
        from core.skill_manager import SkillInfo

        mock_skill = SkillInfo(
            name="test_skill",
            file_path="/tmp/abs/test_skill.yaml",
            source="local",
        )

        fake_yaml = b"name: test_skill\ndependencies:\n  packages:\n    - flask\n"

        with patch("core.skill_manager.SkillManager.list_local",
                   return_value=[mock_skill]):
            with patch("core.skill_manager.SkillManager.list_installed_market",
                       return_value=[]):
                with patch("pathlib.Path.is_absolute", return_value=True):
                    with patch("pathlib.Path.read_text",
                              return_value=fake_yaml):
                        from core.skill_deps import get_deps_from_skill
                        result = get_deps_from_skill("test_skill")
                        assert result["exists"] is True
                        assert "flask" in result["dependencies"]["packages"]

    def test_skill_not_found_local_found_in_repo(self):
        """本地未找到，仓库中找到。"""
        from core.skill_manager import SkillInfo

        with patch("core.skill_manager.SkillManager.list_local",
                   return_value=[]):
            with patch("core.skill_manager.SkillManager.list_installed_market",
                       return_value=[]):
                with patch("core.skill_repo.RepoManager.search",
                          return_value=[{
                              "name": "test_skill",
                              "repo": "my-repo",
                              "url": "https://example.com",
                          }]):
                    from core.skill_deps import get_deps_from_skill
                    result = get_deps_from_skill("test_skill")
                    assert result["exists"] is True
                    assert result["source"] == "remote"

    def test_skill_not_found_anywhere(self):
        """任何地方都找不到技能。"""
        from core.skill_manager import SkillInfo

        with patch("core.skill_manager.SkillManager.list_local",
                   return_value=[]):
            with patch("core.skill_manager.SkillManager.list_installed_market",
                       return_value=[]):
                with patch("core.skill_repo.RepoManager.search",
                          return_value=[]):
                    from core.skill_deps import get_deps_from_skill
                    result = get_deps_from_skill("nonexistent")
                    assert result["exists"] is False
                    assert result["dependencies"] == {}

    def test_skill_found_yaml_read_error(self):
        """找到技能但读取 YAML 出错。"""
        from core.skill_manager import SkillInfo

        mock_skill = SkillInfo(
            name="bad_skill",
            file_path="skills/bad_skill.yaml",
            source="local",
        )

        with patch("core.skill_manager.SkillManager.list_local",
                   return_value=[mock_skill]):
            with patch("core.skill_manager.SkillManager.list_installed_market",
                       return_value=[]):
                with patch("pathlib.Path.is_absolute", return_value=False):
                    with patch("pathlib.Path.read_text",
                              side_effect=Exception("file not found")):
                        from core.skill_deps import get_deps_from_skill
                        result = get_deps_from_skill("bad_skill")
                        assert result["exists"] is True
                        assert "error" in result
