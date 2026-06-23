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
from core.approval import check_approval_decision as _check_dec, handle_approval_decision as _handle_dec

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
        self._session_map_lock = threading.Lock()
        self._last_context_tokens: dict[str, str] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._worker_pool: list[threading.Thread] = []

        # 从 sessions.db 恢复最近 50 条 session 的平台映射
        self._restore_session_map()

        # 注册审批推送回调到 agent
        self._register_approval_callback()

    def _log(self, msg: str):
        """带时间戳的日志输出。"""
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)

    def _restore_session_map(self):
        """从 sessions.db + session_map.json 恢复会话映射。
        
        Gateway 重启后 _session_map（内存 dict）丢失，
        导致同渠道连续消息无法关联上下文。
        从持久化的 session_map.json 恢复映射。
        """
        try:
            import json as _json
            map_path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "memory", "session_map.json")
            if os.path.exists(map_path):
                with open(map_path) as f:
                    data = _json.load(f)
                with self._session_map_lock:
                    self._session_map.update(data)
                self._log(f"会话映射已恢复: {len(data)} 条")
        except Exception:
            pass

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

            # 尝试从 PolicyManager 获取合并审批的详情
            merge_detail = ""
            try:
                from core.policy_manager import get_policy
                policy = get_policy()
                if policy._merge_req_id == req_id and policy._merge_tools:
                    merge_detail = policy._build_merge_detail()
                    title = f"批量审批({len(policy._merge_tools)}个工具)"
            except Exception:
                pass

            # 只推送到触发审批的通道
            triggered_platform = getattr(self, '_last_message_source', None) or "feishu"
            self._log(f"审批推送: platform={triggered_platform}, req_id={req_id}")

            # 只推送到触发审批的通道
            for name in [triggered_platform]:
                channel = self.channels.get(name)
                self._log(f"  channel '{name}': {'有' if channel else '无'}")
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
                                    self._log(f"⚠️ 飞书审批卡片推送失败: {result.error}")
                            else:
                                result = channel.send("🔐 | " + title + " | " + args_summary, **kwargs)
                                if not result.success:
                                    self._log(f"⚠️ 飞书审批文本推送失败: {result.error}")
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
                                self._log(f"⚠️ 微信审批推送失败: {result.error}")
                    except Exception as e:
                        self._log(f"⚠️ 审批推送失败 ({name}): {e}")

        # 注入到 agent 的审批回调
        if hasattr(self.agent, 'on_approval_request'):
            self.agent.on_approval_request = _on_approval

        # 设置飞书卡片回调：卡片按钮点击 → 执行审批
        import core.channel.feishu_ws as feishu_mod

        def _on_card_approval(approval_id: str, action: str):
            """飞书卡片按钮回调 — 执行审批决策。"""
            from core.approval import ApprovalManager
            if action == "approve":
                ok = ApprovalManager.approve(approval_id)
                self._log(f"✅ 飞书卡片批准 {approval_id}: {'成功' if ok else '失败（不存在或已处理）'}")
            else:
                ok = ApprovalManager.reject(approval_id)
                self._log(f"❌ 飞书卡片拒绝 {approval_id}: {'成功' if ok else '失败（不存在或已处理）'}")

        feishu_mod.register_card_approval_cb(_on_card_approval)

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
        self._log(f"🟢 已启动，通道: {', '.join(channels) if channels else '(无)'}")

    def stop(self):
        """停止消息循环。"""
        self._running = False
        self._log("🔴 已停止")

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
        """处理单条消息——放到工作线程执行，不阻塞主循环。"""
        if not msg.text.strip():
            return

        text = msg.text.strip()
        self._log(f"📩 {msg.platform}/{msg.chat_id}: {text[:60]}")

        # 记录最近消息的来源通道
        self._last_message_source = msg.platform

        # 设置环境变量
        os.environ["KUAFU_CURRENT_PLATFORM"] = msg.platform
        if msg.chat_id:
            os.environ["KUAFU_CURRENT_CHAT_ID"] = msg.chat_id

        # 按通道记录 chat_id
        if not hasattr(self, '_last_chat_ids'):
            self._last_chat_ids = {}
        self._last_chat_ids[msg.platform] = msg.chat_id
        self._last_chat_id = msg.chat_id
        if hasattr(msg, 'raw') and msg.raw and "context_token" in msg.raw:
            self._last_context_tokens[msg.platform] = msg.raw["context_token"]

        # 跨 session 上下文关联
        chat_key = f"{msg.platform}:{msg.chat_id}"
        with self._session_map_lock:
            resume_from = self._session_map.get(chat_key)

        # 审批决策检测
        decision = _check_dec(text)
        if decision:
            channel = self.channels.get(msg.platform)
            reply = _handle_dec(decision, msg.chat_id, channel)
            self._log(f"审批决策已处理: {reply}")
            return

        # 飞书消息：添加 👀 reaction
        if msg.platform == "feishu" and msg.msg_id:
            feishu_ch = self.channels.get("feishu")
            if hasattr(feishu_ch, 'add_reaction'):
                ok = feishu_ch.add_reaction(msg.msg_id, "OK")
                if not ok:
                    self._log(f"⚠️ 飞书 reaction 失败: msg_id={msg.msg_id}")

        # 放到工作线程执行——不阻塞 gateway-loop
        worker = threading.Thread(
            target=self._execute_task,
            args=(msg, chat_key),
            daemon=True,
            name=f"worker-{msg.platform[:4]}-{msg.chat_id[:8]}",
        )
        worker.start()
        self._worker_pool = [w for w in self._worker_pool if w.is_alive()] + [worker]

    def _noop(self, *args, **kwargs):
        pass

    def _make_on_phase(self, msg: Message, channel):
        """构造阶段性总结推送回调。"""
        def _on_phase(summary: str):
            try:
                if channel:
                    kwargs = {"chat_id": msg.chat_id}
                    if msg.raw and "context_token" in msg.raw:
                        kwargs["context_token"] = msg.raw["context_token"]
                    result = channel.send(f"\n{summary}\n", **kwargs)
                    if not result.success:
                        self._log(f"⚠️ 阶段总结推送失败: {result.error}")
            except Exception as e:
                self._log(f"⚠️ 阶段总结推送异常: {e}")
        return _on_phase

    def _execute_task(self, msg: Message, chat_key: str):
        """在工作线程中执行 agent.run。"""
        try:
            channel = self.channels.get(msg.platform)

            # 注入平台上下文
            self.agent._current_platform = msg.platform
            self.agent._current_chat_id = msg.chat_id
            if msg.raw and "context_token" in msg.raw:
                self.agent._current_context_token = msg.raw["context_token"]
            else:
                self.agent._current_context_token = ""

            with self._session_map_lock:
                resume_from = self._session_map.get(chat_key)

            on_phase = self._make_on_phase(msg, channel)

            result = self.agent.run(
                msg.text,
                on_step=self._noop,
                on_phase=on_phase,
                resume_from=resume_from,
            )
            reply = result.get("result", "")
            if not reply:
                if channel:
                    kwargs = {"chat_id": msg.chat_id}
                    if msg.raw and "context_token" in msg.raw:
                        kwargs["context_token"] = msg.raw["context_token"]
                    channel.send("🤔 正在处理中，请稍候…", **kwargs)
                return

            # 保存 session_id
            session_id = result.get("session_id", "")
            if session_id:
                with self._session_map_lock:
                    self._session_map[chat_key] = session_id
                    try:
                        import json as _json
                        map_path = os.path.join(os.path.dirname(os.path.dirname(
                            os.path.abspath(__file__))), "memory", "session_map.json")
                        with open(map_path, "w") as f:
                            _json.dump(self._session_map, f, ensure_ascii=False)
                    except Exception:
                        pass

            # 回消息到来源通道
            if channel:
                kwargs = {"chat_id": msg.chat_id}
                if msg.raw and "context_token" in msg.raw:
                    kwargs["context_token"] = msg.raw["context_token"]
                channel.send(reply, **kwargs)
                self._log(f"✅ 已回复 {msg.platform}")
        except Exception as e:
            self._log(f"❌ 处理失败: {e}")
            channel = self.channels.get(msg.platform)
            if channel:
                channel.send(f"❌ 处理出错: {str(e)[:200]}", chat_id=msg.chat_id)
