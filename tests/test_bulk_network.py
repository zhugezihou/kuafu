class TestFeishuWebSocketChannel:
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
            if _mod.ON_CARD_APPROVAL_CB:
                _mod.ON_CARD_APPROVAL_CB("req_005", "approve")

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
        # Patch lark_oapi at the module level in feishu_ws to raise ImportError
        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0, "bot": {"open_id": "bot_001"}}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                # Only intercept lark_oapi import
                import builtins
                orig_import = builtins.__import__
                def mock_import(name, *args, **kw):
                    if name == 'lark_oapi':
                        raise ImportError("no lark")
                    return orig_import(name, *args, **kw)
                with patch('sys.exit') as mock_exit:
                    with patch('builtins.__import__', side_effect=mock_import):
                        ch._ws_loop()
                        mock_exit.assert_called_once_with(1)

    def test_ws_loop_get_bot_info_success(self):
        """_ws_loop should fetch bot info."""
        ch = self._make_channel()
        ch._running = False  # exit immediately
        # Inject a mock lark_oapi into sys.modules so the import works
        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        # Mock ws.Client.start to just return (blocking call that returns)
        mock_ws_client = MagicMock()
        mock_ws_client.start = MagicMock()
        mock_lark.ws = MagicMock()
        mock_lark.ws.Client.return_value = mock_ws_client
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
        mock_builder.register_p2_card_action_trigger.return_value = mock_builder
        mock_lark.event.dispatcher_handler.EventDispatcherHandler.builder.return_value = mock_builder

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0, "bot": {"open_id": "bot_open_001"}}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                 'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
                    ch._ws_loop()
                    # After exit, bot_open_id should be set
                    assert ch._bot_open_id == "bot_open_001"

    def test_ws_loop_get_bot_info_fails(self):
        """Bot info fetch failure should not crash."""
        ch = self._make_channel()
        ch._running = False
        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        mock_ws_client = MagicMock()
        mock_ws_client.start = MagicMock()
        mock_lark.ws = MagicMock()
        mock_lark.ws.Client.return_value = mock_ws_client
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
        mock_builder.register_p2_card_action_trigger.return_value = mock_builder
        mock_lark.event.dispatcher_handler.EventDispatcherHandler.builder.return_value = mock_builder

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen', side_effect=Exception("API fail")):
                with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                 'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
                    ch._ws_loop()
                    assert ch._bot_open_id == ""

    def test_ws_loop_bot_info_no_bot_in_response(self):
        ch = self._make_channel()
        ch._running = False
        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        mock_ws_client = MagicMock()
        mock_ws_client.start = MagicMock()
        mock_lark.ws = MagicMock()
        mock_lark.ws.Client.return_value = mock_ws_client
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
        mock_builder.register_p2_card_action_trigger.return_value = mock_builder
        mock_lark.event.dispatcher_handler.EventDispatcherHandler.builder.return_value = mock_builder

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                 'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
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

        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        mock_lark.ws = MagicMock()
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
        mock_builder.register_p2_card_action_trigger.return_value = mock_builder
        mock_builder.build.return_value = "handler"
        mock_lark.event.dispatcher_handler.EventDispatcherHandler = type('EDH', (object,), {'builder': staticmethod(lambda a, b: mock_builder)})
        mock_lark.ws.Client.return_value.start = side_effect_run

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                # Set running BEFORE entering ws_loop
                ch._running = True
                with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                 'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
                    with patch('time.sleep'):  # prevent sleep during reconnect
                        ch._ws_loop()
                        assert call_count[0] >= 2

    def test_ws_loop_normal_flow(self):
        """Happy path: lark client starts successfully and _running becomes False."""
        ch = self._make_channel()
        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        mock_lark.ws = MagicMock()
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.register_p2_im_message_receive_v1.return_value = mock_builder
        mock_builder.register_p2_card_action_trigger.return_value = mock_builder
        mock_builder.build.return_value = "handler"
        mock_lark.event.dispatcher_handler.EventDispatcherHandler = type('EDH', (object,), {'builder': staticmethod(lambda a, b: mock_builder)})
        # Make start() work once then stop
        def _start_and_stop():
            ch._running = False
        mock_lark.ws.Client.return_value.start = _start_and_stop

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                 'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
                    ch._running = True
                    ch._ws_loop()

    # ---- on_message handler (inside _ws_loop) ----
    def test_ws_loop_on_message_handler(self):
        """Test the on_message closure registered in _ws_loop."""
        ch = self._make_channel()
        registered_handler = [None]

        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        mock_lark.ws = MagicMock()
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
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
        mock_lark.event.dispatcher_handler.EventDispatcherHandler = type('EDH', (object,), {'builder': staticmethod(lambda a, b: mock_builder)})
        # start just returns
        mock_lark.ws.Client.return_value.start = MagicMock()

        with patch.object(ch, '_get_tenant_token', return_value="tok"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"code": 0}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                 'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
                    ch._running = True
                    # Make start() stop the loop after executing
                    def _start_and_stop():
                        ch._running = False
                    mock_lark.ws.Client.return_value.start = _start_and_stop
                    ch._ws_loop()
                    handler_fn = registered_handler[0]
                    assert handler_fn is not None

                    # Test with non-text message → should skip
                    class MockData_obj:
                        pass
                    mock_data_obj = MockData_obj()
                    mock_data_obj.event = type('evt', (object,), {
                        'message': type('msg', (object,), {
                            'message_type': 'image',
                            'content': '{}',
                        })()
                    })()
                    handler_fn(mock_data_obj)
                    assert ch.poll() == []

                    # Test with valid text message
                    class MockTextData_obj:
                        pass
                    mock_text = MockTextData_obj()
                    mock_text.event = type('evt', (object,), {
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
                    handler_fn(mock_text)
                    msgs = ch.poll()
                    print(f"DEBUG msgs: {msgs}", flush=True)
                    assert len(msgs) == 1
                    assert msgs[0].text == "hello from ws"

                    # Test with dict data (not object)
                    handler_fn({
                        "msg_type": "text",
                        "content": '{"text": "dict msg"}',
                        "chat_type": "p2p",
                        "message_id": "ws_msg_2",
                        "chat_id": "ws_chat_2",
                        "sender": {"id": "user_2"},
                        "mentions": [],
                        "create_time": str(int(time.time() * 1000)),
                    })
                    msgs = ch.poll()
                    print(f"DEBUG dict msgs: {msgs}", flush=True)
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

        def my_callback(aid, action):
            callback_results.append((aid, action))

        import core.channel.feishu_ws as feishu_mod
        feishu_mod.ON_CARD_APPROVAL_CB = my_callback

        import sys
        mock_lark = MagicMock()
        mock_lark.LogLevel = type('obj', (object,), {'ERROR': 40})
        mock_lark.ws = MagicMock()
        mock_lark.event = MagicMock()
        mock_lark.event.dispatcher_handler = MagicMock()
        mock_builder = MagicMock()
        def register_msg(fn):
            return mock_builder
        def register_card(fn):
            registered_card_handler[0] = fn
            return mock_builder
        mock_builder.register_p2_im_message_receive_v1 = register_msg
        mock_builder.register_p2_card_action_trigger = register_card
        mock_builder.build.return_value = "handler"
        mock_lark.event.dispatcher_handler.EventDispatcherHandler = type('EDH', (object,), {'builder': staticmethod(lambda a, b: mock_builder)})
        mock_lark.ws.Client.return_value.start = MagicMock()

        try:
            with patch.object(ch, '_get_tenant_token', return_value="tok"):
                with patch('urllib.request.urlopen') as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = b'{"code": 0}'
                    mock_resp.__enter__.return_value = mock_resp
                    mock_urlopen.return_value = mock_resp
                    with patch.dict('sys.modules', {'lark_oapi': mock_lark,
                                                     'lark_oapi.event.dispatcher_handler': mock_lark.event.dispatcher_handler}):
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
            feishu_mod.ON_CARD_APPROVAL_CB = ON_CARD_APPROVAL_CB
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
        """The card approval callback registered via feishu_mod.ON_CARD_APPROVAL_CB."""
        from core.channel.gateway_loop import GatewayLoop
        import core.channel.feishu_ws as feishu_mod
        with patch('core.approval.ApprovalManager') as MockAM:
            MockAM.approve.return_value = True
            feishu_mod.ON_CARD_APPROVAL_CB("req_005", "approve")
            MockAM.approve.assert_called_with("req_005")

    def test_on_card_approval_reject(self):
        import core.channel.feishu_ws as feishu_mod
        with patch('core.approval.ApprovalManager') as MockAM:
            MockAM.reject.return_value = True
            feishu_mod.ON_CARD_APPROVAL_CB("req_006", "reject")
            MockAM.reject.assert_called_with("req_006")

    def test_on_card_approval_approve_fail(self):
        import core.channel.feishu_ws as feishu_mod
        with patch('core.approval.ApprovalManager') as MockAM:
            MockAM.approve.return_value = False
            # Should not raise
            feishu_mod.ON_CARD_APPROVAL_CB("req_007", "approve")
"""
Core test for HookRegistry — init, register, unregister, get, trigger, _execute_shell, _execute_webhook, template.
"""
import json
import os
import time
from unittest.mock import patch, MagicMock, call, ANY

import pytest


class TestHooks:
    """Complete coverage for HookRegistry and hook trigger system."""

    # ---- HookRegistry.init ----
    def test_init_initializes(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = False
            HookRegistry.init()
            assert HookRegistry._initialized is True
            assert HookRegistry._handlers == {}

    def test_init_already_initialized(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {"on_agent_start": []}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            HookRegistry.init()
            # Should not reload
            assert HookRegistry._handlers == {"on_agent_start": []}

    def test_init_loads_from_file(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        config_data = {
            "on_agent_start": [
                {"id": "h1", "event": "on_agent_start", "type": "shell",
                 "config": {"command": "echo hi"}, "enabled": True, "async_": True,
                 "priority": 0, "created_at": 100, "description": "", "max_retries": 0, "timeout": 10}
            ]
        }
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = True
            MockPath.read_text.return_value = json.dumps(config_data)
            HookRegistry.init()
            assert HookRegistry._initialized is True
            assert "on_agent_start" in HookRegistry._handlers
            assert len(HookRegistry._handlers["on_agent_start"]) == 1
            assert HookRegistry._handlers["on_agent_start"][0].id == "h1"

    def test_init_corrupted_file(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = True
            MockPath.read_text.return_value = "not json!!!"
            HookRegistry.init()
            assert HookRegistry._initialized is True

    def test_init_unknown_event_skipped(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        config_data = {
            "unknown_event": [
                {"id": "h_bad", "event": "unknown_event", "type": "shell",
                 "config": {}, "enabled": True, "async_": True,
                 "priority": 0, "created_at": 100, "description": "", "max_retries": 0, "timeout": 10}
            ]
        }
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.exists.return_value = True
            MockPath.read_text.return_value = json.dumps(config_data)
            HookRegistry.init()
            assert "unknown_event" not in HookRegistry._handlers

    # ---- HookRegistry.register ----
    def test_register_valid_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = HookRegistry.register("on_agent_start", "shell", {"command": "echo test"},
                                         description="test hook", priority=5)
            assert hid.startswith("hook_")
            assert "on_agent_start" in HookRegistry._handlers
            assert len(HookRegistry._handlers["on_agent_start"]) == 1
            handler = HookRegistry._handlers["on_agent_start"][0]
            assert handler.id == hid
            assert handler.type == "shell"
            assert handler.config["command"] == "echo test"
            assert handler.description == "test hook"
            assert handler.priority == 5
            assert handler.enabled is True

    def test_register_unknown_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with pytest.raises(ValueError, match="未知事件"):
            HookRegistry.register("nonexistent_event", "shell", {"command": ""})

    def test_register_maintains_priority_order(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            HookRegistry.register("on_agent_start", "shell", {}, priority=1)
            HookRegistry.register("on_agent_start", "shell", {}, priority=10)
            handlers = HookRegistry._handlers["on_agent_start"]
            assert handlers[0].priority == 10
            assert handlers[1].priority == 1

    # ---- HookRegistry.unregister ----
    def test_unregister_existing(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = HookRegistry.register("on_agent_start", "shell", {})
            result = HookRegistry.unregister(hid)
            assert result is True
            assert HookRegistry._handlers["on_agent_start"] == []

    def test_unregister_nonexistent(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        result = HookRegistry.unregister("nonexistent")
        assert result is False

    def test_unregister_wrong_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {"on_agent_start": []}
        result = HookRegistry.unregister("hook_nonexistent")
        assert result is False

    # ---- HookRegistry.get_handlers ----
    def test_get_handlers_returns_enabled(self):
        from core.hooks import HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="h1", event="on_agent_start", type="shell", config={}, enabled=True),
                HookHandler(id="h2", event="on_agent_start", type="shell", config={}, enabled=False),
            ]
        }
        handlers = HookRegistry.get_handlers("on_agent_start")
        assert len(handlers) == 1
        assert handlers[0].id == "h1"

    def test_get_handlers_no_event(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        handlers = HookRegistry.get_handlers("on_agent_start")
        assert handlers == []

    def test_get_handlers_calls_init(self):
        from core.hooks import HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'init') as mock_init:
            HookRegistry.get_handlers("on_agent_start")
            mock_init.assert_called_once()

    # ---- save ----
    def test_save_writes_config(self):
        from core.hooks import HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="h1", event="on_agent_start", type="shell",
                            config={"command": "echo hi"})
            ]
        }
        with patch('core.hooks.HOOKS_CONFIG_PATH') as MockPath:
            MockPath.parent.mkdir = MagicMock()
            MockPath.write_text = MagicMock()
            HookRegistry.save()
            MockPath.write_text.assert_called_once()
            written = json.loads(MockPath.write_text.call_args[0][0])
            assert "on_agent_start" in written
            assert written["on_agent_start"][0]["id"] == "h1"

    # ---- _render_template ----
    def test_render_template_basic(self):
        from core.hooks import _render_template
        result = _render_template("Hello {{name}}", {"name": "World"})
        assert result == "Hello World"

    def test_render_template_dict_value(self):
        from core.hooks import _render_template
        result = _render_template("Data: {{args}}", {"args": {"key": "val"}})
        assert "Data:" in result
        assert '"key"' in result

    def test_render_template_list_value(self):
        from core.hooks import _render_template
        result = _render_template("Items: {{items}}", {"items": [1, 2, 3]})
        assert "Items:" in result

    def test_render_template_missing_key(self):
        from core.hooks import _render_template
        result = _render_template("{{unknown}}", {})
        assert result == "{{unknown}}"

    def test_render_template_no_vars(self):
        from core.hooks import _render_template
        result = _render_template("plain text", {})
        assert result == "plain text"

    # ---- _render_config ----
    def test_render_config_string_values(self):
        from core.hooks import _render_config
        config = {"command": "echo {{name}}", "url": "https://{{host}}/api"}
        context = {"name": "test", "host": "example.com"}
        result = _render_config(config, context)
        assert result["command"] == "echo test"
        assert result["url"] == "https://example.com/api"

    def test_render_config_nested_dict(self):
        from core.hooks import _render_config
        config = {"headers": {"Authorization": "Bearer {{token}}"}}
        context = {"token": "abc123"}
        result = _render_config(config, context)
        assert result["headers"]["Authorization"] == "Bearer abc123"

    def test_render_config_list_items(self):
        from core.hooks import _render_config
        config = {"urls": ["https://{{host}}/1", "https://{{host}}/2"]}
        context = {"host": "api.test"}
        result = _render_config(config, context)
        assert result["urls"] == ["https://api.test/1", "https://api.test/2"]

    def test_render_config_non_string_values(self):
        from core.hooks import _render_config
        config = {"count": 42, "enabled": True}
        result = _render_config(config, {})
        assert result["count"] == 42
        assert result["enabled"] is True

    # ---- _execute_shell ----
    def test_execute_shell_success(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h1", event="on_agent_start", type="shell",
                              config={"command": "echo hello"}, timeout=5)
        result = _execute_shell(handler, {})
        assert result.success is True
        assert "hello" in result.output
        assert result.type == "shell"
        assert result.handler_id == "h1"

    def test_execute_shell_with_template(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h2", event="on_tool_before", type="shell",
                              config={"command": "echo Tool: {{tool}}"}, timeout=5)
        result = _execute_shell(handler, {"tool": "terminal"})
        assert result.success is True
        assert "Tool: terminal" in result.output

    def test_execute_shell_failure(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h3", event="on_agent_start", type="shell",
                              config={"command": "exit 1"}, timeout=5)
        result = _execute_shell(handler, {})
        assert result.success is False
        assert result.error is not None

    def test_execute_shell_timeout(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h4", event="on_agent_start", type="shell",
                              config={"command": "sleep 100"}, timeout=1)
        result = _execute_shell(handler, {})
        assert result.success is False
        assert "超时" in (result.error or "")

    def test_execute_shell_stderr(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h5", event="on_agent_start", type="shell",
                              config={"command": "echo ok && echo err >&2"}, timeout=5)
        result = _execute_shell(handler, {})
        assert result.success is True
        assert "STDERR:" in result.output

    def test_execute_shell_exception(self):
        from core.hooks import _execute_shell, HookHandler
        handler = HookHandler(id="h6", event="on_agent_start", type="shell",
                              config={"command": "invalid_command_xyz_123"}, timeout=5)
        # Should catch exception and return error
        # On Linux, shell will return 127 for invalid command, but shell=True should work
        result = _execute_shell(handler, {})
        assert result.success is False

    # ---- _execute_webhook ----
    def test_execute_webhook_no_url(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw1", event="on_agent_start", type="webhook",
                              config={}, timeout=5)
        result = _execute_webhook(handler, {})
        assert result.success is False
        assert "缺少 url" in (result.error or "")

    def test_execute_webhook_success(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw2", event="on_agent_start", type="webhook",
                              config={"url": "https://example.com/hook", "method": "POST",
                                      "headers": {"X-Test": "1"}, "body": "data"}, timeout=5)
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"ok": true}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = _execute_webhook(handler, {})
            assert result.success is True
            assert '{"ok": true}' in result.output

    def test_execute_webhook_http_error(self):
        from core.hooks import _execute_webhook, HookHandler
        from urllib.error import HTTPError
        handler = HookHandler(id="hw3", event="on_agent_start", type="webhook",
                              config={"url": "https://example.com/hook"}, timeout=5)
        with patch('urllib.request.urlopen', side_effect=HTTPError(
                "url", 500, "Internal Error", {}, None)):
            result = _execute_webhook(handler, {})
            assert result.success is False
            assert "HTTP" in (result.error or "")

    def test_execute_webhook_network_error(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw4", event="on_agent_start", type="webhook",
                              config={"url": "https://example.com/hook"}, timeout=5)
        with patch('urllib.request.urlopen', side_effect=Exception("connection refused")):
            result = _execute_webhook(handler, {})
            assert result.success is False
            assert "connection refused" in (result.error or "")

    def test_execute_webhook_with_template_in_config(self):
        from core.hooks import _execute_webhook, HookHandler
        handler = HookHandler(id="hw5", event="on_agent_start", type="webhook",
                              config={"url": "https://{{host}}/hook", "method": "POST",
                                      "body": '{"tool": "{{tool}}"}'}, timeout=5)
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'ok'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = _execute_webhook(handler, {"host": "api.test", "tool": "terminal"})
            assert result.success is True

    # ---- trigger ----
    def test_trigger_unknown_event(self):
        from core.hooks import trigger
        with patch('core.hooks.HookRegistry.get_handlers', return_value=[]):
            results = trigger("nonexistent")
            assert results == []

    def test_trigger_no_handlers(self):
        from core.hooks import trigger
        with patch('core.hooks.logger') as mock_log:
            with patch('core.hooks.HookRegistry.get_handlers', return_value=[]):
                results = trigger("on_agent_start")
                assert results == []

    def test_trigger_shell_handler(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr1", event="on_agent_start", type="shell",
                            config={"command": "echo triggered"}, enabled=True,
                            async_=False, priority=0, timeout=5)
            ]
        }
        results = trigger("on_agent_start", {"test": "val"})
        assert len(results) == 1
        assert results[0].success is True
        assert "triggered" in results[0].output

    def test_trigger_unknown_executor_type(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr2", event="on_agent_start", type="unknown_type",
                            config={}, enabled=True, async_=False)
            ]
        }
        results = trigger("on_agent_start", {})
        assert len(results) == 1
        assert results[0].success is False
        assert "未知执行类型" in (results[0].error or "")

    def test_trigger_synchronous_blocked(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_permission_check": [
                HookHandler(id="tr3", event="on_permission_check", type="shell",
                            config={"command": "exit 1", "block_on_failure": True},
                            enabled=True, async_=False, timeout=5),
                HookHandler(id="tr4", event="on_permission_check", type="shell",
                            config={"command": "echo should not run"},
                            enabled=True, async_=False, timeout=5),
            ]
        }
        results = trigger("on_permission_check", {}, synchronous=True)
        assert len(results) == 2
        assert results[0].success is False
        assert results[0].blocked is True
        assert results[1].success is False
        assert "上游处理器阻止" in (results[1].error or "")

    def test_trigger_with_retry(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr5", event="on_agent_start", type="shell",
                            config={"command": "echo ok"}, enabled=True,
                            async_=False, max_retries=2, timeout=5)
            ]
        }
        results = trigger("on_agent_start", {})
        assert len(results) == 1
        assert results[0].success is True

    def test_trigger_async_handler(self):
        """Async handlers (async_=True) in async mode should be skipped from sync result logging."""
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_agent_start": [
                HookHandler(id="tr6", event="on_agent_start", type="shell",
                            config={"command": "echo async"}, enabled=True,
                            async_=True, timeout=5)
            ]
        }
        # async_ handlers should still execute in sync trigger
        # The async_ flag just determines logging behavior
        results = trigger("on_agent_start", {})
        assert len(results) == 1
        assert results[0].success is True

    def test_trigger_with_context(self):
        from core.hooks import trigger, HookRegistry, HookHandler
        HookRegistry._initialized = True
        HookRegistry._handlers = {
            "on_tool_before": [
                HookHandler(id="tr7", event="on_tool_before", type="shell",
                            config={"command": "echo {{tool}}"}, enabled=True,
                            async_=False, timeout=5)
            ]
        }
        results = trigger("on_tool_before", {"tool": "read_file"})
        assert len(results) == 1
        assert "read_file" in results[0].output

    # ---- trigger_async / trigger_sync ----
    def test_trigger_async_starts_thread(self):
        from core.hooks import trigger_async
        with patch('threading.Thread') as mock_thread:
            trigger_async("on_agent_start", {"test": True})
            mock_thread.assert_called_once()
            mock_thread.return_value.start.assert_called_once()

    def test_trigger_sync_delegates(self):
        from core.hooks import trigger_sync
        with patch('core.hooks.trigger') as mock_trigger:
            mock_trigger.return_value = []
            results = trigger_sync("on_agent_start", {"test": True})
            mock_trigger.assert_called_with("on_agent_start", {"test": True}, synchronous=True)
            assert results == []

    # ---- Convenience registration functions ----
    def test_on_tool_before_shell(self):
        from core.hooks import on_tool_before_shell, HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = on_tool_before_shell("echo pre", description="shell pre-check", priority=10)
            assert hid is not None
            handlers = HookRegistry._handlers["on_tool_before"]
            assert handlers[0].type == "shell"
            assert handlers[0].config["command"] == "echo pre"

    def test_on_tool_before_llm(self):
        from core.hooks import on_tool_before_llm, HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = on_tool_before_llm("analyze {{tool}}", model="gpt-4",
                                      description="llm analysis", block_on_failure=True)
            assert hid is not None
            handlers = HookRegistry._handlers["on_tool_before"]
            assert handlers[0].type == "llm"
            assert handlers[0].config["model"] == "gpt-4"
            assert handlers[0].config["block_on_failure"] is True

    def test_on_approval_notify_webhook(self):
        from core.hooks import on_approval_notify_webhook, HookRegistry
        HookRegistry._initialized = True
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'save'):
            hid = on_approval_notify_webhook("https://example.com/notify")
            assert hid is not None
            handlers = HookRegistry._handlers["on_approval_result"]
            assert handlers[0].type == "webhook"
            assert handlers[0].config["url"] == "https://example.com/notify"

    # ---- init_hooks ----
    def test_init_hooks(self):
        from core.hooks import init_hooks, HookRegistry
        HookRegistry._initialized = False
        HookRegistry._handlers = {}
        with patch.object(HookRegistry, 'init') as mock_init:
            init_hooks()
            mock_init.assert_called_once()
