"""
workflow_v2_web/server.py — 工作流编辑器 Web API 服务。

启动：
  python workflow_v2_web/server.py

API:
  GET  /api/workflows      — 列出所有工作流
  GET  /api/workflows/:name — 获取工作流定义
  POST /api/workflows       — 创建/更新工作流
  POST /api/workflows/:name/run — 执行工作流
"""

from __future__ import annotations

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# 确保能找到 core/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.workflow_v2.manager import WorkflowManager
from core.workflow_v2.models import WorkflowDef, NodeDef, NodeType


manager = WorkflowManager()


class APIHandler(BaseHTTPRequestHandler):
    
    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
    
    def _error(self, msg, status=400):
        self._json({"error": msg}, status)
    
    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))
    
    def do_GET(self):
        if self.path == "/api/workflows":
            self._json(manager.list_workflows())
        elif self.path.startswith("/api/workflows/"):
            name = self.path.split("/api/workflows/")[1]
            wf = manager.get_workflow(name)
            if wf:
                self._json(wf.to_dict())
            else:
                self._error(f"工作流 '{name}' 不存在", 404)
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html_path = Path(__file__).parent / "index.html"
            self.wfile.write(html_path.read_bytes())
        else:
            self._error("Not found", 404)
    
    def do_POST(self):
        if self.path == "/api/workflows":
            data = self._read_body()
            try:
                wf = WorkflowDef.from_dict(data)
                # 覆盖保存
                path = ROOT / "workflows" / f"{wf.name}.yaml"
                wf.to_yaml(str(path))
                manager.reload()
                self._json({"status": "saved", "name": wf.name})
            except Exception as e:
                self._error(str(e))
        elif self.path.endswith("/run"):
            name = self.path.split("/api/workflows/")[1].split("/run")[0]
            try:
                rt = manager.run(name)
                self._json(rt.to_dict())
            except Exception as e:
                self._error(str(e))
        else:
            self._error("Not found", 404)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def log_message(self, format, *args):
        print(f"[API] {args[0]} {args[1]} {args[2]}")


def main():
    port = int(os.environ.get("PORT", 8899))
    server = HTTPServer(("127.0.0.1", port), APIHandler)
    print(f"🌐 夸父工作流编辑器 → http://localhost:{port}")
    print(f"   API: http://localhost:{port}/api/workflows")
    server.serve_forever()


if __name__ == "__main__":
    main()
