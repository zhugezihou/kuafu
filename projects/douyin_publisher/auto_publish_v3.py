#!/home/asus/kuafu/venv/bin/python
"""
抖音自动发布 v2 - 完整流程
"""
import sys
import os
import time
import json
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from playwright.sync_api import sync_playwright

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_PATH = os.path.join(os.path.dirname(os.path.dirname(PROJECT_DIR)), "douyin/templates/test_video.mp4")
COOKIE_FILE = os.path.join(PROJECT_DIR, "douyin_cookies.json")
TITLE = "AI自动生成的测试视频 🚀 夸父逐日"
SCREENSHOT_DIR = PROJECT_DIR

def main():
    p = sync_playwright().start()
    b = p.chromium.launch(
        executable_path='/home/asus/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome',
        headless=False,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
    )
    ctx = b.new_context(
        viewport={'width': 1280, 'height': 800},
        user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    )

    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            cookies = json.load(f)
        ctx.add_cookies(cookies)
        logger.info(f"已加载 {len(cookies)} 条 cookies")

    page = ctx.new_page()

    try:
        logger.info("进入创作者上传页面...")
        page.goto("https://creator.douyin.com/creator-micro/content/upload")
        time.sleep(8)

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "step1.png"))
        logger.info(f"当前URL: {page.url}")

        if 'login' in page.url.lower():
            logger.info("cookies已过期，需要重新登录")
            page.goto("https://creator.douyin.com/")
            time.sleep(3)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "login_status.png"))
            logger.info("请用抖音APP扫码登录（60秒内）...")
            
            for i in range(60):
                time.sleep(5)
                current_url = page.url.lower()
                if 'login' not in current_url and 'passport' not in current_url:
                    logger.info("登录成功！")
                    new_cookies = ctx.cookies()
                    with open(COOKIE_FILE, 'w') as f:
                        json.dump(new_cookies, f)
                    logger.info(f"已保存 {len(new_cookies)} 条新cookies")
                    break
                logger.info(f"等待扫码... {i+1}/60")
            else:
                logger.error("扫码超时")
                return False

            page.goto("https://creator.douyin.com/creator-micro/content/upload")
            time.sleep(8)

        logger.info("已登录，进入上传页面")
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "step2_upload.png"))

        logger.info("等待上传组件加载...")
        time.sleep(10)

        logger.info("点击发布视频按钮...")
        clicked = False
        
        publish_btn = page.get_by_text("发布视频")
        if publish_btn.count() > 0:
            publish_btn.first.click()
            clicked = True
            logger.info("已点击发布视频按钮（文本匹配）")
            time.sleep(3)
        
        if not clicked:
            for selector in [
                'button:has-text("发布视频")',
                '.semi-button:has-text("发布视频")',
                '[class*="upload"] button',
                '.semi-button',
                'button'
            ]:
                btn = page.locator(selector)
                if btn.count() > 0:
                    try:
                        btn_text = btn.first.text_content() or ""
                        if "发布" in btn_text or "上传" in btn_text:
                            btn.first.click()
                            clicked = True
                            logger.info(f"已点击: {selector}")
                            time.sleep(3)
                            break
                    except:
                        pass

        if not clicked:
            logger.warning("未找到发布视频按钮，尝试直接找文件输入框")

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "after_click_upload.png"))

        logger.info("上传视频文件...")
        file_input = page.locator('input[type="file"]').first
        file_input.wait_for(state="attached", timeout=30000)
        file_input.set_input_files(VIDEO_PATH)
        logger.info("文件已选择，等待上传...")
        time.sleep(3)

        logger.info("等待上传完成...")
        upload_done = False
        for i in range(120):
            time.sleep(2)
            try:
                page_content = page.content()
                if '100%' in page_content or '上传成功' in page_content:
                    logger.info("上传完成")
                    upload_done = True
                    break
            except:
                pass
            if i % 10 == 0:
                logger.info(f"上传中... {i*2}s")

        if not upload_done:
            logger.info("继续等待上传完成（额外30秒）...")
            time.sleep(30)

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "upload_complete.png"))

        logger.info("填写标题...")
        title_filled = False
        
        for placeholder_text in ["标题", "title", "Title", "添加标题"]:
            try:
                input_el = page.locator(f'input[placeholder*="{placeholder_text}"]').first
                if input_el.count() > 0:
                    input_el.click()
                    input_el.fill('')
                    time.sleep(0.5)
                    input_el.fill(TITLE)
                    title_filled = True
                    logger.info(f"标题已填写: {TITLE}")
                    break
            except:
                pass

        if not title_filled:
            try:
                all_inputs = page.locator('input, textarea, [contenteditable="true"]')
                for i in range(all_inputs.count()):
                    try:
                        el = all_inputs.nth(i)
                        if el.is_visible():
                            el.click()
                            time.sleep(0.3)
                            el.fill(TITLE)
                            title_filled = True
                            logger.info(f"通过索引 {i} 填写了标题")
                            break
                    except:
                        pass
            except Exception as e:
                logger.warning(f"遍历输入框失败: {e}")

        if title_filled:
            logger.info(f"标题已设置: {TITLE}")
        else:
            logger.warning("未能填写标题")

        time.sleep(3)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "publish_result.png"))
        logger.info("发布流程执行完成！")
        
        print("\n" + "="*50)
        print("发布流程已执行完成！")
        print(f"   - 视频: {VIDEO_PATH}")
        print(f"   - 标题: {TITLE}")
        print(f"   - 截图: {SCREENSHOT_DIR}/publish_result.png")
        print("="*50)
        print("\n请在浏览器中检查发布状态，可能需要手动点击发布按钮")
        
        input("\n按 Enter 键关闭浏览器...")
        return True

    except Exception as e:
        logger.error(f"发布失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            b.close()
            p.stop()
        except:
            pass


if __name__ == "__main__":
    main()
