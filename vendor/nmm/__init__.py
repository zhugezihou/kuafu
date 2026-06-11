# vendor版 — 内嵌到夸父项目的 NMM 记忆引擎
"""
nmm/__init__.py — Neural Memory Model
"""
import os, warnings
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '')
warnings.filterwarnings('ignore', '.*CUDA initialization.*')

from nmm.core.memory import MemoryController
from nmm.embed import TextEmbedder
from nmm.server import start_server

__all__ = ["MemoryController", "TextEmbedder", "start_server"]
