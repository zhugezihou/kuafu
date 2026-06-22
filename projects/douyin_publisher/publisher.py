"""
抖音自动发布器 - 使用 Playwright 自动化发布视频到抖音创作者平台
"""
import os
import json
import time
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CHROMIUM_PATH = "/home/asus/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "douyin_cookies.json")


class DouyinPublisher:
    """抖音创作者平台视频发布器"""

    def __init__(self, headless=False):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None

    def start(self):
        """启动浏览器"""
        logger.info("启动浏览器...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.page = self.context.new_page()
        logger.info("浏览器启动完成")

    def load_cookies(self):
        """加载已保存的 cookies"""
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)
            self.context.add_cookies(cookies)
            logger.info(f"已加载 {len(cookies)} 条 cookies")
            return True
        logger.warning("未找到 cookie 文件")
        return False

    def save_cookies(self):
        """保存 cookies"""
        cookies = self.context.cookies()
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookies, f, indent=2)
        logger.info(f"已保存 {len(cookies)} 条 cookies")

    def login_by_qr(self):
        """扫码登录抖音创作者平台"""
        logger.info("打开抖音创作者平台登录页...")
        self.page.goto("https://creator.douyin.com/")
        time.sleep(3)

        # 检查是否已登录
        if "login" not in self.page.url.lower():
            logger.info("检测到已登录状态")
            self.save_cookies()
            return True

        logger.info("请使用抖音APP扫码登录...")
        # 等待扫码完成，最多等120秒
        for i in range(120):
            time.sleep(1)
            if "login" not in self.page.url.lower():
                logger.info("扫码登录成功！")
                self.save_cookies()
                return True
            if i % 10 == 0:
                logger.info(f"等待扫码中... ({i+1}s)")

        logger.error("扫码登录超时")
        return False

    def publish_video(self, video_path, title="", tags=None):
        """发布视频"""
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return False

        logger.info(f"开始发布视频: {video_path}")

        # 尝试用已保存的 cookies 登录
        self.load_cookies()
        self.page.goto("https://creator.douyin.com/")
        time.sleep(3)

        # 检查登录状态
        if "login" in self.page.url.lower():
            logger.info("需要重新登录")
            if not self.login_by_qr():
                return False

        # 进入发布页面 - 先访问首页确保登录状态
        logger.info("访问创作者首页...")
        self.page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        logger.info(f"当前URL: {self.page.url}")

        # 检查是否被重定向到登录页
        if "login" in self.page.url.lower() or "passport" in self.page.url.lower():
            logger.info("Cookies已过期，需要重新扫码登录")
            if not self.login_by_qr():
                return False
            logger.info("登录成功，重新进入上传页面")

        # 进入上传页面
        logger.info("进入上传页面...")
        self.page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # 等待上传组件加载
        logger.info("等待上传组件加载...")
        time.sleep(10)

        # 查找并点击"发布视频"按钮激活文件上传控件
        logger.info("查找并点击'发布视频'按钮...")
        publish_btn = self.page.get_by_text("发布视频")
        if publish_btn.count() > 0:
            publish_btn.first.click()
            logger.info("已点击'发布视频'按钮")
            time.sleep(3)
        else:
            logger.info("尝试其他选择器查找发布按钮...")
            for selector in ['button:has-text("发布视频")', '.upload-btn', '[class*="upload"] button']:
                btn = self.page.locator(selector)
                if btn.count() > 0:
                    btn.first.click()
                    logger.info(f"已点击: {selector}")
                    time.sleep(3)
                    break

        # 上传视频 - 通过文件选择器
        logger.info("上传视频文件...")
        file_input = self.page.locator('input[type="file"]').first
        file_input.wait_for(state="attached", timeout=30000)
        file_input.set_input_files(video_path)
        logger.info("文件已选择，等待上传...")
        time.sleep(5)

        # 等待上传完成
        logger.info("等待上传完成...")
        for i in range(90):  # 最多等3分钟
            time.sleep(2)
            try:
                # 检查是否有视频预览出现
                preview = self.page.locator('video, [class*="preview"], [class*="player"]')
                if preview.count() > 0:
                    logger.info(f"视频预览已出现，上传完成 ({i*2}s)")
                    break
                # 检查进度元素是否消失
                progress = self.page.locator('.upload-progress, [class*="progress"], [class*="uploading"]')
                if progress.count() == 0 and i > 5:
                    logger.info(f"上传进度元素消失 ({i*2}s)")
                    break
            except:
                break

        # 填写标题
        if title:
            logger.info(f"填写标题: {title}")
            try:
                title_input = self.page.locator('input[placeholder*="标题"], .title-input input, [placeholder*="填写视频标题"]')
                title_input.first.wait_for(state="visible", timeout=10000)
                title_input.first.fill(title)
                logger.info("标题已填写")
            except Exception as e:
                logger.warning(f"填写标题失败: {e}")

        # 添加标签
        if tags:
            for tag in tags:
                try:
                    tag_input = self.page.locator('input[placeholder*="话题"], .tag-input input')
                    tag_input.fill(tag)
                    time.sleep(0.5)
                    tag_input.press("Enter")
                    time.sleep(0.5)
                except:
                    pass

        # 点击发布（精确匹配"发布"按钮，排除"高清发布"）
        logger.info("点击发布按钮...")
        try:
            # 使用精确匹配的"发布"按钮
            publish_btn = self.page.get_by_role("button", name="发布", exact=True)
            if publish_btn.count() == 0:
                # 备用：使用class选择器
                publish_btn = self.page.locator('button.primary-cECiOJ')
            publish_btn.click()
            logger.info("已点击发布按钮！")
            time.sleep(3)
            return True
        except Exception as e:
            logger.error(f"点击发布失败: {e}")
            return False

    def close(self):
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
        logger.info("浏览器已关闭")


def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description="抖音自动发布器")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("--title", default="", help="视频标题")
    parser.add_argument("--tags", nargs="+", default=[], help="话题标签")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    args = parser.parse_args()

    publisher = DouyinPublisher(headless=args.headless)
    try:
        publisher.start()
        success = publisher.publish_video(args.video, args.title, args.tags)
        if success:
            logger.info("✅ 视频发布成功！")
        else:
            logger.error("❌ 视频发布失败")
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
