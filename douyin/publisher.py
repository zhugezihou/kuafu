"""
抖音视频自动发布引擎
════════════════════
使用 Playwright 模拟浏览器操作，自动登录抖音创作者平台并发布视频。
"""

import time
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from .cookie_manager import CookieManager

try:
    from playwright.sync_api import sync_playwright, Page, Browser
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class DouyinPublisher:
    """抖音视频自动发布器"""

    def __init__(self, config: dict = None):
        self.config = config or self._load_config()
        self.cookie_mgr = CookieManager(self.config.get("cookie", {}).get("file", "douyin/cookies.json"))
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    def _load_config(self) -> dict:
        """加载配置"""
        import yaml
        config_path = Path(__file__).parent / "config.yaml"
        if config_path.exists():
            return yaml.safe_load(config_path.read_text())
        return {}

    def _get_browser_config(self) -> dict:
        """获取浏览器配置"""
        browser_cfg = self.config.get("browser", {})
        return {
            "headless": browser_cfg.get("headless", False),
            "slow_mo": browser_cfg.get("slow_mo", 500),
        }

    def start_browser(self) -> None:
        """启动浏览器"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright 未安装。请运行: pip install playwright && playwright install chromium")

        browser_cfg = self._get_browser_config()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=browser_cfg["headless"],
            slow_mo=browser_cfg["slow_mo"],
        )
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        print("[Browser] ✅ 浏览器已启动")

    def close_browser(self) -> None:
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
        if hasattr(self, "playwright") and self.playwright:
            self.playwright.stop()
        print("[Browser] 🚪 浏览器已关闭")

    def login_with_cookies(self) -> bool:
        """用 Cookie 登录"""
        cookies = self.cookie_mgr.load()
        if not cookies:
            return False

        creator_url = self.config.get("account", {}).get("creator_url", "https://creator.douyin.com")
        self.page.goto(creator_url, wait_until="networkidle")

        # 注入 Cookie
        for cookie in cookies:
            try:
                self.page.context.add_cookies([cookie])
            except Exception:
                pass  # 部分 Cookie 可能不兼容

        # 刷新页面验证登录状态
        self.page.goto(creator_url, wait_until="networkidle")
        time.sleep(3)

        # 检查是否登录成功（页面是否跳转到登录页）
        if "login" in self.page.url.lower():
            print("[Login] ⚠️ Cookie 已过期，需要重新登录")
            return False

        print("[Login] ✅ Cookie 登录成功")
        return True

    def manual_login(self) -> None:
        """手动扫码登录"""
        login_url = self.config.get("account", {}).get("login_url", "https://creator.douyin.com/login")
        self.page.goto(login_url, wait_until="networkidle")
        print("[Login] 📱 请在浏览器中扫码登录...")
        print("[Login] ⏳ 等待登录完成（最多 120 秒）...")

        # 等待登录完成（URL 不再包含 login）
        for i in range(120):
            time.sleep(1)
            if "login" not in self.page.url.lower():
                print(f"[Login] ✅ 登录成功！")
                # 保存 Cookie
                cookies = self.page.context.cookies()
                self.cookie_mgr.save(cookies)
                return
            if i % 10 == 0 and i > 0:
                print(f"[Login] ⏳ 已等待 {i} 秒...")

        raise TimeoutError("[Login] ❌ 登录超时（120秒）")

    def ensure_logged_in(self) -> None:
        """确保已登录"""
        if self.login_with_cookies():
            return

        print("[Login] 🔑 需要手动登录...")
        self.manual_login()

    def publish_video(
        self,
        video_path: str,
        title: str = "",
        tags: List[str] = None,
        schedule_time: str = "",
    ) -> bool:
        """
        发布单个视频

        Args:
            video_path: 视频文件路径
            title: 视频标题
            tags: 标签列表
            schedule_time: 定时发布时间（空=立即发布）

        Returns:
            是否发布成功
        """
        if not self.page:
            raise RuntimeError("浏览器未启动，请先调用 start_browser()")

        video_path = Path(video_path)
        if not video_path.exists():
            print(f"[Publish] ❌ 视频文件不存在: {video_path}")
            return False

        tags = tags or self.config.get("publish", {}).get("default_tags", ["日常"])
        creator_url = self.config.get("account", {}).get("creator_url", "https://creator.douyin.com")

        print(f"[Publish] 📤 开始发布: {video_path.name}")
        print(f"           标题: {title or video_path.stem}")
        print(f"           标签: {', '.join(tags)}")

        try:
            # 导航到发布页面
            self.page.goto(f"{creator_url}/creater/video/upload", wait_until="networkidle")
            time.sleep(2)

            # === 上传视频文件 ===
            # 查找文件上传 input
            upload_selector = 'input[type="file"]'
            if self.page.locator(upload_selector).count() == 0:
                # 尝试点击上传按钮
                upload_btn_selectors = [
                    ".upload-btn",
                    "button:has-text('上传视频')",
                    "div:has-text('点击上传')",
                    "[class*='upload']",
                ]
                for sel in upload_btn_selectors:
                    if self.page.locator(sel).count() > 0:
                        self.page.locator(sel).first.click()
                        time.sleep(1)
                        break

            # 上传文件
            file_input = self.page.locator(upload_selector).first
            file_input.set_input_files(str(video_path.absolute()))
            print(f"[Publish] 📤 文件已上传，等待处理...")

            # 等待上传完成
            time.sleep(5)

            # === 填写标题 ===
            if title:
                title_input_selectors = [
                    "input[placeholder*='标题']",
                    "input[placeholder*='标题']",
                    "[class*='title'] input",
                    "textarea[placeholder*='标题']",
                ]
                for sel in title_input_selectors:
                    if self.page.locator(sel).count() > 0:
                        self.page.locator(sel).first.fill(title)
                        print(f"[Publish] ✏️ 标题已填写: {title}")
                        break

            # === 添加标签 ===
            for tag in tags:
                tag_input_selectors = [
                    "input[placeholder*='话题']",
                    "input[placeholder*='标签']",
                    "[class*='tag'] input",
                ]
                for sel in tag_input_selectors:
                    if self.page.locator(sel).count() > 0:
                        self.page.locator(sel).first.fill(tag)
                        self.page.keyboard.press("Enter")
                        time.sleep(0.5)
                        print(f"[Publish] 🏷️ 标签已添加: #{tag}")
                        break

            # === 设置发布时间 ===
            if schedule_time:
                print(f"[Publish] ⏰ 定时发布: {schedule_time}")
                # 查找定时发布选项并设置时间
                schedule_btn_selectors = [
                    "span:has-text('定时发布')",
                    "label:has-text('定时发布')",
                    "[class*='schedule']",
                ]
                for sel in schedule_btn_selectors:
                    if self.page.locator(sel).count() > 0:
                        self.page.locator(sel).first.click()
                        time.sleep(1)
                        break

            # === 点击发布 ===
            publish_btn_selectors = [
                "button:has-text('发布')",
                "button:has-text('发布')",
                "[class*='publish'] button",
                "div[class*='publish']",
            ]

            for sel in publish_btn_selectors:
                if self.page.locator(sel).count() > 0:
                    self.page.locator(sel).first.click()
                    print(f"[Publish] 🚀 点击发布按钮")
                    time.sleep(3)
                    break

            print(f"[Publish] ✅ 发布成功: {video_path.name}")
            self._record_history(video_path, title, tags, schedule_time)
            return True

        except Exception as e:
            print(f"[Publish] ❌ 发布失败: {e}")
            # 截图保存现场
            screenshot_dir = Path("douyin/screenshots")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.page.screenshot(path=str(screenshot_dir / f"error_{timestamp}.png"))
            print(f"[Publish] 📸 已保存错误截图: douyin/screenshots/error_{timestamp}.png")
            return False

    def _record_history(self, video_path: Path, title: str, tags: list, schedule: str) -> None:
        """记录发布历史"""
        history_file = Path(self.config.get("history_file", "douyin/publish_history.json"))
        history = []
        if history_file.exists():
            history = json.loads(history_file.read_text())

        history.append({
            "video": str(video_path),
            "title": title or video_path.stem,
            "tags": tags,
            "schedule": schedule,
            "published_at": datetime.now().isoformat(),
        })

        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(history, indent=2, ensure_ascii=False))

    def publish_batch(self, video_dir: str, title_template: str = None) -> dict:
        """
        批量发布目录下的所有视频

        Args:
            video_dir: 视频目录
            title_template: 标题模板（可用 {name} 占位）

        Returns:
            {"success": [文件名], "failed": [文件名]}
        """
        video_dir = Path(video_dir)
        if not video_dir.exists():
            print(f"[Batch] ❌ 目录不存在: {video_dir}")
            return {"success": [], "failed": []}

        # 支持的视频格式
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"}
        videos = [f for f in video_dir.iterdir() if f.suffix.lower() in video_exts]

        if not videos:
            print(f"[Batch] ⚠️ 目录中没有视频文件: {video_dir}")
            return {"success": [], "failed": []}

        print(f"[Batch] 📦 找到 {len(videos)} 个视频，开始批量发布...")

        result = {"success": [], "failed": []}
        for i, video in enumerate(videos):
            print(f"\n{'='*50}")
            print(f"[Batch] [{i+1}/{len(videos)}] 处理: {video.name}")
            print(f"{'='*50}")

            title = (title_template or "{name}").format(name=video.stem)

            ok = self.publish_video(str(video), title=title)
            if ok:
                result["success"].append(video.name)
            else:
                result["failed"].append(video.name)

            # 发布间隔
            min_interval = self.config.get("publish", {}).get("min_interval", 300)
            if i < len(videos) - 1:
                print(f"[Batch] ⏳ 等待 {min_interval} 秒后发布下一个...")
                time.sleep(min_interval)

        print(f"\n[Batch] 📊 发布完成: 成功 {len(result['success'])} | 失败 {len(result['failed'])}")
        return result


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="夸父抖音自动发布工具")
    parser.add_argument("action", choices=["login", "publish", "batch"], help="操作类型")
    parser.add_argument("--video", help="视频文件路径")
    parser.add_argument("--dir", help="视频目录路径")
    parser.add_argument("--title", help="视频标题")
    parser.add_argument("--tags", nargs="+", default=[], help="标签列表")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--schedule", help="定时发布时间")

    args = parser.parse_args()
    publisher = DouyinPublisher()

    try:
        publisher.start_browser()

        if args.action == "login":
            publisher.ensure_logged_in()
            print("[Main] ✅ 登录完成，Cookie 已保存")

        elif args.action == "publish":
            if not args.video:
                print("[Main] ❌ 请指定 --video")
                return
            publisher.ensure_logged_in()
            publisher.publish_video(args.video, title=args.title or "", tags=args.tags or None)

        elif args.action == "batch":
            if not args.dir:
                print("[Main] ❌ 请指定 --dir")
                return
            publisher.ensure_logged_in()
            publisher.publish_batch(args.dir, title_template=args.title)

    finally:
        publisher.close_browser()


if __name__ == "__main__":
    main()
