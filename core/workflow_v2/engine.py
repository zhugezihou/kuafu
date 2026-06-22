"""
core/workflow_v2/engine.py — 工作流执行引擎。

核心功能：
1. 拓扑排序 → 分层批次并行执行
2. 模板变量替换 {{nodes.x.output}} / {{input.x}}
3. 节点类型分发：terminal / http / llm / condition
4. 错误处理：重试、超时、失败传播
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
from collections import deque
from typing import Any, Optional

from core.workflow_v2.models import (
    NodeDef,
    NodeRuntime,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRuntime,
)

logger = logging.getLogger("kuafu.workflow_v2")


class WorkflowEngine:
    """工作流执行引擎。"""

    def __init__(self,
                 llm_chat_fn: Optional[callable] = None,
                 on_node_output: Optional[callable] = None):
        self.llm_chat_fn = llm_chat_fn
        self.on_node_output = on_node_output

    def run(self, wf_def: WorkflowDef,
            input_data: dict[str, Any] | None = None) -> WorkflowRuntime:
        """执行一个工作流。"""
        rt = WorkflowRuntime(workflow_name=wf_def.name)
        input_data = input_data or {}
        
        # 初始化节点运行时
        for node in wf_def.nodes:
            rt.nodes[node.id] = NodeRuntime(node_id=node.id)
        
        rt.status = "running"
        rt.started_at = time.time()
        
        # 拓扑排序
        try:
            batches = self._topological_sort(wf_def)
        except ValueError as e:
            rt.status = "failed"
            rt.error = str(e)
            rt.completed_at = time.time()
            return rt
        
        # 模板变量上下文
        context: dict[str, Any] = dict(input_data)
        
        # 逐批执行
        for batch in batches:
            if rt.status != "running":
                break
            
            threads = []
            results: dict[str, Any] = {}
            
            def _exec_node(node_id: str):
                try:
                    result = self._execute_node(wf_def, node_id, context, rt)
                    results[node_id] = result
                except Exception as e:
                    results[node_id] = {"_error": str(e)}
            
            for node_id in batch:
                node_def = self._get_node(wf_def, node_id)
                if node_def is None:
                    continue
                # 检查依赖——看 nodes.{dep}.output 是否在 context 中
                deps_met = all(f"nodes.{d}.output" in context for d in node_def.depends_on)
                if not deps_met:
                    rt.nodes[node_id].status = NodeStatus.SKIPPED
                    continue
                
                t = threading.Thread(target=_exec_node, args=(node_id,), daemon=True)
                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()
            
            # 写回结果
            for node_id, result in results.items():
                nr = rt.nodes[node_id]
                if "_error" in result:
                    nr.status = NodeStatus.FAILED
                    nr.error = result["_error"]
                    rt.error = result["_error"]
                else:
                    nr.status = NodeStatus.COMPLETED
                    stdout = result.get("stdout", "")
                    if not stdout:
                        stdout = str(result)[:500]
                    nr.output = stdout
                    context[f"nodes.{node_id}.output"] = stdout
                
                if self.on_node_output:
                    self.on_node_output(wf_def.name, node_id, nr.status.value, nr.output)
            
            # 有失败节点则停止
            if any(rt.nodes[nid].status == NodeStatus.FAILED for nid in batch):
                rt.status = "failed"
                break
        
        if rt.status == "running":
            rt.status = "completed"
        rt.completed_at = time.time()
        return rt

    def _topological_sort(self, wf_def: WorkflowDef) -> list[list[str]]:
        """Kahn 算法拓扑排序，返回分层批次。"""
        in_degree = {n.id: 0 for n in wf_def.nodes}
        adj = {n.id: [] for n in wf_def.nodes}
        
        for n in wf_def.nodes:
            for dep in n.depends_on:
                if dep not in adj:
                    raise ValueError(f"依赖节点 '{dep}' 不存在")
                adj[dep].append(n.id)
                in_degree[n.id] = in_degree.get(n.id, 0) + 1
        
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        result: list[list[str]] = []
        visited = 0
        
        while queue:
            batch = []
            for _ in range(len(queue)):
                nid = queue.popleft()
                batch.append(nid)
                visited += 1
            result.append(batch)
            for nid in batch:
                for succ in adj.get(nid, []):
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        queue.append(succ)
        
        if visited != len(wf_def.nodes):
            raise ValueError(f"循环依赖: {visited}/{len(wf_def.nodes)} 节点已排序")
        return result

    def _get_node(self, wf_def: WorkflowDef, node_id: str) -> Optional[NodeDef]:
        for n in wf_def.nodes:
            if n.id == node_id:
                return n
        return None

    def _execute_node(self, wf_def: WorkflowDef, node_id: str,
                      context: dict, rt: WorkflowRuntime) -> dict:
        """执行单个节点。"""
        node_def = self._get_node(wf_def, node_id)
        if node_def is None:
            raise ValueError(f"节点 '{node_id}' 不存在")
        
        nr = rt.nodes[node_id]
        nr.status = NodeStatus.RUNNING
        nr.started_at = time.time()
        nr.attempts += 1
        
        for attempt in range(node_def.retry_count + 1):
            try:
                result = self._dispatch(node_def, context)
                nr.completed_at = time.time()
                return result
            except Exception as e:
                if attempt < node_def.retry_count:
                    logger.warning(f"节点 {node_id} 第 {attempt+1} 次失败: {e}，重试中...")
                    time.sleep(node_def.retry_delay)
                else:
                    nr.error = str(e)
                    nr.completed_at = time.time()
                    raise
        
        return {"stdout": ""}  # unreachable

    def _dispatch(self, node_def: NodeDef, context: dict) -> dict:
        """按类型分发。"""
        if node_def.type == NodeType.TERMINAL:
            return self._exec_terminal(node_def, context)
        elif node_def.type == NodeType.HTTP:
            return self._exec_http(node_def, context)
        elif node_def.type == NodeType.LLM:
            return self._exec_llm(node_def, context)
        elif node_def.type == NodeType.CONDITION:
            return self._exec_condition(node_def, context)
        elif node_def.type == NodeType.SUBFLOW:
            return self._exec_subflow(node_def, context)
        else:
            raise ValueError(f"不支持的节点类型: {node_def.type}")

    def _resolve(self, text: str, context: dict) -> str:
        """替换模板变量 {{nodes.x.output}} 和 {{input.x}}。"""
        def _repl(m):
            path = m.group(1).strip()
            if path.startswith("nodes."):
                key = path.split(".", 1)[1]
                val = context.get(f"nodes.{key}", "")
                return str(val)
            if path.startswith("input."):
                key = path.split(".", 1)[1]
                return str(context.get(key, ""))
            return m.group(0)
        return re.sub(r"\{\{(.+?)\}\}", _repl, text)

    def _exec_terminal(self, node: NodeDef, context: dict) -> dict:
        cmd = self._resolve(node.command, context)
        logger.info(f"▶ 执行终端命令: {cmd[:100]}")
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=node.timeout,
        )
        stdout = r.stdout.strip() if r.stdout else ""
        stderr = r.stderr.strip() if r.stderr else ""
        if r.returncode != 0:
            raise RuntimeError(f"命令失败 (exit={r.returncode}): {stderr or stdout[:200]}")
        return {"stdout": stdout, "stderr": stderr, "exit_code": r.returncode}

    def _exec_http(self, node: NodeDef, context: dict) -> dict:
        url = self._resolve(node.url, context)
        body = self._resolve(node.body, context) if node.body else None
        logger.info(f"▶ HTTP {node.method} {url[:100]}")
        
        data = body.encode() if body else None
        req = urllib.request.Request(
            url, data=data, method=node.method,
            headers={k: self._resolve(v, context) for k, v in node.headers.items()},
        )
        try:
            with urllib.request.urlopen(req, timeout=node.timeout) as resp:
                content = resp.read().decode()
                return {"stdout": content[:2000], "status_code": resp.status}
        except urllib.error.HTTPError as e:
            return {"stdout": f"HTTP {e.code}: {e.reason}", "status_code": e.code}

    def _exec_llm(self, node: NodeDef, context: dict) -> dict:
        if not self.llm_chat_fn:
            raise RuntimeError("LLM 回调未设置")
        prompt = self._resolve(node.prompt, context)
        logger.info(f"▶ LLM 调用: {prompt[:80]}...")
        resp = self.llm_chat_fn([
            {"role": "user", "content": prompt},
        ])
        content = ""
        if isinstance(resp, dict):
            content = resp.get("content", "")
        elif isinstance(resp, str):
            content = resp
        return {"stdout": content}

    def _exec_condition(self, node: NodeDef, context: dict) -> dict:
        """条件判断——检查上个节点的 output 是否为空。"""
        # 简单条件：检查依赖节点输出是否非空
        prev_output = ""
        for dep in node.depends_on:
            prev_output = context.get(f"nodes.{dep}.output", "")
        
        is_true = bool(prev_output and prev_output.strip())
        route = node.if_true if is_true else node.if_false
        logger.info(f"▶ 条件判断: {'真' if is_true else '假'} → {route}")
        return {"stdout": route, "condition_result": is_true}

    def _exec_subflow(self, node: NodeDef, context: dict) -> dict:
        if not node.subflow_ref:
            raise ValueError("子工作流未指定路径")
        sub_wf = WorkflowDef.from_yaml(node.subflow_ref)
        sub_rt = self.run(sub_wf, input_data=dict(context))
        if sub_rt.status == "failed":
            raise RuntimeError(f"子工作流失败: {sub_rt.error}")
        # 把子工作流的输出合并过来
        for nid, nr in sub_rt.nodes.items():
            if nr.output:
                context[f"nodes.{node.id}.{nid}"] = nr.output
        return {"stdout": sub_rt.status, "sub_status": sub_rt.status}
