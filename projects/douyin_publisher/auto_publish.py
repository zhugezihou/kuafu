"""
抖音自动发布 - 完整流程
1. 加载已保存的 cookies
2. 进入创作者平台上传页
3. 点击"发布视频"按钮
4. 上传视频文件
5. 填写标题
6. 点击发布
"""
import sys
import os
import time
import json
import logging

sys.path.insert(0, os.path.dirname(__file__))
venv_path = "/home/asus/kuafu/venv"
sys.path.insert(0, os.path.join(venv_path, "lib/python3.11/site-packages"))

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

VIDEO_PATH = "/home/asus/kuafu/douyin/templates/test_video.mp4"
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "douyin_cookies.json")
TITLE = "AI自动生成的测试视频 🚀 夸父逐日"
TAGS = ["AI测试", "自动化"]

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
    
    # 加载 cookies
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            cookies = json.load(f)
        ctx.add_cookies(cookies)
        logger.info(f"已加载 {len(cookies)} 条 cookies")
    
    page = ctx.new_page()
    
    try:
        # 进入上传页
        logger.info("进入创作者上传页面...")
        page.goto("https://creator.douyin.com/creator-micro/content/upload", 
                  wait_until="domcontentloaded", timeout=30000)
        
        # 等待页面加载
        logger.info("等待上传组件加载...")
        for i in range(30):
            time.sleep(2)
            has_upload = page.evaluate(
                '() => document.body.innerHTML.includes("上传") || document.body.innerHTML.includes("发布视频")'
            )
            if has_upload:
                logger.info(f"上传组件已加载 ({i*2+2}s)")
                break
        
        time.sleep(2)
        
        # 检查是否已登录
        if "login" in page.url.lower() or "passport" in page.url.lower():
            logger.error("未登录状态，请先扫码登录")
            return False
        
        # 点击"发布视频"按钮
        logger.info("点击'发布视频'按钮...")
        publish_btn = page.locator('text=发布视频')
        if publish_btn.count() > 0:
            publish_btn.first.click()
            time.sleep(2)
            logger.info("已点击发布视频按钮")
        
        # 查找文件上传控件
        file_input = page.locator('input[type="file"]')
        if file_input.count() == 0:
            logger.error("未找到文件上传控件")
            return False
        
        logger.info(f"上传视频: {VIDEO_PATH}")
        file_input.first.set_input_files(VIDEO_PATH)
        logger.info("已选择视频文件，等待上传...")
        
        # 等待上传完成（最多等90秒）
        for i in range(45):
            time.sleep(2)
            if i % 5 == 0:
                logger.info(f"上传中... ({i*2+2}s)")
            
            # 检查上传进度 - 看是否有"上传完成"或编辑区域出现
            try:
                page_content = page.content()
                if "上传完成" in page_content or "编辑" in page_content:
                    logger.info("上传完成！")
                    break
            except:
                pass
        
        time.sleep(3)
        
        # 填写标题
        logger.info(f"填写标题: {TITLE}")
        try:
            # 查找标题输入框
            title_input = page.locator('input[placeholder*="标题"], [placeholder*="title"], .title-input input')
            if title_input.count() > 0:
                title_input.first.fill(TITLE)
                logger.info("标题已填写")
            else:
                # 尝试通用的输入框
                all_inputs = page.locator('input[type="text"], textarea')
                for i in range(all_inputs.count()):
                    try:
                        placeholder = all_inputs.nth(i).get_attribute('placeholder') or ''
                        if '标题' in placeholder or 'title' in placeholder.lower():
                            all_inputs.nth(i).fill(TITLE)
                            logger.info(f"通过placeholder找到标题框: {placeholder}")
                            break
                    except:
                        pass
        except Exception as e:
            logger.warning(f"填写标题失败: {e}")
        
        time.sleep(2)
        
        # 截图保存结果
        screenshot_path = "/home/asus/kuafu/projects/douyin_publisher/publish_result.png"
        page.screenshot(path=screenshot_path)
        logger.info(f"截图已保存: {screenshot_path}")
        
        logger.info("✅ 发布流程执行完成！请检查浏览器窗口确认状态。")
        return True
        
    except Exception as e:
        logger.error(f"发布失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 不关闭浏览器，让用户可以看到结果
        input("按 Enter 键关闭浏览器...")
        b.close()
        p.stop()


if __name__ == "__main__":
    main()
