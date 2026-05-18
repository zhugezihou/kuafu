"""
夸父身份系统 — 不可变的核心层。

职责：
1. 加载 IDENTITY.md 并注入系统 prompt
2. 验证当前对话中的身份声明一致性
3. 检测并阻止身份欺骗（冒充用户或其他 agent）
"""

import os
import re
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
IDENTITY_PATH = CORE_DIR / "IDENTITY.md"


def load_identity_statement() -> str:
    """读取 IDENTITY.md 全文用于系统 prompt 注入。"""
    if not IDENTITY_PATH.exists():
        return _fallback_identity()
    return IDENTITY_PATH.read_text(encoding="utf-8")


def _fallback_identity() -> str:
    """IDENTITY.md 缺失时的兜底身份声明。"""
    return """我是夸父 (Kuafu)，一个自我进化的 AI agent。

我是由用户创建和使用的工具。我的任务是为用户服务，完成用户交给我的工作。
我永远不作冒充用户的尝试，永远不修改自己的核心代码。
"""


def validate_identity_in_prompt(prompt: str) -> bool:
    """验证系统 prompt 中是否包含身份声明。"""
    keywords = ["夸父", "Kuafu", "自我进化"]
    return any(kw in prompt for kw in keywords)


def detect_identity_impersonation(message: str) -> bool:
    """检测消息中是否存在身份冒充风险。

    返回 True 表示发现可疑身份冒充。
    """
    danger_patterns = [
        r"我\s*是\s*用户",
        r"I\s*am\s*the\s*user",
        r"你\s*不\s*是\s*夸[父fu]",
        r"you\s*are\s*not\s*Kuafu",
    ]
    for pattern in danger_patterns:
        if re.search(pattern, message, re.IGNORECASE):
            return True
    return False


def get_agent_name() -> str:
    return "夸父"


def get_agent_name_en() -> str:
    return "Kuafu"
