"""
Core test for FeishuWebSocketChannel — init, start, stop, send, _reconnect, cards, _handle_event.
"""
import json
import os
import time
import threading
from unittest.mock import patch, MagicMock, PropertyMock, call, ANY

import pytest

from core.channel.base import Message, SendResult


class TestFeishuWebSocket:
    """Complete coverage for FeishuWebSocketChannel."""

    def _make_channel(self, app_id="test_id", app_secret="test_secret"):
        from core.channel.feishu_ws import FeishuWebSocketChannel
        ch = FeishuWebSocketChannel(app_id=app_id, app_secret=app_secret)
        return ch

    # ---- init ----
    def test_init_defaults(self):
        """Init uses env vars when no args given."""
        with patch.dict(os.environ, {"FEISHU_APP_ID": "env_id", "FEISHU_APP_SECRET": "env_secret"}, clear=True):
            from core.channel.feishu_ws import FeishuWebSocketChannel
            ch = FeishuWebSocketChannel()
            assert ch.app_id == "env_id"
            assert ch.app_secret == "env_secret"
            assert ch._running is False
            assert ch._thread is None
            assert ch._ws_client is None
            assert ch._inbox == []
            assert ch._bot_open_id == ""

    def test_init_with_args(self):
        ch = self._make_channel("my_id", "my_secret")
        assert ch.app_id == "my_id"
        assert ch.app_secret == "my_secret"
        assert ch.name == "feishu"
        assert len(ch._seen_msg_ids) == 0
        assert ch._card_approval_state == {}

    def test_init_empty_env(self):
        with patch.dict(os.environ, {}, clear=True):
            from core.channel.feishu_ws import FeishuWebSocketChannel
            ch = FeishuWebSocketChannel()
            assert ch.app_id == ""
            assert ch.app_secret == ""

    # ---- name property ----
    def test_name_property(self):
        ch = self._make_channel()
        assert ch.name == "feishu"

    # ---- start / stop ----
    def test_start_non_running(self):
        ch = self._make_channel()
        with patch.object(ch, '_ws_loop') as mock_loop:
            ch.start()
            assert ch._running is True
            assert ch._thread is not None
            mock_loop.assert_called_once()

    def test_start_already_running(self):
        ch = self._make_channel()
        ch._running = True
        with patch.object(ch, '_ws_loop') as mock_loop:
            ch.start()
            mock_loop.assert_not_called()

    def test_stop(self):
        ch = self._make_channel()
        ch._running = True
        ch.stop()
        assert ch._running is False

    # ---- send ----
    def test_send_delegates_to_send_api(self):
        ch = self._make_channel()
        with patch.object(ch, '_send_api') as mock_api:
            mock_api.return_value = SendResult(success=True, platform="feishu")
            result = ch.send("hello", chat_id="chat_123")
            mock_api.assert_called_once_with("chat_123", "text", {"text": "hello"})
            assert result.success is True

    def test_send_without_chat_id(self):
        ch = self._make_channel()
        with patch.object(ch, '_send_api') as mock_api:
            mock_api.return_value = SendResult(success=True, platform="feishu")
            result = ch.send("hello")
            mock_api.assert_called_once_with("", "text", {"text": "hello"})
            assert result.success is True

    # ---- send_card ----
    def test_send_card(self):
        ch = self._make_channel()
        card = {"header": {"title": "test"}}
        with patch.object(ch, '_send_api') as mock_api:
            mock_api.return_value = SendResult(success=True, platform="feishu")
            result = ch.send_card(card, chat_id="chat_1")
            mock_api.assert_called_once_with("chat_1", "interactive", card)
            assert result.success is True

    # ---- _send_api ----
    def test_send_api_token_fail(self):
        ch = self._make_channel()
        with patch.object(ch, '_get_tenant_token', return_value=""):
            result = ch._send_api("chat_1", "text", {"text": "hi"})
            assert result.success is False
            assert "token" in result.error

    def test_send_api_no_chat_id(self):
        ch = self._make_channel()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(ch, '_get_tenant_token', return_value="my_token"):
                result = ch._send_api("", "text", {"text": "hi"})
                assert result.success is False
                assert "chat_id" in result.error

    def test_send_api_success(self):
        ch = self._make_channel()
        with patch.object(ch, '_get_tenant_token', return_value="my_token"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0, "data": {}}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = ch._send_api("chat_1", "text", {"text": "hi"})
                assert result.success is True
                assert result.error == ""

    def test_send_api_api_error(self):
        ch = self._make_channel()
        with patch.object(ch, '_get_tenant_token', return_value="my_token"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 999001, "msg": "error"}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = ch._send_api("chat_1", "text", {"text": "hi"})
                assert result.success is False

    def test_send_api_network_error(self):
        ch = self._make_channel()
        with patch.object(ch, '_get_tenant_token', return_value="my_token"):
            with patch('urllib.request.urlopen', side_effect=Exception("connection failed")):
                result = ch._send_api("chat_1", "text", {"text": "hi"})
                assert result.success is False
                assert "connection failed" in result.error

    def test_send_api_with_str_content(self):
        ch = self._make_channel()
        with patch.object(ch, '_get_tenant_token', return_value="my_token"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = ch._send_api("chat_1", "text", "raw string content")
                assert result.success is True

    # ---- _get_tenant_token ----
    def test_get_tenant_token_success(self):
        ch = self._make_channel()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"tenant_access_token": "token_abc", "expire": 7200}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            token = ch._get_tenant_token()
            assert token == "token_abc"

    def test_get_tenant_token_fail(self):
        ch = self._make_channel()
        with patch('urllib.request.urlopen', side_effect=Exception("no network")):
            token = ch._get_tenant_token()
            assert token == ""

    def test_get_tenant_token_empty_response(self):
        ch = self._make_channel()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"errcode": 1}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            token = ch._get_tenant_token()
            assert token == ""

    # ---- poll ----
    def test_poll_returns_and_clears(self):
        ch = self._make_channel()
        msg = Message(text="test", platform="feishu")
        ch._inbox.append(msg)
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == "test"
        assert ch._inbox == []

    def test_poll_empty(self):
        ch = self._make_channel()
        msgs = ch.poll()
        assert msgs == []

    # ---- _on_message ----
    def test_on_message_adds_to_inbox(self):
        ch = self._make_channel()
        ch._on_message("hello", msg_id="m1", chat_id="c1", sender="s1", chat_type="p2p")
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == "hello"
        assert msgs[0].msg_id == "m1"
        assert msgs[0].chat_id == "c1"
        assert msgs[0].sender == "s1"

    def test_on_message_deduplicates(self):
        ch = self._make_channel()
        ch._on_message("first", msg_id="m1")
        ch._on_message("second", msg_id="m1")
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_cleans_at_mention(self):
        ch = self._make_channel()
        ch._on_message("@夸父 hello world", msg_id="m2", chat_type="p2p")
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == "hello world"

    def test_on_message_skips_old_message(self):
        ch = self._make_channel()
        old_ts = (time.time() - 30) * 1000  # 30s ago in ms
        ch._on_message("old", msg_id="m3", create_time=str(int(old_ts)))
        msgs = ch.poll()
        assert len(msgs) == 0

    def test_on_message_group_requires_mention(self):
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        # No mention → skip
        ch._on_message("hello", msg_id="m4", chat_type="group")
        msgs = ch.poll()
        assert len(msgs) == 0

    def test_on_message_group_with_bot_mention(self):
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        class MockMention:
            key = type('obj', (object,), {'user_id': 'bot_001'})()
        ch._on_message("hello", msg_id="m5", chat_type="group", mentions=[MockMention()])
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == "hello"

    def test_on_message_group_with_dict_mention(self):
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        mention = {"user_id": "bot_001"}
        ch._on_message("hello", msg_id="m6", chat_type="group", mentions=[mention])
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_group_with_name_match(self):
        ch = self._make_channel()
        mention = {"name": "夸父"}
        ch._on_message("hello", msg_id="m7", chat_type="group", mentions=[mention])
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_group_mention_object_with_id(self):
        """Mention object with 'id' attribute matches bot_open_id."""
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        class MockMention2:
            key = type('obj', (object,), {'id': 'bot_001'})()
        ch._on_message("hello", msg_id="m8", chat_type="group", mentions=[MockMention2()])
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_group_mention_object_with_open_id(self):
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        class MockMention3:
            key = type('obj', (object,), {'open_id': 'bot_001'})()
        ch._on_message("hello", msg_id="m9", chat_type="group", mentions=[MockMention3()])
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_group_dict_key_user_id(self):
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        mention = {"key": {"user_id": "bot_001"}}
        ch._on_message("hello", msg_id="m10", chat_type="group", mentions=[mention])
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_group_dict_key_open_id(self):
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        mention = {"key": {"open_id": "bot_001"}}
        ch._on_message("hello", msg_id="m11", chat_type="group", mentions=[mention])
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_with_invalid_create_time(self):
        """Invalid create_time should not crash."""
        ch = self._make_channel()
        ch._on_message("test", msg_id="m12", create_time="not_a_number")
        msgs = ch.poll()
        assert len(msgs) == 1

    def test_on_message_default_chat_type_group_without_id(self):
        """When chat_type is not p2p and there's a chat_id, treat as group."""
        ch = self._make_channel()
        ch._bot_open_id = "bot_001"
        # chat_id present but no mentions → skip
        ch._on_message("hi", msg_id="m13", chat_id="c1", chat_type="group")
        msgs = ch.poll()
        assert len(msgs) == 0

    def test_on_message_empty_text(self):
        ch = self._make_channel()
        ch._on_message("", msg_id="m14")
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == ""

    # ---- _build_approval_card ----
    def test_build_approval_card_structure(self):
        ch = self._make_channel()
        card = ch._build_approval_card("req_001", "terminal", "ls -la")
        assert card["header"]["title"]["content"] == "🔐 审批请求"
        assert card["header"]["template"] == "orange"
        elements = card["elements"]
        assert len(elements) == 2
        assert elements[0]["tag"] == "markdown"
        assert "req_001" in elements[0]["content"]
        assert "terminal" in elements[0]["content"]
        actions = elements[1]
        assert actions["tag"] == "action"
        assert len(actions["actions"]) == 2
        # approve button
        assert actions["actions"][0]["value"]["action"] == "approve"
        assert actions["actions"][0]["value"]["approval_id"] == "req_001"
        assert actions["actions"][0]["type"] == "primary"
        # reject button
        assert actions["actions"][1]["value"]["action"] == "reject"
        assert actions["actions"][1]["value"]["approval_id"] == "req_001"
        assert actions["actions"][1]["type"] == "danger"

    # ---- send_approval_card ----
    def test_send_approval_card_success(self):
        ch = self._make_channel()
        card_result = SendResult(success=True, msg_id="msg_001", platform="feishu")
        with patch.object(ch, 'send_card', return_value=card_result) as mock_send:
            result = ch.send_approval_card("req_001", "terminal", "ls -la", chat_id="chat_1")
            mock_send.assert_called_once()
            assert result.success is True
            assert "req_001" in ch._card_approval_state
            assert ch._card_msg_ids["req_001"] == "msg_001"

    def test_send_approval_card_failure(self):
        ch = self._make_channel()
        card_result = SendResult(success=False, platform="feishu", error="send failed")
        with patch.object(ch, 'send_card', return_value=card_result) as mock_send:
            result = ch.send_approval_card("req_002", "tool", "args")
            assert result.success is False
            assert "req_002" not in ch._card_approval_state

    def test_send_approval_card_without_msg_id(self):
        ch = self._make_channel()
        card_result = SendResult(success=True, platform="feishu")
        with patch.object(ch, 'send_card', return_value=card_result):
            ch.send_approval_card("req_003", "tool", "args")
            assert "req_003" in ch._card_approval_state

    # ---- wait_approval ----
    def test_wait_approval_without_state(self):
        ch = self._make_channel()
        result = ch.wait_approval("nonexistent", timeout=1)
        assert result is None

    def test_wait_approval_timeout(self):
        ch = self._make_channel()
        ch._card_approval_state["req_004"] = threading.Event()
        result = ch.wait_approval("req_004", timeout=0.1)
        assert result is None

    def test_wait_approval_with_callback(self):
        ch = self._make_channel()
        ev = threading.Event()

        def fire():
            import core.channel.feishu_ws as _mod
            for _cb in list(_mod.ON_CARD_APPROVAL_CBS):
                _cb("req_005", "approve")

        ch._card_approval_state["req_005"] = ev
        # Fire callback after short delay
        timer = threading.Timer(0.05, fire)
        timer.start()
        try:
            result = ch.wait_approval("req_005", timeout=5)
            assert result == "approve"
        finally:
            timer.cancel()

    # ---- _ws_loop (reconnect) ----
    def test_ws_loop_lark_not_installed(self):
        """When lark_oapi is not installed, _ws_loop should call sys.exit(1)."""
        ch = self._make_channel()
        ch._running = False  # ensure loop exits
        with patch('builtins.__import__', side_effect=ImportError("no lark")):
            with patch('sys.exit') as mock_exit:
                with patch.object(ch, '_get_tenant_token', return_value="tok"):
                    with patch('urllib.request.urlopen') as mock_urlopen:
                        mock_resp = MagicMock()
                        mock_resp.read.return_value = b'{"code": 0, "bot": {"open_id": "bot_001"}}'
                        mock_resp.__enter__.return_value = mock_resp
                        mock_urlopen.return_value = mock_resp
                        ch._ws_loop()
                        mock_exit.assert_called_once_with(1)

    def test_ws_loop_get_bot_info_success(self):
        """_ws_loop should fetch bot info."""
        ch = self._make_channel()
        ch._running = False  # exit immediately
        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0, "bot": {"open_id": "bot_open_001"}}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                with patch('builtins.__import__') as mock_import:
                    # Make lark_oapi importable
                    mock_lark = MagicMock()
                    mock_import.side_effect = lambda name, *args, **kw: {
                        'lark_oapi': mock_lark
                    }.get(name, __import__(name, *args, **kw))
                    ch._ws_loop()
                    # After exit, bot_open_id should be set
                    assert ch._bot_open_id == "bot_open_001"

    def test_ws_loop_get_bot_info_fails(self):
        """Bot info fetch failure should not crash."""
        ch = self._make_channel()
        ch._running = False
        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen', side_effect=Exception("API fail")):
                with patch('builtins.__import__') as mock_import:
                    mock_lark = MagicMock()
                    mock_import.side_effect = lambda name, *args, **kw: {
                        'lark_oapi': mock_lark
                    }.get(name, __import__(name, *args, **kw))
                    ch._ws_loop()
                    assert ch._bot_open_id == ""

    def test_ws_loop_bot_info_no_bot_in_response(self):
        ch = self._make_channel()
        ch._running = False
        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                with patch('builtins.__import__') as mock_import:
                    mock_lark = MagicMock()
                    mock_import.side_effect = lambda name, *args, **kw: {
                        'lark_oapi': mock_lark
                    }.get(name, __import__(name, *args, **kw))
                    ch._ws_loop()
                    assert ch._bot_open_id == ""

    def test_ws_loop_reconnect(self):
        """Verify reconnect logic: client.start() raises, loop retries."""
        ch = self._make_channel()
        call_count = [0]

        def side_effect_run():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("disconnected")
            ch._running = False  # stop after 3rd attempt

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
            with patch('builtins.__import__') as mock_import:
                mock_lark = MagicMock()
                mock_ws = MagicMock()
                mock_ws.Client.return_value = mock_lark.ws.Client.return_value
                # Mock EventDispatcherHandler chain
                mock_builder = MagicMock()
                mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
                mock_builder.register_p2_card_action_trigger.return_value = mock_builder
                mock_lark.ws.Client.return_value.start = side_effect_run
                mock_import.side_effect = lambda name, *args, **kw: {
                    'lark_oapi': mock_lark
                }.get(name, __import__(name, *args, **kw))

                # Patch EventDispatcherHandler too
                with patch('lark_oapi.event.dispatcher_handler.EventDispatcherHandler') as mock_dis:
                    mock_dis.builder.return_value = mock_builder
                    ch._ws_loop()
                    assert call_count[0] >= 2

    def test_ws_loop_normal_flow(self):
        """Happy path: lark client starts successfully and _running becomes False."""
        ch = self._make_channel()
        ch._running = False  # exit the while loop immediately after one attempt
        # Actually we want start to return normally (blocking call that returns when disconnected)
        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
            with patch('builtins.__import__') as mock_import:
                mock_lark = MagicMock()
                mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
                mock_lark.ws = MagicMock()
                mock_lark.ws.Client.return_value.start = MagicMock()
                mock_import.side_effect = lambda name, *args, **kw: {
                    'lark_oapi': mock_lark
                }.get(name, __import__(name, *args, **kw))
                with patch('lark_oapi.event.dispatcher_handler.EventDispatcherHandler') as mock_dis:
                    mock_builder = MagicMock()
                    mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
                    mock_builder.register_p2_card_action_trigger.return_value = mock_builder
                    mock_dis.builder.return_value = mock_builder
                    ch._running = True
                    # Make start() work once then stop
                    orig_start = MagicMock()
                    def _start_and_stop():
                        ch._running = False
                    orig_start.side_effect = _start_and_stop
                    mock_lark.ws.Client.return_value.start = orig_start
                    ch._ws_loop()
                    # Should complete without error

    # ---- on_message handler (inside _ws_loop) ----
    def test_ws_loop_on_message_handler(self):
        """Test the on_message closure registered in _ws_loop."""
        ch = self._make_channel()
        ch._running = False
        registered_handler = [None]

        def capture_handler(data):
            pass

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
            with patch('builtins.__import__') as mock_import:
                mock_lark = MagicMock()
                mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
                mock_lark.ws = MagicMock()
                mock_builder = MagicMock()
                # Capture the registered message handler
                def register_msg(fn):
                    registered_handler[0] = fn
                    return mock_builder
                def register_card(fn):
                    return mock_builder
                mock_builder.register_p2_im_message_receive_v1 = register_msg
                mock_builder.register_p2_card_action_trigger = register_card
                mock_builder.build.return_value = "handler"
                mock_dis = type('obj', (object,), {'builder': lambda s, a, b: mock_builder})()
                mock_import.side_effect = lambda name, *args, **kw: {
                    'lark_oapi': mock_lark,
                    'lark_oapi.event.dispatcher_handler': type('mod', (object,), {'EventDispatcherHandler': type('EDH', (object,), {'builder': staticmethod(lambda a, b: mock_builder)})}),
                }.get(name, __import__(name, *args, **kw))

                ch._ws_loop()
                handler_fn = registered_handler[0]
                assert handler_fn is not None

                # Test with non-text message → should skip
                class MockData:
                    event = type('evt', (object,), {
                        'message': type('msg', (object,), {
                            'message_type': 'image',
                            'content': '{}',
                        })()
                    })()
                handler_fn(MockData())
                assert ch.poll() == []

                # Test with valid text message
                class MockTextData:
                    event = type('evt', (object,), {
                        'message': type('msg', (object,), {
                            'message_type': 'text',
                            'content': '{"text": "hello from ws"}',
                            'chat_type': 'p2p',
                            'message_id': 'ws_msg_1',
                            'chat_id': 'ws_chat_1',
                            'sender': type('s', (object,), {'id': 'user_1'})(),
                            'mentions': [],
                            'create_time': str(int(time.time() * 1000)),
                        })()
                    })()
                handler_fn(MockTextData())
                msgs = ch.poll()
                assert len(msgs) == 1
                assert msgs[0].text == "hello from ws"

                # Test with dict data (not object)
                handler_fn({"event": {"message": {
                    "msg_type": "text",
                    "content": '{"text": "dict msg"}',
                    "chat_type": "p2p",
                    "message_id": "ws_msg_2",
                    "chat_id": "ws_chat_2",
                    "sender": {"id": "user_2"},
                    "mentions": [],
                    "create_time": str(int(time.time() * 1000)),
                }}})
                msgs = ch.poll()
                assert len(msgs) == 1
                assert msgs[0].text == "dict msg"

                # Test with missing message
                handler_fn({"event": {}})
                assert ch.poll() == []

                # Test with empty content
                class MockEmptyContent:
                    event = type('evt', (object,), {
                        'message': type('msg', (object,), {
                            'message_type': 'text',
                            'content': '',
                        })()
                    })()
                handler_fn(MockEmptyContent())
                assert ch.poll() == []

    # ---- on_card_action handler ----
    def test_ws_loop_on_card_action_handler(self):
        """Test the on_card_action closure registered in _ws_loop."""
        from core.channel.feishu_ws import ON_CARD_APPROVAL_CB
        ch = self._make_channel()
        ch._running = False
        registered_card_handler = [None]
        callback_results = []

        def capture_card_handler(data):
            pass

        def my_callback(aid, action):
            callback_results.append((aid, action))

        import core.channel.feishu_ws as feishu_mod
        feishu_mod.register_card_approval_cb(my_callback)

        try:
            with patch.object(ch, '_get_tenant_token', return_value="tok"):
                with patch('urllib.request.urlopen') as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = b'{"code": 0}'
                    mock_resp.__enter__.return_value = mock_resp
                    mock_urlopen.return_value = mock_resp
                with patch('builtins.__import__') as mock_import:
                    mock_lark = MagicMock()
                    mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
                    mock_lark.ws = MagicMock()
                    mock_builder = MagicMock()
                    def register_msg(fn):
                        return mock_builder
                    def register_card(fn):
                        registered_card_handler[0] = fn
                        return mock_builder
                    mock_builder.register_p2_im_message_receive_v1 = register_msg
                    mock_builder.register_p2_card_action_trigger = register_card
                    mock_builder.build.return_value = "handler"
                    mock_import.side_effect = lambda name, *args, **kw: {
                        'lark_oapi': mock_lark,
                        'lark_oapi.event.dispatcher_handler': type('mod', (object,), {'EventDispatcherHandler': type('EDH', (object,), {'builder': staticmethod(lambda a, b: mock_builder)})}),
                    }.get(name, __import__(name, *args, **kw))

                    ch._ws_loop()
                    card_fn = registered_card_handler[0]
                    assert card_fn is not None

                    # Test approve card action
                    class MockApproveEvent:
                        event = type('evt', (object,), {
                            'action': type('act', (object,), {
                                'value': {'approval_id': 'card_req_1', 'action': 'approve'}
                            })()
                        })()
                    card_fn(MockApproveEvent())
                    assert len(callback_results) >= 1
                    assert callback_results[-1] == ('card_req_1', 'approve')

                    # Test reject card action
                    class MockRejectEvent:
                        event = type('evt', (object,), {
                            'action': type('act', (object,), {
                                'value': {'approval_id': 'card_req_2', 'action': 'reject'}
                            })()
                        })()
                    card_fn(MockRejectEvent())
                    assert callback_results[-1] == ('card_req_2', 'reject')

                    # Test with no action
                    card_fn({"event": {}})
                    assert len(callback_results) == 2  # unchanged

                    # Test with dict data
                    card_fn({
                        "action": {"value": {"approval_id": "card_req_3", "action": "approve"}}
                    })
                    assert callback_results[-1] == ('card_req_3', 'approve')

                    # Test with approval_id saved in _card_msg_ids
                    ch._card_msg_ids["card_msg_1"] = "msg_v1"
                    class MockWithMsgId:
                        event = type('evt', (object,), {
                            'action': type('act', (object,), {
                                'value': {'approval_id': 'card_msg_1', 'action': 'approve'}
                            })()
                        })()
                    card_fn(MockWithMsgId())
                    assert callback_results[-1] == ('card_msg_1', 'approve')
        finally:
            feishu_mod.unregister_card_approval_cb(my_callback)