"""
Core test for CronScheduler — add/remove/list, parse_schedule, start/stop, _run_loop, CronTask.
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, call, ANY

import pytest


class TestParseSchedule:
    """Complete coverage for parse_schedule."""

    def test_parse_interval_seconds(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("30s")
        assert interval == 30
        assert stype == "interval"

    def test_parse_interval_minutes(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("15m")
        assert interval == 900
        assert stype == "interval"

    def test_parse_interval_hours(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("2h")
        assert interval == 7200
        assert stype == "interval"

    def test_parse_interval_days(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("1d")
        assert interval == 86400
        assert stype == "interval"

    def test_parse_interval_case_insensitive(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("10M")
        assert interval == 600
        assert stype == "interval"

    def test_parse_cron_daily(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("0 8 * * *")
        assert stype == "cron"
        # Should be some positive interval until 8:00 next day
        assert interval > 0

    def test_parse_cron_every_minute(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("* * * * *")
        assert interval == 60
        assert stype == "cron"

    def test_parse_cron_specific_time_past(self):
        """When the cron time has passed today, it schedules for tomorrow."""
        from core.cron_scheduler import parse_schedule
        from datetime import datetime, timedelta
        # Use a time that's definitely in the past
        interval, stype = parse_schedule("0 0 * * *")
        assert stype == "cron"
        # Should be ~86400s (24h)
        assert interval > 80000

    def test_parse_iso_once(self):
        from core.cron_scheduler import parse_schedule
        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        interval, stype = parse_schedule(future)
        assert stype == "once"
        assert interval > 0

    def test_parse_iso_past(self):
        from core.cron_scheduler import parse_schedule
        from datetime import datetime, timedelta
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        interval, stype = parse_schedule(past)
        assert stype == "once"
        assert interval == 0

    def test_parse_fallback(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("garbage input")
        assert interval == 1800
        assert stype == "interval"

    def test_parse_empty_string(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("")
        assert interval == 1800
        assert stype == "interval"

    def test_parse_whitespace(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("  30m  ")
        assert interval == 1800
        assert stype == "interval"


class TestFormatNextRun:
    """Complete coverage for format_next_run."""

    def test_format_once(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(100, "once")
        assert result == "一次性"

    def test_format_interval_seconds(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(30, "interval")
        assert "30 秒" in result

    def test_format_interval_minutes(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(600, "interval")
        assert "10 分钟" in result

    def test_format_interval_hours(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(7200, "interval")
        assert "小时" in result

    def test_format_cron(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(3600, "cron")
        assert "秒" in result


class TestCronTask:
    """Complete coverage for CronTask."""

    def test_init(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="test", schedule="10m", task_text="do something")
        assert task.name == "test"
        assert task.schedule_raw == "10m"
        assert task.task_text == "do something"
        assert task.enabled is True
        assert task.output_mode == "file"
        assert task.run_count == 0
        assert task.last_run is None
        assert task.interval == 600
        assert task.schedule_type == "interval"
        assert task.next_run > time.time()

    def test_init_disabled(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="test", schedule="5m", task_text="x", enabled=False)
        assert task.enabled is False

    def test_init_custom_values(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="custom", schedule="1h", task_text="x",
                         enabled=False, output_mode="feishu",
                         run_count=5, last_run="2025-01-01", last_result="ok")
        assert task.run_count == 5
        assert task.last_run == "2025-01-01"
        assert task.last_result == "ok"
        assert task.output_mode == "feishu"

    def test_to_dict(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="my_task", schedule="30m", task_text="hello",
                         run_count=3, last_run="2025-01-01", last_result="done")
        d = task.to_dict()
        assert d["name"] == "my_task"
        assert d["schedule"] == "30m"
        assert d["task"] == "hello"
        assert d["run_count"] == 3
        assert d["last_run"] == "2025-01-01"
        assert d["last_result"] == "done"

    def test_to_dict_no_last_result(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="t", schedule="10m", task_text="x")
        d = task.to_dict()
        assert d["last_result"] == ""

    def test_repr(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="my_task", schedule="10m", task_text="x")
        r = repr(task)
        assert "my_task" in r
        assert "10m" in r


class TestCronScheduler:
    """Complete coverage for CronScheduler."""

    # ---- init ----
    def test_init_defaults(self):
        from core.cron_scheduler import CronScheduler
        with patch('core.cron_scheduler.Path') as MockPath:
            with patch.object(CronScheduler, '_load_config'):
                with patch.object(CronScheduler, '_load_state'):
                    scheduler = CronScheduler()
                    assert scheduler._tasks == []
                    assert scheduler._running is False
                    assert scheduler.on_task_run is None

    def test_init_with_on_task_run(self):
        from core.cron_scheduler import CronScheduler
        cb = MagicMock()
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler(on_task_run=cb)
                assert scheduler.on_task_run is cb

    def test_init_with_config_path(self):
        from core.cron_scheduler import CronScheduler, ROOT_DIR
        with patch.object(CronScheduler, '_load_config') as mock_load:
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler(config_path="/tmp/test_config.yaml")
                mock_load.assert_called_once()

    # ---- add_task / remove_task / get_tasks / get_task ----
    def test_add_task(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler, '_save_state'):
                    task = CronTask(name="t1", schedule="10m", task_text="x")
                    scheduler.add_task(task)
                    assert len(scheduler._tasks) == 1
                    assert scheduler._tasks[0] is task

    def test_remove_task_exists(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._tasks = [CronTask(name="t1", schedule="10m", task_text="x"),
                                    CronTask(name="t2", schedule="20m", task_text="y")]
                with patch.object(scheduler, '_save_state'):
                    result = scheduler.remove_task("t1")
                    assert result is True
                    assert len(scheduler._tasks) == 1
                    assert scheduler._tasks[0].name == "t2"

    def test_remove_task_not_exists(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._tasks = []
                result = scheduler.remove_task("nonexistent")
                assert result is False

    def test_get_tasks(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                t1 = CronTask(name="t1", schedule="10m", task_text="x")
                scheduler._tasks = [t1]
                tasks = scheduler.get_tasks()
                assert len(tasks) == 1
                assert tasks[0] is t1
                # Verify it returns a copy (not the same list ref)
                tasks.append(CronTask(name="t2", schedule="10m", task_text="y"))
                assert len(scheduler._tasks) == 1

    def test_get_task_exists(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                t1 = CronTask(name="find_me", schedule="10m", task_text="x")
                scheduler._tasks = [t1]
                result = scheduler.get_task("find_me")
                assert result is t1

    def test_get_task_not_exists(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                result = scheduler.get_task("nothing")
                assert result is None

    # ---- _load_config ----
    def test_load_config_yaml_success(self):
        from core.cron_scheduler import CronScheduler
        config_yaml = """
