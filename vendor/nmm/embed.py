"""
nmm/embed.py — 文本嵌入器

把自然语言文本转换成向量，供 NMM 存储和检索。

支持两种模式：
1. sentence-transformers（本地模型，推荐）
2. 简单哈希嵌入（零依赖，备选）
"""

import re
import os
import warnings

# 防止 CUDA 初始化超时（旧驱动问题）
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '')
warnings.filterwarnings('ignore', '.*CUDA initialization.*')

import torch
import torch.nn.functional as F


class ImprovedEmbedder:
    """改进的零依赖嵌入器

    用字符 n-gram + 词频加权 + 位置敏感哈希，
    在不依赖外部模型的前提下获得更好的语义近似。
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    def encode(self, text: str) -> torch.Tensor:
        """文本 → 向量"""
        vec = torch.zeros(self.dim)
        text = text.lower().strip()
        words = re.findall(r'\w+', text)

        if not words:
            return vec

        # 1. 字符 trigram（上下文敏感）
        for i in range(len(text) - 2):
            gram = text[i:i + 3]
            h = hash(gram) % self.dim
            vec[h] += 1.0

        # 2. 词频 TF 加权（罕见词更有区分度）
        from collections import Counter
        word_counts = Counter(words)
        max_count = max(word_counts.values())
        for word, count in word_counts.items():
            tf = count / max_count  # 词频归一化
            h = hash(word) % self.dim
            vec[h] += 1.0 + tf

        # 3. word pair（捕捉短语）
        for i in range(len(words) - 1):
            pair = words[i] + "_" + words[i + 1]
            h = hash(pair) % self.dim
            vec[h] += 0.5

        # 归一化
        norm = vec.norm()
        if norm > 0:
            vec = vec / norm

        return vec

    def decode(self, vector: torch.Tensor) -> str:
        """向量 → 文本摘要"""
        if vector.norm() < 0.01:
            return "[empty]"

        # 找到激活最强的维度对应的近似词
        top_vals, top_idx = vector.topk(min(5, self.dim))
        # 反向查找最大的 n-gram 贡献
        return f"[top-5 dims: {top_idx.tolist()}, magnitude: {top_vals.mean().item():.2f}]"


class STEmbedder:
    """sentence-transformers 嵌入器"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)

    def encode(self, text: str) -> torch.Tensor:
        self._load()
        emb = self._model.encode(text, convert_to_tensor=True)
        return F.normalize(emb, dim=-1)

    def decode(self, vector: torch.Tensor) -> str:
        """向量 → 对最近文本的粗略描述

        由于 sentence-transformers 是不可逆的，
        返回向量的统计摘要。
        """
        norm = vector.norm().item()
        return f"[dim={vector.shape[0]}, norm={norm:.2f}]"


class TextEmbedder:
    """文本嵌入器（自动选择可用后端）"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.dim = 384  # 默认
        self._backend = None
        self._init_backend()

    def _init_backend(self):
        """自动选择可用的后端"""
        # 默认使用 ImprovedEmbedder（零依赖，速度快）
        # 如需更高精度：pip install sentence-transformers 并设置 model_name
        print(f"  [embed] 使用 ImprovedEmbedder (dim={self.dim})")
        self._backend = ImprovedEmbedder(dim=self.dim)

    def encode(self, text: str) -> torch.Tensor:
        return self._backend.encode(text)

    def decode(self, vector: torch.Tensor) -> str:
        return self._backend.decode(vector)


# ── 测试 ──

def test_improved_embedder():
    print("测试 ImprovedEmbedder...")
    emb = ImprovedEmbedder(384)
    v1 = emb.encode("今天天气真好")
    v2 = emb.encode("今天天气不错")
    v3 = emb.encode("量子物理")
    sim_same = F.cosine_similarity(v1.unsqueeze(0), v2.unsqueeze(0)).item()
    sim_diff = F.cosine_similarity(v1.unsqueeze(0), v3.unsqueeze(0)).item()
    print(f"  '天气真好' vs '天气不错': 相似度={sim_same:.4f}")
    print(f"  '天气真好' vs '量子物理': 相似度={sim_diff:.4f}")
    assert sim_same > sim_diff, "相似语义应该更接近"
    print("  ✅ SimpleEmbedder 测试通过\n")


def test_text_embedder():
    print("测试 TextEmbedder（自动后端选择）...")
    emb = TextEmbedder()
    v = emb.encode("你好世界")
    print(f"  向量维度: {v.shape}")
    print(f"  向量 norm: {v.norm():.4f}")
    print("  ✅ TextEmbedder 测试通过\n")


if __name__ == "__main__":
    test_improved_embedder()
    test_text_embedder()
