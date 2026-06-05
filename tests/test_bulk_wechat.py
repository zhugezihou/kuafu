"""
Core test for WeChatILinkChannel — init, start, stop, send, poll, _handle_message, QR code.
"""
import json
import os
import time
import threading
from unittest.mock import patch, MagicMock, PropertyMock, call, ANY

import pytest

from core.channel.base import Message, SendResult


class TestWeChatILink:
    """Complete coverage for WeChatILinkChannel."""

    def _make_channel(self):
        from core.channel.wechat_ilink import WeChatILinkChannel
        with patch.object(WeChatILinkChannel, '_load_state'):
            ch = WeChatILinkChannel()
        return ch

    # ---- init ----
    def test_init_defaults(self):
        from core.channel.wechat_ilink import WeChatILinkChannel
        with patch('core.channel.wechat_ilink.Path') as MockPath:
            mock_path = MagicMock()
            mock_path.parent.parent.parent.__truediv__ = lambda s, x: MagicMock()
            MockPath.return_value = mock_path
            with patch.object(WeChatILinkChannel, '_load_state'):
                ch = WeChatILinkChannel()
                assert ch.name == "wechat"
                assert ch._running is False
                assert ch._bot_token == ""
                assert ch._bot_open_id == ""
                assert ch._inbox == []

    def test_name_property(self):
        ch = self._make_channel()
        assert ch.name == "wechat"

    def test_init_with_env_data_dir(self):
        from core.channel.wechat_ilink import WeChatILinkChannel
        with patch.dict(os.environ, {"WECHAT_ILINK_DATA_DIR": "/tmp/test_wechat"}, clear=True):
            with patch.object(WeChatILinkChannel, '_load_state'):
                ch = WeChatILinkChannel()
                assert str(ch._state_file) == "/tmp/test_wechat/wechat_ilink_state.json"

    # ---- start / stop ----
    def test_start_non_running(self):
        ch = self._make_channel()
        with patch.object(ch, '_run_loop') as mock_loop:
            ch.start()
            assert ch._running is True
            assert ch._thread is not None
            mock_loop.assert_called_once()

    def test_start_already_running(self):
        ch = self._make_channel()
        ch._running = True
        with patch.object(ch, '_run_loop') as mock_loop:
            ch.start()
            mock_loop.assert_not_called()

    def test_stop(self):
        ch = self._make_channel()
        ch._running = True
        with patch.object(ch, '_save_state') as mock_save:
            ch.stop()
            assert ch._running is False
            mock_save.assert_called_once()

    # ---- is_logged_in ----
    def test_is_logged_in_true(self):
        ch = self._make_channel()
        ch._bot_token = "some_token"
        assert ch.is_logged_in() is True

    def test_is_logged_in_false(self):
        ch = self._make_channel()
        assert ch.is_logged_in() is False

    # ---- send ----
    def test_send_not_logged_in(self):
        ch = self._make_channel()
        result = ch.send("hello", chat_id="user_1")
        assert result.success is False
        assert "未登录" in result.error

    def test_send_no_chat_id(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        result = ch.send("hello")
        assert result.success is False
        assert "chat_id" in result.error

    def test_send_success(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"errcode": 0}
            result = ch.send("hello", chat_id="user_1")
            assert result.success is True
            assert result.error == ""

    def test_send_with_context_token(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"errcode": 0}
            result = ch.send("hello", chat_id="user_1", context_token="ctx_001")
            assert result.success is True
            # Verify request body included context_token
            call_body = mock_req.call_args[0][1]
            assert call_body["msg"]["context_token"] == "ctx_001"

    def test_send_api_error(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"errcode": 1001, "errmsg": "rate limit"}
            result = ch.send("hello", chat_id="user_1")
            assert result.success is False
            assert "rate limit" in result.error

    # ---- poll ----
    def test_poll_returns_messages(self):
        ch = self._make_channel()
        msg = Message(text="test", platform="wechat")
        ch._inbox.append(msg)
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == "test"
        assert ch._inbox == []

    def test_poll_empty(self):
        ch = self._make_channel()
        assert ch.poll() == []

    # ---- _request ----
    def test_request_success(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"errcode": 0, "data": "ok"}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = ch._request("getupdates", {"buf": ""})
            assert result["errcode"] == 0

    def test_request_empty_response(self):
        ch = self._make_channel()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b''
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = ch._request("test", {})
            assert result["errcode"] == -1

    def test_request_http_error(self):
        ch = self._make_channel()
        from urllib.error import HTTPError
        with patch('urllib.request.urlopen', side_effect=HTTPError(
                "url", 403, "Forbidden", {}, None)):
            result = ch._request("test", {})
            assert "errcode" in result

    def test_request_network_error(self):
        ch = self._make_channel()
        with patch('urllib.request.urlopen', side_effect=Exception("timeout")):
            result = ch._request("test", {})
            assert result["errcode"] == -1

    def test_request_get_method(self):
        ch = self._make_channel()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"ok": true}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = ch._request("status", {}, method="GET")
            assert result["ok"] is True

    def test_request_without_token(self):
        """When _bot_token is empty, no auth headers."""
        ch = self._make_channel()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"ok": true}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = ch._request("test", {}, method="GET")
            assert result["ok"] is True

    # ---- get_qrcode_token ----
    def test_get_qrcode_token(self):
        ch = self._make_channel()
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"qrcode": "qrcode_token_abc"}
            token = ch.get_qrcode_token()
            assert token == "qrcode_token_abc"
            assert ch._last_qrcode_token == "qrcode_token_abc"

    def test_get_qrcode_token_empty(self):
        ch = self._make_channel()
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {}
            token = ch.get_qrcode_token()
            assert token == ""

    # ---- get_qrcode_img ----
    def test_get_qrcode_img(self):
        ch = self._make_channel()
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"qrcode_img_content": "https://qr.example.com/img", "qrcode": "tok"}
            img = ch.get_qrcode_img()
            assert img == "https://qr.example.com/img"
            assert ch._last_qrcode_token == "tok"

    def test_get_qrcode_img_fallback_to_token(self):
        ch = self._make_channel()
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"qrcode": "qr_tok"}
            img = ch.get_qrcode_img()
            assert img == "qr_tok"

    # ---- wait_for_login ----
    def test_wait_for_login_no_qrcode(self):
        ch = self._make_channel()
        with patch.object(ch, 'get_qrcode_img', return_value=""):
            result = ch.wait_for_login(timeout=1)
            assert result is False

    def test_wait_for_login_qrcode_token_missing(self):
        ch = self._make_channel()
        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            ch._last_qrcode_token = ""
            result = ch.wait_for_login(timeout=1)
            assert result is False

    def test_wait_for_login_direct_poll_success(self):
        """getupdates direct poll path succeeds."""
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request') as mock_req:
                # First call (with qrcode_token as bot_token) succeeds
                mock_req.side_effect = [
                    {"errcode": 0, "get_updates_buf": "buf_1"},
                    {"errcode": 0, "get_updates_buf": "buf_2", "messages": [
                        {"message_type": 1, "message_state": 2, "from_user_id": "u1", "item_list": []}
                    ]},
                ]
                with patch.object(ch, '_save_state'):
                    result = ch.wait_for_login(timeout=5)
                    assert result is True

    def test_wait_for_login_direct_poll_no_messages(self):
        """getupdates works but no messages → timeout."""
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request') as mock_req:
                mock_req.side_effect = [
                    {"errcode": 0, "get_updates_buf": "buf_1"},
                    {"errcode": 0, "get_updates_buf": "buf_2"},
                    {"errcode": 0, "get_updates_buf": "buf_3"},
                ]
                with patch.object(ch, '_save_state'):
                    result = ch.wait_for_login(timeout=1.5)
                    assert result is False

    def test_wait_for_login_with_bot_token_in_response(self):
        """getupdates returns bot_token field."""
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request') as mock_req:
                mock_req.side_effect = [
                    {"errcode": 0},
                    {"errcode": 0, "bot_token": "new_bot_token", "uin": "uin_001"},
                ]
                with patch.object(ch, '_save_state'):
                    result = ch.wait_for_login(timeout=5)
                    assert result is False  # no messages yet
                    assert ch._bot_token == "new_bot_token"
                    assert ch._uin == "uin_001"

    def test_wait_for_login_qrcode_status_confirmed(self):
        """Fallback qrcode_status path with 'confirmed' status."""
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        call_count = [0]

        def request_side_effect(endpoint, body, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"qrcode_img_content": "img", "qrcode": "qr_tok"}
            # First getupdates attempt fails
            if call_count[0] == 2:
                return {"errcode": -1}
            # Then qrcode status returns confirmed
            return {"status": "confirmed", "bot_token": "bot_tok", "uin": "uin_val",
                    "bot_open_id": "open_001", "ilink_bot_id": "ilink_001"}

        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request', side_effect=request_side_effect):
                with patch.object(ch, '_save_state'):
                    result = ch.wait_for_login(timeout=5)
                    assert result is True
                    assert ch._bot_token == "bot_tok"
                    assert ch._uin == "uin_val"
                    assert ch._bot_open_id == "open_001"

    def test_wait_for_login_scaned_then_confirmed(self):
        """Scanned status transitions to confirmed."""
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        call_count = [0]

        def request_side_effect(endpoint, body, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"qrcode_img_content": "img", "qrcode": "qr_tok"}
            if call_count[0] == 2:
                return {"errcode": -1}
            if call_count[0] == 3:
                return {"status": "scaned"}
            return {"status": "confirmed", "bot_token": "bt"}

        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request', side_effect=request_side_effect):
                with patch.object(ch, '_save_state'):
                    with patch('time.sleep'):
                        result = ch.wait_for_login(timeout=30)
                        assert result is True

    def test_wait_for_login_stopped_while_waiting(self):
        """_running becomes False during login wait."""
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        ch._running = True  # will be set to False

        def request_and_stop(endpoint, body, **kwargs):
            ch._running = False
            return {"status": "pending"}

        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request', side_effect=request_and_stop):
                with patch('time.sleep'):
                    result = ch.wait_for_login(timeout=30)
                    assert result is False

    def test_wait_for_login_timeout(self):
        ch = self._make_channel()
        ch._last_qrcode_token = "qr_tok"
        with patch.object(ch, 'get_qrcode_img', return_value="img_url"):
            with patch.object(ch, '_request', return_value={"status": "pending"}):
                with patch('time.sleep'):
                    result = ch.wait_for_login(timeout=0.5)
                    assert result is False

    # ---- _render_qrcode ----
    def test_render_qrcode_with_qrcode_lib(self):
        ch = self._make_channel()
        with patch('importlib.import_module') as mock_import:
            mock_qr = MagicMock()
            mock_import.return_value = mock_qr
            # Make qrcode import succeed
            import builtins
            orig_import = builtins.__import__

            def mock_import_func(name, *args, **kw):
                if name == 'qrcode':
                    mock_mod = MagicMock()
                    mock_qr_instance = MagicMock()
                    mock_mod.QRCode.return_value = mock_qr_instance
                    return mock_mod
                return orig_import(name, *args, **kw)

            with patch('builtins.__import__', side_effect=mock_import_func):
                # Should not raise
                with patch('sys.stdout'):
                    ch._render_qrcode("http://example.com/qr")

    def test_render_qrcode_fallback(self):
        ch = self._make_channel()
        with patch('builtins.__import__', side_effect=ImportError("no qrcode")):
            # Should use fallback
            with patch('builtins.print') as mock_print:
                ch._render_qrcode("http://example.com/qr")
                mock_print.assert_called()

    def test_render_qrcode_exception(self):
        ch = self._make_channel()
        with patch('builtins.__import__', side_effect=Exception("generic error")):
            with patch('builtins.print') as mock_print:
                ch._render_qrcode("http://example.com/qr")
                mock_print.assert_called()

    # ---- _handle_incoming ----
    def test_handle_incoming_text_message(self):
        ch = self._make_channel()
        msg_data = {
            "message_type": 1,
            "message_state": 2,
            "from_user_id": "user_001",
            "client_id": "msg_001",
            "context_token": "ctx_001",
            "item_list": [{"type": 1, "text_item": {"text": "hello world"}}],
        }
        ch._handle_incoming(msg_data)
        msgs = ch.poll()
        assert len(msgs) == 1
        assert msgs[0].text == "hello world"
        assert msgs[0].sender == "user_001"
        assert msgs[0].chat_id == "user_001"
        assert msgs[0].raw["context_token"] == "ctx_001"
        assert msgs[0].platform == "wechat"

    def test_handle_incoming_non_text_type(self):
        ch = self._make_channel()
        msg_data = {"message_type": 2, "message_state": 2, "from_user_id": "u1"}
        ch._handle_incoming(msg_data)
        assert ch.poll() == []

    def test_handle_incoming_wrong_state(self):
        ch = self._make_channel()
        msg_data = {"message_type": 1, "message_state": 1, "from_user_id": "u1"}
        ch._handle_incoming(msg_data)
        assert ch.poll() == []

    def test_handle_incoming_no_from_user(self):
        ch = self._make_channel()
        msg_data = {"message_type": 1, "message_state": 2, "from_user_id": ""}
        ch._handle_incoming(msg_data)
        assert ch.poll() == []

    def test_handle_incoming_no_text_item(self):
        ch = self._make_channel()
        msg_data = {
            "message_type": 1,
            "message_state": 2,
            "from_user_id": "u1",
            "item_list": [{"type": 2}],
        }
        ch._handle_incoming(msg_data)
        assert ch.poll() == []

    def test_handle_incoming_empty_text(self):
        ch = self._make_channel()
        msg_data = {
            "message_type": 1,
            "message_state": 2,
            "from_user_id": "u1",
            "item_list": [{"type": 1, "text_item": {"text": ""}}],
        }
        ch._handle_incoming(msg_data)
        assert ch.poll() == []

    def test_handle_incoming_with_approval_decision(self):
        """When text matches approval decision, it's handled by approval module."""
        ch = self._make_channel()
        with patch('core.channel.wechat_ilink.check_approval_decision', return_value={"action": "approve", "req_id": "abc"}):
            with patch('core.channel.wechat_ilink.handle_approval_decision', return_value="ok"):
                msg_data = {
                    "message_type": 1,
                    "message_state": 2,
                    "from_user_id": "u1",
                    "client_id": "msg_002",
                    "context_token": "ctx_002",
                    "item_list": [{"type": 1, "text_item": {"text": "1 abc"}}],
                }
                ch._handle_incoming(msg_data)
                # Should NOT add to inbox (handled by approval)
                assert ch.poll() == []

    def test_handle_incoming_exception(self):
        ch = self._make_channel()
        # Pass invalid data that causes exception
        with patch('logging.Logger.error') as mock_log:
            ch._handle_incoming(None)
            mock_log.assert_called()

    # ---- _save_state / _load_state ----
    def test_save_state(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        ch._uin = "uin"
        ch._bot_open_id = "open"
        ch._poll_buf = "buf"
        with patch.object(ch._state_file, 'write_text') as mock_write:
            with patch.object(ch._state_file, 'parent'):
                ch._save_state()
                mock_write.assert_called_once()

    def test_save_state_exception(self):
        ch = self._make_channel()
        with patch.object(ch._state_file, 'parent') as mock_parent:
            mock_parent.mkdir.side_effect = PermissionError("denied")
            with patch('logging.Logger.warning') as mock_warn:
                ch._save_state()
                mock_warn.assert_called()

    def test_load_state_file_exists(self):
        ch = self._make_channel()
        state_data = json.dumps({"bot_token": "tok1", "uin": "u1",
                                  "bot_open_id": "o1", "poll_buf": "buf1"})
        with patch.object(ch._state_file, 'exists', return_value=True):
            with patch.object(ch._state_file, 'read_text', return_value=state_data):
                ch._load_state()
                assert ch._bot_token == "tok1"
                assert ch._uin == "u1"
                assert ch._bot_open_id == "o1"
                assert ch._poll_buf == "buf1"

    def test_load_state_no_file(self):
        ch = self._make_channel()
        with patch.object(ch._state_file, 'exists', return_value=False):
            ch._load_state()
            assert ch._bot_token == ""

    def test_load_state_corrupted(self):
        ch = self._make_channel()
        with patch.object(ch._state_file, 'exists', return_value=True):
            with patch.object(ch._state_file, 'read_text', return_value='not json'):
                ch._load_state()
                assert ch._bot_token == ""

    # ---- _run_loop ----
    def test_run_loop_no_token(self):
        """_run_loop tries to login when no token."""
        ch = self._make_channel()
        ch._running = True
        with patch.object(ch, 'wait_for_login', return_value=False):
            ch._run_loop()
            assert ch._running is False

    def test_run_loop_with_token(self):
        """_run_loop with existing token fetches config and polls."""
        ch = self._make_channel()
        ch._bot_token = "tok"
        ch._running = True
        with patch.object(ch, '_fetch_config') as mock_fetch:
            with patch.object(ch, '_poll_loop') as mock_poll:
                ch._run_loop()
                mock_fetch.assert_called_once()
                mock_poll.assert_called_once()

    # ---- _fetch_config ----
    def test_fetch_config_success(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"errcode": 0, "config": "data"}
            ch._fetch_config()
            assert ch._config == {"errcode": 0, "config": "data"}

    def test_fetch_config_failure(self):
        ch = self._make_channel()
        ch._bot_token = "tok"
        with patch.object(ch, '_request') as mock_req:
            mock_req.return_value = {"errcode": 1001}
            ch._fetch_config()
            assert ch._config == {}

    # ---- _poll_loop ----
    def test_poll_loop_empty_result(self):
        ch = self._make_channel()
        ch._running = True
        ch._bot_token = "tok"
        run_count = [0]

        def stop_after_poll():
            run_count[0] += 1
            ch._running = False

        with patch.object(ch, '_request', return_value={"errcode": 0, "get_updates_buf": "new_buf", "msgs": []}):
            with patch('time.sleep', side_effect=stop_after_poll):
                ch._poll_loop()
                assert ch._poll_buf == "new_buf"

    def test_poll_loop_with_messages(self):
        ch = self._make_channel()
        ch._running = True
        ch._bot_token = "tok"
        messages = [{
            "message_type": 1, "message_state": 2,
            "from_user_id": "u1", "client_id": "m1",
            "item_list": [{"type": 1, "text_item": {"text": "hi"}}],
        }]

        def stop():
            ch._running = False

        with patch.object(ch, '_request', return_value={"errcode": 0, "get_updates_buf": "buf", "msgs": messages}):
            with patch('time.sleep', side_effect=lambda s: stop()):
                ch._poll_loop()
                msgs = ch.poll()
                assert len(msgs) == 1
                assert msgs[0].text == "hi"

    def test_poll_loop_token_expired(self):
        ch = self._make_channel()
        ch._running = True
        ch._bot_token = "tok"
        call_count = [0]

        def request_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"errcode": -14, "errmsg": "token expired"}
            return {"errcode": 0, "get_updates_buf": "buf", "msgs": []}

        def stop():
            ch._running = False

        # After token expired, wait_for_login will be called
        with patch.object(ch, '_request', side_effect=request_side_effect):
            with patch.object(ch, 'wait_for_login', return_value=False):
                with patch('time.sleep', side_effect=lambda s: stop()):
                    ch._poll_loop()
                    assert ch._bot_token == ""

    def test_poll_loop_token_expired_relogin_success(self):
        ch = self._make_channel()
        ch._running = True
        ch._bot_token = "tok"
        call_count = [0]

        def request_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"errcode": -14, "errmsg": "token expired", "get_updates_buf": ""}
            return {"errcode": 0, "get_updates_buf": "buf2", "msgs": []}

        def stop():
            ch._running = False

        with patch.object(ch, '_request', side_effect=request_side_effect):
            with patch.object(ch, 'wait_for_login', return_value=True):
                with patch('time.sleep', side_effect=lambda s: stop()):
                    ch._poll_loop()
                    # After successful relogin, it continues

    def test_poll_loop_other_error(self):
        ch = self._make_channel()
        ch._running = True
        ch._bot_token = "tok"

        def stop():
            ch._running = False

        with patch.object(ch, '_request', return_value={"errcode": 999, "errmsg": "other error"}):
            with patch('time.sleep', side_effect=lambda s: stop()):
                ch._poll_loop()

    def test_poll_loop_exception(self):
        ch = self._make_channel()
        ch._running = True
        ch._bot_token = "tok"

        def stop():
            ch._running = False

        with patch.object(ch, '_request', side_effect=Exception("network error")):
            with patch('time.sleep', side_effect=lambda s: stop()):
                ch._poll_loop()
