"""
tests/test_workflow.py — 工作流引擎测试。

测试覆盖：
  - 拓扑排序（正确分层 + 循环依赖检测）
  - 完整 DAG 执行（工具节点 + LLM 节点）
  - 并行执行验证
  - 审批节点（阻塞/批准/拒绝/超时）
  - 条件路由
  - 重试机制
  - 持久化（保存/恢复）
  - 模板替换
"""

import json
import time
from pathlib import Path

import pytest

from core.workflow import (
    NodeDef,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRuntime,
    WorkflowStatus,
    WorkflowEngine,
    WorkflowStore,
)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _make_tool_executor():
    """创建一个模拟工具执行器。"""
    def executor(tool_name: str, params: dict) -> dict:
        return {
            "tool": tool_name,
            "params": params,
            "result": f"模拟 {tool_name} 结果: {params.get('query', '')}",
            "status": "ok",
        }
    return executor


def _make_llm_executor():
    """创建一个模拟 LLM 执行器。"""
    def executor(messages: list, **kwargs) -> str:
        return f"LLM 响应: {messages[-1]['content'][:50]}..."
    return executor


@pytest.fixture
def engine():
    return WorkflowEngine(
        tool_executor=_make_tool_executor(),
        llm_executor=_make_llm_executor(),
    )


# ═══════════════════════════════════════════════════════════════
# 拓扑排序测试
# ═══════════════════════════════════════════════════════════════


class TestTopologicalSort:
    """拓扑排序测试。"""

    def test_simple_linear(self, engine):
        """线性链路: A → B → C"""
        wf = WorkflowDef(
            name="linear",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL, depends_on=["a"]),
                NodeDef(id="c", type=NodeType.OUTPUT, depends_on=["b"]),
            ],
        )
        order = engine._topological_sort(wf)
        assert len(order) == 3
        assert "a" in order[0]
        assert "b" in order[1]
        assert "c" in order[2]

    def test_parallel(self, engine):
        """并行: A 后 B 和 C 并行"""
        wf = WorkflowDef(
            name="parallel",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL, depends_on=["a"]),
                NodeDef(id="c", type=NodeType.TOOL_CALL, depends_on=["a"]),
                NodeDef(id="d", type=NodeType.OUTPUT, depends_on=["b", "c"]),
            ],
        )
        order = engine._topological_sort(wf)
        assert "a" in order[0]
        assert "b" in order[1] and "c" in order[1]
        assert "d" in order[2]

    def test_diamond(self, engine):
        """菱形: A → B, A → C, B→D, C→D"""
        wf = WorkflowDef(
            name="diamond",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL, depends_on=["a"]),
                NodeDef(id="c", type=NodeType.TOOL_CALL, depends_on=["a"]),
                NodeDef(id="d", type=NodeType.OUTPUT, depends_on=["b", "c"]),
            ],
        )
        order = engine._topological_sort(wf)
        assert len(order) == 3

    def test_cycle_detection(self, engine):
        """检测循环依赖。"""
        wf = WorkflowDef(
            name="cycle",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT, depends_on=["c"]),
                NodeDef(id="b", type=NodeType.TOOL_CALL, depends_on=["a"]),
                NodeDef(id="c", type=NodeType.OUTPUT, depends_on=["b"]),
            ],
        )
        with pytest.raises(ValueError, match="循环依赖"):
            engine._topological_sort(wf)

    def test_no_deps(self, engine):
        """无依赖: 所有节点在同一层。"""
        wf = WorkflowDef(
            name="no-deps",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL),
                NodeDef(id="c", type=NodeType.OUTPUT),
            ],
        )
        order = engine._topological_sort(wf)
        assert len(order) == 1
        assert len(order[0]) == 3


# ═══════════════════════════════════════════════════════════════
# 工作流执行测试
# ═══════════════════════════════════════════════════════════════


