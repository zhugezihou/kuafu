"""
core/memory/nmm_engine.py — NMM 记忆引擎桥接

将 NMM（Neural Memory Model）封装为 MemoryManager 的语义增强引擎。
不是平替现有记忆系统，而是作为"潜意识联想层"：

- store(): 文本 → NMMEmbedding → NMM Controller（惊喜度判断是否写入）
- search(): 查询 FTS5 结果基础上，NMM 做语义联想扩展
- sleep(): 后台巩固情景记忆到长期记忆

v2 (2026-06-12):
  依赖 MemoryController.store_text() 和 recall_by_content(text_id) 接口。
  不再需要 _find_closest_text 跨空间线性扫描。
"""

import logging

logger = logging.getLogger("kuafu.nmm_engine")


class NMMEmbedding:
    """简易文本→向量嵌入（纯 Python trigram hash, 零依赖）。
    
    不需要 torch 或其他第三方库。
    """

    def __init__(self, dim: int = 384):
        self.dim = dim
        import re
        self._word_re = re.compile(r"\w+")

    def encode(self, text: str):
        """文本 → 向量（返回 list[float]）"""
        import math
        vec = [0.0] * self.dim
        text = text.lower().strip()
        words = self._word_re.findall(text)

        if not words:
            return vec

        for i in range(len(text) - 2):
            h = hash(text[i: i + 3]) % self.dim
            vec[h] += 1.0

        from collections import Counter
        word_counts = Counter(words)
        max_count = max(word_counts.values()) if word_counts else 1
        for word, count in word_counts.items():
            tf = count / max_count
            h = hash(word) % self.dim
            vec[h] += 1.0 + tf

        for i in range(len(words) - 1):
            pair = words[i] + "_" + words[i + 1]
            h = hash(pair) % self.dim
            vec[h] += 0.5

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def cosine_similarity(self, a: list, b: list) -> float:
        import math
        dot = sum(ai * bi for ai, bi in zip(a, b))
        norm_a = math.sqrt(sum(ai * ai for ai in a))
        norm_b = math.sqrt(sum(bi * bi for bi in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class NMMEngine:
    """NMM 记忆引擎桥接。

    封装 MemoryController 为 MemoryManager 可用的接口。
    延迟初始化 torch 和 NMM，避免导入时启动时间开销。
    """

    def __init__(self, input_dim: int = 384, hidden_dim: int = 512,
                 episodic_size: int = 256, longterm_size: int = 512):
        self._input_dim = input_dim
        self._hidden_dim = hidden_dim
        self._episodic_size = episodic_size
        self._longterm_size = longterm_size
        self._controller = None
        self._embed = None
        self._embed_dim = 384  # NMMEmbedding 维度
        self._initialized = False

    def _lazy_init(self):
        """延迟初始化（首次使用时才加载 torch）"""
        if self._initialized:
            return
        try:
            import sys
            from pathlib import Path
            # 把 vendor/nmm 加入 sys.path
            vendor_path = Path(__file__).resolve().parent.parent.parent / "vendor"
            if str(vendor_path) not in sys.path:
                sys.path.insert(0, str(vendor_path))

            from nmm.core.memory import MemoryController as NMMController

            self._embed = NMMEmbedding(dim=self._embed_dim)
            self._controller = NMMController(
                input_dim=self._input_dim,
                hidden_dim=self._hidden_dim,
                episodic_size=self._episodic_size,
                longterm_size=self._longterm_size,
                concept_count=32,
            )
            self._initialized = True
            logger.info("[NMMEngine] 初始化完成")
        except Exception as e:
            logger.warning(f"[NMMEngine] 初始化失败: {e}")

    @property
    def is_active(self) -> bool:
        return self._initialized

    # ── 对外接口 ──

    def store(self, text: str, text_id: str = '',
              force_write: bool = False) -> dict:
        """存储一段文本到 NMM。

        Args:
            text: 原始文本内容
            text_id: 文本唯一 ID（对应 SQLite 的 mem_id）
            force_write: 是否强制写入（跳过惊喜度过滤）

        Returns:
            {'stored': bool, 'surprise': float, 'episodic_size': int}
        """
        self._lazy_init()
        if not self._initialized or not text.strip():
            return {'stored': False, 'surprise': 0.0, 'episodic_size': 0}

        vector = self._embed.encode(text)  # 384维 list[float]
        import torch
        v = torch.tensor(vector, dtype=torch.float32)  # [384]
        # 通过 encoder 映射到记忆空间（hidden_dim=512）
        encoded = self._controller.encoder(v.unsqueeze(0)).squeeze(0)  # [512]

        return self._controller.store_text(encoded, text_id=text_id,
                                            force_write=force_write)

    def search(self, query: str, k: int = 5) -> list[dict]:
        """基于 NMM 做语义联想检索。

        Args:
            query: 查询文本
            k: 返回条数

        Returns:
            [{'text_id': str, 'score': float, 'source': str, ...}]
        """
        self._lazy_init()
        if not self._initialized or not query.strip():
            return []

        vector = self._embed.encode(query)
        import torch
        v = torch.tensor(vector, dtype=torch.float32)
        # 通过 encoder 映射到记忆空间
        encoded = self._controller.encoder(v.unsqueeze(0)).squeeze(0)  # [512]

        raw_results = self._controller.recall_by_content(encoded, k=k)
        results = []
        for r in raw_results:
            results.append({
                'text_id': r.get('text_id', ''),
                'score': r['score'],
                'source': f"nmm_{r['source']}",
                'slot': r.get('slot', -1),
                'vector_ptr': r['vector'].data_ptr() if 'vector' in r else 0,
            })
        return results

    def reflect_sync(self, query: str) -> dict:
        """使用 NMM ThinkingEngine 做联想推理。

        不依赖 LLM，纯 NMM 内部机制（关联/推理/创造/元认知）。

        Args:
            query: 查询文本

        Returns:
            {'mode': str, 'confidence': float, 'associations': list, ...}
        """
        self._lazy_init()
        result = {
            'mode': 'unavailable', 'confidence': 0.0,
            'associations': [], 'knowledge': {},
        }
        if not self._initialized or not query.strip():
            return result

        try:
            from nmm.core.thinking import ThinkingEngine

            vector = self._embed.encode(query)
            import torch
            v = torch.tensor(vector, dtype=torch.float32)
            encoded = self._controller.encoder(v.unsqueeze(0)).squeeze(0)

            engine = ThinkingEngine(self._controller, self._controller.hidden_dim)
            thought = engine.think(encoded, mode="auto")

            result['mode'] = thought.get('mode', 'unknown')
            result['confidence'] = thought.get('metacognition', {}).get('confidence', 0.0)
            result['knowledge'] = thought.get('metacognition', {})
            result['associations'] = thought.get('associations', [])
            result['raw'] = thought
        except Exception as e:
            logger.debug(f"[NMMEngine] reflect_sync 失败: {e}")

        return result

    def sleep(self) -> dict:
        """触发睡眠巩固"""
        if not self._initialized or not self._controller:
            return {'consolidated': 0}
        try:
            return self._controller.sleep()
        except Exception as e:
            logger.warning(f"[NMMEngine] sleep 失败: {e}")
            return {'consolidated': 0}

    def get_stats(self) -> dict:
        if not self._initialized or not self._controller:
            return {'nmm_active': False}
        stats = self._controller.get_stats()
        stats['nmm_active'] = True
        return stats
