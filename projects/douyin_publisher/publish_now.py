#!/home/asus/kuafu/venv/bin/python
"""
抖音自动发布 - 当前执行脚本
先尝试用已有cookies，失败则引导扫码登录
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
PARENT_DIR = os.path.dirname(os.path.dirname(PROJECT_DIR))
VIDEO_PATH = os.path.join(PARENT_DIR, "douyin/templates/test_video.mp4")
COOKIE_FILE = os.path.join(PROJECT_DIR, "douyin_cookies.json")
TITLE = "AI自动生成的测试视频 🚀 夸父逐日"
SCREENSHOT_DIR = PROJECT_DIR

def wait_and_fill_title(page, title, timeout=30):
    """等待标题输入框出现并填写"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            for placeholder in ["标题", "title", "Title", "添加标题", "请输入标题"]:
                inp = page.locator(f'input[placeholder*="{placeholder}"]').first
                if inp.count() > 0 and inp.is_visible():
                    inp.click()
                    time.sleep(0.3)
                    inp.fill('')
                    time.sleep(0.3)
                    inp.fill(title)
                    logger.info(f"✅ 标题已填写: {title}")
                    return True
            # 尝试找div可编辑区域
            edit_div = page.locator('[contenteditable="true"]').first
            if edit_div.count() > 0 and edit_div.is_visible():
                edit_div.click()
                time.sleep(0.3)
                edit_div.fill(title)
                logger.info(f"✅ 通过contenteditable填写标题")
                return True
        except:
            pass
        time.sleep(1)
    logger.warning("⚠️ 未能找到标题输入框")
    return False

def wait_and_upload(page, video_path, timeout=120):
    """等待上传控件出现并上传视频"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            # 方法1: 直接找file input
            file_input = page.locator('input[type="file"]')
            if file_input.count() > 0 and file_input.first.is_visible():
                file_input.first.set_input_files(video_path)
                logger.info(f"✅ 通过file input上传: {video_path}")
                return True
            
            # 方法2: 找"上传"或"发布视频"按钮并点击
            for btn_text in ["上传视频", "发布视频", "选择文件", "上传"]:
                btn = page.locator(f'text={btn_text}').first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    time.sleep(2)
                    file_input = page.locator('input[type="file"]')
                    if file_input.count() > 0:
                        file_input.first.set_input_files(video_path)
                        logger.info(f"✅ 点击'{btn_text}'后上传文件")
                        return True
        except:
            pass
        time.sleep(2)
    
    logger.error("❌ 上传控件未找到")
    return False

def main():
    if not os.path.exists(VIDEO_PATH):
        logger.error(f"❌ 视频文件不存在: {VIDEO_PATH}")
        return False
    
    video_size = os.path.getsize(VIDEO_PATH)
    logger.info(f"视频文件: {VIDEO_PATH} ({video_size/1024/1024:.1f}MB)")

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
    page = ctx.new_page()

    try:
        # 加载cookies
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE) as f:
                cookies = json.load(f)
            ctx.add_cookies(cookies)
            logger.info(f"已加载 {len(cookies)} 条 cookies")

        # 访问上传页
        logger.info("访问创作者上传页面...")
        page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
        time.sleep(5)
        logger.info(f"URL: {page.url}")

        # 检查是否需要登录
        if 'login' in page.url.lower() or 'passport' in page.url.lower():
            logger.info("❌ cookies已过期，需要重新扫码登录")
            logger.info("=" * 50)
            logger.info("请在打开的浏览器窗口中用抖音APP扫码登录")
            logger.info("=" * 50)
            
            page.goto("https://creator.douyin.com/")
            time.sleep(3)
            
            # 等待扫码（最多120秒）
            for i in range(120):
                time.sleep(2)
                current_url = page.url.lower()
                if 'login' not in current_url and 'passport' not in current_url:
                    logger.info(f"✅ 登录成功！URL: {page.url}")
                    # 保存新cookies
                    new_cookies = ctx.cookies()
                    with open(COOKIE_FILE, 'w') as f:
                        json.dump(new_cookies, f)
                    logger.info(f"已保存 {len(new_cookies)} 条新cookies")
                    
                    # 重新导航到上传页
                    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
                    time.sleep(5)
                    break
                if i % 10 == 0:
                    logger.info(f"⏳ 等待扫码中... {i*2}s/240s")
            else:
                logger.error("❌ 扫码超时")
                return False
        else:
            logger.info("✅ cookies有效，已登录！")

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "logged_in.png"))
        logger.info(f"当前页面: {page.url}")

        # 上传视频
        logger.info("开始上传视频...")
        if not wait_and_upload(page, VIDEO_PATH):
            # 尝试JS注入方式
            try:
                page.evaluate("""
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.style.display = 'none';
                    document.body.appendChild(input);
                """)
                file_input = page.locator('input[type="file"]').last
                file_input.set_input_files(VIDEO_PATH)
                logger.info("✅ 通过JS注入上传文件")
            except Exception as e:
                logger.error(f"❌ 所有上传方式均失败: {e}")
                return False

        # 等待上传完成
        logger.info("⏳ 等待上传完成（约3分钟）...")
        for i in range(90):
            time.sleep(2)
            if i % 15 == 0:
                logger.info(f"上传中... {i*2}s/180s")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"upload_progress_{i}.png"))
        time.sleep(30)  # 额外等待

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "upload_done.png"))
        logger.info("✅ 上传完成")

        # 填写标题
        logger.info("填写标题...")
        wait_and_fill_title(page, TITLE)
        time.sleep(2)
        
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "final.png"))
        
        print("\n" + "=" * 50)
        print("✅ 发布流程执行完成！")
        print(f"   视频: {VIDEO_PATH}")
        print(f"   标题: {TITLE}")
        print(f"   大小: {video_size/1024/1024:.1f}MB")
        print(f"   截图: {SCREENSHOT_DIR}/final.png")
        print("=" * 50)
        print("\n⚠️ 请在浏览器中手动检查并点击发布按钮完成最终发布")
        
        input("\n按 Enter 关闭浏览器...")
        return True

    except Exception as e:
        logger.error(f"❌ 失败: {e}")
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
