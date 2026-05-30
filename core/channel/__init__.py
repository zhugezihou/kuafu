"""
channel 包 — 多平台消息通道系统。

使用:
    from core.channel import ChannelManager, FeishuWebSocketChannel
    manager = ChannelManager()
    manager.register(feishu_channel)
    manager.start_all()
"""

from core.channel.base import MessageChannel, Message, SendResult
from core.channel.manager import ChannelManager
from core.channel.feishu_ws import FeishuWebSocketChannel
from core.channel.wechat_ilink import WeChatILinkChannel

__all__ = [
    "MessageChannel", "Message", "SendResult",
    "ChannelManager",
    "FeishuWebSocketChannel",
    "WeChatILinkChannel",
]