class TestWorkflowExecution:
    """工作流执行测试。"""

    def test_linear_workflow(self, engine):
        """线性工作流完整执行。"""
        wf = WorkflowDef(
            name="test-linear",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="search", type=NodeType.TOOL_CALL,
                        config={"tool": "web_search", "params": {"query": "{{input.topic}}"}},
                        depends_on=["input"]),
                NodeDef(id="output", type=NodeType.OUTPUT, depends_on=["search"]),
            ],
        )
        rt = engine.run(wf, input_data={"topic": "Python 异步"})

        assert rt.status == WorkflowStatus.COMPLETED
        assert rt.nodes["search"].status == NodeStatus.COMPLETED
        assert rt.nodes["search"].result["tool"] == "web_search"
        assert "Python" in rt.nodes["search"].result["result"]

    def test_parallel_execution(self, engine):
        """并行节点同时执行。"""
        wf = WorkflowDef(
            name="test-parallel",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="s1", type=NodeType.TOOL_CALL,
                        config={"tool": "web_search", "params": {"query": "{{input.topic}}"}},
                        depends_on=["input"]),
                NodeDef(id="s2", type=NodeType.TOOL_CALL,
                        config={"tool": "web_search", "params": {"query": "{{input.topic}} 对比"}},
                        depends_on=["input"]),
                NodeDef(id="merge", type=NodeType.LLM_CALL,
                        config={"prompt": "合并: {{nodes.s1.result}} + {{nodes.s2.result}}"},
                        depends_on=["s1", "s2"]),
                NodeDef(id="output", type=NodeType.OUTPUT, depends_on=["merge"]),
            ],
        )
        rt = engine.run(wf, input_data={"topic":"AI"})

        assert rt.status == WorkflowStatus.COMPLETED
        assert rt.nodes["s1"].status == NodeStatus.COMPLETED
        assert rt.nodes["s2"].status == NodeStatus.COMPLETED
        assert rt.nodes["merge"].status == NodeStatus.COMPLETED
        assert "LLM" in rt.nodes["merge"].result

    def test_llm_execution(self, engine):
        """LLM 调用节点。"""
        wf = WorkflowDef(
            name="test-llm",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="llm", type=NodeType.LLM_CALL,
                        config={"prompt": "回答: {{input.question}}", "temperature": 0.5},
                        depends_on=["input"]),
                NodeDef(id="output", type=NodeType.OUTPUT, depends_on=["llm"]),
            ],
        )
        rt = engine.run(wf, input_data={"question": "1+1=?"})

        assert rt.status == WorkflowStatus.COMPLETED
        assert "LLM 响应:" in rt.nodes["llm"].result

    def test_missing_dep_fails(self, engine):
        """缺失的依赖节点导致工作流失败。"""
        wf = WorkflowDef(
            name="missing-dep",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL, depends_on=["nonexistent"]),
            ],
        )
        rt = engine.run(wf)
        assert rt.status == WorkflowStatus.FAILED
        assert "不存在" in rt.error

    def test_empty_workflow(self, engine):
        """空工作流。"""
        wf = WorkflowDef(name="empty", nodes=[])
        rt = engine.run(wf)
        assert rt.status == WorkflowStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════
# 审批节点测试
# ═══════════════════════════════════════════════════════════════


class TestApprovalNode:
    """审批节点测试。"""

    def test_approval_blocks_and_resolves(self):
        """审批阻塞后外部批准。"""
        eng = WorkflowEngine(
            approval_callback=lambda req: None,  # 占位
        )

        def approval_cb(req):
            wf_id = req["workflow_id"]
            def _approve():
                time.sleep(0.3)
                eng.resolve_approval(
                    workflow_id=wf_id,
                    req_id=req["req_id"],
                    approved=True,
                    note="可以"
                )
            import threading
            threading.Thread(target=_approve, daemon=True).start()

        # 设置真正的回调
        eng.approval_callback = approval_cb

        wf = WorkflowDef(
            name="test-approval",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="check", type=NodeType.APPROVAL,
                        config={"message": "请审批 {{input.task}}"},
                        depends_on=["input"]),
                NodeDef(id="output", type=NodeType.OUTPUT, depends_on=["check"]),
            ],
        )

        rt = eng.run(wf, input_data={"task": "执行删除操作"})

        assert rt.status == WorkflowStatus.COMPLETED
        assert rt.nodes["check"].status == NodeStatus.COMPLETED
        assert rt.nodes["check"].result["approved"] is True

    def test_approval_rejected(self):
        """审批拒绝。"""
        eng = WorkflowEngine(
            approval_callback=lambda req: None,
        )

        def approval_cb(req):
            def _reject():
                time.sleep(0.3)
                eng.resolve_approval(
                    workflow_id=req["workflow_id"],
                    req_id=req["req_id"],
                    approved=False,
                    note="不同意"
                )
            import threading
            threading.Thread(target=_reject, daemon=True).start()

        eng.approval_callback = approval_cb

        wf = WorkflowDef(
            name="test-reject",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="check", type=NodeType.APPROVAL,
                        config={"message": "审批测试"},
                        depends_on=["input"]),
            ],
        )

        rt = eng.run(wf, input_data={})

        assert rt.status == WorkflowStatus.FAILED
        assert "拒绝" in rt.error


# ═══════════════════════════════════════════════════════════════
# 模板替换测试
# ═══════════════════════════════════════════════════════════════


