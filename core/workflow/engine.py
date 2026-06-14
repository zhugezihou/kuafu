"""
core/workflow/engine.py — 夸父 DAG 工作流执行引擎

核心职责：
  1. 拓扑排序：解析节点依赖关系，确定执行顺序
  2. 并行调度：无依赖的节点并行执行
  3. 状态管理：推进节点生命周期（pending→running→completed/failed）
  4. 条件路由：根据节点结果或共享状态选择分支
  5. 错误处理：重试、超时、失败传播

设计参考:
  - LangGraph 的 StateGraph: 共享状态 + 节点函数 + 条件边
  - Apache Airflow 的拓扑排序: DAG 的调度基础
  - LlamaIndex Workflows 的事件驱动: 节点解耦

零外部依赖。通过 ToolRegistry 和 ApprovalManager 与夸父现有模块集成。
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from typing import Any, Callable, Optional

from core.workflow.models import (
    NodeDef,
    NodeRuntime,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRuntime,
    WorkflowStatus,
)


class WorkflowEngine:
    """DAG 工作流执行引擎。

    用法:
        engine = WorkflowEngine()
        rt = engine.run(workflow_def, input_data={"topic": "Rust AI"})
        print(rt.status)  # WorkflowStatus.COMPLETED
    """

    def __init__(
        self,
        tool_executor: Optional[Callable] = None,       # 工具执行函数
        llm_executor: Optional[Callable] = None,         # LLM 调用函数
        approval_callback: Optional[Callable] = None,    # 审批回调
        code_executor: Optional[Callable] = None,        # 代码执行函数
        on_node_complete: Optional[Callable] = None,     # 节点完成回调
        on_workflow_complete: Optional[Callable] = None, # 工作流完成回调
    ):
        self.tool_executor = tool_executor
        self.llm_executor = llm_executor
        self.approval_callback = approval_callback
        self.code_executor = code_executor
        self.on_node_complete = on_node_complete
        self.on_workflow_complete = on_workflow_complete

        # 运行时注册表（支持查询工作流状态）
        self._runtimes: dict[str, WorkflowRuntime] = {}
        self._lock = threading.Lock()

    # ═══════════════════════════════════════════════════════════
    # 核心执行
    # ═══════════════════════════════════════════════════════════

    def run(
        self,
        workflow_def: WorkflowDef,
        input_data: dict[str, Any] | None = None,
        workflow_id: str | None = None,
    ) -> WorkflowRuntime:
        """执行一个工作流。

        Args:
            workflow_def: 工作流定义
            input_data: 输入参数
            workflow_id: 可选的自定义 ID

        Returns:
            WorkflowRuntime: 运行时（包含最终状态和结果）
        """
        rt = WorkflowRuntime(
            workflow_id=workflow_id or f"wf_{int(time.time() * 1000)}_{id(workflow_def)}",
            workflow_def=workflow_def,
            input_data=input_data or {},
        )

        # 初始化节点运行时
        for node_def in workflow_def.nodes:
            rt.nodes[node_def.id] = NodeRuntime(node_id=node_def.id)

        with self._lock:
            self._runtimes[rt.workflow_id] = rt

        rt.status = WorkflowStatus.RUNNING
        rt.started_at = time.time()

        # 拓扑排序
        try:
            exec_order = self._topological_sort(workflow_def)
        except ValueError as e:
            rt.status = WorkflowStatus.FAILED
            rt.error = str(e)
            rt.completed_at = time.time()
            with self._lock:
                self._runtimes[rt.workflow_id] = rt
            return rt

        # 逐轮执行（每轮执行可并行的节点）
        completed_nodes: set[str] = set()

        for batch in exec_order:
            # 检查是否需要暂停/取消
            if rt.status in (WorkflowStatus.PAUSED, WorkflowStatus.CANCELLED):
                break

            # 并行执行本轮节点
            threads = []
            results: dict[str, Any] = {}

            def _run_and_capture(node_id: str):
                try:
                    result = self._execute_node(rt, node_id, completed_nodes)
                    results[node_id] = result
                except Exception as e:
                    results[node_id] = {"_error": str(e), "_traceback": traceback.format_exc()}

            for node_id in batch:
                node_def = workflow_def.get_node(node_id)
                if node_def is None:
                    continue
                # 检查依赖是否满足
                deps_met = all(d in completed_nodes for d in node_def.depends_on)
                if not deps_met:
                    rt.nodes[node_id].status = NodeStatus.SKIPPED
                    continue

                t = threading.Thread(target=_run_and_capture, args=(node_id,), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            # 处理结果
            for node_id, result in results.items():
                nr = rt.nodes[node_id]
                if "_error" in result:
                    nr.status = NodeStatus.FAILED
                    nr.error = result["_error"]
                    rt.error = result["_error"]
                else:
                    nr.status = NodeStatus.COMPLETED
                    nr.result = result
                    rt.set_node_result(node_id, result)
                    completed_nodes.add(node_id)

                if self.on_node_complete:
                    self.on_node_complete(rt.workflow_id, node_id, nr.status.value)

            # 检查是否有失败节点
            has_failure = any(
                rt.nodes[nid].status == NodeStatus.FAILED for nid in batch
            )
            if has_failure:
                # 是否继续取决于工作流配置，默认失败即停止
                rt.status = WorkflowStatus.FAILED
                break

        # 完成
        if rt.status == WorkflowStatus.RUNNING:
            rt.status = WorkflowStatus.COMPLETED
        rt.completed_at = time.time()

        if self.on_workflow_complete:
            self.on_workflow_complete(rt.workflow_id, rt.status.value)

        return rt

    # ═══════════════════════════════════════════════════════════
    # 拓扑排序
    # ═══════════════════════════════════════════════════════════

    def _topological_sort(self, wf_def: WorkflowDef) -> list[list[str]]:
        """拓扑排序——返回分层批次。

        每批内的节点可并行执行。
        使用 Kahn 算法。

        Returns:
            [[层0节点], [层1节点], ...]  每层内并行

        Raises:
            ValueError: 检测到循环依赖
        """
        # 构建入度表和邻接表
        in_degree: dict[str, int] = {}
        adjacency: dict[str, list[str]] = {}

        for node in wf_def.nodes:
            in_degree[node.id] = 0
            adjacency[node.id] = []

        for node in wf_def.nodes:
            for dep in node.depends_on:
                if dep not in adjacency:
                    raise ValueError(f"依赖节点 '{dep}' 不存在")
                adjacency[dep].append(node.id)
                in_degree[node.id] = in_degree.get(node.id, 0) + 1

        # Kahn 算法
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        result: list[list[str]] = []
        visited_count = 0

        while queue:
            batch = []
            for _ in range(len(queue)):
                nid = queue.popleft()
                batch.append(nid)
                visited_count += 1

            result.append(batch)

            # 减少后驱节点的入度
            for nid in batch:
                for successor in adjacency.get(nid, []):
                    in_degree[successor] -= 1
                    if in_degree[successor] == 0:
                        queue.append(successor)

        if visited_count != len(wf_def.nodes):
            raise ValueError(
                f"检测到循环依赖: {visited_count}/{len(wf_def.nodes)} 个节点已排序"
            )

        return result

    # ═══════════════════════════════════════════════════════════
    # 节点执行
    # ═══════════════════════════════════════════════════════════

    def _execute_node(
        self,
        rt: WorkflowRuntime,
        node_id: str,
        completed_nodes: set[str],
    ) -> Any:
        """执行单个节点。"""
        node_def = rt.workflow_def.get_node(node_id)
        if node_def is None:
            raise ValueError(f"节点 '{node_id}' 未找到")

        nr = rt.nodes[node_id]
        nr.status = NodeStatus.RUNNING
        nr.started_at = time.time()
        nr.attempts += 1

        try:
            # 根据节点类型执行
            result = self._dispatch_node(rt, node_def, completed_nodes)

            # 条件路由检查
            if node_def.on_success:
                # 可在此处理成功后的显式路由
                pass

            nr.status = NodeStatus.COMPLETED
            nr.completed_at = time.time()
            return result

        except Exception as e:
            nr.error = str(e)
            nr.completed_at = time.time()

            if nr.attempts <= node_def.retry_count:
                time.sleep(node_def.retry_delay)
                return self._execute_node(rt, node_id, completed_nodes)

            nr.status = NodeStatus.FAILED
            raise

    def _dispatch_node(
        self,
        rt: WorkflowRuntime,
        node_def: NodeDef,
        completed_nodes: set[str],
    ) -> Any:
        """根据节点类型分派执行。"""
        config = node_def.config

        if node_def.type == NodeType.INPUT:
            return rt.input_data

        elif node_def.type == NodeType.OUTPUT:
            return rt.context

        elif node_def.type == NodeType.TOOL_CALL:
            return self._execute_tool(node_def, rt)

        elif node_def.type == NodeType.LLM_CALL:
            return self._execute_llm(node_def, rt)

        elif node_def.type == NodeType.APPROVAL:
            return self._execute_approval(node_def, rt)

        elif node_def.type == NodeType.CODE:
            return self._execute_code(node_def, rt)

        elif node_def.type == NodeType.CONDITION:
            return self._execute_condition(node_def, rt)

        elif node_def.type == NodeType.SUB_WORKFLOW:
            return self._execute_sub_workflow(node_def, rt)

        else:
            raise ValueError(f"不支持的节点类型: {node_def.type}")

    # ═══════════════════════════════════════════════════════════
    # 节点类型处理
    # ═══════════════════════════════════════════════════════════

    def _execute_tool(self, node_def: NodeDef, rt: WorkflowRuntime) -> Any:
        """执行工具调用节点。"""
        tool_name = node_def.config.get("tool", "")
        params = dict(node_def.config.get("params", {}))

        # 模板替换：{{nodes.xxx.result}} 或 {{input.yyy}}
        params = self._resolve_templates(params, rt)

        if not self.tool_executor:
            raise RuntimeError("ToolExecutor 未设置")

        result = self.tool_executor(tool_name, params)
        return result

    def _execute_llm(self, node_def: NodeDef, rt: WorkflowRuntime) -> Any:
        """执行 LLM 调用节点。"""
        prompt = node_def.config.get("prompt", "")
        prompt = self._resolve_string_templates(prompt, rt)

        model = node_def.config.get("model", "")
        temperature = node_def.config.get("temperature", 0.7)

        if not self.llm_executor:
            raise RuntimeError("LLMExecutor 未设置")

        messages = [{"role": "user", "content": prompt}]
        result = self.llm_executor(messages, model=model, temperature=temperature)
        return result

    def _execute_approval(self, node_def: NodeDef, rt: WorkflowRuntime) -> Any:
        """执行审批节点——阻塞等待人工审批。"""
        message = node_def.config.get("message", "请审批")
        message = self._resolve_string_templates(message, rt)

        req_id = f"wf_{rt.workflow_id}_node_{node_def.id}"

        approval_req = {
            "req_id": req_id,
            "workflow_id": rt.workflow_id,
            "node_id": node_def.id,
            "message": message,
            "config": node_def.config,
        }
        rt.pending_approvals.append(approval_req)

        # 标记节点为阻塞
        rt.nodes[node_def.id].status = NodeStatus.BLOCKED

        if self.approval_callback:
            self.approval_callback(approval_req)

        # 轮询等待审批结果
        max_wait = node_def.max_wait or 86400  # 默认 24h
        poll_interval = 2.0
        waited = 0.0

        while waited < max_wait:
            # 检查审批结果（外部调用 approve/reject 会修改 pending_approvals）
            for i, ap in enumerate(rt.pending_approvals):
                if ap.get("req_id") == req_id and ap.get("resolved", False):
                    rt.pending_approvals.pop(i)
                    approved = ap.get("approved", False)
                    if approved:
                        return {"approved": True, "note": ap.get("note", "")}
                    else:
                        raise RuntimeError(f"审批被拒绝: {ap.get('note', '无理由')}")

            time.sleep(poll_interval)
            waited += poll_interval

        raise TimeoutError(f"审批超时（等待 {max_wait}s）")

    def _execute_code(self, node_def: NodeDef, rt: WorkflowRuntime) -> Any:
        """执行代码节点。"""
        source = node_def.config.get("source", "")
        if self.code_executor:
            return self.code_executor(source, rt.context)
        # 默认用 exec
        namespace = {"ctx": rt.context, "result": None}
        exec(source, namespace)
        return namespace.get("result", None)

    def _execute_condition(self, node_def: NodeDef, rt: WorkflowRuntime) -> Any:
        """执行条件路由节点。"""
        conditions = node_def.config.get("conditions", [])
        default = node_def.config.get("default", "")

        for cond in conditions:
            condition_expr = cond.get("if", "")
            # 简单变量替换后 eval
            expr = self._resolve_string_templates(condition_expr, rt)
            try:
                if eval(expr, {"__builtins__": {}}, dict(rt.context)):
                    return {"route": cond.get("then", "")}
            except:
                continue

        return {"route": default}

    def _execute_sub_workflow(self, node_def: NodeDef, rt: WorkflowRuntime) -> Any:
        """执行子工作流。"""
        # 子工作流需预注册或从路径加载
        # 简化：子工作流作为新的 WorkflowDef 嵌入
        raise NotImplementedError("子工作流暂未实现")

    # ═══════════════════════════════════════════════════════════
    # 模板替换
    # ═══════════════════════════════════════════════════════════

    def _resolve_templates(self, params: dict, rt: WorkflowRuntime) -> dict:
        """递归解析模板变量。"""
        result = {}
        for k, v in params.items():
            if isinstance(v, str):
                result[k] = self._resolve_string_templates(v, rt)
            elif isinstance(v, dict):
                result[k] = self._resolve_templates(v, rt)
            elif isinstance(v, list):
                result[k] = [self._resolve_string_templates(i, rt) if isinstance(i, str) else i for i in v]
            else:
                result[k] = v
        return result

    def _resolve_string_templates(self, text: str, rt: WorkflowRuntime) -> str:
        """替换 {{nodes.x.result}} 和 {{input.x}} 模板。"""
        import re

        def _replace(match):
            path = match.group(1).strip()
            # nodes.{id}.result
            if path.startswith("nodes."):
                parts = path.split(".")
                if len(parts) >= 3 and parts[2] == "result":
                    node_result = rt.get_node_result(parts[1])
                    if node_result is not None:
                        if isinstance(node_result, (dict, list)):
                            return json.dumps(node_result, ensure_ascii=False)
                        if isinstance(node_result, str):
                            return node_result
                        return str(node_result)
            # input.xxx
            if path.startswith("input."):
                key = path.split(".", 1)[1]
                val = rt.input_data.get(key, "")
                if isinstance(val, (dict, list)):
                    return json.dumps(val, ensure_ascii=False)
                return str(val)
            # context.xxx
            if path.startswith("context."):
                key = path.split(".", 1)[1]
                val = rt.context.get(key, "")
                if isinstance(val, (dict, list)):
                    return json.dumps(val, ensure_ascii=False)
                return str(val)
            return match.group(0)

        return re.sub(r"\{\{(.+?)\}\}", _replace, text)

    # ═══════════════════════════════════════════════════════════
    # 外部操作
    # ═══════════════════════════════════════════════════════════

    def resolve_approval(self, workflow_id: str, req_id: str, approved: bool, note: str = ""):
        """外部操作：审批通过/拒绝。"""
        rt = self._runtimes.get(workflow_id)
        if not rt:
            return False

        for ap in rt.pending_approvals:
            if ap.get("req_id") == req_id:
                ap["resolved"] = True
                ap["approved"] = approved
                ap["note"] = note
                return True
        return False

    def pause(self, workflow_id: str) -> bool:
        """暂停工作流。"""
        rt = self._runtimes.get(workflow_id)
        if rt and rt.status == WorkflowStatus.RUNNING:
            rt.status = WorkflowStatus.PAUSED
            return True
        return False

    def resume(self, workflow_id: str) -> bool:
        """恢复暂停的工作流。"""
        rt = self._runtimes.get(workflow_id)
        if rt and rt.status == WorkflowStatus.PAUSED:
            rt.status = WorkflowStatus.RUNNING
            return True
        return False

    def cancel(self, workflow_id: str) -> bool:
        """取消工作流。"""
        rt = self._runtimes.get(workflow_id)
        if rt and rt.status in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED):
            rt.status = WorkflowStatus.CANCELLED
            return True
        return False

    def get_status(self, workflow_id: str) -> Optional[dict]:
        """查询工作流状态。"""
        rt = self._runtimes.get(workflow_id)
        return rt.to_dict() if rt else None

    def list_runtimes(self) -> list[dict]:
        """列出所有运行时。"""
        return [rt.to_dict() for rt in self._runtimes.values()]
