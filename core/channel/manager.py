"""
channel/manager.py — ChannelManager 多通道管理器（支持热加载）

统一管理所有消息通道的注册、启动、停止和消息轮询。
支持运行时热添加/移除通道，无需重启 Gateway Loop。
"""

from __future__ import annotations

import logging
import os
import importlib
import pkgutil
from typing import Optional

from .base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.channel")


class ChannelManager:
    """多平台消息通道管理器。"""

    def __init__(self):
        self._channels: dict[str, MessageChannel] = {}

    def register(self, channel: MessageChannel) -> None:
        """注册一个消息通道（若名称已存在则先停止旧通道）。"""
        name = channel.name
        if name in self._channels:
            old = self._channels[name]
            logger.info(f"通道已存在，将替换: {name}")
            try:
                old.stop()
            except Exception:
                pass

        self._channels[name] = channel
        logger.info(f"通道已注册: {name}")

    def get(self, name: str) -> Optional[MessageChannel]:
        """按名称获取通道。"""
        return self._channels.get(name)

    def list(self) -> list[str]:
        """列出所有已注册通道。"""
        return list(self._channels.keys())

    def remove(self, name: str) -> bool:
        """移除并停止指定通道。"""
        channel = self._channels.pop(name, None)
        if channel is None:
            return False
        try:
            channel.stop()
            logger.info(f"通道已移除并停止: {name}")
        except Exception as e:
            logger.error(f"通道 {name} 停止失败: {e}")
        return True

    def restart(self, name: str) -> bool:
        """重启指定通道（stop → start）。"""
        channel = self._channels.get(name)
        if channel is None:
            return False
        try:
            channel.stop()
        except Exception:
            pass
        try:
            channel.start()
            logger.info(f"通道已重启: {name}")
            return True
        except Exception as e:
            logger.error(f"通道 {name} 重启失败: {e}")
            return False

    def start_all(self) -> None:
        """启动所有已注册通道。"""
        for name, channel in self._channels.items():
            logger.info(f"启动通道: {name}")
            try:
                channel.start()
            except Exception as e:
                logger.error(f"通道 {name} 启动失败: {e}")

    def stop_all(self) -> None:
        """停止所有通道。"""
        for name, channel in self._channels.items():
            try:
                channel.stop()
            except Exception as e:
                logger.error(f"通道 {name} 停止失败: {e}")

    def poll_all(self) -> list[Message]:
        """轮询所有通道的新消息。"""
        messages: list[Message] = []
        for name, channel in self._channels.items():
            try:
                msgs = channel.poll()
                if msgs:
                    logger.debug(f"通道 {name}: {len(msgs)} 条新消息")
                    messages.extend(msgs)
            except Exception as e:
                logger.error(f"通道 {name} 轮询失败: {e}")
        return messages

    def broadcast(self, text: str, **kwargs) -> list[SendResult]:
        """向所有通道广播消息。"""
        results: list[SendResult] = []
        for name, channel in self._channels.items():
            try:
                result = channel.send(text, **kwargs)
                results.append(result)
            except Exception as e:
                results.append(SendResult(success=False, platform=name, error=str(e)))
        return results

    # ── 热加载 ─────────────────────────────────────────────────

    _CHANNEL_REGISTRY: dict[str, type[MessageChannel]] = {}
    """名称 → 通道类的注册表（由 discover_channels() 填充）。"""

    @classmethod
    def discover_channels(cls, package: str = "core.channel") -> dict[str, type[MessageChannel]]:
        """扫描 core.channel 包下的所有 MessageChannel 子类。

        遍历包内所有模块，查找继承了 MessageChannel 且 name 属性不为默认值的类。
        返回 {channel_name: class} 映射。
        """
        registry: dict[str, type[MessageChannel]] = {}

        try:
            pkg = importlib.import_module(package)
        except ImportError:
            logger.warning(f"通道发现：无法导入包 {package}")
            return registry

        pkg_path = getattr(pkg, "__path__", None)
        if not pkg_path:
            return registry

        for importer, modname, is_pkg in pkgutil.walk_packages(pkg_path, prefix=f"{package}."):
            if is_pkg:
                continue
            try:
                mod = importlib.import_module(modname)
            except Exception as e:
                logger.debug(f"通道发现：跳过 {modname}: {e}")
                continue

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if not isinstance(attr, type) or not issubclass(attr, MessageChannel) or attr is MessageChannel:
                    continue
                # 尝试实例化以获取 name
                try:
                    instance = attr()
                    ch_name = instance.name
                    if ch_name and ch_name != "unknown":
                        registry[ch_name] = attr
                except Exception:
                    # 跳过需要外部配置的通道类
                    pass

        cls._CHANNEL_REGISTRY = registry
        return registry

    def load_channel(self, name: str, **kwargs) -> Optional[MessageChannel]:
        """按名称加载并注册一个通道（从已发现的注册表中）。"""
        cls = self._CHANNEL_REGISTRY.get(name)
        if cls is None:
            logger.error(f"热加载：未找到通道类 '{name}'，请先调用 discover_channels()")
            return None

        try:
            channel = cls(**kwargs)
            self.register(channel)
            channel.start()
            logger.info(f"热加载成功: {name}")
            return channel
        except Exception as e:
            logger.error(f"热加载失败 {name}: {e}")
            return None

    def reload_channel(self, name: str, **kwargs) -> bool:
        """热重载单个通道（stop → unregister → load → start）。"""
        self.remove(name)
        return self.load_channel(name, **kwargs) is not None

    def refresh_all(self) -> dict[str, bool]:
        """从注册表重新加载所有可发现的通道。

        返回 {通道名: 是否成功}。
        """
        results: dict[str, bool] = {}
        self.discover_channels()
        for name in self._CHANNEL_REGISTRY:
            self.remove(name)
            ch = self.load_channel(name)
            results[name] = ch is not None
        return results
