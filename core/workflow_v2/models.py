"""
core/workflow_v2/models.py — 工作流数据模型。

工作流 = WorkflowDef → 多个 NodeDef → 执行 → WorkflowRuntime
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeType(str, Enum):
    """节点类型。"""
    TERMINAL = "terminal"        # 终端命令
    HTTP = "http"                # HTTP 请求
    LLM = "llm"                  # LLM 调用
    CONDITION = "condition"      # 条件分支
    SUBFLOW = "subflow"          # 子工作流


class NodeStatus(str, Enum):
    """节点执行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class NodeDef:
    """工作流节点定义。"""
    id: str                                    # 节点唯一 ID
    type: NodeType                             # 节点类型
    name: str = ""                             # 人类可读名称
    description: str = ""                      # 描述
    depends_on: list[str] = field(default_factory=list)  # 前置节点 ID 列表
    parallel: bool = False                     # 是否可并行（自动推断）
    
    # 节点配置（按 type 不同）
    command: str = ""                          # TERMINAL: shell 命令
    url: str = ""                              # HTTP: 请求 URL
    method: str = "GET"                        # HTTP: 方法
    headers: dict = field(default_factory=dict) # HTTP: 请求头
    body: str = ""                             # HTTP: 请求体
    prompt: str = ""                           # LLM: 提示词
    model: str = ""                            # LLM: 模型名
    condition: str = ""                        # CONDITION: 条件表达式
    if_true: str = ""                          # CONDITION: 真时跳转的节点
    if_false: str = ""                         # CONDITION: 假时跳转的节点
    subflow_ref: str = ""                      # SUBFLOW: 子工作流文件路径
    
    # 控制
    timeout: int = 300                         # 超时秒数
    retry_count: int = 0                       # 重试次数
    retry_delay: int = 5                       # 重试间隔秒数


@dataclass
class NodeRuntime:
    """节点运行时——一次执行的快照。"""
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    output: str = ""                           # 标准输出
    error: str = ""                            # 错误信息
    started_at: float = 0.0
    completed_at: float = 0.0
    attempts: int = 0


@dataclass
class WorkflowDef:
    """工作流定义。"""
    name: str                                  # 工作流名称
    description: str = ""                      # 描述
    nodes: list[NodeDef] = field(default_factory=list)  # 节点列表
    
    # 触发配置
    trigger: str = ""                          # cron 表达式，如 "0 10 * * *"
    trigger_type: str = "cron"                 # cron | webhook | manual
    
    # 输入参数定义（飞书卡片配置用）
    inputs: list[dict] = field(default_factory=list)
    # [{"key": "video_dir", "label": "视频目录", "type": "text", "default": "douyin/templates"}]

    @classmethod
    def from_yaml(cls, path: str) -> WorkflowDef:
        """从 YAML 文件加载。"""
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: dict) -> WorkflowDef:
        nodes = []
        for nd in data.get("nodes", []):
            nd["type"] = NodeType(nd["type"])
            nodes.append(NodeDef(**nd))
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            nodes=nodes,
            trigger=data.get("trigger", ""),
            trigger_type=data.get("trigger_type", "manual"),
            inputs=data.get("inputs", []),
        )
    
    def to_yaml(self, path: str):
        """保存为 YAML 文件。"""
        import yaml
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, allow_unicode=True, default_flow_style=False)
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "trigger": self.trigger,
            "trigger_type": self.trigger_type,
            "inputs": self.inputs,
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type.value,
                    "name": n.name,
                    "depends_on": n.depends_on,
                    "parallel": n.parallel,
                    "command": n.command,
                    "url": n.url,
                    "method": n.method,
                    "headers": n.headers,
                    "body": n.body,
                    "prompt": n.prompt,
                    "model": n.model,
                    "condition": n.condition,
                    "if_true": n.if_true,
                    "if_false": n.if_false,
                    "subflow_ref": n.subflow_ref,
                    "timeout": n.timeout,
                    "retry_count": n.retry_count,
                    "retry_delay": n.retry_delay,
                }
                for n in self.nodes
            ],
        }


@dataclass
class WorkflowRuntime:
    """工作流运行时——一次执行的完整记录。"""
    workflow_name: str
    status: str = "pending"                    # pending | running | completed | failed
    started_at: float = 0.0
    completed_at: float = 0.0
    nodes: dict[str, NodeRuntime] = field(default_factory=dict)
    error: str = ""
    
    @property
    def duration(self) -> float:
        if self.completed_at and self.started_at:
            return round(self.completed_at - self.started_at, 3)
        return 0.0
    
    def to_dict(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration": self.duration,
            "error": self.error,
            "nodes": {
                nid: {
                    "status": nr.status.value,
                    "output": nr.output[:500] if nr.output else "",
                    "error": nr.error[:200] if nr.error else "",
                    "attempts": nr.attempts,
                }
                for nid, nr in self.nodes.items()
            },
        }
