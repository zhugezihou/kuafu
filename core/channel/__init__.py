"""
channel 包 — 多平台消息通道系统。

使用:
    from core.channel import ChannelManager
    from core.channel import FeishuChannel
    manager = ChannelManager()
    manager.register(feishu_channel)
    manager.start_all()
"""

from core.channel.base import MessageChannel, Message, SendResult
from core.channel.manager import ChannelManager
from core.channel.feishu import FeishuChannel
from core.channel.wechat import WeChatChannel

__all__ = [
    "MessageChannel", "Message", "SendResult",
    "ChannelManager",
    "FeishuChannel",
    "WeChatChannel",
]
