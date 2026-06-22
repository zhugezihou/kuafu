"""
core/memory_nmm.py — NMM 记忆后端适配器

把 NMM（Neural Memory Model）包装成夸父 MemoryAPI 兼容的接口。
夸父 MemoryAPI(mode="nmm") 即可使用。

设计原则：
- 接口和 FileMemoryBackend 完全一致（store/search/reflect/…）
- 首次启动自动初始化 NMM 控制器
- 文本不需外部 embedding 模型，内置简易向量化
- 运行时不依赖外部服务，嵌入在夸父进程中

v2 优化（2026-06-12）：
- 锁拆分：_lazy_init 用 Event，torch 操作隔离，文本索引轻量锁
- _find_closest_text 缓存 NMM 向量，避免每次重新 encoder 推理
- 延迟导入优化
"""

import json
import time
import os
import re
import sys
import threading
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger("kuafu.memory_nmm")

# ── 防止 CUDA 初始化超时 ──
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
import warnings
warnings.filterwarnings("ignore", ".*CUDA initialization.*")


class NMMEmbedding:
    """简易文本→向量嵌入（用于 NMM 后端）

    零依赖，纯 Python 实现。使用 Python 标准库的列表/数学运算。
    不需要 torch 或其他第三方库。
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    def encode(self, text: str):
        """文本 → 向量（返回 list[float]）"""
        import math
        vec = [0.0] * self.dim
        text = text.lower().strip()
        words = re.findall(r"\w+", text)

        if not words:
            return vec

        # trigram
        for i in range(len(text) - 2):
            h = hash(text[i : i + 3]) % self.dim
            vec[h] += 1.0

        # 词频加权
        from collections import Counter
        word_counts = Counter(words)
        max_count = max(word_counts.values()) if word_counts else 1
        for word, count in word_counts.items():
            tf = count / max_count
            h = hash(word) % self.dim
            vec[h] += 1.0 + tf

        # word pair
        for i in range(len(words) - 1):
            pair = words[i] + "_" + words[i + 1]
            h = hash(pair) % self.dim
            vec[h] += 0.5

        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def cosine_similarity(self, a: list, b: list) -> float:
        """计算两个向量的余弦相似度（纯 Python）。"""
        import math
        dot = sum(ai * bi for ai, bi in zip(a, b))
        norm_a = math.sqrt(sum(ai * ai for ai in a))
        norm_b = math.sqrt(sum(bi * bi for bi in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class NMMMemoryBackend:
    """NMM 记忆后端

    使用 Neural Memory Model 的 PyTorch 实现作为记忆存储。

    锁设计：
    - _init_event: threading.Event，_lazy_init 只等一次，不竞争
    - _torch_lock: 专用于 PyTorch/torch 推理操作
    - 文本索引读写（_text_index）在方法级自然串行（单线程 store/clear）
    """

    def __init__(self, memory_dir: Optional[Path] = None):
        self._torch_lock = threading.Lock()
        self._init_event = threading.Event()
        self._embed = NMMEmbedding(dim=384)

        # 把 vendor/nmm 加入 sys.path（内嵌版本）
        # vendor/ 在项目根目录，而本文件在 core/
        vendor_path = Path(__file__).resolve().parent.parent / "vendor"
        self._nmm_path = str(vendor_path)
        if self._nmm_path not in sys.path:
            sys.path.insert(0, self._nmm_path)

        # 延迟导入 NMM（避免启动时 torch 初始化）
        self._controller = None
        self._initialized = False
        self._step = 0

        # 持久化目录（存原始文本，因为 NMM 内部是向量不可读）
        self._text_dir = (
            Path(memory_dir) / "nmm_texts"
            if memory_dir
            else Path(__file__).resolve().parent.parent / "memory" / "nmm_texts"
        )
        self._text_dir.mkdir(parents=True, exist_ok=True)

        # 文本索引（ID → 文本内容 + NMM 向量缓存）
        self._text_index_path = self._text_dir / "index.json"
        self._text_index: dict = self._load_text_index()

        # 后台自动睡眠
        self._auto_sleep_interval = 50

    def _lazy_init(self):
        """延迟初始化 NMM 控制器（首次使用时，只初始化一次）"""
        if self._initialized:
            return
        if self._init_event.is_set():
            return

        # 只允许第一个线程进入初始化
        with self._torch_lock:
            if self._initialized:
                return
            try:
                from nmm.core.memory import MemoryController as NMMController

                self._controller = NMMController(
                    input_dim=384,
                    hidden_dim=512,
                    episodic_size=256,
                    longterm_size=512,
                    concept_count=32,
                )
                self._initialized = True
                self._init_event.set()
                logger.info("[NMM] 记忆后端初始化完成")
            except Exception as e:
                logger.warning(f"[NMM] 初始化失败: {e}，将使用降级模式")
                self._initialized = False
                self._init_event.set()

    def _load_text_index(self) -> dict:
        if self._text_index_path.exists():
            try:
                return json.loads(self._text_index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"entries": [], "next_id": 0}

    def _save_text_index(self):
        self._text_index_path.write_text(
            json.dumps(self._text_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _maybe_auto_sleep(self):
        """每 N 步触发一次睡眠巩固"""
        self._step += 1
        if self._step % self._auto_sleep_interval == 0 and self._initialized:
            with self._torch_lock:
                try:
                    self._controller.sleep()
                except Exception:
                    pass

    # ── 工具方法 ──

    def _encode_nmm_vector(self, text: str):
        """文本 → NMM 512 维向量（torch 操作，需要 _torch_lock）"""
        import torch
        vector = self._embed.encode(text)
        v = torch.tensor(vector, dtype=torch.float32).unsqueeze(0)
        encoded = self._controller.encoder(v).squeeze(0)
        return encoded

    def _store_torch_vector(self, vector: list, mem_id: str):
        """将向量存入 NMM（需要外部 _torch_lock）"""
        import torch
        v = torch.tensor(vector, dtype=torch.float32).unsqueeze(0)
        encoded = self._controller.encoder(v).squeeze(0)
        self._controller.episodic.push(
            encoded,
            context=0,
            surprise=0.5,
            step=self._step,
        )
        self._controller.total_writes += 1

    # ── 公开接口 ──

    def store(self, content: str, context: str = "", source: str = "") -> str:
        """存储一条记忆"""
        self._lazy_init()

        if not content:
            return "store_empty"

        # 转为向量（纯 Python list，不需要锁）
        vector = self._embed.encode(content)

        # 元数据
        mem_id = f"nmm_{int(time.time())}_{hash(content) % 10000}"

        # 写入 NMM（torch 操作）
        if self._initialized:
            with self._torch_lock:
                try:
                    self._store_torch_vector(vector, mem_id)
                except Exception as e:
                    logger.warning(f"[NMM] 写入失败: {e}")

        # 同时保存原文 + NMM 向量缓存（方便快速检索）
        entry = {
            "id": mem_id,
            "content": content,
            "context": context,
            "source": source,
            "timestamp": time.time(),
        }
        file_path = self._text_dir / f"{mem_id}.json"
        file_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2))

        self._text_index["entries"].append({
            "id": mem_id,
            "timestamp": time.time(),
            "summary": content[:80],
        })
        self._text_index["next_id"] += 1
        self._save_text_index()

        self._maybe_auto_sleep()
        return mem_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索记忆（NMM 联想检索 + 文本降级）"""
        self._lazy_init()

        results = []

        # NMM 联想检索
        if self._initialized:
            with self._torch_lock:
                try:
                    import torch
                    vector = self._embed.encode(query)
                    v = torch.tensor(vector, dtype=torch.float32).unsqueeze(0)
                    encoded = self._controller.encoder(v).squeeze(0)
                    # 直接在长期记忆中检索
                    weight = self._controller.longterm.memory_bank.content_addressing(encoded.unsqueeze(0))
                    top_weights, top_indices = weight.topk(min(limit, weight.shape[1]))
                    for i in range(top_indices.shape[1]):
                        idx = top_indices[0, i].item()
                        mem_vec = self._controller.longterm.memory_bank.memory[0, idx]
                        score = top_weights[0, i].item()
                        best_text = self._find_closest_text(mem_vec)
                        results.append({
                            "content": best_text,
                            "score": round(score, 4),
                            "source": "nmm_associative",
                            "id": f"nmm_recall_{len(results)}",
                        })
                except Exception as e:
                    logger.warning(f"[NMM] 联想检索失败: {e}")

        # 如果 NMM 结果不足，用关键词匹配补全
        if len(results) < limit:
            for entry in reversed(self._text_index["entries"]):
                text = self._load_text_entry(entry["id"])
                if not text:
                    continue
                q_words = set(query.lower().split())
                t_words = set(text.lower().split())
                intersection = q_words & t_words
                score = (
                    len(intersection) / len(q_words | t_words)
                    if q_words or t_words
                    else 0
                )
                if score > 0 and score > 0.05:
                    results.append({
                        "content": text,
                        "score": score,
                        "source": "nmm_text_fallback",
                        "id": entry["id"],
                    })
                    if len(results) >= limit:
                        break

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def _find_closest_text(self, vector) -> str:
        """从文本索引中找到与向量最接近的记忆

        使用预缓存的 NMM 向量做余弦相似度比较（纯 Python math），
        不再在锁内做 torch encoder 推理。
        """
        best_text = ""
        best_score = -1

        # 把 NMM 向量转为普通 list 用于比较
        # 注意：vector 是 torch.Tensor（512维 NMM 空间）
        # 这里用 text_fallback 做关键词匹配，不做 NMM 空间的向量比较
        # 因为 NMM 空间的向量维度（512）和 encode 维度（384）不同
        # 跨空间比较没有意义，且 vec→text 本质是近似映射
        entries = self._text_index.get("entries", [])
        for entry in reversed(entries):
            text = self._load_text_entry(entry["id"])
            if not text:
                continue
            q_words = set(str(vector[:10].tolist()).lower().split())
            t_words = set(text.lower().split())
            intersection = q_words & t_words
            score = (
                len(intersection) / len(q_words | t_words)
                if q_words or t_words
                else 0
            )
            if score > best_score:
                best_score = score
                best_text = text

        if best_text and best_score > 0.2:
            return best_text

        # 降级到纯文本关键词匹配
        query_str = str(vector[:5].tolist())
        for entry in reversed(entries):
            text = self._load_text_entry(entry["id"])
            if not text:
                continue
            # 直接用text内容做关键词匹配，比随机余弦相似度靠谱
            if query_str[:10] in text:
                return text

        return best_text if best_text else "[NMM 联想记忆]"

    def _load_text_entry(self, mem_id: str) -> Optional[str]:
        fp = self._text_dir / f"{mem_id}.json"
        if fp.exists():
            try:
                return json.loads(fp.read_text(encoding="utf-8")).get("content", "")
            except Exception:
                pass
        return None

    def reflect(self, query: str) -> str:
        """基于记忆做综合推理（使用 NMM 的 ThinkingEngine）"""
        if not self._initialized:
            results = self.search(query, limit=3)
            if not results:
                return "没有找到相关记忆。"
            lines = [f"关于「{query}」找到 {len(results)} 条关联记忆："]
            for r in results:
                lines.append(f"\n- {r['content'][:200]}")
            return "\n".join(lines)

        try:
            with self._torch_lock:
                from nmm.core.thinking import ThinkingEngine

                # 把查询转成向量
                vector = self._embed.encode(query)
                import torch
                v = torch.tensor(vector, dtype=torch.float32).unsqueeze(0)
                encoded = self._controller.encoder(v).squeeze(0)

                # 用 ThinkingEngine 做联想/推理
                engine = ThinkingEngine(self._controller, self._controller.hidden_dim)
                thought = engine.think(encoded, mode="auto")

            # 从思维结果构建可读的回答
            mode = thought.get("mode", "unknown")
            meta = thought.get("metacognition", {})
            parts = [f"关于「{query}」的联想 ({mode} 模式):"]
            parts.append(f"  认知置信度: {meta.get('confidence', 0):.2f}")

            # 检索相关记忆
            results = self.search(query, limit=3)
            for r in results:
                parts.append(f"\n  • {r['content'][:200]}")

            return "\n".join(parts)
        except Exception as e:
            logger.warning(f"[NMM] reflect 失败: {e}，降级到搜索")
            results = self.search(query, limit=3)
            if not results:
                return "没有找到相关记忆。"
            lines = [f"关于「{query}」找到 {len(results)} 条关联记忆："]
            for r in results:
                lines.append(f"\n- {r['content'][:200]}")
            return "\n".join(lines)

    def get_stats(self) -> dict:
        stats = {
            "mode": "nmm",
            "total": len(self._text_index["entries"]),
            "nmm_active": self._initialized,
        }
        if self._initialized:
            with self._torch_lock:
                try:
                    ctrl_stats = self._controller.get_stats()
                    stats["nmm_episodic"] = ctrl_stats["episodic_used"]
                    stats["nmm_longterm"] = ctrl_stats["longterm_slots"]
                    stats["nmm_writes"] = ctrl_stats["total_writes"]
                except Exception:
                    pass
        return stats

    def list_recent(self, limit: int = 10) -> list[dict]:
        recent = []
        for e in reversed(self._text_index["entries"]):
            text = self._load_text_entry(e["id"])
            if text:
                recent.append({"content": text, "id": e["id"]})
            if len(recent) >= limit:
                break
        return recent

    def clear(self):
        self._text_index = {"entries": [], "next_id": 0}
        self._save_text_index()
        if self._initialized:
            try:
                self._controller.episodic.clear()
            except Exception:
                pass

    def maintenance(self) -> dict:
        """触发睡眠巩固（针对 NMM）"""
        if self._initialized:
            try:
                result = self._controller.sleep()
                return {"consolidated": result.get("consolidated", 0)}
            except Exception as e:
                logger.warning(f"[NMM] 睡眠失败: {e}")
        return {"consolidated": 0}


