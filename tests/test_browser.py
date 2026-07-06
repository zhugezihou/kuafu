"""测试 core/browser.py — 浏览器工具。"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestGetBrowser:
    """_get_browser / _ensure_browser 测试。"""

    def _reset_globals(self):
        import core.browser
        core.browser._playwright_instance = None
        core.browser._browser = None
        core.browser._page = None
        core.browser._last_active = 0

    def test_get_browser_none(self):
        """无浏览器时返回 (None, None)。"""
        self._reset_globals()
        from core.browser import _get_browser
        assert _get_browser() == (None, None)

    def test_get_browser_healthy(self):
        """浏览器健康时返回。"""
        self._reset_globals()
        import core.browser
        mock_page = MagicMock()
        mock_browser = MagicMock()
        core.browser._browser = mock_browser
        core.browser._page = mock_page
        core.browser._last_active = 9999999999  # far in future

        from core.browser import _get_browser
        b, p = _get_browser()
        assert b is mock_browser
        assert p is mock_page

    def test_get_browser_timeout(self):
        """超时后清理并返回 (None, None)。"""
        self._reset_globals()
        import core.browser
        mock_page = MagicMock()
        mock_browser = MagicMock()
        core.browser._browser = mock_browser
        core.browser._page = mock_page
        core.browser._last_active = 0  # long ago

        with patch("core.browser.time.time", return_value=999999):
            from core.browser import _get_browser
            b, p = _get_browser()
            assert b is None
            assert p is None

    def test_get_browser_broken(self):
        """浏览器异常时清理。"""
        self._reset_globals()
        import core.browser
        mock_page = MagicMock()
        mock_page.title.side_effect = Exception("broken")
        core.browser._browser = MagicMock()
        core.browser._page = mock_page
        core.browser._last_active = 9999999999

        from core.browser import _get_browser
        with patch("core.browser._cleanup") as mock_cleanup:
            b, p = _get_browser()
            mock_cleanup.assert_called_once()

    def test_ensure_browser_existing(self):
        """已有浏览器时直接返回。"""
        self._reset_globals()
        import core.browser
        core.browser._browser = MagicMock()
        core.browser._page = MagicMock()
        core.browser._last_active = 9999999999

        from core.browser import _ensure_browser
        b, p = _ensure_browser()
        assert b is not None

    def test_ensure_browser_start(self):
        """启动新浏览器。"""
        self._reset_globals()
        mock_playwright_instance = MagicMock()
        mock_chromium = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        from core.browser import _ensure_browser
        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.start.return_value = mock_playwright_instance
            b, p = _ensure_browser()
            assert b is not None

    def test_ensure_browser_start_failure(self):
        """启动失败。"""
        self._reset_globals()
        from core.browser import _ensure_browser
        with patch("playwright.sync_api.sync_playwright", side_effect=Exception("no playwright")):
            b, p = _ensure_browser()
            assert b is None


class TestCleanup:
    """_cleanup 测试。"""

    def _reset_globals(self):
        import core.browser
        core.browser._playwright_instance = None
        core.browser._browser = None
        core.browser._page = None

    def test_cleanup_with_objects(self):
        self._reset_globals()
        import core.browser
        core.browser._page = MagicMock()
        core.browser._browser = MagicMock()
        core.browser._playwright_instance = MagicMock()
        from core.browser import _cleanup
        _cleanup()
        assert core.browser._page is None
        assert core.browser._browser is None

    def test_cleanup_none(self):
        self._reset_globals()
        from core.browser import _cleanup
        _cleanup()  # no error

    def test_cleanup_page_close_exception(self):
        """_page.close() 抛出异常时走 except Exception: pass (L110-111)。"""
        self._reset_globals()
        import core.browser
        mock_page = MagicMock()
        mock_page.close.side_effect = Exception("close failed")
        core.browser._page = mock_page
        core.browser._browser = MagicMock()
        core.browser._playwright_instance = MagicMock()
        from core.browser import _cleanup
        _cleanup()
        assert core.browser._page is None

    def test_cleanup_browser_close_exception(self):
        """_browser.close() 抛出异常时走 except Exception: pass (L115-116)。"""
        self._reset_globals()
        import core.browser
        core.browser._page = MagicMock()
        mock_browser = MagicMock()
        mock_browser.close.side_effect = Exception("close failed")
        core.browser._browser = mock_browser
        core.browser._playwright_instance = MagicMock()
        from core.browser import _cleanup
        _cleanup()
        assert core.browser._browser is None

    def test_cleanup_playwright_stop_exception(self):
        """_playwright_instance.stop() 抛出异常时走 except Exception: pass (L120-121)。"""
        self._reset_globals()
        import core.browser
        core.browser._page = MagicMock()
        core.browser._browser = MagicMock()
        mock_pw = MagicMock()
        mock_pw.stop.side_effect = Exception("stop failed")
        core.browser._playwright_instance = mock_pw
        from core.browser import _cleanup
        _cleanup()
        assert core.browser._playwright_instance is None


class TestManageScreenshots:
    """_manage_screenshots 测试。"""

    def test_under_limit(self):
        import core.browser
        core.browser.SCREENSHOTS_DIR = MagicMock()
        core.browser.SCREENSHOTS_DIR.glob.return_value = ["a.png", "b.png"]
        from core.browser import _manage_screenshots
        _manage_screenshots()  # no error

    def test_over_limit(self):
        import core.browser
        core.browser.SCREENSHOTS_DIR = MagicMock()
        from pathlib import Path
        import tempfile
        tmpdir = tempfile.mkdtemp()
        try:
            for i in range(60):
                (Path(tmpdir) / f"file_{i:03d}.png").write_text("")
            core.browser.SCREENSHOTS_DIR = Path(tmpdir)
            from core.browser import _manage_screenshots
            _manage_screenshots()
            remaining = len(list(Path(tmpdir).glob("*.png")))
            assert remaining <= 50
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_over_limit_unlink_exception(self):
        """删除截图抛出异常时走 except Exception: break (L132-136)。"""
        import core.browser
        from pathlib import Path
        import tempfile
        tmpdir = tempfile.mkdtemp()
        try:
            for i in range(55):
                (Path(tmpdir) / f"file_{i:03d}.png").write_text("")
            core.browser.SCREENSHOTS_DIR = Path(tmpdir)
            from core.browser import _manage_screenshots
            # Mock unlink to fail on the first call to trigger except Exception: break
            original_unlink = Path.unlink
            call_count = [0]
            def _failing_unlink(self, *a, **kw):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise PermissionError("permission denied")
                return original_unlink(self, *a, **kw)
            with patch("pathlib.Path.unlink", _failing_unlink):
                _manage_screenshots()
            # Should have broken out of the loop after first failure
            remaining = len(list(Path(tmpdir).glob("*.png")))
            assert remaining > 50  # not all excess were removed
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestExtractSnapshot:
    """_extract_snapshot 测试（mock page.evaluate）。"""

    def test_full_mode(self):
        """完整模式返回 body 文本。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "Page body text"
        result = _extract_snapshot(mock_page, full=True)
        assert "Page body text" in result

    def test_compact_mode_with_elements(self):
        """紧凑模式返回交互元素。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        # First evaluate call: get interactive elements
        # Second call: get title
        # Third call: get url
        mock_page.evaluate.side_effect = [
            [{"id": 1, "tag": "a", "text": "Click me", "href": "https://example.com",
              "name": "", "placeholder": "", "aria_label": "", "type": "", "checked": None}],
            "Test Page",
        ]
        # .url property
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        result = _extract_snapshot(mock_page, full=False)
        assert "@e1" in result
        assert "Click me" in result
        assert "Test Page" in result

    def test_compact_with_placeholder(self):
        """元素有 placeholder 时显示 [placeholder=...] (L210)。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = [
            [{"id": 1, "tag": "input", "text": "", "href": "",
              "name": "", "placeholder": "Search...", "aria_label": "", "type": "text", "checked": None}],
            "Title",
        ]
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        result = _extract_snapshot(mock_page, full=False)
        assert "[placeholder=Search...]" in result

    def test_compact_with_aria_label(self):
        """元素有 aria_label 时显示 [label=...] (L212)。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = [
            [{"id": 1, "tag": "button", "text": "Submit", "href": "",
              "name": "", "placeholder": "", "aria_label": "submit button", "type": "", "checked": None}],
            "Title",
        ]
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        result = _extract_snapshot(mock_page, full=False)
        assert "[label=submit button]" in result

    def test_compact_with_checked(self):
        """元素 checked 时显示 ✓ 或 □ (L214)。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = [
            [{"id": 1, "tag": "input", "text": "", "href": "",
              "name": "", "placeholder": "", "aria_label": "", "type": "checkbox", "checked": True}],
            "Title",
        ]
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        result = _extract_snapshot(mock_page, full=False)
        assert "✓" in result or "□" in result

    def test_compact_no_elements(self):
        """无交互元素时回退到文本摘要。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = [
            [],           # interactive elements
            "Empty Page", # document.title
            "some text content",  # body text fallback
        ]
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        result = _extract_snapshot(mock_page, full=False)
        assert "无交互元素" in result

    def test_exception(self):
        """异常时返回错误消息。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("fail")
        result = _extract_snapshot(mock_page, full=False)
        assert "失败" in result

    def test_full_truncated(self):
        """完整模式超长截断。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "x" * 10000
        result = _extract_snapshot(mock_page, full=True)
        assert len(result) <= 8000 + 50  # MAX_SNAPSHOT_LENGTH * 2 + truncation notice

    def test_snapshot_truncated(self):
        """紧凑模式超长截断。"""
        from core.browser import _extract_snapshot
        mock_page = MagicMock()
        many_elements = []
        for i in range(200):
            many_elements.append({
                "id": i, "tag": "a", "text": f"link{i}" * 20,
                "href": "https://x.com/" + "x" * 50,
                "name": "", "placeholder": "", "aria_label": "", "type": "", "checked": None,
            })
        mock_page.evaluate.side_effect = [
            many_elements,
            "Title",
        ]
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        result = _extract_snapshot(mock_page, full=False)
        assert len(result) <= 4000 + 50  # MAX_SNAPSHOT_LENGTH


class TestNavigate:
    """navigate 测试。"""

    def test_invalid_url(self):
        from core.browser import navigate
        result = navigate("")
        assert result["success"] is False

    def test_browser_failure(self):
        with patch("core.browser._ensure_browser", return_value=(None, None)):
            from core.browser import navigate
            result = navigate("https://example.com")
            assert result["success"] is False

    def test_navigate_success(self):
        mock_page = MagicMock()
        mock_page.url = "https://example.com"
        with patch("core.browser._ensure_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="snapshot"):
                from core.browser import navigate
                result = navigate("example.com")
                assert result["success"] is True
                mock_page.goto.assert_called_once()

    def test_navigate_networkidle_timeout(self):
        """wait_for_load_state('networkidle') 超时走 except Exception: pass (L264-265)。"""
        mock_page = MagicMock()
        mock_page.url = "https://example.com"
        mock_page.wait_for_load_state.side_effect = Exception("timeout")
        with patch("core.browser._ensure_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="snapshot"):
                from core.browser import navigate
                result = navigate("https://example.com")
                assert result["success"] is True
                mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=5000)

    def test_navigate_exception(self):
        mock_page = MagicMock()
        mock_page.goto.side_effect = Exception("network error")
        with patch("core.browser._ensure_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import navigate
            result = navigate("https://example.com")
            assert result["success"] is False


class TestSnapshot:
    """snapshot 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import snapshot
            result = snapshot()
            assert result["success"] is False

    def test_snapshot_ok(self):
        mock_page = MagicMock()
        type(mock_page).url = PropertyMock(return_value="https://example.com")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="snap"):
                from core.browser import snapshot
                result = snapshot(full=True)
                assert result["success"] is True

    def test_snapshot_exception(self):
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("fail")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", side_effect=Exception("fail")):
                from core.browser import snapshot
                result = snapshot()
                assert result["success"] is False


