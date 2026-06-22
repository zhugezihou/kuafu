#!/home/asus/kuafu/venv/bin/python
"""检查上传页面结构"""
import sys, os, time, json, logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from playwright.sync_api import sync_playwright

DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE = os.path.join(DIR, "douyin_cookies.json")

p = sync_playwright().start()
b = p.chromium.launch(
    executable_path='/home/asus/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome',
    headless=False,
    args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
)
ctx = b.new_context(viewport={'width': 1280, 'height': 800})
page = ctx.new_page()

if os.path.exists(COOKIE):
    with open(COOKIE) as f:
        cookies = json.load(f)
    ctx.add_cookies(cookies)

try:
    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
    time.sleep(10)
    
    logger.info(f"URL: {page.url}")
    logger.info(f"Title: {page.title()}")
    
    # 保存完整HTML
    html = page.content()
    with open(os.path.join(DIR, "upload_page_current.html"), "w") as f:
        f.write(html)
    
    # 提取所有可见文本
    body_text = page.locator('body').inner_text()
    print("=== 页面可见文本 ===")
    print(body_text[:3000])
    
    print("\n=== 所有button文本 ===")
    buttons = page.locator('button')
    for i in range(buttons.count()):
        try:
            text = buttons.nth(i).inner_text()
            if text.strip():
                print(f"  [{i}] {text.strip()[:100]}")
        except:
            pass
    
    print("\n=== 所有input ===")
    inputs = page.locator('input')
    for i in range(inputs.count()):
        try:
            placeholder = inputs.nth(i).get_attribute('placeholder') or ''
            type_attr = inputs.nth(i).get_attribute('type') or ''
            print(f"  [{i}] type={type_attr}, placeholder={placeholder}")
        except:
            pass
    
    print("\n=== 包含'发布'或'上传'的元素 ===")
    for text in ["发布", "上传", "视频", "选择文件"]:
        els = page.get_by_text(text)
        if els.count() > 0:
            print(f"  文本'{text}': {els.count()} 个元素")
    
    page.screenshot(path=os.path.join(DIR, "page_check.png"))
    
except Exception as e:
    logger.error(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    input("按 Enter 关闭...")
    try:
        b.close()
        p.stop()
    except:
        pass
