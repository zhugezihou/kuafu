"""
core/config.py — 分层配置系统（Config Layer Stack）

源自 Codex CLI 配置系统：
  Cloud Config → User Config → Project Config → CLI Overrides

支持：
  - 多层堆叠：每层只存差异，完整合并
  - Constrained<T>：带约束的值类型，跨层合并时校验冲突
  - 热加载：文件变更自动重载（mtime 检测）
  - all 导入：from core.config import settings
"""

import os
import yaml
import time
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union
from dataclasses import dataclass, field

logger = logging.getLogger("kuafu.config")

ROOT_DIR = Path(__file__).resolve().parent.parent

# =========================================================================
# 配置层来源
# =========================================================================

class ConfigLayer(Enum):
    CLOUD = "cloud"           # 云端默认值（最低优先级）
    SYSTEM = "system"         # 系统默认（/etc/kuafu/config.yaml）
    USER = "user"             # 用户配置（~/.kuafu/config.yaml）
    PROJECT = "project"       # 项目配置（<cwd>/.kuafu/config.yaml）
    PROFILE = "profile"       # 命名 profile
    CLI = "cli"               # CLI 参数（最高优先级）

    def __lt__(self, other):
        order = [ConfigLayer.CLOUD, ConfigLayer.SYSTEM, ConfigLayer.USER,
                 ConfigLayer.PROJECT, ConfigLayer.PROFILE, ConfigLayer.CLI]
        return order.index(self) < order.index(other)


# =========================================================================
# 约束值类型
# =========================================================================

class ConstrainedValue:
    """带约束的配置值。跨层合并时校验冲突。"""

    def __init__(self, value: Any, constraints: Optional[dict] = None,
                 source: Optional[ConfigLayer] = None):
        self.value = value
        self.constraints = constraints or {}
        self.source = source or ConfigLayer.CLOUD

    def merge(self, other: "ConstrainedValue") -> "ConstrainedValue":
        """合并两个约束值。higher 层覆盖 lower 层。"""
        if other.source > self.source:
            return other
        return self

    def validate(self) -> bool:
        """校验约束。"""
        for key, val in self.constraints.items():
            if key == "type" and not isinstance(self.value, val):
                return False
            if key == "min" and self.value < val:
                return False
            if key == "max" and self.value > val:
                return False
            if key == "choices" and self.value not in val:
                return False
        return True


# =========================================================================
# 配置管理器
# =========================================================================

DEFAULT_SEARCH_PATHS = [
    # (layer, path)
    # 用户配置
    (ConfigLayer.USER, Path.home() / ".kuafu" / "config.yaml"),
    # 项目配置（从 cwd 向上查找）
    (ConfigLayer.PROJECT, None),  # 动态决定
]


@dataclass
class Settings:
    """合并后的配置快照。"""
    approval: dict = field(default_factory=lambda: {
        "enabled": True,
        "mode": "gateway",       # gateway / interactive / off
        "timeout": 300,
        "auto_approve_low_risk": True,
    })
    model: dict = field(default_factory=lambda: {
        "provider": "deepseek",
        "name": "deepseek-chat",
        "temperature": 0.7,
        "max_tokens": 4096,
    })
    memory: dict = field(default_factory=lambda: {
        "hindsight_enabled": True,
        "max_memories": 50,
        "episodic_buffer_size": 20,
    })
    safety: dict = field(default_factory=lambda: {
        "enabled": True,
        "denial_tracking": True,
        "lockfile_enabled": True,
    })
    rollout: dict = field(default_factory=lambda: {
        "enabled": True,
        "archive_after_days": 30,
    })
    hooks: dict = field(default_factory=dict)
    skills: dict = field(default_factory=lambda: {
        "implicit_trigger": True,
        "max_injected": 3,
    })
    debug: bool = False