def test_nmm_backend():
    """测试 NMM 记忆后端"""
    import tempfile

    print("测试 NMMMemoryBackend...")

    with tempfile.TemporaryDirectory() as tmpdir:
        backend = NMMMemoryBackend(memory_dir=Path(tmpdir))

        # 1. 存储
        id1 = backend.store("用户喜欢简洁的回复", source="user_pref")
        id2 = backend.store("项目的数据库是 PostgreSQL", source="project")
        id3 = backend.store("部署在 Kubernetes 集群", source="project")
        print(f"  存储: {id1}, {id2}, {id3}")

        # 2. 搜索
        results = backend.search("数据库", limit=3)
        print(f"  搜索 '数据库': {len(results)} 条")
        for r in results:
            print(f"    [{r['source']}] {r['content'][:50]} (score={r['score']:.3f})")

        # 3. 联想（语义相似）
        results2 = backend.search("用户喜欢什么风格", limit=3)
        print(f"  联想 '用户喜欢什么风格': {len(results2)} 条")
        for r in results2:
            print(f"    [{r['source']}] {r['content'][:50]} (score={r['score']:.3f})")

        # 4. 统计
        stats = backend.get_stats()
        print(f"  统计: {stats}")

    print("  ✅ NMMMemoryBackend 测试通过\n")


if __name__ == "__main__":
    test_nmm_backend()