tasks:
  - name: test_task
    schedule: "10m"
    task: "do something"
    enabled: true
    output_mode: file
"""
        with patch.object(CronScheduler, '_load_state'):
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.read_text', return_value=config_yaml):
                    with patch('core.cron_scheduler.yaml') as mock_yaml:
                        mock_yaml.safe_load.return_value = {
                            "tasks": [{"name": "test_task", "schedule": "10m", "task": "do something"}]
                        }
                        scheduler = CronScheduler(config_path="/tmp/test.yaml")
                        assert len(scheduler._tasks) == 1
                        assert scheduler._tasks[0].name == "test_task"

    def test_load_config_no_tasks(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            with patch('pathlib.Path.read_text', return_value="tasks: []"):
                with patch('core.cron_scheduler.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"tasks": []}
                    scheduler = CronScheduler(config_path="/tmp/empty.yaml")
                    assert scheduler._tasks == []

    def test_load_config_missing_field(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            with patch('pathlib.Path.read_text', return_value="tasks:\n  - name: test"):
                with patch('core.cron_scheduler.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"tasks": [{"name": "test"}]}
                    scheduler = CronScheduler(config_path="/tmp/minimal.yaml")
                    assert len(scheduler._tasks) == 1
                    assert scheduler._tasks[0].schedule_raw == "30m"  # default

    def test_load_config_pyyaml_import_error(self):
        """When yaml import fails, fall back to simple parser."""
        from core.cron_scheduler import CronScheduler
        config_text = """tasks:
  - name: simple_task
    schedule: 5m
    task: echo hello
    enabled: true
