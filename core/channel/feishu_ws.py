"""
channel/feishu_ws.py — 飞书 WebSocket 通道

通过 lark-oapi SDK 连接飞书 WebSocket，收发消息和卡片交互。
支持：
- 接收群聊/私聊文本消息（@bot 过滤）
- 发送文本消息
- 发送审批卡片（interactive card）
- 接收卡片按钮回调（批准/拒绝审批）

环境变量：
  FEISHU_APP_ID — 飞书应用 App ID
  FEISHU_APP_SECRET — 飞书应用 App Secret
  FEISHU_CHAT_ID — 默认飞书群聊天 ID
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional, Pattern

from core.channel.base import MessageChannel, Message, SendResult

logger = logging.getLogger("kuafu.feishu_ws")

# 审批按钮回调 — 支持多回调注册（列表形式，防止单例覆盖丢失）
ON_CARD_APPROVAL_CBS: list[Callable[[str, str], None]] = []
"""回调签名: (approval_id: str, action: str) -> None, action 为 'approve' 或 'reject'"""
_CARD_CB_LOCK = threading.Lock()  # 保护 _CBS 的并发写

def register_card_approval_cb(cb: Callable[[str, str], None]) -> None:
    """注册卡片审批回调。支持多注册，避免单例覆盖导致回调丢失。"""
    with _CARD_CB_LOCK:
        if cb not in ON_CARD_APPROVAL_CBS:
            ON_CARD_APPROVAL_CBS.append(cb)

def unregister_card_approval_cb(cb: Callable[[str, str], None]) -> None:
    """注销卡片审批回调。"""
    with _CARD_CB_LOCK:
        if cb in ON_CARD_APPROVAL_CBS:
            ON_CARD_APPROVAL_CBS.remove(cb)


class FeishuWebSocketChannel(MessageChannel):
    """飞书 WebSocket 通道（lark-oapi SDK）。"""

    @property
    def name(self) -> str:
        return "feishu"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
    ):
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._lock = threading.Lock()
        self._inbox: list[Message] = []
        self._ws_client: Any = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._bot_open_id: str = ""  # 保存 bot 自己的 open_id，用于 @bot 过滤
        self._card_approval_state: dict[str, threading.Event] = {}
        self._seen_msg_ids: deque[str] = deque(maxlen=500)  # 已处理消息ID缓存，防止重连后重放
        self._ws_start_time: float = time.time()  # WS 连接时间，用于过滤连接前的历史消息
        # token 缓存
        self._cached_token: str = ""
        self._token_expires_at: float = 0
        """approval_id → Event，用于解阻塞等待审批的线程"""

    # ── 消息发送 ──────────────────────────────────────────────

    def send(self, text: str, **kwargs) -> SendResult:
        """通过飞书 API 发送文本消息。自动检测 Markdown 表格 → 转为卡片。"""
        chat_id = kwargs.get("chat_id", "")
        # 检测是否含 Markdown 表格，自动转为卡片
        if self._contains_table(text):
            card = self._markdown_to_card(text)
            if card:
                return self.send_card(card, chat_id=chat_id)
        return self._send_api(chat_id, "text", {"text": text})

    def send_card(self, card: dict, chat_id: str = "") -> SendResult:
        """发送飞书消息卡片（interactive）。"""
        return self._send_api(chat_id, "interactive", card)

    # ── 智能表格检测与转换 ──────────────────────────────

    _TABLE_RE = re.compile(r"\|[\s\-]+\|[\s\-]+\|")
    """检测 Markdown 表格模式：包含 |---| 分隔行即视为表格。"""

    def _contains_table(self, text: str) -> bool:
        """检查文本是否包含 Markdown 表格。"""
        return bool(self._TABLE_RE.search(text))

    def _markdown_to_card(self, text: str) -> Optional[dict]:
        """将含 Markdown 表格的文本转为飞书卡片（schema 1.0 原生 table 组件）。
        
        飞书卡片 schema 1.0 支持 tag=table 原生表格组件，
        有对齐的列、表头、颜色标签，效果远好于 Markdown 表格。
        
        如果解析失败，兜底为列表格式。
        """
        try:
            lines = text.split("\n")
            # 识别表格区域
            in_table = False
            table_lines = []
            before_parts = []
            after_parts = []
            state = "before"

            for line in lines:
                stripped = line.strip()
                is_table_line = stripped.startswith("|") and "|" in stripped[1:-1]
                
                if is_table_line and not in_table:
                    in_table = True
                    state = "table"
                    table_lines.append(stripped)
                elif is_table_line and in_table:
                    table_lines.append(stripped)
                elif not is_table_line and in_table:
                    in_table = False
                    state = "after"
                    if stripped:
                        after_parts.append(line)
                elif state == "before":
                    if stripped:
                        before_parts.append(line)
                elif state == "after":
                    if stripped:
                        after_parts.append(line)

            # 解析表格行，跳过 --- 分隔行
            data_rows = []
            header_row = None
            for row in table_lines:
                if row.replace(" ", "").startswith("|") and "-" in row:
                    continue  # 跳过分隔行
                cols = [c.strip() for c in row.strip("|").split("|")]
                if header_row is None:
                    header_row = cols
                else:
                    data_rows.append(cols)

            if not header_row or not data_rows:
                return None  # 无法解析

            # 构建 columns
            columns = []
            col_names = []
            col_width = "auto"
            for i, h in enumerate(header_row):
                cname = f"col{i}"
                col_names.append(cname)
                columns.append({
                    "name": cname,
                    "display_name": h,
                    "data_type": "text",
                    "width": "auto",
                    "horizontal_align": "left",
                    "vertical_align": "center"
                })

            # 构建 rows
            rows = []
            for dr in data_rows:
                row_obj = {}
                for i, val in enumerate(dr):
                    if i < len(col_names):
                        row_obj[col_names[i]] = val
                rows.append(row_obj)

            # 构建 card elements
            elements = []

            # 表格前文本
            before_text = "\n".join(before_parts)
            if before_text:
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": before_text[:1800]}})

            # 表格组件
            elements.append({
                "tag": "table",
                "page_size": min(10, max(1, len(rows))),  # 简单全显示
                "row_height": "low",
                "header_style": {
                    "text_align": "left",
                    "text_size": "normal",
                    "background_style": "grey",
                    "text_color": "grey",
                    "bold": True,
                    "lines": 1
                },
                "columns": columns,
                "rows": rows
            })

            # 表格后文本
            after_text = "\n".join(after_parts)
            if after_text:
                elements.append({"tag": "hr"})
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": after_text[:1800]}})

            # 取第一行作为卡片标题
            title = "📊 查询结果"
            if before_text:
                first_line = before_text.strip().split("\n")[0]
                if len(first_line) < 30:
                    title = first_line

            # schema 1.0 卡片（无 schema 字段，elements 在根级）
            card = {
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": title[:40]}
                },
                "elements": elements
            }
            return card

        except Exception as e:
            print(f"[Feishu] 表格转卡片失败: {e}")
            return None

    def _send_api(self, chat_id: str, msg_type: str, content: dict | str) -> SendResult:
        """飞书 API 消息发送底层方法。"""

        # text 类型消息有 8000 字节限制，截断过长内容
        if msg_type == "text":
            if isinstance(content, dict):
                text_val = content.get("text", "")
                # 截断到 7500 字节（留 500 字节给 JSON 外壳）
                text_bytes = text_val.encode("utf-8")
                if len(text_bytes) > 7500:
                    text_val = text_bytes[:7497].decode("utf-8", errors="ignore") + "..."
                    content["text"] = text_val
            elif isinstance(content, str) and len(content.encode("utf-8")) > 7500:
                content = content.encode("utf-8")[:7497].decode("utf-8", errors="ignore") + "..."

        token = self._get_tenant_token()
        if not token:
            return SendResult(success=False, platform="feishu", error="token 获取失败")

        from urllib.request import Request, urlopen

        target = chat_id or os.environ.get("FEISHU_CHAT_ID", "")
        if not target:
            return SendResult(success=False, platform="feishu", error="chat_id 未指定")

        if isinstance(content, str):
            content_str = content
        else:
            content_str = json.dumps(content, ensure_ascii=False)

        body = json.dumps({
            "receive_id": target,
            "msg_type": msg_type,
            "content": content_str,
        }, ensure_ascii=False).encode("utf-8")

        req = Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ok = data.get("code") == 0
                msg_id = ""
                if ok:
                    msg_data = data.get("data", {})
                    if isinstance(msg_data, dict):
                        msg_id = msg_data.get("message_id", "")
                return SendResult(
                    success=ok,
                    platform="feishu",
                    error="" if ok else str(data),
                    msg_id=msg_id,
                )
        except Exception as e:
            return SendResult(success=False, platform="feishu", error=str(e))

    # ── 消息表情回复（Reaction） ──────────────────────────────

    def add_reaction(self, message_id: str, emoji: str = "EYES") -> bool:
        """给指定消息添加表情回复。
        
        Args:
            message_id: 飞书消息 ID
            emoji: emoji 类型，如 EYES / THUMBS_UP / THINKING / SMILE 等（飞书 emoji_type）
        """
        try:
            token = self._get_tenant_token()
            if not token:
                return False
            body = json.dumps({"reaction_type": {"emoji_type": emoji}}).encode("utf-8")
            from urllib.request import Request, urlopen
            req = Request(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions",
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                code = data.get("code")
                if code != 0:
                    print(f"[Feishu] reaction 失败: code={code}, msg={data.get('msg', '')}")
                return code == 0
        except Exception as e:
            print(f"[Feishu] reaction 异常: {e}")
            return False

    # ── Token 管理（带缓存，飞书 tenant_access_token 有效期 2 小时） ──

    def _get_tenant_token(self) -> str:
        # 缓存命中且未过期（提前 5 分钟刷新）
        now = time.time()
        if self._cached_token and now < self._token_expires_at - 300:
            return self._cached_token

        from urllib.request import Request, urlopen
        body = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }).encode("utf-8")
        req = Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                token = data.get("tenant_access_token", "")
                expire = data.get("expire", 7200)  # 默认 2 小时
                if token:
                    self._cached_token = token
                    self._token_expires_at = now + expire
                return token
        except Exception:
            # token 获取失败时，返回缓存的旧 token（可能还能用几分钟）
            return self._cached_token

    # ── 消息接收 ──────────────────────────────────────────────

    def poll(self) -> list[Message]:
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        return msgs

    def _on_message(self, text: str, msg_id: str = "", chat_id: str = "", sender: str = "", chat_type: str = "", mentions: list | None = None, create_time: str = ""):
        # 时间过滤：跳过 WS 连接前的历史消息
        if create_time:
            try:
                # 飞书 create_time 是毫秒级时间戳
                msg_ts = int(create_time) / 1000.0
                start_ts = getattr(self, '_ws_start_time', time.time())
                # 只处理 WS 连接前 5 秒内的消息（防止连接瞬间消息丢失）
                if msg_ts < start_ts - 5:
                    return
            except (ValueError, TypeError):
                pass

        # 去重：WS重连后飞书可能重放已处理的消息，跳过已见过的 msg_id
        if msg_id and msg_id in self._seen_msg_ids:
            return
        if msg_id:
            self._seen_msg_ids.append(msg_id)

        # 群聊消息必须 @bot 才处理（使用 SDK mentions 字段，不依赖文本显示名）
        # 私聊（p2p）消息无需 @，直接处理
        if chat_type == "group" or (chat_id and chat_type != "p2p"):
            bot_id = getattr(self, '_bot_open_id', None)
            if not mentions or not any(
                # SDK object: m.key 是 UserId 对象, 属性是 user_id / open_id / union_id
                (hasattr(m, 'key') and (
                    (isinstance(m.key, dict) and m.key.get("user_id", "") == bot_id)
                    or (hasattr(m.key, 'user_id') and m.key.user_id == bot_id)
                    or (hasattr(m.key, 'open_id') and m.key.open_id == bot_id)
                    or (hasattr(m.key, 'id') and m.key.id == bot_id)
                ))
                # Mention 对象自身的 id (MentionEvent.id) 不是 user_id, 不匹配
                # dict 形态的 mentions：兼容旧 SDK
                or (isinstance(m, dict) and (
                    m.get("user_id", "") == bot_id
                    or m.get("open_id", "") == bot_id
                    or m.get("id", "") == bot_id
                    or m.get("key", {}).get("user_id", "") == bot_id
                    or m.get("key", {}).get("open_id", "") == bot_id
                ))
                # name 匹配：任何时候都检查 name 作为兜底
                or (isinstance(m, dict) and m.get("name", "") == "夸父")
                or (hasattr(m, 'name') and m.name == "夸父")
                for m in mentions
            ):
                print(f"[FeishuWS] 忽略非@bot消息: {text[:60]}")
                return

        # 清洗 text：去掉所有飞书 @mention 前缀（如 @夸父 / @中书令 / @user_1）
        # 飞书 WS 消息 content.text 中 @ 标记在不同场景下可能渲染不同的显示名，
        # 因此统一用正则去掉 text 首部的 @mention，只保留实际用户输入内容。
        import re as _re
        cleaned = _re.sub(r"^@[^\s]+\s*", "", text, count=1).strip()

        msg = Message(
            text=cleaned,
            msg_id=msg_id,
            platform="feishu",
            chat_id=chat_id,
            sender=sender,
        )
        with self._lock:
            self._inbox.append(msg)

    # ── 审批卡片 ──────────────────────────────────────────────

    def _build_approval_card(self, approval_id: str, tool: str, args_summary: str) -> dict:
        """构建审批按钮卡片 JSON，含人类可读的参数说明。"""
        # 解析 args_summary（格式: "工具: xxx\n{yaml参数}"）
        parts = args_summary.split("\n", 1)
        tool_name = parts[0] if parts else tool
        args_text = parts[1] if len(parts) > 1 else ""

        # 生成人类可读的操作说明
        description = self._format_approval_description(tool_name, args_text)

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🔐 审批请求"},
                "template": "orange",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**{tool_name}**\n\n{description}\n\n---\nID: `{approval_id[-8:]}`",
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 批准"},
                            "type": "primary",
                            "value": {"approval_id": approval_id, "action": "approve"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                            "type": "danger",
                            "value": {"approval_id": approval_id, "action": "reject"},
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _format_approval_description(tool_name: str, args_text: str) -> str:
        """将工具名称和参数转为人类可读的操作说明。"""
        try:
            # args_text 可能是 JSON，也可能是其他格式
            import json as _json
            args = None
            if args_text.strip().startswith("{"):
                try:
                    args = _json.loads(args_text)
                except _json.JSONDecodeError:
                    args = None

            if args:
                return FeishuWebSocketChannel._describe_tool_args(tool_name, args)

            # 不是 JSON，直接展示
            return args_text[:300] if args_text else "（无参数）"
        except Exception:
            return args_text[:300] if args_text else "（无参数）"

    @staticmethod
    def _describe_tool_args(tool: str, args: dict) -> str:
        """根据工具类型生成人类可读的参数说明。"""
        tool_lower = tool.lower()

        # terminal
        if "terminal" in tool_lower or ("终端" in tool):
            cmd = args.get("command", args.get("cmd", ""))
            if len(cmd) > 200:
                cmd = cmd[:200] + "..."
            return f"执行命令:\n```\n{cmd}\n```"

        # write_file
        if "write_file" in tool_lower or "write" in tool_lower:
            path = args.get("path", "?")
            content = args.get("content", "")
            content_len = len(content) if content else 0
            mode = args.get("mode", "replace")
            return f"**写入文件** `{path}`\n内容长度: {content_len} 字符\n模式: {mode}"

        # patch
        if "patch" in tool_lower:
            path = args.get("path", "?")
            old = args.get("old_string", "")
            new = args.get("new_string", "")
            mode = args.get("mode", "replace")
            desc_parts = [f"**修改文件** `{path}`"]
            if mode:
                desc_parts.append(f"模式: {mode}")
            if old:
                desc_parts.append(f"查找: `{old[:60]}...`" if len(old) > 60 else f"查找: `{old}`")
            if new:
                desc_parts.append(f"替换为: `{new[:60]}...`" if len(new) > 60 else f"替换为: `{new}`")
            return "\n".join(desc_parts)

        # read_file
        if "read_file" in tool_lower or "read" in tool_lower:
            path = args.get("path", "?")
            offset = args.get("offset", 1)
            limit = args.get("limit", "全部")
            return f"**读取文件** `{path}`\n从第 {offset} 行起，读取 {limit} 行"

        # delete / remove
        if "delete" in tool_lower or "remove" in tool_lower or "rm" in tool_lower:
            path = args.get("path", args.get("target", "?"))
            return f"**删除** `{path}`"

        # search_files
        if "search" in tool_lower or "grep" in tool_lower:
            pattern = args.get("pattern", "")
            path = args.get("path", ".")
            return f"**搜索** `{pattern}` 在 `{path}` 中"

        # git
        if "git" in tool_lower:
            cmd = args.get("command", "")
            return f"**Git 操作** `{cmd[:200]}`"

        # browser
        if "browser" in tool_lower:
            url = args.get("url", "")
            action = args.get("action", "导航")
            return f"**浏览器** {action}: `{url[:200]}`"

        # 兜底：列出关键参数
        lines = []
        for k, v in list(args.items())[:5]:
            v_str = str(v)
            if len(v_str) > 100:
                v_str = v_str[:100] + "..."
            lines.append(f"▪ {k}: {v_str}")
        if len(args) > 5:
            lines.append(f"▪ ... 还有 {len(args)-5} 个参数")
        return "\n".join(lines) if lines else "（无参数）"

    def send_approval_card(self, approval_id: str, tool: str, args_summary: str, chat_id: str = "") -> SendResult:
        """发送审批卡片并注册等待事件。"""
        ev = threading.Event()
        self._card_approval_state[approval_id] = ev
        card = self._build_approval_card(approval_id, tool, args_summary)
        result = self.send_card(card, chat_id=chat_id)
        if result.success and result.msg_id:
            # 保存卡片对应的消息 ID，后续更新用
            if not hasattr(self, '_card_msg_ids'):
                self._card_msg_ids: dict[str, str] = {}
            self._card_msg_ids[approval_id] = result.msg_id
        if not result.success:
            self._card_approval_state.pop(approval_id, None)
        return result

    def wait_approval(self, approval_id: str, timeout: float = 300) -> Optional[str]:
        """阻塞等待审批结果。返回 'approve' 或 'reject'，超时返回 None。"""
        ev = self._card_approval_state.get(approval_id)
        if not ev:
            return None
        # 将卡片按钮回调写入事件
        result_holder: list[Optional[str]] = [None]
        import core.channel.feishu_ws as _feishu_mod

        def _cb(aid: str, action: str):
            if aid == approval_id:
                result_holder[0] = action
                ev.set()

        _feishu_mod.register_card_approval_cb(_cb)

        ev.wait(timeout=timeout)
        _feishu_mod.unregister_card_approval_cb(_cb)
        self._card_approval_state.pop(approval_id, None)
        return result_holder[0]

    # ── 待审批恢复（WS 重连后恢复） ─────────────────────────

    def _recover_pending_approvals(self) -> None:
        """WS 重连后扫描磁盘审批记录，恢复内存中的等待事件。

        WS 断开期间用户点击的审批卡片回调会丢失（新 WS 连接收不到）。
        通过重新扫描 memory/approvals/ 中 status=pending 的记录，
        为每一条重新注册 threading.Event。

        注意：不会重发审批卡片（已经发过），只是恢复监听。
        重连前已点过卡片的用户需要重新点一次（飞书卡片状态仍有效）。
        """
        try:
            from core.approval import ApprovalManager
            pending = ApprovalManager.list_pending()
            if not pending:
                return
            count = 0
            for req in pending:
                if req.id not in self._card_approval_state:
                    ev = threading.Event()
                    self._card_approval_state[req.id] = ev
                    count += 1
            if count:
                print(f"[FeishuWS] ♻️ 恢复 {count} 个待审批监听", flush=True)
        except Exception as e:
            print(f"[FeishuWS] ⚠️ 恢复待审批失败: {e}", flush=True)

    # ── WS 启动（lark-oapi SDK） ──────────────────────────────

    def start(self) -> None:
        """启动飞书 WebSocket 订阅。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._ws_loop,
            daemon=True,
            name="feishu-ws",
        )
        self._thread.start()
        print("[FeishuWS] 启动 WebSocket 直连...")

    def _ws_loop(self):
        """WebSocket 事件循环（含自动重连）。"""
        try:
            from urllib.request import Request, urlopen
            import json
            # 获取 bot 自己的 open_id，用于 @bot 过滤
            token = self._get_tenant_token()
            if token:
                req = Request(
                    "https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                    method="GET",
                )
                try:
                    with urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("code") == 0:
                            self._bot_open_id = (data.get("bot", {}) or {}).get("open_id", "")
                            print(f"[FeishuWS] Bot open_id: {self._bot_open_id[:8]}...")
                except Exception:
                    pass

            import lark_oapi as lark
        except ImportError:
            import sys
            print("[FeishuWS] ❌ lark-oapi 未安装，无法启动飞书 WebSocket 通道。请安装: pip install lark-oapi")
            sys.exit(1)

        reconnect_count = 0
        while self._running:
            try:
                from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

                def on_message(data) -> None:
                    """收到飞书消息事件。"""
                    try:
                        event_data = getattr(data, 'event', data) if not isinstance(data, dict) else data
                        msg = getattr(event_data, 'message', None) or (isinstance(event_data, dict) and event_data.get('message'))
                        if not msg:
                            return

                        if isinstance(msg, dict):
                            msg_type = msg.get('msg_type', msg.get('message_type', ''))
                            if msg_type != 'text':
                                return
                            content_raw = msg.get('content', '')
                            chat_type = msg.get('chat_type', '')
                            msg_id = msg.get('message_id', '')
                            chat_id = msg.get('chat_id', '')
                            sender = (msg.get('sender') or {}).get('id', '')
                            mentions = msg.get('mentions', [])
                            create_time = msg.get('create_time', '')
                        else:
                            msg_type = getattr(msg, 'message_type', getattr(msg, 'msg_type', ''))
                            if msg_type != 'text':
                                return
                            content_raw = getattr(msg, 'content', '')
                            chat_type = getattr(msg, 'chat_type', '')
                            msg_id = getattr(msg, 'message_id', '')
                            chat_id = getattr(msg, 'chat_id', '')
                            sender = getattr(getattr(msg, 'sender', None), 'id', '') if hasattr(msg, 'sender') else ''
                            mentions = getattr(msg, 'mentions', [])
                            create_time = getattr(msg, 'create_time', '')

                        if not content_raw:
                            return
                        content = json.loads(content_raw).get("text", content_raw)

                        self._on_message(
                            text=content,
                            msg_id=msg_id,
                            chat_id=chat_id,
                            sender=sender,
                            chat_type=chat_type,
                            mentions=mentions if isinstance(mentions, list) else [],
                            create_time=create_time,
                        )
                    except Exception as e:
                        import traceback
                        print(f"[FeishuWS] 处理消息异常: {e}")
                        traceback.print_exc()

                def on_card_action(data) -> None:
                    """收到飞书卡片按钮回调事件。"""
                    try:
                        event = data.event if hasattr(data, 'event') else data
                        action = getattr(event, 'action', None) if not isinstance(event, dict) else event.get('action')
                        if not action:
                            action = getattr(data, 'action', None) if not isinstance(data, dict) else data.get('action')
                        if not action:
                            return
                        value = getattr(action, 'value', {}) if not isinstance(action, dict) else action.get('value', {})
                        if not value:
                            return
                        approval_id = value.get('approval_id') if isinstance(value, dict) else None
                        action_type = value.get('action') if isinstance(value, dict) else None
                        print(f"[FeishuWS] 卡片按钮: {action_type} (ID: {approval_id})")

                        # 用飞书 SDK 更新卡片为已处理状态（交互卡片 → 只读文本）
                        token = self._get_tenant_token()
                        if token and approval_id:
                            result_text = "✅ 已批准" if action_type == "approve" else "❌ 已拒绝"
                            template = "green" if action_type == "approve" else "red"
                            # 获取卡片 message_id
                            msg_id = ""
                            if hasattr(self, '_card_msg_ids') and approval_id in self._card_msg_ids:
                                msg_id = self._card_msg_ids.pop(approval_id)
                            if not msg_id:
                                _evt = getattr(data, 'event', None)
                                if _evt:
                                    _ctx = getattr(_evt, 'context', None)
                                    if _ctx and hasattr(_ctx, 'open_message_id') and _ctx.open_message_id:
                                        msg_id = _ctx.open_message_id
                            if msg_id:
                                try:
                                    _token = self._get_tenant_token()
                                    if _token:
                                        card_content = json.dumps({
                                            "config": {"wide_screen_mode": True},
                                            "header": {
                                                "title": {"tag": "plain_text", "content": result_text},
                                                "template": "green" if action_type == "approve" else "red",
                                            },
                                            "elements": [
                                                {"tag": "markdown", "content": f"**审批ID**: `{approval_id}`\n**状态**: {result_text}"},
                                            ],
                                        }, ensure_ascii=False)
                                        from urllib.request import Request as _Req, urlopen as _urlopen
                                        _patch_body = json.dumps({
                                            "content": card_content,
                                            "msg_type": "interactive",
                                        }).encode("utf-8")
                                        import threading as _th
                                        import time as _tm
                                        def _update_card(silent=False):
                                            _tm.sleep(0.5 if not silent else 2.0)
                                            _p_req = _Req(
                                                f"https://open.feishu.cn/open-apis/im/v1/messages/{msg_id}",
                                                data=_patch_body,
                                                headers={"Authorization": f"Bearer {_token}", "Content-Type": "application/json"},
                                                method="PATCH",
                                            )
                                            try:
                                                with _urlopen(_p_req, timeout=10) as _p_resp:
                                                    _p_data = json.loads(_p_resp.read())
                                                    if _p_data.get("code") == 0:
                                                        if not silent:
                                                            print(f"[FeishuWS] 卡片更新成功")
                                                    else:
                                                        print(f"[FeishuWS] 卡片更新失败: code={_p_data.get('code')}")
                                            except Exception:
                                                pass
                                        # 第一次 PATCH 先刷新服务端缓存（不打印日志，避免"更新成功"混淆）
                                        _th.Thread(target=lambda: (_update_card(silent=True)), daemon=True).start()
                                        # 第二次 PATCH 延迟渲染，覆盖客户端回退
                                        _th.Thread(target=lambda: (_update_card(silent=False)), daemon=True).start()
                                except Exception as e2:
                                    print(f"[FeishuWS] 卡片更新异常: {e2}")

                        # 快照遍历回调列表（防止并发修改导致 list changed during iteration）
                        with _CARD_CB_LOCK:
                            _snapshot = list(ON_CARD_APPROVAL_CBS)
                        for _cb in _snapshot:
                            try:
                                _cb(str(approval_id), str(action_type))
                            except Exception:
                                pass
                    except Exception as e:
                        import traceback
                        print(f"[FeishuWS] 处理卡片回调异常: {e}")
                        traceback.print_exc()

                handler = (
                    EventDispatcherHandler.builder("", "")
                    .register_p2_im_message_receive_v1(on_message)
                    .register_p2_card_action_trigger(on_card_action)
                    .build()
                )

                cli = lark.ws.Client(
                    self.app_id,
                    self.app_secret,
                    event_handler=handler,
                    log_level=lark.LogLevel.ERROR,
                )

                print(f"[FeishuWS] WebSocket 已连接 (重连 #{reconnect_count})", flush=True)
                reconnect_count = 0
                # 更新 WS 连接时间，用于过滤重连后飞书重放的历史消息
                self._ws_start_time = time.time()

                # ── 恢复待审批卡片的状态监听 ──
                self._recover_pending_approvals()

                cli.start()  # 阻塞直到断开

            except Exception as e:
                reconnect_count += 1
                delay = min(reconnect_count * 5, 60)
                print(f"[FeishuWS] 连接断开 ({e}), {delay}s 后重连...")
                for _ in range(delay * 2):
                    if not self._running:
                        return
                    time.sleep(0.5)

    def stop(self) -> None:
        """停止通道。"""
        self._running = False
        print("[FeishuWS] 已停止")
