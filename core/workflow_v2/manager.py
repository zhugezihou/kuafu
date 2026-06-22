"""
core/workflow_v2/manager.py — 工作流管理器。

职责：
1. 扫描 workflows/ 目录加载 YAML
2. 执行工作流
3. 与 cron 调度器集成
4. 构建飞书卡片（后续实现）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from core.workflow_v2.models import WorkflowDef, WorkflowRuntime
from core.workflow_v2.engine import WorkflowEngine

logger = logging.getLogger("kuafu.workflow_v2.manager")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
WORKFLOWS_DIR = ROOT_DIR / "workflows"


class WorkflowManager:
    """工作流管理器——扫描、加载、执行。"""

    def __init__(self, llm_chat_fn: Optional[callable] = None):
        self.llm_chat_fn = llm_chat_fn
        self._workflows: dict[str, WorkflowDef] = {}
        self._load_all()

    def _load_all(self):
        """扫描 workflows/ 目录加载所有 YAML。"""
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
        self._workflows.clear()
        for f in sorted(WORKFLOWS_DIR.glob("*.yaml")):
            try:
                wf = WorkflowDef.from_yaml(str(f))
                self._workflows[wf.name] = wf
                logger.info(f"📋 加载工作流: {wf.name} ({f.name})")
            except Exception as e:
                logger.warning(f"⚠️ 工作流加载失败 {f.name}: {e}")

    def reload(self):
        """重新加载所有工作流。"""
        self._load_all()

    def list_workflows(self) -> list[dict]:
        """列出所有工作流摘要。"""
        return [
            {
                "name": wf.name,
                "description": wf.description,
                "trigger": wf.trigger,
                "trigger_type": wf.trigger_type,
                "node_count": len(wf.nodes),
                "inputs": wf.inputs,
            }
            for wf in self._workflows.values()
        ]

    def get_workflow(self, name: str) -> Optional[WorkflowDef]:
        return self._workflows.get(name)

    def create_workflow(self, wf_def: WorkflowDef) -> WorkflowDef:
        """创建新工作流并保存 YAML。"""
        path = WORKFLOWS_DIR / f"{wf_def.name}.yaml"
        if path.exists():
            raise FileExistsError(f"工作流 {wf_def.name} 已存在")
        wf_def.to_yaml(str(path))
        self._workflows[wf_def.name] = wf_def
        return wf_def

    def delete_workflow(self, name: str) -> bool:
        """删除工作流。"""
        wf = self._workflows.pop(name, None)
        if wf:
            path = WORKFLOWS_DIR / f"{name}.yaml"
            if path.exists():
                path.unlink()
            return True
        return False

    def run(self, name: str,
            input_data: dict[str, Any] | None = None) -> WorkflowRuntime:
        """执行指定工作流。"""
        wf = self._workflows.get(name)
        if wf is None:
            raise ValueError(f"工作流 '{name}' 不存在")
        
        engine = WorkflowEngine(llm_chat_fn=self.llm_chat_fn)
        rt = engine.run(wf, input_data=input_data or {})
        
        # 保存运行日志
        log_dir = ROOT_DIR / "memory" / "workflow_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{name}_{int(rt.started_at)}.json"
        log_path.write_text(
            json.dumps(rt.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        
        return rt

    def build_feishu_card(self, name: str) -> Optional[dict]:
        """构建工作流的飞书配置卡片。"""
        wf = self._workflows.get(name)
        if wf is None:
            return None
        
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{wf.name}**\n{wf.description}\n\n⏱ 触发: {wf.trigger or '手动'}\n📦 节点: {len(wf.nodes)} 个",
                },
            },
            {"tag": "hr"},
        ]
        
        # 参数配置区
        if wf.inputs:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**⚙️ 参数配置**"},
            })
            for inp in wf.inputs:
                key = inp.get("key", "")
                label = inp.get("label", key)
                default = inp.get("default", "")
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{label}**\n当前值: `{default}`"},
                })
        
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "▶ 立即执行"},
                    "type": "primary",
                    "value": {"action": "run_workflow", "name": name},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✏️ 编辑参数"},
                    "type": "default",
                    "value": {"action": "edit_workflow", "name": name},
                },
            ],
        })
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🔄 {wf.name}"},
                "template": "blue",
            },
            "elements": elements,
        }