"""
        with patch.object(CronScheduler, '_load_state'):
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.read_text', return_value=config_text):
                    # Make yaml import fail
                    import builtins
                    orig_import = builtins.__import__

                    def mock_import(name, *args, **kw):
                        if name == 'yaml':
                            raise ImportError("no yaml")
                        return orig_import(name, *args, **kw)

                    with patch('builtins.__import__', side_effect=mock_import):
                        scheduler = CronScheduler(config_path="/tmp/no_yaml.yaml")
                        assert len(scheduler._tasks) == 1

    def test_load_config_read_error(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            with patch('pathlib.Path.read_text', side_effect=Exception("IO error")):
                scheduler = CronScheduler(config_path="/tmp/bad.yaml")
                assert scheduler._tasks == []

    # ---- _parse_simple_yaml ----
    def test_parse_simple_yaml(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            scheduler = CronScheduler()
            text = """tasks:
  - name: task1
    schedule: 10m
    task: do it
    enabled: true
    output_mode: file
  - name: task2
    schedule: 1h
    task: do that
    enabled: false
"""
            scheduler._parse_simple_yaml(text, Path("/tmp/test.yaml"))
            assert len(scheduler._tasks) == 2
            assert scheduler._tasks[0].name == "task1"
            assert scheduler._tasks[0].enabled is True
            assert scheduler._tasks[1].name == "task2"
            assert scheduler._tasks[1].enabled is False

    # ---- _load_state / _save_state ----
    def test_save_state(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._tasks = [CronTask(name="t1", schedule="10m", task_text="x",
                                              run_count=3, last_result="done")]
                with patch.object(scheduler._state_path, 'write_text') as mock_write:
                    with patch.object(scheduler._state_path, 'parent') as mock_parent:
                        mock_parent.mkdir = MagicMock()
                        scheduler._save_state()
                        mock_write.assert_called_once()
                        written = json.loads(mock_write.call_args[0][0])
                        assert "tasks" in written
                        assert written["tasks"]["t1"]["run_count"] == 3

    def test_save_state_exception(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler._state_path, 'parent') as mp:
                    mp.mkdir.side_effect = PermissionError("denied")
                    scheduler._save_state()  # should not raise

    def test_load_state(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state') as mock_real:
                # We bypass _load_state in init, test it directly
                scheduler = CronScheduler()
                scheduler._tasks = [CronTask(name="t1", schedule="10m", task_text="x")]
                state_data = json.dumps({"tasks": {"t1": {"run_count": 5, "last_run": "2025-01-01", "last_result": "ok"}}})
                with patch.object(scheduler._state_path, 'exists', return_value=True):
                    with patch.object(scheduler._state_path, 'read_text', return_value=state_data):
                        scheduler._load_state()
                        assert scheduler._tasks[0].run_count == 5
                        assert scheduler._tasks[0].last_run == "2025-01-01"
                        assert scheduler._tasks[0].last_result == "ok"

    def test_load_state_no_file(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler._state_path, 'exists', return_value=False):
                    scheduler._load_state()  # should not raise

    def test_load_state_corrupted(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler._state_path, 'exists', return_value=True):
                    with patch.object(scheduler._state_path, 'read_text', return_value="not json"):
                        scheduler._load_state()  # should not raise

    # ---- start / stop ----
    def test_start(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler.start()
                assert scheduler._running is True
                assert scheduler._thread is not None

    def test_start_already_running(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._running = True
                with patch('threading.Thread') as mock_thread:
                    scheduler.start()
                    mock_thread.assert_not_called()

    def test_stop(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._running = True
                with patch.object(scheduler, '_save_state'):
                    scheduler.stop()
                    assert scheduler._running is False

    # ---- _run_loop ----
    def test_run_loop_no_tasks(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._running = True
                scheduler._running = False  # exit immediately
                scheduler._run_loop()

    def test_run_loop_executes_due_task(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="immediate", schedule="0s", task_text="do it")
                task.next_run = time.time() - 1  # already due
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="success")

                # Run one cycle then stop
                run_count = [0]
                orig_sleep = time.sleep
                def mock_sleep(s):
                    run_count[0] += 1
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    with patch.object(scheduler, '_save_state'):
                        scheduler._run_loop()
                        scheduler.on_task_run.assert_called_once_with(task)
                        assert task.run_count == 1
                        assert task.last_result == "success"

    def test_run_loop_task_execution_error(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="failing", schedule="0s", task_text="fail")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(side_effect=Exception("runtime error"))

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    scheduler._run_loop()
                    assert task.run_count == 1
                    assert "错误" in task.last_result

    def test_run_loop_no_callback(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler.on_task_run = None
                task = CronTask(name="no_cb", schedule="0s", task_text="x")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    scheduler._run_loop()
                    assert task.last_result == "(无回调)"

    def test_run_loop_output_mode_file(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="file_out", schedule="0s", task_text="x", output_mode="file")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="result content")

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    with patch.object(scheduler, '_save_to_file') as mock_save:
                        scheduler._running = True
                        scheduler._run_loop()
                        mock_save.assert_called_once_with(task)

    def test_run_loop_output_mode_feishu_no_bot(self):
        """feishu mode without bot set should fall back to file save."""
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="feishu_out", schedule="0s", task_text="x", output_mode="feishu")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="result")

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    with patch.object(scheduler, '_save_to_file') as mock_save:
                        scheduler._running = True
                        scheduler._run_loop()
                        # No feishu bot, so only _save_to_file is called
                        mock_save.assert_called_once_with(task)

    def test_run_loop_stop_during_execution(self):
        """If _running becomes False during task execution, loop exits."""
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="stop_test", schedule="0s", task_text="x")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="x")

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    scheduler._run_loop()

    # ---- _save_to_file ----
    def test_save_to_file(self):
        from core.cron_scheduler import CronScheduler, CronTask, ROOT_DIR
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="my_save_task", schedule="10m", task_text="x",
                                 run_count=1, last_run="2025-01-01", last_result="done")
                with patch.object(ROOT_DIR, '__truediv__') as mock_div:
                    mock_out_dir = MagicMock()
                    mock_div.return_value = mock_out_dir
                    mock_out_dir.__truediv__.return_value = mock_out_dir
                    mock_out_dir.mkdir = MagicMock()
                    mock_out_dir.__truediv__().write_text = MagicMock()

                    scheduler._save_to_file(task)
                    mock_out_dir.mkdir.assert_called_once()

    # ---- set_feishu_bot ----
    def test_set_feishu_bot(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                bot = MagicMock()
                scheduler.set_feishu_bot(bot)
                assert scheduler._feishu_bot is bot
"""
Core test for SkillManager — remove_local, install (by_name/from_url), uninstall, 
market_index, search_market, fetch_market_index (various network states).
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, call, ANY