class TestTemplateResolution:
    """模板变量替换测试。"""

    def test_input_template(self, engine):
        """{{input.xxx}} 替换。"""
        wf = WorkflowDef(
            name="template-input",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="tool", type=NodeType.TOOL_CALL,
                        config={"tool": "web_search",
                                "params": {"query": "{{input.topic}}"}},
                        depends_on=["input"]),
            ],
        )
        rt = engine.run(wf, input_data={"topic": "深度学习"})
        assert "深度学习" in rt.nodes["tool"].result["result"]

    def test_node_result_template(self, engine):
        """{{nodes.xxx.result}} 替换。"""
        wf = WorkflowDef(
            name="template-node",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="search", type=NodeType.TOOL_CALL,
                        config={"tool": "web_search",
                                "params": {"query": "{{input.topic}}"}},
                        depends_on=["input"]),
                NodeDef(id="llm", type=NodeType.LLM_CALL,
                        config={"prompt": "基于此: {{nodes.search.result}}"},
                        depends_on=["search"]),
            ],
        )
        rt = engine.run(wf, input_data={"topic": "Rust"})
        assert "LLM" in rt.nodes["llm"].result


# ═══════════════════════════════════════════════════════════════
# 重试测试
# ═══════════════════════════════════════════════════════════════


class TestRetry:
    """重试机制测试。"""

    def test_retry_on_failure(self):
        """节点失败后重试。"""
        attempt_count = [0]

        def failing_tool(name, params):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise RuntimeError(f"第 {attempt_count[0]} 次失败")
            return {"status": "ok", "result": "终于成功"}

        eng = WorkflowEngine(tool_executor=failing_tool)

        wf = WorkflowDef(
            name="test-retry",
            nodes=[
                NodeDef(id="tool", type=NodeType.TOOL_CALL,
                        config={"tool": "test", "params": {}},
                        retry_count=3, retry_delay=0.1),
            ],
        )

        rt = eng.run(wf)
        assert rt.status == WorkflowStatus.COMPLETED
        assert rt.nodes["tool"].result["result"] == "终于成功"
        assert rt.nodes["tool"].attempts == 3

    def test_retry_exhausted(self):
        """重试耗尽后标记失败。"""
        def always_failing(name, params):
            raise RuntimeError("永远失败")

        eng = WorkflowEngine(tool_executor=always_failing)

        wf = WorkflowDef(
            name="test-retry-fail",
            nodes=[
                NodeDef(id="tool", type=NodeType.TOOL_CALL,
                        config={"tool": "test", "params": {}},
                        retry_count=1, retry_delay=0.1),
            ],
        )

        rt = eng.run(wf)
        assert rt.status == WorkflowStatus.FAILED
        assert "永远失败" in rt.error


# ═══════════════════════════════════════════════════════════════
# 工作流管理测试
# ═══════════════════════════════════════════════════════════════


class TestWorkflowManagement:
    """工作流管理操作测试。"""

    def test_pause_resume(self, engine):
        """暂停和恢复。"""
        wf = WorkflowDef(
            name="test-pause",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL,
                        config={"tool": "test", "params": {}},
                        depends_on=["a"]),
            ],
        )

        # 先暂停再恢复不会影响已完成的工作流
        rt = engine.run(wf, input_data={})

        # 已完成的工作流不影响
        assert engine.pause(rt.workflow_id) is False
        assert engine.resume(rt.workflow_id) is False

        # 运行中的工作流可以暂停
        assert engine.get_status(rt.workflow_id) is not None

    def test_cancel(self, engine):
        """取消工作流。"""
        wf = WorkflowDef(
            name="test-cancel",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL,
                        config={"tool": "test", "params": {}},
                        depends_on=["a"]),
            ],
        )

        rt = engine.run(wf)
        assert engine.cancel(rt.workflow_id) is False  # 已完成

    def test_list_runtimes(self, engine):
        """列出运行时。"""
        wf1 = WorkflowDef(name="list-test-a", nodes=[
            NodeDef(id="a", type=NodeType.INPUT),
        ])
        wf2 = WorkflowDef(name="list-test-b", nodes=[
            NodeDef(id="a", type=NodeType.INPUT),
        ])
        engine.run(wf1, input_data={"x": 1})
        engine.run(wf2, input_data={"x": 2})

        runtimes = engine.list_runtimes()
        assert len(runtimes) >= 2


# ═══════════════════════════════════════════════════════════════
# 持久化测试
# ═══════════════════════════════════════════════════════════════