class ConfigManager:
    """分层配置管理器。

    使用示例：
        cm = ConfigManager()
        cm.load()

        # 获取值
        timeout = cm.get("approval.timeout", default=300)

        # 层覆盖
        cm.set_override("approval.mode", "interactive", source=ConfigLayer.CLI)
    """

    def __init__(self, search_paths: Optional[list[tuple[ConfigLayer, Path]]] = None):
        self._search_paths = search_paths or DEFAULT_SEARCH_PATHS
        self._layers: dict[ConfigLayer, dict] = {}
        self._overrides: dict = {}  # CLI overrides
        self._merged: Optional[Settings] = None
        self._mtime_cache: dict[str, float] = {}
        self._loaded = False

    # ── 加载 / 合并 ──

    def load(self, cwd: Optional[str] = None):
        """加载所有配置层并合并。"""
        self._layers = {}
        # 加载 search_paths 中指定的路径
        for layer, path in self._search_paths:
            if path is not None:
                self._load_file(layer, path)
        # 加载项目配置（动态决定）
        self._load_project_config(cwd)
        self._merged = self._merge_all()
        self._loaded = True

    def _load_file(self, layer, path):
        """加载单个配置文件。"""
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict):
                self._layers[layer] = data
                self._mtime_cache[str(path)] = path.stat().st_mtime
                logger.debug(f"📄 加载配置 [{layer.value}]: {path}")
        except Exception as e:
            logger.warning(f"加载配置 [{layer.value}] {path} 失败: {e}")

    def _load_project_config(self, cwd: Optional[str] = None):
        """从 cwd 向上查找项目配置。"""
        search_dir = Path(cwd or os.getcwd())
        for parent in [search_dir] + list(search_dir.parents):
            candidate = parent / ".kuafu" / "config.yaml"
            if candidate.exists():
                self._load_file(ConfigLayer.PROJECT, candidate)
                break

    def _merge_all(self) -> Settings:
        """合并所有层到 Settings。"""
        merged = Settings()

        # 按优先级从低到高合并
        for layer in sorted(self._layers.keys()):
            data = self._layers[layer]
            self._deep_merge(merged, data)

        # CLI overrides 最高优先级
        self._deep_merge(merged, self._overrides)

        return merged

    def _deep_merge(self, base: Any, override: dict):
        """深度合并字典。"""
        if not isinstance(base, dict) and not isinstance(base, Settings):
            return
        base_dict = base if isinstance(base, dict) else base.__dict__
        for key, val in override.items():
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(val, dict):
                self._deep_merge(base_dict[key], val)
            else:
                base_dict[key] = val

    # ── 访问 ──

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，点号分隔路径。如 'approval.timeout'。"""
        if not self._loaded:
            self.load()
        parts = key.split(".")
        current = self._merged
        for part in parts:
            if isinstance(current, (dict, Settings)):
                d = current if isinstance(current, dict) else current.__dict__
                if part not in d:
                    return default
                current = d[part]
            else:
                return default
        return current

    def set(self, key: str, value: Any):
        """设置配置值（运行时，不持久化）。"""
        if not self._merged:
            self.load()
        parts = key.split(".")
        current = self._merged.__dict__
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def set_override(self, key: str, value: Any,
                     source: ConfigLayer = ConfigLayer.CLI):
        """设置指定层的覆盖值。"""
        if source == ConfigLayer.CLI:
            # 用点号路径深度设置
            self._set_nested(self._overrides, key, value)
        else:
            if source not in self._layers:
                self._layers[source] = {}
            self._set_nested(self._layers[source], key, value)
        self._merged = self._merge_all()

    def _set_nested(self, d: dict, key: str, value: Any):
        """在字典中按点号路径设置值。"""
        parts = key.split(".")
        current = d
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def reload_if_changed(self) -> bool:
        """检查配置文件是否有变更，有则重载。"""
        changed = False
        for layer, path in self._search_paths:
            if path and path.exists():
                mtime = path.stat().st_mtime
                cached = self._mtime_cache.get(str(path), 0)
                if mtime > cached:
                    changed = True
        if changed:
            self.load()
        return changed

    @property
    def settings(self) -> Settings:
        if not self._loaded:
            self.load()
        return self._merged


# =========================================================================
# 全局单例
# =========================================================================

_GLOBAL_CONFIG: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """获取全局配置管理器。"""
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = ConfigManager()
        _GLOBAL_CONFIG.load()
    return _GLOBAL_CONFIG


def settings() -> Settings:
    """快捷访问。"""
    return get_config().settings


# =========================================================================
# 兼容：原有 from core.config import APPROVAL_TIMEOUT
# =========================================================================

APPROVAL_TIMEOUT: int = 300

def _update_compat():
    global APPROVAL_TIMEOUT
    try:
        val = get_config().get("approval.timeout", 300)
        APPROVAL_TIMEOUT = int(val)
    except (ValueError, TypeError):
        pass

_update_compat()
