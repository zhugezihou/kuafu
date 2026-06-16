"""
channel/gateway_loop.py — Gateway 消息循环

消费所有已注册通道的新消息，交给夸父 Agent 处理。
自动回复或通过指定通道发送结果。
"""

from __future__ import annotations

import logging
import json
import os
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
        self._session_map: dict[str, str] = {}
        self._last_context_tokens: dict[str, str] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 注册审批推送回调到 agent
        self._register_approval_callback()

    def _register_approval_callback(self):
        """注册审批推送回调：审批 pending 时通过消息通道通知用户。

        飞书 → 带按钮的审批卡片
        微信 → 文字短指令（回复 1 批准 / 0 拒绝）
        """
        def _on_approval(tool: str, args: dict, req_id: str):
            """审批请求回调 — 推送到所有消息通道。"""
            title = tool
            if tool == "terminal":
                title = "终端: " + args.get("command", "")[:60]
            # 构造人类可读的参数摘要
            try:
                from core.channel.feishu_ws import FeishuWebSocketChannel
                readable_args = FeishuWebSocketChannel._describe_tool_args(tool, args)
            except Exception:
                readable_args = json.dumps(args, ensure_ascii=False)[:200]
            args_summary = f"{title}\n{readable_args}"

            # 只推送到触发审批的通道
            triggered_platform = getattr(self, '_last_message_source', None) or "feishu"

            # 只推送到触发审批的通道
            for name in [triggered_platform]:
                channel = self.channels.get(name)
                if channel:
                    try:
                        chat_id = None
                        ctx_token = ""
                        if hasattr(self, '_last_chat_ids') and name in self._last_chat_ids:
                            chat_id = self._last_chat_ids[name]
                        elif hasattr(self, '_last_chat_id'):
                            chat_id = self._last_chat_id
                        # 带上 context_token（微信发送需要）
                        if hasattr(self, '_last_context_tokens') and name in self._last_context_tokens:
                            ctx_token = self._last_context_tokens[name]

                        # 飞书 → 卡片按钮，用朝堂群 ID 作为默认
                        if name == "feishu":
                            # 飞书用独立 chat_id（微信的 chat_id 对飞书无效）
                            feishu_chat_id = ""
                            if hasattr(self, '_last_chat_ids') and name in self._last_chat_ids:
                                feishu_chat_id = self._last_chat_ids[name]
                            if not feishu_chat_id:
                                feishu_chat_id = os.environ.get("FEISHU_CHAT_ID", "oc_d860f9f653e3421db6ea419a81414cf6")
                            kwargs = {"chat_id": feishu_chat_id}
                            if hasattr(channel, 'send_approval_card'):
                                result = channel.send_approval_card(
                                    approval_id=req_id,
                                    tool=title,
                                    args_summary=args_summary,
                                    chat_id=feishu_chat_id,
                                )
                                if not result.success:
                                    print(f"[GatewayLoop] ⚠️ 飞书审批卡片推送失败: {result.error}")
                            else:
                                result = channel.send("🔐 | " + title + " | " + detail, **kwargs)
                                if not result.success:
                                    print(f"[GatewayLoop] ⚠️ 飞书审批文本推送失败: {result.error}")
                        else:
                            # 微信/其他 → 短指令文本，用4位短ID方便输入
                            kwargs = {}
                            if chat_id:
                                kwargs['chat_id'] = chat_id
                            if ctx_token:
                                kwargs['context_token'] = ctx_token
                            import hashlib as _hlib
                            short_id = req_id[-4:] if len(req_id) > 4 else req_id
                            msg = (
                                "🔐 审批请求\n"
                                + "━━━━━━━━━━━━━━━━\n"
                                + f"工具: {title}\n"
                                + f"详情: {readable_args[:100]}\\n"
                                + f"ID: {short_id}\n"
                                + "━━━━━━━━━━━━━━━━\n"
                                + f"回复「1 {short_id}」批准\n"
                                + f"回复「0 {short_id}」拒绝"
                            )
                            result = channel.send(msg, **kwargs)
                            if not result.success:
                                print(f"[GatewayLoop] ⚠️ 微信审批推送失败: {result.error}")
                    except Exception as e:
                        print(f"[GatewayLoop] ⚠️ 审批推送失败 ({name}): {e}")

        # 注入到 agent 的审批回调
        if hasattr(self.agent, 'on_approval_request'):
            self.agent.on_approval_request = _on_approval
        # 同时也注入到全局回调
        import core.approval as approval_mod
        approval_mod.ON_APPROVAL_REQUEST_CB = lambda tool, args, req_id: _on_approval(tool, args, req_id)

        # 设置飞书卡片回调：卡片按钮点击 → 执行审批
        from core.channel.feishu_ws import ON_CARD_APPROVAL_CB as feishu_cb_global
        import core.channel.feishu_ws as feishu_mod

        def _on_card_approval(approval_id: str, action: str):
            """飞书卡片按钮回调 — 执行审批决策。"""
            from core.approval import ApprovalManager
            if action == "approve":
                ok = ApprovalManager.approve(approval_id)
                print(f"[GatewayLoop] ✅ 飞书卡片批准 {approval_id}: {'成功' if ok else '失败（不存在或已处理）'}")
            else:
                ok = ApprovalManager.reject(approval_id)
                print(f"[GatewayLoop] ❌ 飞书卡片拒绝 {approval_id}: {'成功' if ok else '失败（不存在或已处理）'}")

        feishu_mod.ON_CARD_APPROVAL_CB = _on_card_approval

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
        - 1 abc123 / 0 abc123（短指令）
        - 批准 abc123 / 拒绝 abc123（文字指令）
        - approve abc123 / reject abc123（英文指令）

        短指令只匹配 req_id 后 8 位。
        """
        text = text.strip()
        import re
        # 短指令：1 / 0 + req_id（可支持后8位短ID）
        m = re.match(r"^([10])\s+(\S{4,})$", text)
        if m:
            raw_action = m.group(1)
            raw_req_id = m.group(2)
            # 如果是短ID（8位），尝试补全匹配
            return {"action": "approve" if raw_action == "1" else "reject", "req_id": raw_req_id}

        # 文字指令：批准 / 拒绝 + req_id
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

        # 记录最近消息的来源通道（用于审批推送通道选择）
        self._last_message_source = msg.platform

        # 设置环境变量，供 send_file_to_user 等工具读取触发通道
        os.environ["KUAFU_CURRENT_PLATFORM"] = msg.platform
        if msg.chat_id:
            os.environ["KUAFU_CURRENT_CHAT_ID"] = msg.chat_id

        # 记录最近消息的来源（用于审批推送） — 按通道分别保存
        if not hasattr(self, '_last_chat_ids'):
            self._last_chat_ids = {}
        self._last_chat_ids[msg.platform] = msg.chat_id
        self._last_chat_id = msg.chat_id  # 保留向后兼容
        # 记录 context_token（微信发送需要）
        if hasattr(msg, 'raw') and msg.raw and "context_token" in msg.raw:
            self._last_context_tokens[msg.platform] = msg.raw["context_token"]

        # 跨 session 上下文关联：同一用户/频道的连续消息关联到上一个 session
        chat_key = f"{msg.platform}:{msg.chat_id}"
        resume_from = self._session_map.get(chat_key)

        # 审批决策检测：批准/拒绝 + req_id
        from core.approval import check_approval_decision as _check_dec, handle_approval_decision as _handle_dec
        decision = _check_dec(text)
        if decision:
            channel = self.channels.get(msg.platform)
            reply = _handle_dec(decision, msg.chat_id, channel)
            print(f"[GatewayLoop] 审批决策已处理: {reply}")
            return

        try:
            # 实时进度推送：每步都通过消息通道发送
            channel = self.channels.get(msg.platform)

            import time as _time

            def _on_step(step_text: str):
                """每步进度仅打印终端，不推送消息"""
                pass

            def _on_phase(summary: str):
                """阶段性总结推送到消息通道（不节流、不过滤）"""
                try:
                    if channel:
                        kwargs = {"chat_id": msg.chat_id}
                        if msg.raw and "context_token" in msg.raw:
                            kwargs["context_token"] = msg.raw["context_token"]
                        result = channel.send(f"\n{summary}\n", **kwargs)
                        if not result.success:
                            print(f"[GatewayLoop] ⚠️ 阶段总结推送失败: {result.error}")
                except Exception as e:
                    print(f"[GatewayLoop] ⚠️ 阶段总结推送异常: {e}")

            # 注入当前消息的通道信息，供 finish 工具发文件时使用
            self.agent._current_platform = msg.platform
            self.agent._current_chat_id = msg.chat_id
            if msg.raw and "context_token" in msg.raw:
                self.agent._current_context_token = msg.raw["context_token"]
            else:
                self.agent._current_context_token = ""

            result = self.agent.run(text, on_step=_on_step, on_phase=_on_phase, resume_from=resume_from)
            reply = result.get("result", "")
            if not reply:
                return

            # 保存 session_id 供后续消息关联上下文
            session_id = result.get("session_id", "")
            if session_id:
                self._session_map[chat_key] = session_id

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
