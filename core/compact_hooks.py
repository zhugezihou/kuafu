"""
core/compact_hooks.py — 上下文压缩 Hook 接口（CompactHook）

源自 Codex CLI Compact Hook 模式：
  - pre_compact：压缩前调用，可干预压缩策略
  - post_compact：压缩后调用，可检查/修改压缩结果
"""

import logging
from typing import Protocol

logger = logging.getLogger("kuafu.compact_hooks")


class CompactContext:
    """压缩上下文。"""
    def __init__(self, original_tokens: int, target_tokens: int,
                 messages_count: int, strategy: str = ""):
        self.original_tokens = original_tokens
        self.target_tokens = target_tokens
        self.messages_count = messages_count
        self.strategy = strategy


class CompactResult:
    """压缩结果。"""
    def __init__(self, messages_removed: int = 0,
                 summary: str = "", compressed_tokens: int = 0):
        self.messages_removed = messages_removed
        self.summary = summary
        self.compressed_tokens = compressed_tokens


class CompactHook(Protocol):
    """上下文压缩 Hook 接口。"""

    def pre_compact(self, ctx: CompactContext) -> None:
        ...

    def post_compact(self, ctx: CompactContext,
                     result: CompactResult) -> None:
        ...


class CompactHookManager:
    """压缩 Hook 管理器。"""

    def __init__(self):
        self._hooks: list[CompactHook] = []

    def register(self, hook: CompactHook):
        self._hooks.append(hook)
        logger.info(f"🔌 注册 CompactHook: {type(hook).__name__}")

    def unregister(self, hook: CompactHook) -> bool:
        try:
            self._hooks.remove(hook)
            return True
        except ValueError:
            return False

    def fire_pre(self, ctx: CompactContext) -> None:
        for hook in self._hooks:
            try:
                hook.pre_compact(ctx)
            except Exception as e:
                logger.warning(f"CompactHook pre_compact 异常: {e}")

    def fire_post(self, ctx: CompactContext, result: CompactResult) -> None:
        for hook in self._hooks:
            try:
                hook.post_compact(ctx, result)
            except Exception as e:
                logger.warning(f"CompactHook post_compact 异常: {e}")


class LoggingCompactHook:
    """记录压缩事件的 Hook。"""

    def pre_compact(self, ctx: CompactContext) -> None:
        logger.info(f"📏 即将压缩: {ctx.messages_count} 条消息 "
                    f"({ctx.original_tokens} tokens → {ctx.target_tokens})")

    def post_compact(self, ctx: CompactContext, result: CompactResult) -> None:
        logger.info(f"✅ 压缩完成: 移除 {result.messages_removed} 条消息, "
                    f"摘要 {len(result.summary)} chars")


class CompactOnIdleHook:
    """仅在 agent 空闲时允许压缩。"""

    def __init__(self):
        self._running = True

    def set_running(self, running: bool):
        self._running = running

    def pre_compact(self, ctx: CompactContext) -> None:
        if self._running:
            ctx.strategy = "skip"
