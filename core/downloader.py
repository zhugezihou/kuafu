"""
夸父下载引擎 — 多引擎自动 fallback 的文件下载系统

架构：
1. DownloadEngine: 调度层，选择最佳引擎 + 自动 fallback
2. 引擎层：requests_stream → aria2c → wget → curl（按优先级）
3. 文件管理器：自动命名 + 去重 + 路径安全

设计原则：
- 零新增依赖（stdlib + 系统工具）
- 大文件分块下载，内存安全
- URL → 智能文件名（从 Content-Disposition / URL path / fallback）
- 下载到配置的安全目录，防路径穿越
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# ── 配置 ──────────────────────────────────────────────────────────

DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "downloads"
STREAM_CHUNK_SIZE = 8192       # 8KB 标准块
LARGE_CHUNK_SIZE = 256 * 1024   # 256KB 大文件分块
PROGRESS_INTERVAL = 2.0         # 进度更新间隔（秒）
MAX_REDIRECTS = 10
DEFAULT_TIMEOUT = 60            # 分钟级超时（秒）
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50MB 以上视为大文件


# ── 文件路径安全 ─────────────────────────────────────────────────

def _safe_filename(url: str, content_type: str = "", disposition: str = "") -> str:
    """从 URL 和响应头生成安全的文件名。

    优先级：Content-Disposition > URL 最后路径段 > URL hash > "download"
    """
    # 1. 从 Content-Disposition 提取
    if disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\n]*)["\']?',
                      disposition, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            # URL 解码（filename* 可能是 URL 编码的）
            try:
                name = urllib.parse.unquote(name)
            except Exception:
                pass
            if name:
                return _sanitize_name(name)

    # 2. 从 URL 路径提取
    path = urllib.parse.urlparse(url).path
    name = path.rstrip("/").split("/")[-1] if path else ""
    if name and "." in name and len(name) < 200:
        return _sanitize_name(name)

    # 3. 从 content-type 推断扩展名
    ext = _ext_from_content_type(content_type)

    # 4. URL hash 作为文件名
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    return f"{url_hash}{ext}"


def _sanitize_name(name: str) -> str:
    """清理文件名中的不安全字符。"""
    # 只保留安全字符
    name = re.sub(r'[^\w\.\-]', '_', name)
    # 去重下划线
    name = re.sub(r'_+', '_', name)
    # 限制长度
    name = name[:200]
    # 去掉首尾特殊字符
    name = name.strip("._-")
    if not name:
        name = "download"
    return name


def _ext_from_content_type(content_type: str) -> str:
    """根据 Content-Type 返回扩展名。"""
    ct = content_type.lower().split(";")[0].strip()
    ext_map = {
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
    return ext_map.get(ct, "")


# ── 下载引擎 ─────────────────────────────────────────────────────


class DownloadResult:
    """下载结果。"""

    def __init__(self, path: str, size: int, elapsed: float, engine: str,
                 url: str, success: bool = True, error: str = ""):
        self.path = path          # 完整文件路径
        self.size = size          # 字节数
        self.elapsed = elapsed    # 耗时（秒）
        self.engine = engine      # 使用的引擎名
        self.url = url            # 原始 URL
        self.success = success
        self.error = error
        self.speed = size / elapsed if elapsed > 0 else 0

    @property
    def size_str(self) -> str:
        """人类可读的文件大小。"""
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        elif self.size < 1024 * 1024 * 1024:
            return f"{self.size / 1024 / 1024:.1f} MB"
        else:
            return f"{self.size / 1024 / 1024 / 1024:.2f} GB"

    @property
    def speed_str(self) -> str:
        """人类可读的下载速度。"""
        if self.speed < 1024:
            return f"{self.speed:.0f} B/s"
        elif self.speed < 1024 * 1024:
            return f"{self.speed / 1024:.1f} KB/s"
        else:
            return f"{self.speed / 1024 / 1024:.1f} MB/s"

    def summarize(self) -> str:
        if not self.success:
            return f"❌ 下载失败: {self.error}"
        return (
            f"✅ 下载成功\n"
            f"   • 文件: {self.path}\n"
            f"   • 大小: {self.size_str}\n"
            f"   • 耗时: {self.elapsed:.1f}s\n"
            f"   • 速度: {self.speed_str}\n"
            f"   • 引擎: {self.engine}"
        )


class DownloadEngine:
    """下载引擎 — 多引擎自动 fallback。"""

    # ── 公开 API ──────────────────────────────────────────────

    @staticmethod
    def download(url: str, output_dir: Optional[str] = None,
                 filename: Optional[str] = None,
                 timeout: int = DEFAULT_TIMEOUT) -> DownloadResult:
        """下载文件。自动选择最佳引擎 + fallback。

        Args:
            url: 要下载的 URL
            output_dir: 输出目录（默认 ~/kuafu/downloads/）
            filename: 自定义文件名（不指定则自动生成）
            timeout: 超时秒数

        Returns:
            DownloadResult
        """
        out_dir = Path(output_dir) if output_dir else DEFAULT_DOWNLOAD_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        # 准备临时目录
        tmp_dir = out_dir / ".tmp"
        tmp_dir.mkdir(exist_ok=True)

        # 先尝试 requests_stream
        result = DownloadEngine._try_requests_stream(
            url, out_dir, tmp_dir, filename, timeout
        )
        if result and result.success:
            return result

        # fallback: aria2c（如果安装）
        result = DownloadEngine._try_aria2c(
            url, out_dir, tmp_dir, filename, timeout
        )
        if result and result.success:
            return result

        # fallback: wget
        result = DownloadEngine._try_wget(
            url, out_dir, tmp_dir, filename, timeout
        )
        if result and result.success:
            return result

        # fallback: curl
        result = DownloadEngine._try_curl(
            url, out_dir, tmp_dir, filename, timeout
        )
        if result and result.success:
            return result

        # 全部失败
        return DownloadResult(
            path="", size=0, elapsed=0, engine="none",
            url=url, success=False,
            error="所有下载引擎均失败"
        )

    @staticmethod
    def check_engines() -> list[str]:
        """列出系统中可用的下载引擎。"""
        available = []
        if DownloadEngine._requests_available():
            available.append("python_requests")
        if shutil.which("aria2c"):
            available.append("aria2c")
        if shutil.which("wget"):
            available.append("wget")
        if shutil.which("curl"):
            available.append("curl")
        return available

    # ── 引擎 1: Python requests 流式下载 ──────────────────────

    @staticmethod
    def _requests_available() -> bool:
        try:
            import requests
            return True
        except ImportError:
            return False

    @staticmethod
    def _try_requests_stream(url: str, out_dir: Path, tmp_dir: Path,
                              filename: Optional[str], timeout: int
                              ) -> Optional[DownloadResult]:
        """用 Python requests 流式分块下载。"""
        if not DownloadEngine._requests_available():
            return None

        import requests as req_lib

        start = time.time()
        tmp_path = tmp_dir / f".partial_{int(start)}_{abs(hash(url)) % 10000}"

        try:
            resp = req_lib.get(
                url, stream=True, timeout=(10, timeout),
                headers={
                    "User-Agent": "KuafuDownloader/1.0",
                    "Accept": "*/*",
                },
                allow_redirects=True,
            )
            resp.raise_for_status()

            # 如果没自定义文件名，从响应头推断
            if not filename:
                ct = resp.headers.get("Content-Type", "")
                disp = resp.headers.get("Content-Disposition", "")
                filename = _safe_filename(url, ct, disp)

            final_path = out_dir / filename
            final_path = DownloadEngine._deduplicate(final_path)

            # 获取 Content-Length
            content_length = resp.headers.get("Content-Length")
            total = int(content_length) if content_length and content_length.isdigit() else None

            downloaded = 0
            last_progress = time.time()

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=STREAM_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # 大文件才报进度
                        if total and total > LARGE_FILE_THRESHOLD:
                            now = time.time()
                            if now - last_progress >= PROGRESS_INTERVAL:
                                pct = downloaded / total * 100
                                print(f"  ⬇  下载中... {pct:.0f}% ({downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB)")
                                last_progress = now

            # 校验：Content-Length 如果存在
            if total and downloaded != total:
                raise IOError(
                    f"下载不完整: 预期 {total} 字节，实际 {downloaded} 字节"
                )

            # 安全移动到目标目录
            shutil.move(str(tmp_path), str(final_path))
            elapsed = time.time() - start

            return DownloadResult(
                path=str(final_path),
                size=downloaded,
                elapsed=elapsed,
                engine="python_requests",
                url=url,
            )

        except Exception as e:
            # 清理临时文件
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return None

        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    # ── 引擎 2: aria2c ───────────────────────────────────────

    @staticmethod
    def _try_aria2c(url: str, out_dir: Path, tmp_dir: Path,
                     filename: Optional[str], timeout: int
                     ) -> Optional[DownloadResult]:
        """用 aria2c 下载（多连接加速）。"""
        aria2c = shutil.which("aria2c")
        if not aria2c:
            return None

        start = time.time()

        # 如果没指定文件名，aria2c 会自动处理
        if not filename:
            ct = ""
            disp = ""
            try:
                # 发一个 HEAD 请求获取 Content-Type
                try:
                    req = urllib.request.Request(url, method="HEAD")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        ct = resp.headers.get("Content-Type", "")
                        disp = resp.headers.get("Content-Disposition", "")
                except Exception:
                    pass
            except Exception:  # pragma: no cover
                pass  # pragma: no cover
            filename = _safe_filename(url, ct, disp)

        final_path = out_dir / filename
        final_path = DownloadEngine._deduplicate(final_path)

        # aria2c 的 --dir 和 --out 分开
        try:
            subprocess.run(
                [aria2c, "-x", "4", "-s", "4", "--timeout", "30",
                 "--max-tries", "2", "--retry-wait", "2",
                 "--dir", str(final_path.parent),
                 "--out", final_path.name,
                 url],
                capture_output=True, text=True, timeout=timeout,
            )
            if final_path.exists() and final_path.stat().st_size > 0:
                elapsed = time.time() - start
                return DownloadResult(
                    path=str(final_path),
                    size=final_path.stat().st_size,
                    elapsed=elapsed,
                    engine="aria2c",
                    url=url,
                )
        except Exception:
            pass

        return None

    # ── 引擎 3: wget ──────────────────────────────────────────

    @staticmethod
    def _try_wget(url: str, out_dir: Path, tmp_dir: Path,
                   filename: Optional[str], timeout: int
                   ) -> Optional[DownloadResult]:
        """用 wget 下载。"""
        wget = shutil.which("wget")
        if not wget:
            return None

        start = time.time()

        if not filename:
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    disp = resp.headers.get("Content-Disposition", "")
            except Exception:
                ct = ""
                disp = ""
            filename = _safe_filename(url, ct, disp)

        final_path = out_dir / filename
        final_path = DownloadEngine._deduplicate(final_path)

        try:
            subprocess.run(
                [wget, "-O", str(final_path),
                 "--timeout=30", "--tries=2",
                 "--user-agent=KuafuDownloader/1.0",
                 url],
                capture_output=True, text=True, timeout=timeout,
            )
            if final_path.exists() and final_path.stat().st_size > 0:
                elapsed = time.time() - start
                return DownloadResult(
                    path=str(final_path),
                    size=final_path.stat().st_size,
                    elapsed=elapsed,
                    engine="wget",
                    url=url,
                )
        except Exception:
            pass

        return None

    # ── 引擎 4: curl ──────────────────────────────────────────

    @staticmethod
    def _try_curl(url: str, out_dir: Path, tmp_dir: Path,
                   filename: Optional[str], timeout: int
                   ) -> Optional[DownloadResult]:
        """用 curl 下载。"""
        curl = shutil.which("curl")
        if not curl:
            return None

        start = time.time()

        if not filename:
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    disp = resp.headers.get("Content-Disposition", "")
            except Exception:
                ct = ""
                disp = ""
            filename = _safe_filename(url, ct, disp)

        final_path = out_dir / filename
        final_path = DownloadEngine._deduplicate(final_path)

        try:
            subprocess.run(
                [curl, "-L", "-o", str(final_path),
                 "--connect-timeout", "30",
                 "--max-time", str(timeout),
                 "-A", "KuafuDownloader/1.0",
                 url],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            if final_path.exists() and final_path.stat().st_size > 0:
                elapsed = time.time() - start
                return DownloadResult(
                    path=str(final_path),
                    size=final_path.stat().st_size,
                    elapsed=elapsed,
                    engine="curl",
                    url=url,
                )
        except Exception:
            pass

        return None

    # ── 工具方法 ──────────────────────────────────────────────

    @staticmethod
    def _deduplicate(path: Path) -> Path:
        """如果文件已存在，自动添加数字后缀。"""
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent

        counter = 1
        while True:
            new_name = f"{stem}_{counter}{suffix}"
            new_path = parent / new_name
            if not new_path.exists():
                return new_path
            counter += 1


# ── 便捷函数 ─────────────────────────────────────────────────────

def download_file(url: str, output_dir: Optional[str] = None,
                  filename: Optional[str] = None,
                  timeout: int = DEFAULT_TIMEOUT) -> DownloadResult:
    """下载文件的便捷函数。

    自动选择最佳引擎，返回 DownloadResult。
    """
    return DownloadEngine.download(url, output_dir, filename, timeout)


def list_downloads(download_dir: Optional[str] = None) -> list[dict]:
    """列出已下载的文件。"""
    d = Path(download_dir) if download_dir else DEFAULT_DOWNLOAD_DIR
    if not d.exists():
        return []

    files = []
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and not f.name.startswith("."):
            stat_info = f.stat()
            files.append({
                "name": f.name,
                "path": str(f),
                "size": stat_info.st_size,
                "size_str": _format_size(stat_info.st_size),
                "mtime": stat_info.st_mtime,
            })
    return files


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    else:
        return f"{size / 1024 / 1024 / 1024:.2f} GB"
