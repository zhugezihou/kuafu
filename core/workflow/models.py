"""
core/workflow/models.py — 夸父工作流数据模型

工作流引擎的核心类型定义。零外部依赖，纯 dataclass + 枚举。

设计参考:
  - LangGraph 的 StateGraph 模型（共享状态 + 条件路由）
  - Temporal 的 Durable Execution（状态持久化概念）
  - Prefect 的 @flow/@task 声明式风格

所有类型都是 JSON 可序列化的，用于持久化、HTTP API 传输。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════
# 节点类型
# ═══════════════════════════════════════════════════════════════


class NodeType(str, Enum):
    """内置节点类型。"""

    LLM_CALL = "llm_call"           # LLM 调用
    TOOL_CALL = "tool_call"         # 工具调用（web_search, write_file 等）
    SUB_WORKFLOW = "sub_workflow"   # 子工作流
    APPROVAL = "approval"           # 人工审批关卡
    CODE = "code"                   # 纯 Python 代码
    CONDITION = "condition"         # 条件路由（根据状态选择下一节点）
    INPUT = "input"                 # 工作流入参
    OUTPUT = "output"               # 工作流输出


class NodeStatus(str, Enum):
    """节点执行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"            # 等待审批/外部输入
    TIMEOUT = "timeout"


class WorkflowStatus(str, Enum):
    """工作流整体状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"              # 人工暂停
    CANCELLED = "cancelled"


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════


@dataclass
class NodeDef:
    """工作流节点定义。

    这是用户在 YAML/Python 中定义的节点规格。
    """
    id: str                                  # 节点唯一 ID
    type: NodeType                           # 节点类型
    name: str = ""                           # 人类可读名称
    description: str = ""                    # 描述

    # 依赖关系
    depends_on: list[str] = field(default_factory=list)  # 前置节点 ID 列表

    # 节点参数
    config: dict[str, Any] = field(default_factory=dict)
    # LLM_CALL:  {"prompt": "...", "model": "deepseek", "temperature": 0.7}
    # TOOL_CALL: {"tool": "web_search", "params": {"query": "..."}}
    # APPROVAL:  {"message": "请审批以下内容", "timeout": 3600}
    # CODE:      {"source": "def run(ctx): return ctx['input']"}
    # CONDITION: {"conditions": [{"if": "result.status == 'ok'", "then": "node_b"}], "default": "node_c"}

    # 执行策略
    retry_count: int = 0                     # 失败重试次数
    retry_delay: float = 5.0                 # 重试间隔（秒）
    timeout: float = 0.0                     # 超时（0 = 不超时）
    max_wait: float = 0.0                    # 审批最大等待时间（0 = 无限）

    # 条件路由（简化版，复杂条件走 CONDITION 节点）
    on_success: str = ""                     # 成功后显式指定下一节点
    on_failure: str = ""                     # 失败后走的路由

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name or self.id,
            "description": self.description,
            "depends_on": self.depends_on,
            "config": self.config,
            "retry_count": self.retry_count,
            "retry_delay": self.retry_delay,
            "timeout": self.timeout,
            "max_wait": self.max_wait,
            "on_success": self.on_success,
            "on_failure": self.on_failure,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NodeDef:
        d = dict(d)
        d["type"] = NodeType(d["type"]) if isinstance(d["type"], str) else d["type"]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkflowDef:
    """工作流定义——完整的 DAG 规格。

    包含所有节点定义和元信息。
    """
    name: str                                # 工作流名称
    version: str = "1.0.0"                   # 版本
    description: str = ""                    # 描述

    nodes: list[NodeDef] = field(default_factory=list)

    # 全局配置
    max_concurrency: int = 4                 # 最大并行节点数
    timeout: float = 0.0                     # 整体超时
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "max_concurrency": self.max_concurrency,
            "timeout": self.timeout,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowDef:
        nodes = [NodeDef.from_dict(n) for n in d.get("nodes", [])]
        return cls(
            name=d["name"],
            version=d.get("version", "1.0.0"),
            description=d.get("description", ""),
            nodes=nodes,
            max_concurrency=d.get("max_concurrency", 4),
            timeout=d.get("timeout", 0.0),
            tags=d.get("tags", []),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> WorkflowDef:
        """从 YAML 文件加载工作流定义。"""
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def get_node(self, node_id: str) -> Optional[NodeDef]:
        """按 ID 查找节点。"""
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None


# ═══════════════════════════════════════════════════════════════
# 运行时状态
# ═══════════════════════════════════════════════════════════════


@dataclass
class NodeRuntime:
    """节点运行时状态——单次执行记录。"""
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    result: Any = None                       # 执行结果
    error: str = ""                          # 错误信息
    started_at: float = 0.0
    completed_at: float = 0.0
    attempts: int = 0                        # 已尝试次数
    output: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        if self.completed_at > 0:
            return self.completed_at - self.started_at
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0.0

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "attempts": self.attempts,
            "output": self.output,
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NodeRuntime:
        return cls(
            node_id=d["node_id"],
            status=NodeStatus(d["status"]) if isinstance(d["status"], str) else d["status"],
            result=d.get("result"),
            error=d.get("error", ""),
            started_at=d.get("started_at", 0.0),
            completed_at=d.get("completed_at", 0.0),
            attempts=d.get("attempts", 0),
            output=d.get("output", {}),
        )


@dataclass
class WorkflowRuntime:
    """工作流运行时——一次执行的完整状态。"""
    workflow_id: str                         # 唯一执行 ID
    workflow_def: WorkflowDef                 # 工作流定义
    status: WorkflowStatus = WorkflowStatus.PENDING
    status: WorkflowStatus = WorkflowStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    nodes: dict[str, NodeRuntime] = field(default_factory=dict)  # node_id → runtime

    # 共享状态（工作流全局）
    context: dict[str, Any] = field(default_factory=dict)

    # 输入/输出
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    # 审批等待队列
    pending_approvals: list[dict] = field(default_factory=list)

    @property
    def progress(self) -> dict:
        """进度摘要。"""
        total = len(self.workflow_def.nodes)
        completed = sum(1 for n in self.nodes.values() if n.status == NodeStatus.COMPLETED)
        failed = sum(1 for n in self.nodes.values() if n.status == NodeStatus.FAILED)
        running = sum(1 for n in self.nodes.values() if n.status == NodeStatus.RUNNING)
        blocked = sum(1 for n in self.nodes.values() if n.status == NodeStatus.BLOCKED)
        pending = sum(1 for n in self.nodes.values() if n.status == NodeStatus.PENDING)
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "blocked": blocked,
            "pending": pending,
            "percent": round(completed / total * 100, 1) if total > 0 else 0,
        }

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_def.name,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "progress": self.progress,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "error": self.error,
            "pending_approvals": self.pending_approvals,
        }

    def get_node_result(self, node_id: str) -> Any:
        """获取某节点的执行结果。"""
        nr = self.nodes.get(node_id)
        if nr and nr.status == NodeStatus.COMPLETED:
            return nr.result
        return None

    def set_node_result(self, node_id: str, result: Any):
        """设置节点结果并写入共享 context。"""
        nr = self.nodes.get(node_id)
        if nr:
            nr.result = result
            nr.output = {"result": result}
        self.context[f"nodes.{node_id}.result"] = result
