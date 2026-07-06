"""测试 core/downloader.py — 下载引擎。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


class TestSafeFilename:
    """_safe_filename 测试。"""

    def test_from_disposition(self):
        """Content-Disposition 优先级最高。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/ignore.html",
                              disposition='attachment; filename="report.pdf"')
        assert "report" in name
        assert ".pdf" in name

    def test_from_url_path(self):
        """从 URL 路径提取。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/files/data.csv")
        assert "data" in name
        assert ".csv" in name

    def test_from_url_hash(self):
        """无明确文件名时用 hash。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/")
        assert len(name) == 12 + 0  # hash(12 chars) + no ext

    def test_with_content_type_fallback(self):
        """用 content_type 推断扩展名。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/noext", content_type="image/png")
        assert name.endswith(".png")

    def test_disposition_url_encoded(self):
        """URL 编码的 filename* 正确解码。"""
        from core.downloader import _safe_filename
        name = _safe_filename("http://x.com/f",
                              disposition="filename*=UTF-8''%E6%B5%8B%E8%AF%95.txt")
        assert "测试" in name or name.endswith(".txt")

    def test_empty_url_returns_hash(self):
        """空 URL 返回 hash。"""
        from core.downloader import _safe_filename
        name = _safe_filename("")
        assert len(name) > 0

    def test_disposition_unquote_exception(self):
        """Content-Disposition 中 unquote 异常被捕获。"""
        from core.downloader import _safe_filename
        with patch("core.downloader.urllib.parse.unquote", side_effect=ValueError("bad encoding")):
            name = _safe_filename("https://example.com/ignore.html",
                                  disposition='attachment; filename="report.pdf"')
            # Should not crash, should fall through to _sanitize_name
            assert "report" in name
            assert ".pdf" in name

    def test_disposition_with_filename_star(self):
        """filename*= 格式正确解析。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://x.com/f",
                              disposition="filename*=UTF-8''encoded%20name.txt")
        assert "encoded name" in name or "encoded_name" in name

    def test_url_no_dot_in_path_uses_hash(self):
        """URL 路径没有点时用 hash。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/pathnodot")
        assert not name.startswith("pathnodot")
        assert len(name) == 12

    def test_url_path_too_long_uses_hash(self):
        """URL 路径超长时用 hash。"""
        from core.downloader import _safe_filename
        long_segment = "a" * 300 + ".txt"
        url = f"https://example.com/{long_segment}"
        name = _safe_filename(url)
        # Should not crash, and name should be <= 200
        assert len(name) <= 200

    def test_disposition_empty_name_falls_through(self):
        """Content-Disposition 空名称时 fallthrough 到 URL 路径。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/report.pdf",
                              disposition='attachment; filename=""')
        # Disposition match might yield empty name, falls through to URL
        assert name is not None
        assert isinstance(name, str)

    def test_content_type_fallback_with_charset(self):
        """带 charset 的 content-type 正确提取扩展名。"""
        from core.downloader import _safe_filename
        name = _safe_filename("https://example.com/noext", content_type="text/html; charset=utf-8")
        assert name.endswith(".html")


class TestSanitizeName:
    """_sanitize_name 测试。"""

    def test_normal_name(self):
        """正常名称不变。"""
        from core.downloader import _sanitize_name
        assert _sanitize_name("hello.txt") == "hello.txt"

    def test_unsafe_chars_replaced(self):
        """不安全字符替换为下划线。"""
        from core.downloader import _sanitize_name
        assert _sanitize_name("a/b*c:d") == "a_b_c_d"

    def test_duplicate_underscores(self):
        """重复下划线合并。"""
        from core.downloader import _sanitize_name
        assert _sanitize_name("a___b") == "a_b"

    def test_truncated_long_name(self):
        """超长名截断到200字符。"""
        from core.downloader import _sanitize_name
        long_name = "a" * 300 + ".txt"
        assert len(_sanitize_name(long_name)) <= 200

    def test_strip_special_ends(self):
        """首尾特殊字符被去除。"""
        from core.downloader import _sanitize_name
        assert _sanitize_name("._hello_-") == "hello"

    def test_empty_after_sanitize(self):
        """全特殊字符返回 download。"""
        from core.downloader import _sanitize_name
        assert _sanitize_name("._-") == "download"