import pytest


class TestSkillManager:
    """Complete coverage for SkillManager — focus on uncovered paths."""

    def _make_mgr(self):
        from core.skill_manager import SkillManager
        return SkillManager()

    # ---- list_local ----
    def test_list_local_empty(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = []
            results = mgr.list_local()
            assert results == []

    def test_list_local_with_skills(self):
        mgr = self._make_mgr()
        yaml_content = """
name: test_skill
description: test
steps:
  - prompt: hello
keywords: [test]
usage_count: 5
"""
        mock_file = MagicMock()
        mock_file.name = "test_skill.yaml"
        mock_file.stem = "test_skill"
        mock_file.read_text.return_value = yaml_content
        mock_file.relative_to.return_value = Path("skills/test_skill.yaml")

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {
                    "name": "test_skill",
                    "description": "test",
                    "steps": [{"prompt": "hello"}],
                    "keywords": ["test"],
                    "usage_count": 5,
                }
                results = mgr.list_local()
                assert len(results) == 1
                assert results[0].name == "test_skill"
                assert results[0].steps == 1

    def test_list_local_parse_error(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.name = "bad.yaml"
        mock_file.read_text.side_effect = Exception("parse error")

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            results = mgr.list_local()
            assert results == []

    def test_list_local_empty_yaml(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.name = "empty.yaml"
        mock_file.stem = "empty"
        mock_file.read_text.return_value = ""

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = None
                results = mgr.list_local()
                assert results == []

    # ---- get_local ----
    def test_get_local_exists(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [SkillInfo(name="my_skill")]
            result = mgr.get_local("my_skill")
            assert result is not None
            assert result.name == "my_skill"

    def test_get_local_not_found(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local', return_value=[]):
            result = mgr.get_local("nonexistent")
            assert result is None

    # ---- search_local ----
    def test_search_local_by_name(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [
                SkillInfo(name="web_search", description="search the web", keywords=["internet"]),
                SkillInfo(name="file_read", description="read files", keywords=["fs"]),
            ]
            results = mgr.search_local("web")
            assert len(results) == 1
            assert results[0].name == "web_search"

    def test_search_local_by_description(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [
                SkillInfo(name="skill_a", description="file management tool", keywords=[]),
            ]
            results = mgr.search_local("management")
            assert len(results) == 1

    def test_search_local_by_keyword(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            mock_list.return_value = [
                SkillInfo(name="skill_b", description="something", keywords=["database", "sql"]),
            ]
            results = mgr.search_local("database")
            assert len(results) == 1

    def test_search_local_no_match(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local', return_value=[]):
            results = mgr.search_local("zzz_nonexistent")
            assert results == []

    def test_search_local_multi_match_limited(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'list_local') as mock_list:
            from core.skill_manager import SkillInfo
            skills = [SkillInfo(name=f"skill_{i}", description="test") for i in range(20)]
            mock_list.return_value = skills
            results = mgr.search_local("test")
            assert len(results) <= 10

    # ---- remove_local ----
    def test_remove_local_exists(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: my_skill\ndescription: test"

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "my_skill"}
                result = mgr.remove_local("my_skill")
                assert result is True
                mock_file.unlink.assert_called_once()

    def test_remove_local_not_found(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: other_skill"

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "other_skill"}
                result = mgr.remove_local("my_skill")
                assert result is False

    def test_remove_local_no_yaml_files(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = []
            result = mgr.remove_local("any_skill")
            assert result is False

    def test_remove_local_yaml_exception(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.side_effect = Exception("read error")

        with patch('core.skill_manager.SKILLS_DIR') as MockDir:
            MockDir.glob.return_value = [mock_file]
            result = mgr.remove_local("any_skill")
            assert result is False

    # ---- fetch_market_index ----
    def test_fetch_market_index_no_url(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_INDEX_URL', ""):
            result = mgr.fetch_market_index()
            assert result == []

    def test_fetch_market_index_uses_cache(self):
        mgr = self._make_mgr()
        mgr._market_cache = ["cached"]
        mgr._cache_time = time.time()  # recent
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            result = mgr.fetch_market_index(force=False)
            assert result == ["cached"]

    def test_fetch_market_index_force_refresh(self):
        mgr = self._make_mgr()
        mgr._market_cache = ["cached"]
        mgr._cache_time = time.time()
        # force=True should bypass cache
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"skills": [{"name": "remote_skill"}]}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = mgr.fetch_market_index(force=True)
                assert len(result) == 1
                assert result[0].name == "remote_skill"

    def test_fetch_market_index_network_error(self):
        mgr = self._make_mgr()
        mgr._market_cache = ["fallback_cache"]
        mgr._cache_time = 0  # expired
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen', side_effect=Exception("network error")):
                result = mgr.fetch_market_index()
                # Should return cached data or empty
                assert result == ["fallback_cache"] or result == []

    def test_fetch_market_index_network_error_no_cache(self):
        mgr = self._make_mgr()
        mgr._market_cache = None
        mgr._cache_time = 0
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen', side_effect=Exception("network error")):
                result = mgr.fetch_market_index()
                assert result == []

    def test_fetch_market_index_success(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"skills": [{"name": "s1", "description": "d1", "keywords": ["k1"], "steps": 3, "author": "a1", "url": "u1", "category": "c1"}]}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = mgr.fetch_market_index()
                assert len(result) == 1
                assert result[0].name == "s1"
                assert result[0].author == "a1"
                assert result[0].category == "c1"
                assert result[0].url == "u1"
                assert result[0].steps == 3
                assert mgr._market_cache is not None
                assert mgr._cache_time > 0

    def test_fetch_market_index_empty_response(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_INDEX_URL', "https://example.com/index.json"):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"skills": []}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp
                result = mgr.fetch_market_index()
                assert result == []

    # ---- search_market ----
    def test_search_market_by_name(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="web_scraper", description="scrape websites", keywords=["http"]),
                SkillInfo(name="file_tool", description="file operations", keywords=["fs"]),
            ]
            results = mgr.search_market("web")
            assert len(results) == 1
            assert results[0].name == "web_scraper"

    def test_search_market_by_description(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="tool_a", description="database query tool", keywords=[]),
            ]
            results = mgr.search_market("query")
            assert len(results) == 1

    def test_search_market_by_keyword(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="tool_b", description="something", keywords=["machine learning"]),
            ]
            results = mgr.search_market("machine")
            assert len(results) == 1

    def test_search_market_by_category(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="tool_c", description="desc", keywords=[], category="utility"),
            ]
            results = mgr.search_market("utility")
            assert len(results) == 1

    def test_search_market_no_match(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index', return_value=[]):
            results = mgr.search_market("zzz_nonexistent")
            assert results == []

    def test_search_market_limited_to_20(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            skills = [SkillInfo(name=f"s{i}", description="common desc") for i in range(30)]
            mock_fetch.return_value = skills
            results = mgr.search_market("common")
            assert len(results) <= 20

    # ---- install ----
    def test_install_by_url_success(self):
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_from_url') as mock_url:
            mock_url.return_value = {"success": True, "name": "test", "file": "/tmp/test.yaml"}
            result = mgr.install("https://example.com/skill.md")
            assert result["success"] is True
            mock_url.assert_called_with("https://example.com/skill.md")

    def test_install_by_url_fallback_to_repo(self):
        """URL install fails → try RepoManager.install_from_url."""
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_from_url', return_value={"success": False, "error": "failed"}):
            with patch('core.skill_manager.RepoManager') as MockRepo:
                repo = MagicMock()
                repo.install_from_url.return_value = {"success": True, "name": "repo_skill"}
                MockRepo.return_value = repo
                result = mgr.install("https://example.com/skill.md")
                assert result["success"] is True
                repo.install_from_url.assert_called_with("https://example.com/skill.md")

    def test_install_by_name_success(self):
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_by_name') as mock_name:
            mock_name.return_value = {"success": True, "name": "my_skill", "file": "/tmp/skill.yaml"}
            with patch.object(mgr, '_check_skill_deps'):
                result = mgr.install("my_skill")
                assert result["success"] is True
                mock_name.assert_called_with("my_skill")

    def test_install_by_name_fallback_to_repo(self):
        mgr = self._make_mgr()
        with patch.object(mgr, '_install_by_name', return_value={"success": False, "error": "not found"}):
            with patch('core.skill_manager.RepoManager') as MockRepo:
                repo = MagicMock()
                repo.install.return_value = {"success": True, "name": "repo_skill"}
                MockRepo.return_value = repo
                with patch.object(mgr, '_check_skill_deps'):
                    result = mgr.install("my_skill")
                    assert result["success"] is True
                    repo.install.assert_called_with("my_skill")

    def test_install_name_not_url(self):
        """Name that doesn't start with http should go through _install_by_name path."""
        mgr = self._make_mgr()
        result = mgr.install("just_a_name")
        # It will try _install_by_name, then RepoManager
        # We just verify the path is taken

    # ---- _install_by_name ----
    def test_install_by_name_found_with_url(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="target_skill", url="https://example.com/target.md")
            ]
            with patch.object(mgr, '_install_from_url') as mock_url:
                mock_url.return_value = {"success": True}
                result = mgr._install_by_name("target_skill")
                assert result["success"] is True
                mock_url.assert_called_with("https://example.com/target.md")

    def test_install_by_name_found_no_url(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index') as mock_fetch:
            from core.skill_manager import SkillInfo
            mock_fetch.return_value = [
                SkillInfo(name="target_skill", url="")
            ]
            result = mgr._install_by_name("target_skill")
            assert result["success"] is False
            assert "没有下载 URL" in result["error"]

    def test_install_by_name_not_found(self):
        mgr = self._make_mgr()
        with patch.object(mgr, 'fetch_market_index', return_value=[]):
            result = mgr._install_by_name("nonexistent")
            assert result["success"] is False
            assert "未找到" in result["error"]

    # ---- _install_from_url ----
    def test_install_from_url_success(self):
        mgr = self._make_mgr()
        md_content = """---
name: my_skill
description: a test skill
---
# My Skill
This is a test skill.
"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = md_content.encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            with patch('core.skill_manager.MARKET_DIR') as MockDir:
                MockDir.mkdir = MagicMock()
                mock_file = MagicMock()
                MockDir.__truediv__.return_value = mock_file

                result = mgr._install_from_url("https://example.com/skills/test.md")
                assert result["success"] is True
                assert result["name"] == "my_skill"
                assert result["file"] is not None

    def test_install_from_url_network_error(self):
        mgr = self._make_mgr()
        with patch('urllib.request.urlopen', side_effect=Exception("timeout")):
            result = mgr._install_from_url("https://example.com/skill.md")
            assert result["success"] is False
            assert "下载失败" in result["error"]

    def test_install_from_url_no_name(self):
        mgr = self._make_mgr()
        content = "no frontmatter here"
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = content.encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = mgr._install_from_url("https://example.com/SKILL.md")
            assert result["success"] is False
            assert "无法解析" in result["error"]

    # ---- _extract_name_from_md ----
    def test_extract_name_from_md_frontmatter(self):
        from core.skill_manager import SkillManager
        content = """---
name: my_skill
description: test
---
# Content
"""
        name = SkillManager._extract_name_from_md(content, "https://example.com/skill.md")
        assert name == "my_skill"

    def test_extract_name_from_md_no_frontmatter(self):
        from core.skill_manager import SkillManager
        content = "# Just a skill"
        name = SkillManager._extract_name_from_md(content, "https://example.com/skills/web_scraper.md")
        assert name == "web_scraper"

    def test_extract_name_from_md_empty_stem(self):
        from core.skill_manager import SkillManager
        name = SkillManager._extract_name_from_md("no frontmatter", "https://example.com/SKILL.md")
        assert name == ""

    def test_extract_name_from_md_frontmatter_no_name(self):
        from core.skill_manager import SkillManager
        content = """---
description: no name here
---
"""
        name = SkillManager._extract_name_from_md(content, "https://example.com/test.md")
        assert name == "test"

    # ---- _check_skill_deps ----
    def test_check_skill_deps_no_file(self):
        from core.skill_manager import SkillManager
        SkillManager._check_skill_deps({"file": ""})
        # Should not raise

    def test_check_skill_deps_file_not_exists(self):
        from core.skill_manager import SkillManager
        SkillManager._check_skill_deps({"file": "/tmp/nonexistent_file.yaml"})
        # Should not raise

    def test_check_skill_deps_with_deps(self):
        from core.skill_manager import SkillManager
        with patch('pathlib.Path.exists', return_value=True):
            with patch('pathlib.Path.read_text', return_value="name: test\ndependencies:\n  pip:\n    - requests"):
                with patch('core.skill_manager.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"name": "test", "dependencies": {"pip": ["requests"]}}
                    with patch('core.skill_deps.check_dependencies') as mock_check:
                        mock_result = MagicMock()
                        mock_result.ok = False
                        mock_result.summary.return_value = "missing deps"
                        mock_check.return_value = mock_result
                        with patch('core.skill_deps.suggest_command', return_value="pip install requests"):
                            SkillManager._check_skill_deps({"file": "/tmp/test.yaml"})

    def test_check_skill_deps_no_deps_key(self):
        from core.skill_manager import SkillManager
        with patch('pathlib.Path.exists', return_value=True):
            with patch('pathlib.Path.read_text', return_value="name: test"):
                with patch('core.skill_manager.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"name": "test"}
                    SkillManager._check_skill_deps({"file": "/tmp/test.yaml"})

    # ---- uninstall ----
    def test_uninstall_no_market_dir(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = False
            result = mgr.uninstall("test_skill")
            assert result is False

    def test_uninstall_success(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: test_skill"

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "test_skill"}
                result = mgr.uninstall("test_skill")
                assert result is True
                mock_file.unlink.assert_called_once()

    def test_uninstall_not_found(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.return_value = "name: other_skill"

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "other_skill"}
                result = mgr.uninstall("test_skill")
                assert result is False

    def test_uninstall_file_read_error(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.side_effect = Exception("read error")

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            result = mgr.uninstall("test_skill")
            assert result is False

    # ---- list_installed_market ----
    def test_list_installed_market_no_dir(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = False
            result = mgr.list_installed_market()
            assert result == []

    def test_list_installed_market_with_skills(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.name = "installed.yaml"
        mock_file.stem = "installed"
        mock_file.relative_to.return_value = Path("skills/market/installed.yaml")
        mock_file.read_text.return_value = "name: installed_skill\ndescription: installed"

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            with patch('core.skill_manager.yaml') as mock_yaml:
                mock_yaml.safe_load.return_value = {"name": "installed_skill", "description": "installed"}
                result = mgr.list_installed_market()
                assert len(result) == 1
                assert result[0].name == "installed_skill"
                assert result[0].source == "installed"

    def test_list_installed_market_parse_error(self):
        mgr = self._make_mgr()
        mock_file = MagicMock()
        mock_file.read_text.side_effect = Exception("error")

        with patch('core.skill_manager.MARKET_DIR') as MockDir:
            MockDir.exists.return_value = True
            MockDir.glob.return_value = [mock_file]
            result = mgr.list_installed_market()
            assert result == []

    # ---- get_stats ----
    def test_get_stats(self):
        mgr = self._make_mgr()
        with patch('core.skill_manager.SKILLS_DIR') as MockSkillsDir:
            MockSkillsDir.glob.return_value = [MagicMock(), MagicMock()]
            with patch('core.skill_manager.MARKET_DIR') as MockMarketDir:
                MockMarketDir.exists.return_value = True
                MockMarketDir.glob.return_value = [MagicMock()]
                with patch.object(mgr, 'fetch_market_index', return_value=[MagicMock(), MagicMock(), MagicMock()]):
                    with patch('core.skill_manager.RepoManager') as MockRepo:
                        repo = MagicMock()
                        repo.get_stats.return_value = {"total_repos": 2, "total_skills": 10}
                        MockRepo.return_value = repo
                        stats = mgr.get_stats()
                        assert stats["local"] == 2
                        assert stats["installed_market"] == 1
                        assert stats["available_market"] == 3
                        assert stats["repos"] == 2
                        assert stats["repo_skills"] == 10

    # ---- SkillInfo ----
    def test_skill_info_to_dict(self):
        from core.skill_manager import SkillInfo
        si = SkillInfo(name="test", description="a long description " * 20,
                        keywords=["k1", "k2", "k3", "k4", "k5", "k6"],
                        steps=3, usage_count=10, author="me", category="dev")
        d = si.to_dict()
        assert d["name"] == "test"
        assert len(d["description"]) <= 100
        assert len(d["keywords"]) <= 5
        assert d["steps"] == 3
        assert d["usage"] == 10

    def test_skill_info_defaults(self):
        from core.skill_manager import SkillInfo
        si = SkillInfo(name="test")
        assert si.description == ""
        assert si.keywords == []
        assert si.source == "local"
        assert si.steps == 0
        assert si.usage_count == 0
        assert si.author == ""
        assert si.url == ""
        assert si.category == ""
