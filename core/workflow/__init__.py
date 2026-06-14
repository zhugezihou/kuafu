"""
core/workflow/ — 夸父 DAG 工作流引擎。

工作流引擎让用户定义可复用的执行流程（DAG），
支持并行、条件分支、人工审批、重试、持久化。

与现有架构的关系：
  - 不修改现有模块（agent_loop/tool_registry/approval 等）
  - 通过 ToolRegistry 调用工具，ApprovalManager 处理审批
  - 与 AgentLoop 平行——简单任务走 AgentLoop，复杂任务走 Workflow

用法:
    # Python API
    from core.workflow import (
        WorkflowEngine, WorkflowDef, WorkflowRuntime,
        WorkflowStore, NodeDef, NodeType, WorkflowStatus
    )

    wf = WorkflowDef(
        name="调研报告",
        nodes=[
            NodeDef(id="search", type=NodeType.TOOL_CALL,
                    config={"tool": "web_search",
                            "params": {"query": "{{input.topic}}"}}),
            NodeDef(id="draft", type=NodeType.LLM_CALL,
                    config={"prompt": "总结: {{nodes.search.result}}"},
                    depends_on=["search"]),
        ]
    )

    engine = WorkflowEngine()
    rt = engine.run(wf, input_data={"topic": "Rust"})
    print(rt.output_data)

    # YAML 定义
    wf = WorkflowDef.from_yaml("workflows/report.yaml")
"""

from core.workflow.models import (
    NodeDef,
    NodeRuntime,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRuntime,
    WorkflowStatus,
)
from core.workflow.engine import WorkflowEngine
from core.workflow.persistence import WorkflowStore

__all__ = [
    "NodeDef",
    "NodeRuntime",
    "NodeStatus",
    "NodeType",
    "WorkflowDef",
    "WorkflowRuntime",
    "WorkflowStatus",
    "WorkflowEngine",
    "WorkflowStore",
]