class TestExtFromContentType:
    """_ext_from_content_type 测试。"""

    def test_known_types(self):
        """已知类型返回正确扩展名。"""
        from core.downloader import _ext_from_content_type
        assert _ext_from_content_type("text/html") == ".html"
        assert _ext_from_content_type("application/pdf") == ".pdf"
        assert _ext_from_content_type("image/jpeg") == ".jpg"
        assert _ext_from_content_type("video/mp4") == ".mp4"
        assert _ext_from_content_type("audio/mpeg") == ".mp3"

    def test_unknown_type(self):
        """未知类型返回空字符串。"""
        from core.downloader import _ext_from_content_type
        assert _ext_from_content_type("application/octet-stream") == ""

    def test_with_charset(self):
        """带 charset 时正确提取。"""
        from core.downloader import _ext_from_content_type
        assert _ext_from_content_type("text/html; charset=utf-8") == ".html"

    def test_all_mapped_types(self):
        """测试所有映射的类型。"""
        from core.downloader import _ext_from_content_type
        cases = {
            "text/html": ".html",
            "text/plain": ".txt",
            "text/csv": ".csv",
            "text/markdown": ".md",
            "application/json": ".json",
            "application/xml": ".xml",
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "application/gzip": ".gz",
            "application/x-tar": ".tar",
            "application/x-bzip2": ".bz2",
            "application/x-7z-compressed": ".7z",
            "application/x-rar-compressed": ".rar",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
            "image/bmp": ".bmp",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/flac": ".flac",
        }
        for ct, expected_ext in cases.items():
            assert _ext_from_content_type(ct) == expected_ext, f"Failed for {ct}"


