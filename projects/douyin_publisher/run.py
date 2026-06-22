"""抖音发布 - 启动脚本"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# 使用虚拟环境的 playwright
venv_path = "/home/asus/kuafu/venv"
sys.path.insert(0, os.path.join(venv_path, "lib/python3.11/site-packages"))

from publisher import DouyinPublisher
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

VIDEO_PATH = "/home/asus/kuafu/douyin/templates/test_video.mp4"

publisher = DouyinPublisher(headless=False)
try:
    publisher.start()
    
    # 先尝试加载 cookies
    has_cookies = publisher.load_cookies()
    
    # 访问创作者平台
    publisher.page.goto("https://creator.douyin.com/")
    import time
    time.sleep(5)
    
    # 截图看看当前状态
    screenshot_path = "/home/asus/kuafu/projects/douyin_publisher/status.png"
    publisher.page.screenshot(path=screenshot_path)
    print(f"截图已保存: {screenshot_path}")
    print(f"当前URL: {publisher.page.url}")
    
    # 检查登录状态
    if "login" in publisher.page.url.lower():
        print("❌ 需要登录")
        # 尝试扫码登录
        print("请用抖音APP扫码登录...")
        success = publisher.login_by_qr()
        if success:
            print("✅ 登录成功！")
        else:
            print("❌ 登录失败")
    else:
        print("✅ 已登录状态")
        
finally:
    publisher.close()
