"""抖音发布视频 - v2 更稳健的版本"""
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(__file__))
venv_path = "/home/asus/kuafu/venv"
sys.path.insert(0, os.path.join(venv_path, "lib/python3.11/site-packages"))

from publisher import DouyinPublisher

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

VIDEO_PATH = "/home/asus/kuafu/douyin/templates/test_video.mp4"

publisher = DouyinPublisher(headless=False)
try:
    publisher.start()
    
    # 访问创作者平台 - 使用更宽松的等待策略
    logger.info("访问抖音创作者平台...")
    publisher.page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    
    # 再等待一下，让页面完全加载
    try:
        publisher.page.wait_for_load_state("networkidle", timeout=30000)
    except:
        logger.info("网络空闲等待超时，继续执行...")
    
    logger.info(f"当前URL: {publisher.page.url}")
    publisher.page.screenshot(path="/home/asus/kuafu/projects/douyin_publisher/step1.png")
    
    if "login" in publisher.page.url.lower():
        logger.info("需要扫码登录...")
        publisher.login_by_qr()
    
    # 进入发布页面
    logger.info("进入发布页面...")
    publisher.page.goto("https://creator.douyin.com/creator-micro/content/upload", 
                        wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    
    try:
        publisher.page.wait_for_load_state("networkidle", timeout=30000)
    except:
        pass
    
    publisher.page.screenshot(path="/home/asus/kuafu/projects/douyin_publisher/step2_upload.png")
    logger.info(f"上传页面URL: {publisher.page.url}")
    
    # 上传视频
    logger.info(f"上传视频: {VIDEO_PATH}")
    
    # 查找文件上传input - 多种选择器
    selectors = [
        'input[type="file"]',
        'input[accept*="video"]',
        '.upload-input input',
        '[data-testid*="upload"] input'
    ]
    
    file_input = None
    for selector in selectors:
        locator = publisher.page.locator(selector)
        if locator.count() > 0:
            file_input = locator.first
            logger.info(f"通过选择器 '{selector}' 找到文件上传控件")
            break
    
    if file_input:
        file_input.set_input_files(VIDEO_PATH)
        logger.info("已选择视频文件，等待上传...")
        
        # 等待上传完成 - 最多等90秒
        for i in range(45):
            time.sleep(2)
            if i % 5 == 0:
                logger.info(f"上传中... ({i*2+2}s)")
        
        publisher.page.screenshot(path="/home/asus/kuafu/projects/douyin_publisher/step3_uploaded.png")
        logger.info("上传流程完成")
    else:
        logger.error("未找到文件上传控件")
        # 输出页面HTML片段帮助调试
        html = publisher.page.content()
        with open("/home/asus/kuafu/projects/douyin_publisher/page_html.txt", "w") as f:
            f.write(html[:5000])
        logger.info("已保存页面HTML前5000字符")
        
except Exception as e:
    logger.error(f"发布失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    publisher.close()
