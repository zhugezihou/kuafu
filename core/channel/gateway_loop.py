"""
channel/gateway_loop.py — Gateway 消息循环

消费所有已注册通道的新消息，交给夸父 Agent 处理。
自动回复或通过指定通道发送结果。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from core.channel.base import MessageChannel, Message, SendResult
from core.channel.manager import ChannelManager

logger = logging.getLogger("kuafu.gateway")


class GatewayLoop:
    """Gateway 消息循环：消费通道消息 → Agent 处理 → 回复。"""

    def __init__(
        self,
        agent: Any,
        channel_manager: ChannelManager,
        poll_interval: float = 2.0,
    ):
        self.agent = agent
        self.channels = channel_manager
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 注册审批推送回调到 agent
        self._register_approval_callback()

    def _register_approval_callback(self):
        """注册审批推送回调：审批 pending 时通过消息通道通知用户。"""
        def _on_approval(tool: str, args: dict, req_id: str):
            """审批请求回调 — 推送到所有消息通道。"""
            title = tool
            if tool == "terminal":
                title = "终端: " + args.get("command", "")[:60]
            detail = json.dumps(args, ensure_ascii=False)[:200]

            msg = (
                "🔐 **审批请求** `" + req_id + "`\n"
                "工具: " + title + "\n"
                "参数: " + detail + "\n\n"
                '回复「批准 ' + req_id + '」或「拒绝 ' + req_id + '」'
            )

            # 推送到所有通道
            for name in self.channels.list():
                channel = self.channels.get(name)
                if channel:
                    try:
                        chat_id = None
                        if hasattr(self, '_last_chat_ids') and name in self._last_chat_ids:
                            chat_id = self._last_chat_ids[name]
                        elif hasattr(self, '_last_chat_id'):
                            chat_id = self._last_chat_id
                        kwargs = {}
                        if chat_id:
                            kwargs['chat_id'] = chat_id
                        channel.send(msg, **kwargs)
                    except Exception as e:
                        print(f"[GatewayLoop] ⚠️ 审批推送失败 ({name}): {e}")

        # 注入到 agent 的审批回调（注意 agent 本身就是 AgentLoop，没有 _loop 属性）
        if hasattr(self.agent, 'on_approval_request'):
            self.agent.on_approval_request = _on_approval
        # 同时也注入到全局回调（approval.py 的 ON_APPROVAL_REQUEST_CB）
        import core.approval as approval_mod
        approval_mod.ON_APPROVAL_REQUEST_CB = lambda tool, args, req_id: _on_approval(tool, args, req_id)

    def start(self):
        """启动消息循环（后台线程）。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="gateway-loop",
        )
        self._thread.start()
        channels = self.channels.list()
        print(f"[GatewayLoop] 🟢 已启动，通道: {', '.join(channels) if channels else '(无)'}")

    def stop(self):
        """停止消息循环。"""
        self._running = False
        print("[GatewayLoop] 🔴 已停止")

    def _loop(self):
        """主循环：轮询所有通道的消息并处理。"""
        while self._running:
            try:
                messages = self.channels.poll_all()
                for msg in messages:
                    self._handle_message(msg)
            except Exception as e:
                logger.error(f"GatewayLoop 轮询异常: {e}")

            for _ in range(int(self.poll_interval / 0.2)):
                if not self._running:
                    return
                time.sleep(0.2)

    def _check_approval_decision(self, text: str) -> Optional[dict]:
        """检查用户消息是否是审批决策。
        
        支持格式：
        - 批准 abc123
        - 拒绝 abc123
        - approve abc123
        - reject abc123
        """
        text = text.strip()
        import re
        m = re.match(r"^(批准|拒绝|approve|reject)\s+(\S+)", text, re.IGNORECASE)
        if not m:
            return None
        action_word = m.group(1).lower()
        req_id = m.group(2)
        if action_word in ("批准", "approve"):
            action = "approve"
        else:
            action = "reject"
        return {"action": action, "req_id": req_id}

    def _handle_message(self, msg: Message):
        """处理单条消息。"""
        if not msg.text.strip():
            return

        text = msg.text.strip()
        print(f"[GatewayLoop] 📩 {msg.platform}/{msg.chat_id}: {text[:60]}")

        # 记录最近消息的来源（用于审批推送） — 按通道分别保存
        if not hasattr(self, '_last_chat_ids'):
            self._last_chat_ids = {}
        self._last_chat_ids[msg.platform] = msg.chat_id
        self._last_chat_id = msg.chat_id  # 保留向后兼容

        # 审批决策检测：批准/拒绝 + req_id
        decision = self._check_approval_decision(text)
        if decision:
            # 执行审批决策，不进入 agent.run()
            from core.approval import ApprovalManager
            req_id = decision["req_id"]
            if decision["action"] == "approve":
                ok = ApprovalManager.approve(req_id)
                reply = f"✅ 已批准 `{req_id}`" if ok else f"❌ 审批失败: {req_id} 不存在或已处理"
            else:
                ok = ApprovalManager.reject(req_id)
                reply = f"⛔ 已拒绝 `{req_id}`" if ok else f"❌ 拒绝失败: {req_id} 不存在或已处理"
            channel = self.channels.get(msg.platform)
            if channel:
                kwargs = {"chat_id": msg.chat_id}
                if msg.raw and "context_token" in msg.raw:
                    kwargs["context_token"] = msg.raw["context_token"]
                channel.send(reply, **kwargs)
            return

        try:
            result = self.agent.run(text)
            reply = result.get("result", "")
            if not reply:
                return

            # 回消息到来源通道
            channel = self.channels.get(msg.platform)
            if channel:
                # 传递 raw 中的 context_token 给 send（微信 iLink 需要）
                kwargs = {"chat_id": msg.chat_id}
                if msg.raw and "context_token" in msg.raw:
                    kwargs["context_token"] = msg.raw["context_token"]
                channel.send(reply, **kwargs)
                print(f"[GatewayLoop] ✅ 已回复 {msg.platform}")
        except Exception as e:
            print(f"[GatewayLoop] ❌ 处理失败: {e}")
            channel = self.channels.get(msg.platform)
            if channel:
                channel.send(f"❌ 处理出错: {str(e)[:200]}", chat_id=msg.chat_id)