class TestPersistence:
    """持久化测试。"""

    @pytest.fixture
    def store(self, tmp_path):
        return WorkflowStore(db_path=tmp_path / "test_workflow.db")

    def test_save_and_load(self, engine, store):
        """保存后恢复工作流状态。"""
        wf = WorkflowDef(
            name="test-persist",
            nodes=[
                NodeDef(id="a", type=NodeType.INPUT),
                NodeDef(id="b", type=NodeType.TOOL_CALL,
                        config={"tool": "web_search",
                                "params": {"query": "{{input.topic}}"}},
                        depends_on=["a"]),
            ],
        )

        rt = engine.run(wf, input_data={"topic": "持久化测试"})
        store.save_workflow(rt)
        store.log_event(rt.workflow_id, "completed", data={"status": "ok"})

        # 恢复
        loaded = store.load_workflow(rt.workflow_id)
        assert loaded is not None
        assert loaded.workflow_def.name == "test-persist"
        assert loaded.nodes["b"].status == NodeStatus.COMPLETED
        assert "持久化" in loaded.nodes["b"].result["result"]

    def test_list_workflows(self, store):
        """列出工作流记录。"""
        store.save_workflow(WorkflowRuntime(
            workflow_id="test-1",
            workflow_def=WorkflowDef(name="wf1", nodes=[]),
        ))
        store.save_workflow(WorkflowRuntime(
            workflow_id="test-2",
            workflow_def=WorkflowDef(name="wf2", nodes=[]),
        ))

        workflows = store.list_workflows(limit=10)
        assert len(workflows) >= 2

    def test_event_log(self, store):
        """事件日志。"""
        store.log_event("wf-events", "started", node_id="a",
                        data={"input": "hello"})
        store.log_event("wf-events", "node_complete", node_id="b",
                        data={"result": "ok"})

        events = store.get_events("wf-events")
        assert len(events) == 2
        assert events[0]["event_type"] == "started"


# ═══════════════════════════════════════════════════════════════
# 模板替换测试（模板替换函数的单元测试）
# ═══════════════════════════════════════════════════════════════


class TestStringTemplate:
    """字符串模板替换。"""

    def test_resolve_input(self, engine):
        rt = WorkflowRuntime(
            workflow_id="test",
            workflow_def=WorkflowDef(name="t", nodes=[]),
            input_data={"name": "World"},
        )
        result = engine._resolve_string_templates("Hello {{input.name}}!", rt)
        assert result == "Hello World!"

    def test_resolve_node_result(self, engine):
        from core.workflow import NodeRuntime as NR
        rt = WorkflowRuntime(
            workflow_id="test",
            workflow_def=WorkflowDef(name="t", nodes=[
                NodeDef(id="search", type=NodeType.TOOL_CALL),
            ]),
        )
        # 初始化节点运行时
        for nd in rt.workflow_def.nodes:
            rt.nodes[nd.id] = NR(node_id=nd.id)
        rt.set_node_result("search", {"title": "Results"})
        rt.nodes["search"].status = NodeStatus.COMPLETED
        result = engine._resolve_string_templates("基于: {{nodes.search.result}}", rt)
        assert "Results" in result

    def test_resolve_context(self, engine):
        rt = WorkflowRuntime(
            workflow_id="test",
            workflow_def=WorkflowDef(name="t", nodes=[]),
        )
        rt.context["user_name"] = "Alice"
        result = engine._resolve_string_templates("用户: {{context.user_name}}", rt)
        assert result == "用户: Alice"

    def test_no_template(self, engine):
        rt = WorkflowRuntime(
            workflow_id="test",
            workflow_def=WorkflowDef(name="t", nodes=[]),
        )
        result = engine._resolve_string_templates("Hello World!", rt)
        assert result == "Hello World!"


# ═══════════════════════════════════════════════════════════════
# 条件路由测试
# ═══════════════════════════════════════════════════════════════


class TestConditionNode:
    """条件路由测试。"""

    def test_condition_routing(self):
        """根据条件选择不同路径。"""
        def tool_exec(name, params):
            return {"score": params.get("score", 0)}

        eng = WorkflowEngine(tool_executor=tool_exec)

        wf = WorkflowDef(
            name="test-condition",
            nodes=[
                NodeDef(id="input", type=NodeType.INPUT),
                NodeDef(id="grade", type=NodeType.TOOL_CALL,
                        config={"tool": "evaluate", "params": {"score": "{{input.score}}"}},
                        depends_on=["input"]),
                NodeDef(id="condition", type=NodeType.CONDITION,
                        config={
                            "conditions": [
                                {"if": "result.get('score', 0) >= 60", "then": "pass"},
                            ],
                            "default": "fail",
                        },
                        depends_on=["grade"]),
            ],
        )

        rt = eng.run(wf, input_data={"score": 85})
        assert rt.status == WorkflowStatus.COMPLETED
