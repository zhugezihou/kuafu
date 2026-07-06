"""
Core test for GatewayLoop — _loop, _handle_message, _check_approval_decision, _register_approval_callback.
"""
import json
import os
import time
from unittest.mock import patch, MagicMock, call, ANY

import pytest

from core.channel.base import Message, SendResult


class TestGatewayLoop:
    """Complete coverage for GatewayLoop."""

    def _make_loop(self, agent=None, channels=None):
        from core.channel.gateway_loop import GatewayLoop
        mgr = channels or MagicMock()
        mgr.list.return_value = ["feishu", "wechat"]
        agent = agent or MagicMock()
        gl = GatewayLoop(agent=agent, channel_manager=mgr)
        return gl

    # ---- init ----
    def test_init(self):
        gl = self._make_loop()
        assert gl._running is False
        assert gl.agent is not None
        assert gl.channels is not None
        assert gl.poll_interval == 2.0

    def test_init_custom_poll_interval(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        agent = MagicMock()
        gl = GatewayLoop(agent=agent, channel_manager=mgr, poll_interval=5.0)
        assert gl.poll_interval == 5.0

    # ---- start / stop ----
    def test_start(self):
        gl = self._make_loop()
        gl.start()
        assert gl._running is True
        assert gl._thread is not None

    def test_start_already_running(self):
        gl = self._make_loop()
        gl._running = True
        with patch.object(gl, '_loop') as mock_loop:
            gl.start()
            mock_loop.assert_not_called()

    def test_stop(self):
        gl = self._make_loop()
        gl._running = True
        gl.stop()
        assert gl._running is False

    # ---- _loop ----
    def test_loop_polls_and_handles(self):
        gl = self._make_loop()
        gl._running = True
        msg = Message(text="hello", platform="feishu", chat_id="c1")

        def poll_and_stop():
            gl._running = False
            return [msg]

        gl.channels.poll_all = poll_and_stop
        with patch.object(gl, '_handle_message') as mock_handle:
            gl._loop()
            mock_handle.assert_called_once_with(msg)

    def test_loop_poll_exception(self):
        gl = self._make_loop()
        gl._running = True

        def poll_and_stop():
            gl._running = False
            raise ValueError("poll error")

        gl.channels.poll_all = poll_and_stop
        with patch('core.channel.gateway_loop.logger') as mock_log:
            gl._loop()
            mock_log.error.assert_called()

    def test_loop_empty_messages(self):
        gl = self._make_loop()
        gl._running = True

        def poll_and_stop():
            gl._running = False
            return []

        gl.channels.poll_all = poll_and_stop
        with patch.object(gl, '_handle_message') as mock_handle:
            gl._loop()
            mock_handle.assert_not_called()

    # ---- _handle_message ----
    def test_handle_message_empty_text(self):
        gl = self._make_loop()
        msg = Message(text="", platform="feishu")
        with patch.object(gl.agent, 'run') as mock_run:
            gl._handle_message(msg)
            mock_run.assert_not_called()

    def test_handle_message_adds_context(self):
        gl = self._make_loop()
        msg = Message(text="hello", platform="wechat", chat_id="u1",
                      raw={"context_token": "ctx_1"})
        gl.agent.run.return_value = {"result": "reply"}
        gl.channels.get.return_value = MagicMock()
        gl._handle_message(msg)
        gl.channels.get.assert_called_with("wechat")
        # Check that context_token is passed
        send_call = gl.channels.get.return_value.send.call_args
        assert send_call[0][0] == "reply"
        assert send_call[1]["context_token"] == "ctx_1"

    def test_handle_message_approval_decision(self):
        gl = self._make_loop()
        msg = Message(text="1 abc123", platform="wechat", chat_id="u1")
        with patch('core.channel.gateway_loop._check_dec', return_value={"action": "approve", "req_id": "abc123"}):
            with patch('core.channel.gateway_loop._handle_dec', return_value="已批准"):
                gl._handle_message(msg)
                gl.agent.run.assert_not_called()

    def test_handle_message_agent_run_fail(self):
        gl = self._make_loop()
        msg = Message(text="hello", platform="wechat", chat_id="u1")
        gl.agent.run.side_effect = Exception("agent error")
        mock_channel = MagicMock()
        gl.channels.get.return_value = mock_channel
        gl._handle_message(msg)
        mock_channel.send.assert_called_once()
        assert "处理出错" in mock_channel.send.call_args[0][0]

    def test_handle_message_no_result(self):
        gl = self._make_loop()
        msg = Message(text="hello", platform="wechat", chat_id="u1")
        gl.agent.run.return_value = {}
        gl._handle_message(msg)
        # No send should happen (empty result)
        gl.channels.get.assert_called_once()

    def test_handle_message_sets_last_source(self):
        gl = self._make_loop()
        msg = Message(text="hello", platform="feishu", chat_id="chat_1")
        gl.agent.run.return_value = {"result": "hi"}
        gl.channels.get.return_value = MagicMock()
        gl._handle_message(msg)
        assert gl._last_message_source == "feishu"
        assert gl._last_chat_ids == {"feishu": "chat_1"}
        assert gl._last_chat_id == "chat_1"

    def test_handle_message_channel_not_found(self):
        gl = self._make_loop()
        msg = Message(text="hello", platform="slack", chat_id="c1")
        gl.agent.run.return_value = {"result": "reply"}
        gl.channels.get.return_value = None
        gl._handle_message(msg)
        # No send attempted (no channel)

    # ---- _check_approval_decision ----
    def test_check_approval_decision_short_approve(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("1 abc123")
        assert result == {"action": "approve", "req_id": "abc123"}

    def test_check_approval_decision_short_reject(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("0 def456")
        assert result == {"action": "reject", "req_id": "def456"}

    def test_check_approval_decision_chinese_approve(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("批准 abc123")
        assert result == {"action": "approve", "req_id": "abc123"}

    def test_check_approval_decision_chinese_reject(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("拒绝 abc123")
        assert result == {"action": "reject", "req_id": "abc123"}

    def test_check_approval_decision_english_approve(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("approve abc123")
        assert result == {"action": "approve", "req_id": "abc123"}

    def test_check_approval_decision_english_reject(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("reject xyz789")
        assert result == {"action": "reject", "req_id": "xyz789"}

    def test_check_approval_decision_no_match(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("hello world")
        assert result is None

    def test_check_approval_decision_short_3chars(self):
        """Short ID with 3 chars should not match (min 4)."""
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("1 ab")
        assert result is None

    def test_check_approval_decision_empty_text(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("")
        assert result is None

    def test_check_approval_decision_whitespace(self):
        from core.channel.gateway_loop import GatewayLoop
        mgr = MagicMock()
        gl = GatewayLoop(agent=MagicMock(), channel_manager=mgr)
        result = gl._check_approval_decision("   ")
        assert result is None

    # ---- _register_approval_callback ----
    def test_register_approval_callback_sets_handler(self):
        gl = self._make_loop()
        assert gl.agent.on_approval_request is not None
        # The callback should be callable
        assert callable(gl.agent.on_approval_request)

    def test_approval_callback_feishu_card(self):
        gl = self._make_loop()
        feishu_ch = MagicMock()
        feishu_ch.send_approval_card = MagicMock()
        gl.channels.get.return_value = feishu_ch
        gl._last_message_source = "feishu"
        gl._last_chat_ids = {"feishu": "oc_chat_1"}
        gl.agent.on_approval_request("terminal", {"command": "ls"}, "req_001")

        feishu_ch.send_approval_card.assert_called_once()
        call_kwargs = feishu_ch.send_approval_card.call_args[1]
        assert call_kwargs["approval_id"] == "req_001"
        assert call_kwargs["tool"] == "终端: ls"
        assert "FEISHU_CHAT_ID" in call_kwargs.get("chat_id", "") or call_kwargs.get("chat_id") == "oc_chat_1"

    def test_approval_callback_feishu_no_card_method(self):
        """Feishu channel without send_approval_card → fallback to send()."""
        gl = self._make_loop()
        feishu_ch = MagicMock()
        # Remove send_approval_card
        del feishu_ch.send_approval_card
        gl.channels.get.return_value = feishu_ch
        gl._last_message_source = "feishu"
        gl._last_chat_ids = {"feishu": "oc_chat_1"}
        gl.agent.on_approval_request("terminal", {"command": "ls"}, "req_001")
        feishu_ch.send.assert_called_once()

    def test_approval_callback_wechat(self):
        gl = self._make_loop()
        wechat_ch = MagicMock()
        gl.channels.get.return_value = wechat_ch
        gl._last_message_source = "wechat"
        gl._last_chat_ids = {"wechat": "u1"}
        gl.agent.on_approval_request("terminal", {"command": "ls"}, "req_001_long_id")
        wechat_ch.send.assert_called_once()
        sent_text = wechat_ch.send.call_args[0][0]
        assert "🔐 审批请求" in sent_text
        assert "long" in sent_text  # short ID should be last 4 chars

    def test_approval_callback_channel_error(self):
        gl = self._make_loop()
        feishu_ch = MagicMock()
        feishu_ch.send_approval_card.side_effect = Exception("send error")
        gl.channels.get.return_value = feishu_ch
        gl._last_message_source = "feishu"
        # Should not raise
        gl.agent.on_approval_request("tool", {}, "req_002")

    def test_approval_callback_no_channel(self):
        gl = self._make_loop()
        gl.channels.get.return_value = None
        gl._last_message_source = "unknown"
        # Should not raise
        gl.agent.on_approval_request("tool", {}, "req_003")

    def test_approval_callback_with_chat_id(self):
        """When _last_chat_ids is not set, use _last_chat_id."""
        gl = self._make_loop()
        feishu_ch = MagicMock()
        feishu_ch.send_approval_card = MagicMock()
        gl.channels.get.return_value = feishu_ch
        gl._last_message_source = "feishu"
        gl._last_chat_id = "oc_fallback"
        gl.agent.on_approval_request("read_file", {"path": "/etc/passwd"}, "req_004")
        assert "oc_fallback" in str(feishu_ch.send_approval_card.call_args)

    def test_on_card_approval_approve(self):
        """The card approval callback registered via feishu_mod.ON_CARD_APPROVAL_CBS."""
        from core.channel.gateway_loop import GatewayLoop
        import core.channel.feishu_ws as feishu_mod
        captured = []
        feishu_mod.register_card_approval_cb(lambda aid, act: captured.append((aid, act)))
        with patch('core.approval.ApprovalManager') as MockAM:
            MockAM.approve.return_value = True
            for _cb in list(feishu_mod.ON_CARD_APPROVAL_CBS):
                _cb("req_005", "approve")
            MockAM.approve.assert_called_with("req_005")
        feishu_mod.ON_CARD_APPROVAL_CBS.clear()

    def test_on_card_approval_reject(self):
        import core.channel.feishu_ws as feishu_mod
        captured = []
        feishu_mod.register_card_approval_cb(lambda aid, act: captured.append((aid, act)))
        with patch('core.approval.ApprovalManager') as MockAM:
            MockAM.reject.return_value = True
            for _cb in list(feishu_mod.ON_CARD_APPROVAL_CBS):
                _cb("req_006", "reject")
            MockAM.reject.assert_called_with("req_006")
        feishu_mod.ON_CARD_APPROVAL_CBS.clear()

    def test_on_card_approval_approve_fail(self):
        import core.channel.feishu_ws as feishu_mod
        captured = []
        feishu_mod.register_card_approval_cb(lambda aid, act: captured.append((aid, act)))
        with patch('core.approval.ApprovalManager') as MockAM:
            MockAM.approve.return_value = False
            # Should not raise
            for _cb in list(feishu_mod.ON_CARD_APPROVAL_CBS):
                _cb("req_007", "approve")
        feishu_mod.ON_CARD_APPROVAL_CBS.clear()
