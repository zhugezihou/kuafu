"""
core/expert_registry.py — 专家注册表

管理所有可用的角色 Agent（专家），每个专家有：
- 身份定义（system prompt 片段）
- 可用工具白名单
- 最大执行轮次
- 专属 NMM 记忆标签

主 Agent 通过 invoke_expert / invoke_experts 工具触发专家。
专家按需激活，用完即销毁，不常驻。
"""

from __future__ import annotations

import json
import logging
import os
import yaml
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("kuafu.expert")

ROOT_DIR = Path(__file__).resolve().parent.parent
EXPERTS_DIR = ROOT_DIR / "experts"


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class ExpertProfile:
    """一个专家的配置定义。

    Args:
        name: 专家唯一标识（如 "tech", "finance", "research"）
        description: 简短描述，供 LLM 判断是否调用
        identity: 系统身份定义，注入到子 Agent 的 system prompt
        tools: 允许使用的工具列表
        max_turns: 最大执行轮次
        memory_label: NMM 记忆标签，用于隔离不同专家的记忆
    """
    name: str
    description: str = ""
    identity: str = ""
    tools: list[str] = field(default_factory=list)
    max_turns: int = 6
    memory_label: str = ""

    def to_tool_param(self) -> dict:
        """返回供 LLM 调用的参数描述。"""
        return {
            "name": self.name,
            "description": self.description[:200],
        }


# ── 注册表 ──────────────────────────────────────────────


class ExpertRegistry:
    """专家注册表。

    从 experts/*.yaml 加载专家配置，提供查询接口。
    主 Agent 通过此注册表找到合适的专家。
    """

    def __init__(self, experts_dir: Optional[Path] = None):
        self._experts_dir = experts_dir or EXPERTS_DIR
        self._experts: dict[str, ExpertProfile] = {}
        self._load_all()

    def _load_all(self):
        """扫描 experts/ 目录加载所有 YAML 配置。"""
        if not self._experts_dir.exists():
            self._experts_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"[ExpertRegistry] 创建专家目录: {self._experts_dir}")
            return

        for yaml_file in sorted(self._experts_dir.glob("*.yaml")):
            try:
                profile = self._load_one(yaml_file)
                if profile:
                    self._experts[profile.name] = profile
                    logger.debug(f"[ExpertRegistry] 加载专家: {profile.name}")
            except Exception as e:
                logger.warning(f"[ExpertRegistry] 加载 {yaml_file.name} 失败: {e}")

        if self._experts:
            logger.info(f"[ExpertRegistry] 已加载 {len(self._experts)} 个专家: {', '.join(self._experts.keys())}")

    def _load_one(self, path: Path) -> Optional[ExpertProfile]:
        """加载单个 YAML 文件。"""
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data or not isinstance(data, dict):
            return None
        name = data.get("name", path.stem)
        return ExpertProfile(
            name=name,
            description=data.get("description", ""),
            identity=data.get("identity", ""),
            tools=data.get("tools", []),
            max_turns=int(data.get("max_turns", 6)),
            memory_label=data.get("memory_label", f"expert:{name}"),
        )

    def get(self, name: str) -> Optional[ExpertProfile]:
        """按名称获取专家配置。"""
        return self._experts.get(name)

    def list(self) -> list[ExpertProfile]:
        """列出所有可用专家。"""
        return list(self._experts.values())

    def search(self, query: str) -> list[ExpertProfile]:
        """按关键词搜索匹配的专家。"""
        q = query.lower()
        results = []
        for expert in self._experts.values():
            if q in expert.name.lower() or q in expert.description.lower():
                results.append(expert)
        return results

    def get_tool_descriptions(self) -> list[dict]:
        """生成供 LLM 调用的工具参数描述列表。"""
        experts = self.list()
        return [e.to_tool_param() for e in experts]

    def get_system_prompt_block(self) -> str:
        """生成注入到主 Agent system prompt 的专家说明。"""
        experts = self.list()
        if not experts:
            return ""
        lines = ["=== 可用专家 ==="]
        lines.append("当任务需要专业领域知识时，可以调用 invoke_expert 或 invoke_experts 工具委派给专家处理。")
        lines.append("可用专家：")
        for e in experts:
            lines.append(f"  • {e.name}: {e.description[:100]}")
        lines.append("用法：invoke_expert(expert=\"技术\", task=\"分析...\") — 单个专家")
        lines.append("      invoke_experts(expert_names=[\"技术\",\"市场\"], task=\"分析...\") — 多个专家并行")
        lines.append("简单任务不需要委派，直接自己处理即可。")
        return "\n".join(lines)


# ── 全局实例 ────────────────────────────────────────────

_registry: Optional[ExpertRegistry] = None


def get_registry() -> ExpertRegistry:
    """获取全局专家注册表实例。"""
    global _registry
    if _registry is None:
        _registry = ExpertRegistry()
    return _registry


def reload_registry():
    """重新加载专家配置（热更新）。"""
    global _registry
    _registry = ExpertRegistry()
    return _registry
