"""
tests/test_config.py — 分层配置系统测试
"""

import os
import tempfile
from pathlib import Path
import pytest
import yaml

from core.config import ConfigManager, ConfigLayer, Settings, APPROVAL_TIMEOUT


class TestSettings:

    def test_defaults(self):
        s = Settings()
        assert s.approval["enabled"] is True
        assert s.approval["timeout"] == 300
        assert s.model["name"] == "deepseek-chat"
        assert s.memory["hindsight_enabled"] is True


class TestConfigManager:

    def test_default_load(self):
        """不传参数加载，使用默认值"""
        cm = ConfigManager(search_paths=[])  # 空，无外部文件
        cm.load()
        assert cm.get("approval.timeout") == 300
        assert cm.get("model.name") == "deepseek-chat"

    def test_get_with_default(self):
        cm = ConfigManager(search_paths=[])
        cm.load()
        assert cm.get("nonexistent.key", "fallback") == "fallback"
        assert cm.get("approval.nonexistent", 42) == 42

    def test_cli_override(self):
        """CLI 覆盖最高优先级"""
        cm = ConfigManager(search_paths=[])
        cm.load()
        cm.set_override("approval.timeout", 600, source=ConfigLayer.CLI)
        assert cm.get("approval.timeout") == 600

    def test_set_and_get(self):
        cm = ConfigManager(search_paths=[])
        cm.load()
        cm.set("approval.mode", "off")
        assert cm.get("approval.mode") == "off"

    def test_layer_precedence(self):
        """高优先级覆盖低优先级"""
        cm = ConfigManager(search_paths=[])
        cm.load()
        # 模拟用户层
        cm._layers[ConfigLayer.USER] = {"approval": {"timeout": 120}}
        cm._merged = cm._merge_all()
        assert cm.get("approval.timeout") == 120

        # CLI 覆盖
        cm.set_override("approval.timeout", 999, source=ConfigLayer.CLI)
        assert cm.get("approval.timeout") == 999


class TestFileLoading:

    def test_load_user_config(self):
        """加载用户配置文件"""
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d) / ".kuafu"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "config.yaml"
            config_path.write_text(yaml.dump({
                "approval": {"timeout": 500, "mode": "interactive"},
                "model": {"name": "gpt-4"},
            }))

            cm = ConfigManager(search_paths=[
                (ConfigLayer.USER, config_path),
            ])
            cm.load()
            assert cm.get("approval.timeout") == 500
            assert cm.get("approval.mode") == "interactive"
            assert cm.get("model.name") == "gpt-4"

    def test_project_config_overrides_user(self):
        """项目配置覆盖用户配置"""
        with tempfile.TemporaryDirectory() as d:
            # 用户配置
            user_dir = Path(d) / ".kuafu"
            user_dir.mkdir(parents=True)
            (user_dir / "config.yaml").write_text(yaml.dump({
                "approval": {"timeout": 100},
            }))

            # 项目配置（同级目录下嵌套 .kuafu/）
            proj_dir = Path(d) / "project"
            proj_dir.mkdir(parents=True)
            proj_config = proj_dir / ".kuafu" / "config.yaml"
            proj_config.parent.mkdir(parents=True)
            proj_config.write_text(yaml.dump({
                "approval": {"timeout": 200},
            }))

            cm = ConfigManager(search_paths=[
                (ConfigLayer.USER, user_dir / "config.yaml"),
            ])
            cm.load(cwd=str(proj_dir))
            assert cm.get("approval.timeout") == 200


class TestCompat:

    def test_approval_timeout_compat(self):
        """兼容 APPROVAL_TIMEOUT 变量"""
        assert APPROVAL_TIMEOUT > 0