class TestClick:
    """click 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import click
            result = click("@e1")
            assert result["success"] is False

    def test_invalid_ref(self):
        with patch("core.browser._get_browser", return_value=(MagicMock(), MagicMock())):
            from core.browser import click
            result = click("invalid")
            assert result["success"] is False

    def test_click_success(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {"ok": True, "tag": "a", "text": "link"}
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="new snap"):
                from core.browser import click
                result = click("@e5")
                assert result["success"] is True

    def test_click_not_found(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {"ok": False}
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import click
            result = click("@e99")
            assert result["success"] is False

    def test_click_exception(self):
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("click failed")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import click
            result = click("@e1")
            assert result["success"] is False


class TestTypeText:
    """type_text 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import type_text
            result = type_text("@e1", "hello")
            assert result["success"] is False

    def test_invalid_ref(self):
        with patch("core.browser._get_browser", return_value=(MagicMock(), MagicMock())):
            from core.browser import type_text
            result = type_text("bad", "text")
            assert result["success"] is False

    def test_type_success(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {"ok": True, "tag": "input", "placeholder": ""}
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import type_text
            result = type_text("@e3", "test text")
            assert result["success"] is True

    def test_type_not_found(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {"ok": False}
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import type_text
            result = type_text("@e99", "text")
            assert result["success"] is False

    def test_type_exception(self):
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("type failed")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import type_text
            result = type_text("@e1", "text")
            assert result["success"] is False


class TestPressKey:
    """press_key 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import press_key
            assert press_key("enter")["success"] is False

    def test_key_press_success(self):
        mock_page = MagicMock()
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="snap"):
                from core.browser import press_key
                result = press_key("escape")
                assert result["success"] is True
                mock_page.keyboard.press.assert_called_with("Escape")

    def test_key_mapping(self):
        mock_page = MagicMock()
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value=""):
                from core.browser import press_key
                press_key("up")
                mock_page.keyboard.press.assert_called_with("ArrowUp")

    def test_key_exception(self):
        mock_page = MagicMock()
        mock_page.keyboard.press.side_effect = Exception("key fail")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import press_key
            assert press_key("enter")["success"] is False


class TestScroll:
    """scroll 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import scroll
            assert scroll("down")["success"] is False

    def test_scroll_down(self):
        mock_page = MagicMock()
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="snap"):
                from core.browser import scroll
                result = scroll("down")
                assert result["success"] is True

    def test_scroll_up(self):
        mock_page = MagicMock()
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._extract_snapshot", return_value="snap"):
                from core.browser import scroll
                result = scroll("up")
                assert result["success"] is True

    def test_scroll_exception(self):
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("scroll fail")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import scroll
            assert scroll("down")["success"] is False


class TestExecuteJS:
    """execute_js 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import execute_js
            assert execute_js("1+1")["success"] is False

    def test_js_success(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = 42
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import execute_js
            result = execute_js("1+1")
            assert result["success"] is True
            assert "42" in result["output"]

    def test_js_undefined(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = None
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import execute_js
            result = execute_js("undefined")
            assert "undefined" in result["output"]

    def test_js_truncated(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "x" * 3000
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import execute_js
            result = execute_js("long output")
            assert len(result["output"]) <= 2000 + 50

    def test_js_exception(self):
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("js error")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import execute_js
            assert execute_js("bad()")["success"] is False


class TestScreenshot:
    """screenshot 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import screenshot
            assert screenshot()["success"] is False

    def test_screenshot_success(self):
        mock_page = MagicMock()
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._manage_screenshots"):
                from core.browser import screenshot
                result = screenshot()
                assert result["success"] is True
                mock_page.screenshot.assert_called_once()

    def test_screenshot_with_filename(self):
        mock_page = MagicMock()
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            with patch("core.browser._manage_screenshots"):
                from core.browser import screenshot
                result = screenshot(filename="custom.png")
                assert result["success"] is True

    def test_screenshot_exception(self):
        mock_page = MagicMock()
        mock_page.screenshot.side_effect = Exception("capture fail")
        with patch("core.browser._get_browser", return_value=(MagicMock(), mock_page)):
            from core.browser import screenshot
            assert screenshot()["success"] is False


class TestClose:
    """close 测试。"""

    def test_close(self):
        with patch("core.browser._cleanup") as mock_cleanup:
            from core.browser import close
            result = close()
            assert result["success"] is True
            mock_cleanup.assert_called_once()


class TestGetConsoleLogs:
    """get_console_logs 测试。"""

    def test_no_browser(self):
        with patch("core.browser._get_browser", return_value=(None, None)):
            from core.browser import get_console_logs
            assert get_console_logs()["success"] is False

    def test_with_browser(self):
        with patch("core.browser._get_browser", return_value=(MagicMock(), MagicMock())):
            from core.browser import get_console_logs
            result = get_console_logs()
            assert result["success"] is True
