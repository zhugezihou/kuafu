#!/home/asus/kuafu/venv/bin/python
"""
抖音发布 - 直接上传方案
使用更真实的浏览器指纹
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
    args=[
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-web-security',
        '--disable-features=IsolateOrigins,site-per-process'
    ]
)

# 更真实的上下文
ctx = b.new_context(
    viewport={'width': 1920, 'height': 1080},
    user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    locale='zh-CN',
    timezone_id='Asia/Shanghai'
)

# 注入反检测脚本
page = ctx.new_page()
page.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
""")

try:
    if os.path.exists(COOKIE):
        with open(COOKIE) as f:
            cookies = json.load(f)
        ctx.add_cookies(cookies)
        logger.info(f"已加载 {len(cookies)} 条cookies")

    logger.info("访问上传页面...")
    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="networkidle", timeout=60000)
    time.sleep(10)
    logger.info(f"URL: {page.url}")

    if 'login' in page.url.lower() or 'passport' in page.url.lower():
        logger.info("需要扫码登录")
        for i in range(120):
            time.sleep(2)
            if 'login' not in page.url.lower() and 'passport' not in page.url.lower():
                logger.info("✅ 登录成功")
                cookies = ctx.cookies()
                with open(COOKIE, 'w') as f:
                    json.dump(cookies, f)
                break
            if i % 10 == 0:
                logger.info(f"等待扫码... {i*2}s")
        else:
            logger.error("扫码超时")
            sys.exit(1)
        time.sleep(5)

    # 等待页面完全加载
    logger.info("等待页面加载...")
    page.wait_for_load_state("networkidle", timeout=30000)
    time.sleep(5)
    
    page.screenshot(path=os.path.join(DIR, "page_loaded.png"))
    logger.info(f"页面标题: {page.title()}")
    
    # 获取页面所有可见文本
    body_text = page.evaluate("() => document.body.innerText")
    logger.info(f"页面文本(前500字): {body_text[:500]}")
    
    # 查找所有可见的交互元素
    all_visible = page.evaluate("""() => {
        const els = document.querySelectorAll('button, a, [role="button"], input, [tabindex]');
        const result = [];
        els.forEach((el, i) => {
            if (el.offsetParent !== null) {
                const rect = el.getBoundingClientRect();
                result.push({
                    index: i,
                    tag: el.tagName,
                    type: el.type || '',
                    text: (el.innerText || el.value || el.placeholder || '').substring(0, 30),
                    class: (el.className || '').substring(0, 40),
                    rect: `${Math.round(rect.x)},${Math.round(rect.y)} ${Math.round(rect.w)}x${Math.round(rect.h)}`,
                    visible: rect.width > 0 && rect.height > 0
                });
            }
        });
        return result;
    }""")
    
    logger.info(f"可见交互元素: {len(all_visible)}")
    for el in all_visible[:30]:
        logger.info(f"  [{el['index']}] <{el['tag']}> type={el['type']} text=\"{el['text']}\" class=\"{el['class']}\" pos={el['rect']}")
    
    # 查找file input
    file_count = page.locator('input[type="file"]').count()
    logger.info(f"file input: {file_count}")
    
    # 如果有file input直接上传
    if file_count > 0:
        page.locator('input[type="file"]').first.set_input_files(VIDEO)
        logger.info("✅ 文件已上传")
    else:
        # 尝试点击各种上传入口
        clicked = False
        for selector in [
            'text=发布视频',
            'text=上传视频',
            'text=上传',
            'text=选择文件',
            '[class*="upload"]',
            '[class*="publish"]',
            '[class*="file"]'
        ]:
            try:
                el = page.locator(selector).first
                if el.count() > 0 and el.is_visible():
                    logger.info(f"点击: {selector}")
                    el.click()
                    time.sleep(3)
                    clicked = True
                    break
            except:
                pass
        
        if clicked:
            file_count = page.locator('input[type="file"]').count()
            if file_count > 0:
                page.locator('input[type="file"]').first.set_input_files(VIDEO)
                logger.info("✅ 文件已上传")
            else:
                logger.warning("点击后仍无file input")
    
    input("\n按 Enter 关闭浏览器...")

except Exception as e:
    logger.error(f"❌ {e}")
    import traceback
    traceback.print_exc()
finally:
    try:
        b.close()
        p.stop()
    except:
        pass
