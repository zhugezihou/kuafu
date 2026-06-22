#!/home/asus/kuafu/venv/bin/python
"""
抖音发布 - 扫码登录 + 上传
先清除旧cookies，打开登录页让用户扫码
"""
import sys, os, time, json, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from playwright.sync_api import sync_playwright

DIR = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(os.path.dirname(DIR))
VIDEO = os.path.join(PARENT, "douyin/templates/test_video.mp4")
COOKIE = os.path.join(DIR, "douyin_cookies.json")
TITLE = "AI自动生成的测试视频 🚀 夸父逐日"

if not os.path.exists(VIDEO):
    logger.error(f"❌ 视频不存在: {VIDEO}")
    sys.exit(1)

size_mb = os.path.getsize(VIDEO) / 1024 / 1024
logger.info(f"📹 视频: {VIDEO} ({size_mb:.1f}MB)")

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
    # 直接访问上传页
    logger.info("访问创作者上传页面...")
    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
    time.sleep(5)
    logger.info(f"URL: {page.url}")

    # 检查登录状态
    if 'login' in page.url.lower() or 'passport' in page.url.lower():
        logger.info("=" * 50)
        logger.info("📱 请在浏览器窗口中用抖音APP扫码登录！")
        logger.info("=" * 50)
        
        # 等待登录（最多2分钟）
        for i in range(120):
            time.sleep(2)
            current_url = page.url.lower()
            if 'login' not in current_url and 'passport' not in current_url:
                logger.info(f"✅ 登录成功！")
                # 保存cookies
                cookies = ctx.cookies()
                with open(COOKIE, 'w') as f:
                    json.dump(cookies, f)
                logger.info(f"已保存 {len(cookies)} 条cookies")
                break
            if i % 10 == 0:
                logger.info(f"⏳ 等待扫码... {i*2}s/240s")
        else:
            logger.error("❌ 扫码超时")
            sys.exit(1)
    else:
        logger.info("✅ 已登录")

    # 确认在上传页
    if 'upload' not in page.url:
        logger.info("导航到上传页面...")
        page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
        time.sleep(5)
    
    logger.info(f"当前页面: {page.url}")
    page.screenshot(path=os.path.join(DIR, "step_upload.png"))

    # ---- 上传视频 ----
    logger.info("开始上传视频...")
    
    # 先找"发布视频"按钮
    for btn_text in ["发布视频", "上传视频", "选择文件"]:
        try:
            btn = page.get_by_text(btn_text).first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                logger.info(f"点击了'{btn_text}'按钮")
                time.sleep(3)
                break
        except:
            pass
    
    # 找file input上传
    uploaded = False
    for attempt in range(5):
        try:
            file_input = page.locator('input[type="file"]')
            if file_input.count() > 0:
                file_input.first.set_input_files(VIDEO)
                uploaded = True
                logger.info("✅ 文件已上传")
                break
        except:
            pass
        time.sleep(2)
    
    if not uploaded:
        logger.error("❌ 无法上传文件")
        sys.exit(1)

    # 等待上传 + 填写标题
    logger.info("⏳ 等待上传处理（约3分钟）...")
    for i in range(90):
        time.sleep(2)
        if i % 15 == 0:
            logger.info(f"处理中... {i*2}s/180s")
    
    time.sleep(30)
    page.screenshot(path=os.path.join(DIR, "after_upload.png"))
    
    # 填写标题
    logger.info("填写标题...")
    for placeholder in ["标题", "title", "Title", "添加标题", "请输入标题"]:
        try:
            inp = page.locator(f'input[placeholder*="{placeholder}"]').first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                time.sleep(0.3)
                inp.fill('')
                time.sleep(0.3)
                inp.fill(TITLE)
                logger.info(f"✅ 标题已填写")
                break
        except:
            pass
    
    # 也尝试找div编辑框
    try:
        edit_div = page.locator('[contenteditable="true"]').first
        if edit_div.count() > 0 and edit_div.is_visible():
            edit_div.click()
            time.sleep(0.3)
            page.keyboard.select_all()
            page.keyboard.press('Delete')
            page.keyboard.type(TITLE, delay=50)
            logger.info("✅ 通过contenteditable填写标题")
    except:
        pass
    
    time.sleep(2)
    page.screenshot(path=os.path.join(DIR, "final.png"))
    
    print("\n" + "="*50)
    print("✅ 发布流程已完成！")
    print(f"   视频: {VIDEO} ({size_mb:.1f}MB)")
    print(f"   标题: {TITLE}")
    print(f"   截图: {DIR}/final.png")
    print("="*50)
    print("\n⚠️ 请在浏览器中手动检查并点击发布按钮")
    
    input("\n按 Enter 关闭浏览器...")

except Exception as e:
    logger.error(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    try:
        b.close()
        p.stop()
    except:
        pass
