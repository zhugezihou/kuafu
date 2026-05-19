#!/home/asus/kuafu/venv/bin/python3
"""夸父 CLI — python3 run.py '你的任务'"""

import sys
from pathlib import Path

# 确保能找到包
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))

from core.main import main

if __name__ == "__main__":
    main()
