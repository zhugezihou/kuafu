"""
nmm/server.py — NMM HTTP 服务器

提供 REST API，支持文本记忆功能。
"""

import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import torch
import torch.nn.functional as F

from nmm import MemoryController
from nmm.embed import TextEmbedder


class NMMRequestHandler(BaseHTTPRequestHandler):
    """NMM HTTP 请求处理器"""

    # 静态配置（server 启动时设置）
    controller = None
    embedder = None
    sleep_interval = 100
    _step_counter = 0

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/status":
            self._handle_status()
        elif path == "/recall":
            self._handle_recall(parsed)
        elif path == "/concepts":
            self._handle_concepts()
        else:
            self._send_json(404, {"error": f"unknown path: {path}"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return

        if path == "/remember":
            self._handle_remember(data)
        elif path == "/sleep":
            self._handle_sleep()
        elif path == "/reset":
            self._handle_reset()
        else:
            self._send_json(404, {"error": f"unknown path: {path}"})

    # ── 处理器 ──

    def _handle_status(self):
        ctrl = self.controller
        stats = ctrl.get_stats()
        self._send_json(200, {
            "steps": stats["steps"],
            "memory_writes": stats["total_writes"],
            "memory_reads": stats["total_reads"],
            "episodic": {
                "used": stats["episodic_used"],
                "capacity": stats["episodic_capacity"],
            },
            "longterm": {
                "slots": stats["longterm_slots"],
                "concepts": stats["concepts"],
            },
            "server_step": self._step_counter,
        })

    def _handle_recall(self, parsed):
        params = parse_qs(parsed.query)
        query_text = params.get("q", [""])[0]
        k = int(params.get("k", ["5"])[0])

        if not query_text:
            self._send_json(400, {"error": "missing 'q' parameter"})
            return

        # 文本 → 向量 → 编码到记忆空间 → 检索
        raw_vector = self.embedder.encode(query_text)  # [384]
        encoded = self.controller.encoder(raw_vector.unsqueeze(0)).squeeze(0)  # [512]
        results = self.controller.recall_by_content(encoded, k=k)

        items = []
        for r in results:
            # 把向量解码回最相似的文本摘要（近似重构）
            decoded = self.embedder.decode(r['vector'])
            items.append({
                "score": round(r['score'], 4),
                "source": r['source'],
            })

        self._send_json(200, {
            "query": query_text,
            "results": items,
            "count": len(items),
        })

    def _handle_concepts(self):
        ctrl = self.controller
        concepts = []
        for i in range(ctrl.longterm.num_concepts):
            center = ctrl.longterm.concept_centers[i]
            label = self.embedder.decode(center)
            concepts.append({
                "index": i,
                "label": label[:100],
            })
        self._send_json(200, {"concepts": concepts})

    def _handle_remember(self, data):
        text = data.get("text", "")
        context = data.get("context", 0)

        if not text:
            self._send_json(400, {"error": "missing 'text' field"})
            return

        # 文本 → 向量 → 编码到记忆空间 → 存储
        raw_vector = self.embedder.encode(text)  # [384]
        encoded = self.controller.encoder(raw_vector.unsqueeze(0)).squeeze(0)  # [512]

        self.controller.episodic.push(
            encoded, context=context, surprise=0.5, step=self.controller.step)
        self.controller.total_writes += 1
        self._step_counter += 1

        # 自动睡眠检查
        if self._step_counter % self.sleep_interval == 0:
            threading.Thread(target=self._auto_sleep, daemon=True).start()

        self._send_json(200, {
            "memorized": True,
            "text": text[:100],
            "step": self._step_counter,
        })

    def _handle_sleep(self):
        ctrl = self.controller
        old_episodic = ctrl.episodic.size
        result = ctrl.sleep()
        self._send_json(200, {
            "consolidated": result.get("consolidated", 0),
            "episodic_before": old_episodic,
            "episodic_after": ctrl.episodic.size,
        })

    def _handle_reset(self):
        self.controller = MemoryController(
            self.controller.input_dim, self.controller.hidden_dim)
        self._send_json(200, {"reset": True})

    def _auto_sleep(self):
        """后台自动睡眠"""
        try:
            ctrl = self.controller
            ctrl.sleep()
        except Exception:
            pass

    # ── 工具 ──

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 静默日志


def start_server(host: str = "0.0.0.0", port: int = 8765,
                 input_dim: int = 384, hidden_dim: int = 512,
                 episodic_size: int = 256, longterm_size: int = 512,
                 concept_count: int = 32,
                 embed_model: str = "all-MiniLM-L6-v2"):
    """启动 NMM 记忆服务器

    Args:
        host: 监听地址
        port: 端口
        input_dim: 输入向量维度（和 embedding 模型匹配）
        hidden_dim: 记忆空间维度
        embed_model: embedding 模型名
    """
    print(f"[NMM] 初始化记忆系统...")

    # 创建记忆控制器
    controller = MemoryController(
        input_dim=input_dim,
        hidden_dim=hidden_dim,
        episodic_size=episodic_size,
        longterm_size=longterm_size,
        concept_count=concept_count,
    )
    NMMRequestHandler.controller = controller

    # 创建文本嵌入器
    print(f"[NMM] 加载 embedding 模型: {embed_model}...")
    embedder = TextEmbedder(model_name=embed_model)
    NMMRequestHandler.embedder = embedder

    # 启动 HTTP 服务
    server = HTTPServer((host, port), NMMRequestHandler)

    print(f"[NMM] 记忆服务器已启动")
    print(f"      REST API: http://{host}:{port}")
    print(f"      情景容量: {episodic_size}")
    print(f"      长期容量: {longterm_size}")
    print(f"      概念数量: {concept_count}")
    print(f"      自动睡眠: 每 {NMMRequestHandler.sleep_interval} 步")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[NMM] 服务器关闭")
        server.shutdown()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NMM 记忆服务器")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--embed-model", default="all-MiniLM-L6-v2",
                        help="sentence-transformers 模型名")
    parser.add_argument("--episodic", type=int, default=256)
    parser.add_argument("--longterm", type=int, default=512)
    parser.add_argument("--concepts", type=int, default=32)
    args = parser.parse_args()

    start_server(
        host=args.host,
        port=args.port,
        input_dim=384,
        hidden_dim=512,
        episodic_size=args.episodic,
        longterm_size=args.longterm,
        concept_count=args.concepts,
        embed_model=args.embed_model,
    )


if __name__ == "__main__":
    main()
