"""
夸父 (Kuafu) — 自我进化的 AI Agent.

快速开始：
    git clone https://github.com/zhugezihou/kuafu.git
    cd kuafu
    pip install -r requirements.txt
    echo 'DEEPSEEK_API_KEY=sk-...' > .env
    python -c "from kuafu import KuafuAgent; print('夸父已就绪')"
"""

import os
import sys

# 确保能直接导入 core.* （无论从哪个目录导入 kuafu）
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from core.main import KuafuAgent

__all__ = ["KuafuAgent"]