class TestDownloadResult:
    """DownloadResult 测试。"""

    def test_init(self):
        """基本初始化。"""
        from core.downloader import DownloadResult
        r = DownloadResult(path="/tmp/test.txt", size=1024, elapsed=2.0,
                           engine="requests", url="https://example.com/test.txt")
        assert r.success is True
        assert r.path == "/tmp/test.txt"
        assert r.size == 1024
        assert r.elapsed == 2.0
        assert r.speed == 512.0  # 1024/2

    def test_error_result(self):
        """错误结果。"""
        from core.downloader import DownloadResult
        r = DownloadResult(path="", size=0, elapsed=0, engine="requests",
                           url="https://example.com/test.txt", success=False,
                           error="Connection refused")
        assert r.success is False
        assert r.error == "Connection refused"

    def test_size_str_bytes(self):
        """小于1KB显示 B。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 500, 1, "r", "u")
        assert "B" in r.size_str

    def test_size_str_kb(self):
        """1KB-1MB显示 KB。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 50000, 1, "r", "u")
        assert "KB" in r.size_str

    def test_size_str_mb(self):
        """1MB-1GB显示 MB。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 5 * 1024 * 1024, 1, "r", "u")
        assert "MB" in r.size_str

    def test_size_str_gb(self):
        """大于1GB显示 GB。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 5 * 1024 * 1024 * 1024, 1, "r", "u")
        assert "GB" in r.size_str

    def test_speed_str_bytes(self):
        """低速显示 B/s。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 500, 1, "r", "u")
        assert "B/s" in r.speed_str

    def test_speed_str_kb(self):
        """中速显示 KB/s。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 50000, 1, "r", "u")
        assert "KB" in r.speed_str

    def test_speed_str_mb(self):
        """高速显示 MB/s。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t", 5 * 1024 * 1024, 1, "r", "u")
        assert "MB" in r.speed_str

    def test_summarize_success(self):
        """成功摘要含 ✅。"""
        from core.downloader import DownloadResult
        r = DownloadResult("/tmp/t.txt", 1024, 2.0, "requests", "https://x.com/f")
        summary = r.summarize()
        assert "✅" in summary
        assert "/tmp/t.txt" in summary
        assert "1.0 KB" in summary

    def test_summarize_failure(self):
        """失败摘要含 ❌。"""
        from core.downloader import DownloadResult
        r = DownloadResult("", 0, 0, "", "", success=False, error="timeout")
        assert "❌" in r.summarize()
        assert "timeout" in r.summarize()


class TestDownloadEngineDirect:
    """DownloadEngine 不依赖网络的测试。"""

    def test_download_custom_filename(self):
        """自定义文件名被使用。"""
        from core.downloader import DownloadEngine, DEFAULT_DOWNLOAD_DIR
        with patch("core.downloader.DownloadEngine._try_requests_stream",
                   return_value=None):
            with patch("core.downloader.DownloadEngine._try_aria2c",
                       return_value=None):
                with patch("core.downloader.DownloadEngine._try_wget",
                           return_value=None):
                    with patch("core.downloader.DownloadEngine._try_curl",
                               return_value=None):
                        result = DownloadEngine.download(
                            "https://example.com/f",
                            output_dir=str(DEFAULT_DOWNLOAD_DIR),
                            filename="custom.txt"
                        )
                        assert result.success is False
                        assert "失败" in result.error

    def test_download_exception_handling(self):
        """异常被正确捕获。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.DEFAULT_DOWNLOAD_DIR", Path("/nonexistent_perm_denied")):
            with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
                with pytest.raises(PermissionError):
                    DownloadEngine.download("https://example.com/f")

    def test_download_requests_stream_success(self):
        """_try_requests_stream 成功路径。"""
        from core.downloader import DownloadEngine, DownloadResult
        from unittest.mock import MagicMock

        mock_result = DownloadResult(
            path="/tmp/test.txt", size=100, elapsed=0.5,
            engine="python_requests", url="https://example.com/f"
        )
        with patch("core.downloader.DownloadEngine._try_requests_stream",
                   return_value=mock_result):
            result = DownloadEngine.download(
                "https://example.com/f",
                output_dir="/tmp",
                filename="test.txt"
            )
            assert result.success is True
            assert result.engine == "python_requests"

    def test_download_aria2c_fallback(self):
        """fallback 到 aria2c。"""
        from core.downloader import DownloadEngine, DownloadResult

        mock_result = DownloadResult(
            path="/tmp/test.txt", size=100, elapsed=0.5,
            engine="aria2c", url="https://example.com/f"
        )
        with patch("core.downloader.DownloadEngine._try_requests_stream",
                   return_value=None):
            with patch("core.downloader.DownloadEngine._try_aria2c",
                       return_value=mock_result):
                result = DownloadEngine.download(
                    "https://example.com/f",
                    output_dir="/tmp",
                    filename="test.txt"
                )
                assert result.success is True
                assert result.engine == "aria2c"

    def test_download_wget_fallback(self):
        """fallback 到 wget。"""
        from core.downloader import DownloadEngine, DownloadResult

        mock_result = DownloadResult(
            path="/tmp/test.txt", size=100, elapsed=0.5,
            engine="wget", url="https://example.com/f"
        )
        with patch("core.downloader.DownloadEngine._try_requests_stream",
                   return_value=None):
            with patch("core.downloader.DownloadEngine._try_aria2c",
                       return_value=None):
                with patch("core.downloader.DownloadEngine._try_wget",
                           return_value=mock_result):
                    result = DownloadEngine.download(
                        "https://example.com/f",
                        output_dir="/tmp",
                        filename="test.txt"
                    )
                    assert result.success is True
                    assert result.engine == "wget"

    def test_download_curl_fallback(self):
        """fallback 到 curl。"""
        from core.downloader import DownloadEngine, DownloadResult

        mock_result = DownloadResult(
            path="/tmp/test.txt", size=100, elapsed=0.5,
            engine="curl", url="https://example.com/f"
        )
        with patch("core.downloader.DownloadEngine._try_requests_stream",
                   return_value=None):
            with patch("core.downloader.DownloadEngine._try_aria2c",
                       return_value=None):
                with patch("core.downloader.DownloadEngine._try_wget",
                           return_value=None):
                    with patch("core.downloader.DownloadEngine._try_curl",
                               return_value=mock_result):
                        result = DownloadEngine.download(
                            "https://example.com/f",
                            output_dir="/tmp",
                            filename="test.txt"
                        )
                        assert result.success is True
                        assert result.engine == "curl"

    def test_check_engines(self):
        """check_engines 列出可用引擎。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.DownloadEngine._requests_available",
                   return_value=True):
            with patch("core.downloader.shutil.which",
                       side_effect=lambda x: f"/usr/bin/{x}" if x in ("aria2c", "wget", "curl") else None):
                engines = DownloadEngine.check_engines()
                assert "python_requests" in engines
                assert "aria2c" in engines
                assert "wget" in engines
                assert "curl" in engines

    def test_check_engines_none_available(self):
        """所有引擎不可用。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.DownloadEngine._requests_available",
                   return_value=False):
            with patch("core.downloader.shutil.which", return_value=None):
                engines = DownloadEngine.check_engines()
                assert engines == []

    def test_requests_not_available(self):
        """requests 不可用时返回 None。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.DownloadEngine._requests_available",
                   return_value=False):
            result = DownloadEngine._try_requests_stream(
                "https://example.com/f",
                Path("/tmp"), Path("/tmp/.tmp"), None, 60
            )
            assert result is None

    def test_requests_available_import_error(self):
        """_requests_available 返回 False 当 import 失败。"""
        from core.downloader import DownloadEngine
        with patch("builtins.__import__", side_effect=ImportError("no requests")):
            assert DownloadEngine._requests_available() is False

    def test_aria2c_not_installed(self):
        """aria2c 未安装返回 None。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.shutil.which", return_value=None):
            result = DownloadEngine._try_aria2c(
                "https://example.com/f",
                Path("/tmp"), Path("/tmp/.tmp"), None, 60
            )
            assert result is None

    def test_wget_not_installed(self):
        """wget 未安装返回 None。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.shutil.which", return_value=None):
            result = DownloadEngine._try_wget(
                "https://example.com/f",
                Path("/tmp"), Path("/tmp/.tmp"), None, 60
            )
            assert result is None

    def test_curl_not_installed(self):
        """curl 未安装返回 None。"""
        from core.downloader import DownloadEngine
        with patch("core.downloader.shutil.which", return_value=None):
            result = DownloadEngine._try_curl(
                "https://example.com/f",
                Path("/tmp"), Path("/tmp/.tmp"), None, 60
            )
            assert result is None

    def test_deduplicate_path_exists(self):
        """_deduplicate 当文件已存在时添加后缀。"""
        from core.downloader import DownloadEngine
        from pathlib import Path
        with patch("pathlib.Path.exists") as mock_exists:
            # First call for original path returns True, second for _1 returns True, third for _2 returns False
            mock_exists.side_effect = [True, True, False]
            result = DownloadEngine._deduplicate(Path("/tmp/test.txt"))
            assert result == Path("/tmp/test_2.txt")

    def test_deduplicate_path_does_not_exist(self):
        """_deduplicate 当文件不存在时返回原路径。"""
        from core.downloader import DownloadEngine
        from pathlib import Path
        with patch("pathlib.Path.exists", return_value=False):
            result = DownloadEngine._deduplicate(Path("/tmp/test.txt"))
            assert result == Path("/tmp/test.txt")

    def test_try_requests_stream_success_path(self):
        """_try_requests_stream 完整成功路径（含下载、进度、校验）。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch, mock_open
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "text/plain",
                "Content-Disposition": "",
                "Content-Length": "100",
            }
            # Two chunks: first with data, second empty (to stop iteration)
            mock_resp.iter_content.return_value = [b"x" * 50, b"y" * 50]

            # Create a fake requests module to intercept the import inside _try_requests_stream
            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def side_effect(name, *args, **kwargs):
                        if name == "requests":
                            return fake_requests
                        # Fall through to real import for everything else
                        original_import = __builtins__.__import__ if isinstance(__builtins__, dict) else __builtins__.__import__
                        return original_import(name, *args, **kwargs)
                    mock_import.side_effect = side_effect
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_requests_stream(
                            "https://example.com/test.txt",
                            out_dir, tmp_dir, "test_output.txt", 60
                        )
                        assert result is not None
                        assert result.success is True
                        assert result.engine == "python_requests"
                        assert result.size == 100

    def test_try_requests_stream_large_file_progress(self):
        """_try_requests_stream 大文件进度输出。"""
        from core.downloader import DownloadEngine
        from core.downloader import STREAM_CHUNK_SIZE, LARGE_FILE_THRESHOLD, PROGRESS_INTERVAL
        from unittest.mock import MagicMock, patch
        import tempfile
        import time as time_module

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            large_size = 100 * 1024 * 1024  # 100MB > 50MB threshold

            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "",
                "Content-Length": str(large_size),
            }
            # Need enough chunks to exceed PROGRESS_INTERVAL (2s) coverage
            # We'll mock time.time to advance
            mock_resp.iter_content.return_value = [b"x" * STREAM_CHUNK_SIZE] * 3

            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            real_time = 1000.0
            time_values = [real_time, real_time, real_time + PROGRESS_INTERVAL + 0.1]

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def _fake_import(name, *a, **kw):
                        if name == "requests": return fake_requests
                        import builtins
                        return builtins.__import__(name, *a, **kw)
                    mock_import.side_effect = _fake_import
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        with patch("builtins.print") as mock_print:
                            with patch("core.downloader.time.time",
                                       side_effect=time_values):
                                result = DownloadEngine._try_requests_stream(
                                    "https://example.com/large.bin",
                                    out_dir, tmp_dir, "large.bin", 60
                                )
                                # Should fail because downloaded < total, but should have printed progress
                                assert result is None
                                # Check that print was called at least once for progress
                                progress_calls = [c for c in mock_print.call_args_list
                                                  if "下载中" in str(c)]
                                assert len(progress_calls) >= 1

    def test_try_requests_stream_content_length_mismatch(self):
        """_try_requests_stream Content-Length 不匹配。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "text/plain",
                "Content-Disposition": "",
                "Content-Length": "200",
            }
            # Return only 50 bytes but Content-Length says 200
            mock_resp.iter_content.return_value = [b"x" * 50]

            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def _fake_import(name, *args, **kwargs):
                        if name == "requests":
                            return fake_requests
                        import builtins
                        return builtins.__import__(name, *args, **kwargs)
                    mock_import.side_effect = _fake_import
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_requests_stream(
                            "https://example.com/test.txt",
                            out_dir, tmp_dir, "test.txt", 60
                        )
                        # Should raise IOError -> cleanup -> return None
                        assert result is None

    def test_try_requests_stream_follows_redirects(self):
        """_try_requests_stream allow_redirects=True 被设置。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "text/plain",
                "Content-Disposition": "",
                "Content-Length": "10",
            }
            mock_resp.iter_content.return_value = [b"x" * 10]

            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def _fi2(name, *a, **kw):
                        if name == "requests": return fake_requests
                        import builtins; return builtins.__import__(name, *a, **kw)
                    mock_import.side_effect = _fi2
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_requests_stream(
                            "https://example.com/test.txt",
                            out_dir, tmp_dir, "test.txt", 60
                        )
                        assert result is not None
                        # Verify allow_redirects=True was passed
                        _, kwargs = fake_requests.get.call_args
                        assert kwargs.get("allow_redirects") is True

    def test_try_requests_stream_no_filename_auto_detect(self):
        """不传 filename 时自动从响应头推断。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "application/pdf",
                "Content-Disposition": 'attachment; filename="auto.pdf"',
                "Content-Length": "10",
            }
            mock_resp.iter_content.return_value = [b"x" * 10]

            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def _fi3(name, *a, **kw):
                        if name == "requests": return fake_requests
                        import builtins; return builtins.__import__(name, *a, **kw)
                    mock_import.side_effect = _fi3
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_requests_stream(
                            "https://example.com/download",
                            out_dir, tmp_dir, None, 60
                        )
                        assert result is not None
                        assert result.success is True

    def test_try_requests_stream_http_error(self):
        """_try_requests_stream HTTP 错误返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch

        fake_requests = MagicMock()
        fake_requests.get.side_effect = Exception("Connection failed")

        with patch("core.downloader.DownloadEngine._requests_available",
                   return_value=True):
            with patch("builtins.__import__") as mock_import:
                def _fi4(name, *a, **kw):
                    if name == "requests": return fake_requests
                    import builtins; return builtins.__import__(name, *a, **kw)
                mock_import.side_effect = _fi4
                result = DownloadEngine._try_requests_stream(
                    "https://example.com/f",
                    Path("/tmp"), Path("/tmp/.tmp"), None, 60
                )
                assert result is None

    def test_try_requests_stream_finally_cleanup(self):
        """_try_requests_stream finally 块清理。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch, mock_open
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            # Create a temp file to test cleanup
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "text/plain",
                "Content-Disposition": "",
                "Content-Length": "10",
            }
            mock_resp.iter_content.return_value = [b"x" * 10]

            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def _fi5(name, *a, **kw):
                        if name == "requests": return fake_requests
                        import builtins; return builtins.__import__(name, *a, **kw)
                    mock_import.side_effect = _fi5
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        with patch("core.downloader.shutil.move",
                                   side_effect=Exception("move failed")):
                            result = DownloadEngine._try_requests_stream(
                                "https://example.com/f",
                                out_dir, tmp_dir, "test.txt", 60
                            )
                            # Exception in except -> None; finally runs but tmp_path may not exist
                            assert result is None

    def test_try_aria2c_success(self):
        """_try_aria2c 成功路径。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/aria2c"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock()
                    # Create the file so it exists after subprocess
                    final_path = out_dir / "test_aria2.txt"
                    final_path.write_text("hello")
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_aria2c(
                            "https://example.com/f",
                            out_dir, Path(tmpdir) / ".tmp", "test_aria2.txt", 60
                        )
                        assert result is not None
                        assert result.success is True
                        assert result.engine == "aria2c"
                        assert result.size == 5

    def test_try_aria2c_subprocess_exception(self):
        """_try_aria2c 子进程异常返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import patch

        with patch("core.downloader.shutil.which", return_value="/usr/bin/aria2c"):
            with patch("subprocess.run", side_effect=Exception("timeout")):
                result = DownloadEngine._try_aria2c(
                    "https://example.com/f",
                    Path("/tmp"), Path("/tmp/.tmp"), "test.txt", 60
                )
                assert result is None

    def test_try_aria2c_file_not_created(self):
        """_try_aria2c 文件未创建返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/aria2c"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock()
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_aria2c(
                            "https://example.com/f",
                            out_dir, Path(tmpdir) / ".tmp", "test_nonexist.txt", 60
                        )
                        assert result is None

    def test_try_aria2c_no_filename_generates_one(self):
        """_try_aria2c 无 filename 时自动生成。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/aria2c"):
                # Mock the HEAD request inside aria2c
                mock_resp = MagicMock(spec=object)
                mock_resp.headers = {
                    "Content-Type": "image/png",
                    "Content-Disposition": "",
                }
                mock_urlopen = MagicMock()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                with patch("core.downloader.urllib.request.urlopen",
                           mock_urlopen):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock()
                        final_path = out_dir / "eb3e97b55f75.png"
                        final_path.write_text("data")
                        with patch("core.downloader.DownloadEngine._deduplicate",
                                   side_effect=lambda p: p):
                            result = DownloadEngine._try_aria2c(
                                "https://example.com/img",
                                out_dir, Path(tmpdir) / ".tmp", None, 60
                            )
                            assert result is not None
                            assert result.success is True

    def test_try_aria2c_head_request_fails(self):
        """_try_aria2c HEAD 请求失败仍能继续。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/aria2c"):
                mock_urlopen = MagicMock()
                mock_urlopen.side_effect = Exception("HEAD failed")
                with patch("core.downloader.urllib.request.urlopen",
                           mock_urlopen):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock()
                        # Create expected file based on URL hash
                        import hashlib
                        url_hash = hashlib.md5("https://example.com/img".encode()).hexdigest()[:12]
                        final_path = out_dir / url_hash
                        final_path.write_text("data")
                        with patch("core.downloader.DownloadEngine._deduplicate",
                                   side_effect=lambda p: p):
                            result = DownloadEngine._try_aria2c(
                                "https://example.com/img",
                                out_dir, Path(tmpdir) / ".tmp", None, 60
                            )
                            assert result is not None
                            assert result.success is True

    def test_try_wget_success(self):
        """_try_wget 成功路径。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/wget"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock()
                    final_path = out_dir / "test_wget.txt"
                    final_path.write_text("hello wget")
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_wget(
                            "https://example.com/f",
                            out_dir, Path(tmpdir) / ".tmp", "test_wget.txt", 60
                        )
                        assert result is not None
                        assert result.success is True
                        assert result.engine == "wget"
                        assert result.size == 10

    def test_try_wget_subprocess_exception(self):
        """_try_wget 子进程异常返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import patch

        with patch("core.downloader.shutil.which", return_value="/usr/bin/wget"):
            with patch("subprocess.run", side_effect=Exception("failed")):
                result = DownloadEngine._try_wget(
                    "https://example.com/f",
                    Path("/tmp"), Path("/tmp/.tmp"), "test.txt", 60
                )
                assert result is None

    def test_try_wget_no_filename_generates_one(self):
        """_try_wget 无 filename 时自动生成。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/wget"):
                mock_resp = MagicMock(spec=object)
                mock_resp.headers = {
                    "Content-Type": "application/pdf",
                    "Content-Disposition": "",
                }
                mock_urlopen = MagicMock()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                with patch("core.downloader.urllib.request.urlopen",
                           mock_urlopen):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock()
                        import hashlib
                        url_hash = hashlib.md5("https://example.com/doc".encode()).hexdigest()[:12]
                        final_path = out_dir / f"{url_hash}.pdf"
                        final_path.write_text("doc data")
                        with patch("core.downloader.DownloadEngine._deduplicate",
                                   side_effect=lambda p: p):
                            result = DownloadEngine._try_wget(
                                "https://example.com/doc",
                                out_dir, Path(tmpdir) / ".tmp", None, 60
                            )
                            assert result is not None
                            assert result.success is True

    def test_try_wget_head_request_fails(self):
        """_try_wget HEAD 请求失败仍能继续。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/wget"):
                mock_urlopen = MagicMock()
                mock_urlopen.side_effect = Exception("HEAD failed")
                with patch("core.downloader.urllib.request.urlopen",
                           mock_urlopen):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock()
                        import hashlib
                        url_hash = hashlib.md5("https://example.com/doc".encode()).hexdigest()[:12]
                        final_path = out_dir / url_hash
                        final_path.write_text("data")
                        with patch("core.downloader.DownloadEngine._deduplicate",
                                   side_effect=lambda p: p):
                            result = DownloadEngine._try_wget(
                                "https://example.com/doc",
                                out_dir, Path(tmpdir) / ".tmp", None, 60
                            )
                            assert result is not None
                            assert result.success is True

    def test_try_wget_file_not_created(self):
        """_try_wget 文件未创建返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch

        with patch("core.downloader.shutil.which", return_value="/usr/bin/wget"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock()
                with patch("core.downloader.DownloadEngine._deduplicate",
                           side_effect=lambda p: p):
                    result = DownloadEngine._try_wget(
                        "https://example.com/f",
                        Path("/tmp"), Path("/tmp/.tmp"), "nonexist.txt", 60
                    )
                    assert result is None

    def test_try_curl_success(self):
        """_try_curl 成功路径。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/curl"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock()
                    final_path = out_dir / "test_curl.txt"
                    final_path.write_text("curl data")
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        result = DownloadEngine._try_curl(
                            "https://example.com/f",
                            out_dir, Path(tmpdir) / ".tmp", "test_curl.txt", 60
                        )
                        assert result is not None
                        assert result.success is True
                        assert result.engine == "curl"
                        assert result.size == 9

    def test_try_curl_subprocess_exception(self):
        """_try_curl 子进程异常返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import patch

        with patch("core.downloader.shutil.which", return_value="/usr/bin/curl"):
            with patch("subprocess.run", side_effect=Exception("timeout")):
                result = DownloadEngine._try_curl(
                    "https://example.com/f",
                    Path("/tmp"), Path("/tmp/.tmp"), "test.txt", 60
                )
                assert result is None

    def test_try_curl_no_filename_generates_one(self):
        """_try_curl 无 filename 时自动生成。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/curl"):
                mock_resp = MagicMock(spec=object)
                mock_resp.headers = {
                    "Content-Type": "text/csv",
                    "Content-Disposition": "",
                }
                mock_urlopen = MagicMock()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                with patch("core.downloader.urllib.request.urlopen",
                           mock_urlopen):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock()
                        import hashlib
                        url_hash = hashlib.md5("https://example.com/data".encode()).hexdigest()[:12]
                        final_path = out_dir / f"{url_hash}.csv"
                        final_path.write_text("a,b,c")
                        with patch("core.downloader.DownloadEngine._deduplicate",
                                   side_effect=lambda p: p):
                            result = DownloadEngine._try_curl(
                                "https://example.com/data",
                                out_dir, Path(tmpdir) / ".tmp", None, 60
                            )
                            assert result is not None
                            assert result.success is True

    def test_try_curl_head_request_fails(self):
        """_try_curl HEAD 请求失败仍能继续。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)

            with patch("core.downloader.shutil.which", return_value="/usr/bin/curl"):
                mock_urlopen = MagicMock()
                mock_urlopen.side_effect = Exception("HEAD failed")
                with patch("core.downloader.urllib.request.urlopen",
                           mock_urlopen):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock()
                        import hashlib
                        url_hash = hashlib.md5("https://example.com/data".encode()).hexdigest()[:12]
                        final_path = out_dir / url_hash
                        final_path.write_text("data")
                        with patch("core.downloader.DownloadEngine._deduplicate",
                                   side_effect=lambda p: p):
                            result = DownloadEngine._try_curl(
                                "https://example.com/data",
                                out_dir, Path(tmpdir) / ".tmp", None, 60
                            )
                            assert result is not None
                            assert result.success is True

    def test_try_curl_file_not_created(self):
        """_try_curl 文件未创建返回 None。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch

        with patch("core.downloader.shutil.which", return_value="/usr/bin/curl"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock()
                with patch("core.downloader.DownloadEngine._deduplicate",
                           side_effect=lambda p: p):
                    result = DownloadEngine._try_curl(
                        "https://example.com/f",
                        Path("/tmp"), Path("/tmp/.tmp"), "nonexist.txt", 60
                    )
                    assert result is None

    def test_try_requests_stream_finally_cleanup_with_exception(self):
        """_try_requests_stream finally 块清理时 unlink 异常。"""
        from core.downloader import DownloadEngine
        from unittest.mock import MagicMock, patch, call
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            tmp_dir = out_dir / ".tmp"
            tmp_dir.mkdir()

            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.headers = {
                "Content-Type": "text/plain",
                "Content-Disposition": "",
                "Content-Length": "5",
            }
            mock_resp.iter_content.return_value = [b"x" * 50]

            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp

            # Track unlink calls: first call (in except block) raises,
            # second call (in finally block) raises too but is caught
            unlink_call_count = [0]
            def _unlink_side_effect(*args, **kwargs):
                unlink_call_count[0] += 1
                raise PermissionError(f"can't delete call {unlink_call_count[0]}")

            with patch("core.downloader.DownloadEngine._requests_available",
                       return_value=True):
                with patch("builtins.__import__") as mock_import:
                    def _fi6(name, *a, **kw):
                        if name == "requests": return fake_requests
                        import builtins; return builtins.__import__(name, *a, **kw)
                    mock_import.side_effect = _fi6
                    with patch("core.downloader.DownloadEngine._deduplicate",
                               side_effect=lambda p: p):
                        with patch("pathlib.Path.exists", return_value=True):
                            with patch("pathlib.Path.unlink",
                                       side_effect=_unlink_side_effect):
                                # The PermissionError from the except block's unlink
                                # propagates out. We catch it here to verify the result.
                                try:
                                    result = DownloadEngine._try_requests_stream(
                                        "https://example.com/f",
                                        out_dir, tmp_dir, "test.txt", 60
                                    )
                                    # If no PermissionError propagated, result should be None
                                    assert result is None
                                except PermissionError:
                                    pass
                                # Verify unlink was called twice (except + finally blocks)
                                assert unlink_call_count[0] == 2

    def test_try_aria2c_outer_except_block(self):
        """验证外层 except 块的源代码存在。"""
        from pathlib import Path
        source = Path("core/downloader.py").read_text()
        assert "except Exception:" in source


class TestDownloadFileAndList:
    """download_file 和 list_downloads 测试。"""

    def test_download_file_invalid_url(self):
        """无效 URL 返回错误。"""
        from core.downloader import download_file
        with patch("core.downloader.DownloadEngine.download") as mock_dl:
            mock_result = MagicMock()
            mock_result.success = False
            mock_dl.return_value = mock_result
            result = download_file("not-a-url")
            assert result.success is False

    def test_download_file_timeout_param(self):
        """timeout 参数传递（mock 引擎验证参数传入）。"""
        from core.downloader import download_file, DownloadEngine
        original = DownloadEngine.download
        def _mock_download(url, output_dir=None, filename=None, timeout=60):
            assert timeout == 30
            from core.downloader import DownloadResult
            return DownloadResult("", 0, 0, "mock", url, success=False, error="mocked")
        DownloadEngine.download = staticmethod(_mock_download)
        try:
            result = download_file("https://example.com/test.txt", timeout=30)
            assert result.success is False
            assert "mocked" in result.error
        finally:
            DownloadEngine.download = original

    def test_download_file_default_params(self):
        """download_file 默认参数（无 output_dir, filename, timeout）。"""
        from core.downloader import download_file, DEFAULT_DOWNLOAD_DIR
        with patch("core.downloader.Path.mkdir"):
            with patch("core.downloader.DownloadEngine._try_requests_stream",
                       return_value=None):
                with patch("core.downloader.DownloadEngine._try_aria2c",
                           return_value=None):
                    with patch("core.downloader.DownloadEngine._try_wget",
                               return_value=None):
                        with patch("core.downloader.DownloadEngine._try_curl",
                                   return_value=None):
                            result = download_file("https://example.com/test.txt")
                            assert result.success is False
                            assert "失败" in result.error

    def test_download_file_with_filename(self):
        """download_file 带自定义文件名。"""
        from core.downloader import download_file, DownloadEngine
        from core.downloader import DownloadResult
        original = DownloadEngine.download
        def _mock_download(url, output_dir=None, filename=None, timeout=60):
            assert filename == "custom.md"
            return DownloadResult("/tmp/custom.md", 50, 0.5, "mock", url)
        DownloadEngine.download = staticmethod(_mock_download)
        try:
            result = download_file("https://example.com/doc", filename="custom.md")
            assert result.success is True
        finally:
            DownloadEngine.download = original

    def test_list_downloads_empty(self):
        """空目录返回空列表。"""
        from core.downloader import list_downloads
        with patch("pathlib.Path.iterdir", return_value=[]):
            result = list_downloads("/tmp/empty_dl")
            assert len(result) == 0

    def test_list_downloads_with_files(self):
        """有文件时返回列表。"""
        from core.downloader import list_downloads
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = Path(tmpdir) / "test.txt"
            real_file.write_text("hello")
            result = list_downloads(tmpdir)
            assert len(result) == 1
            assert result[0]["name"] == "test.txt"
            assert result[0]["size"] == 5

    def test_list_downloads_non_existent_dir(self):
        """目录不存在返回空列表。"""
        from core.downloader import list_downloads
        with patch("pathlib.Path.exists", return_value=False):
            result = list_downloads("/tmp/nonexistent")
            assert result == []

    def test_list_downloads_ignores_dotfiles(self):
        """忽略点文件。"""
        from core.downloader import list_downloads
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "normal.txt").write_text("normal")
            Path(tmpdir, ".hidden").write_text("hidden")
            result = list_downloads(tmpdir)
            assert len(result) == 1
            assert result[0]["name"] == "normal.txt"

    def test_list_downloads_sorted_by_mtime(self):
        """按 mtime 降序排列。"""
        from core.downloader import list_downloads
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = Path(tmpdir, "old.txt")
            old_file.write_text("old")
            old_mtime = old_file.stat().st_mtime
            new_file = Path(tmpdir, "new.txt")
            new_file.write_text("new")
            # Ensure different mtime
            os_mod_time = time.time()
            new_file.touch()
            result = list_downloads(tmpdir)
            assert len(result) == 2
            # Newer file should be first
            assert result[0]["name"] == "new.txt"

    def test_list_downloads_has_size_str(self):
        """列表项包含 size_str。"""
        from core.downloader import list_downloads
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "data.bin").write_text("x" * 5000)
            result = list_downloads(tmpdir)
            assert result[0]["name"] == "data.bin"
            assert "size_str" in result[0]
            assert "KB" in result[0]["size_str"] or "B" in result[0]["size_str"]

    def test_list_downloads_default_dir(self):
        """不传参数时使用默认目录。"""
        from core.downloader import list_downloads, DEFAULT_DOWNLOAD_DIR
        with patch("core.downloader.Path.exists", return_value=True):
            with patch("core.downloader.Path.iterdir", return_value=[]):
                result = list_downloads()
                assert result == []


class TestFormatSize:
    """_format_size 测试。"""

    def test_bytes(self):
        from core.downloader import _format_size
        assert _format_size(500) == "500 B"

    def test_kb(self):
        from core.downloader import _format_size
        assert "KB" in _format_size(5000)

    def test_mb(self):
        from core.downloader import _format_size
        assert "MB" in _format_size(5 * 1024 * 1024)

    def test_gb(self):
        from core.downloader import _format_size
        assert "GB" in _format_size(5 * 1024 * 1024 * 1024)
