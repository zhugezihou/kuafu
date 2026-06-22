#!/home/asus/kuafu/venv/bin/python
"""
抖音发布 - 分析页面 + 上传
先分析页面结构，找到上传入口
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
    if os.path.exists(COOKIE):
        with open(COOKIE) as f:
            cookies = json.load(f)
        ctx.add_cookies(cookies)
        logger.info(f"已加载 {len(cookies)} 条cookies")

    logger.info("访问上传页面...")
    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
    time.sleep(8)
    logger.info(f"URL: {page.url}")

    if 'login' in page.url.lower():
        logger.info("需要扫码登录，等待中...")
        for i in range(120):
            time.sleep(2)
            if 'login' not in page.url.lower():
                logger.info("✅ 登录成功")
                cookies = ctx.cookies()
                with open(COOKIE, 'w') as f:
                    json.dump(cookies, f)
                break
            if i % 10 == 0:
                logger.info(f"⏳ 等待扫码... {i*2}s")
        else:
            logger.error("❌ 扫码超时")
            sys.exit(1)
        time.sleep(5)

    # 分析页面元素
    logger.info("分析页面元素...")
    
    # 1. 查找所有可见按钮
    buttons = page.locator('button')
    btn_count = buttons.count()
    logger.info(f"按钮数量: {btn_count}")
    for i in range(btn_count):
        try:
            btn = buttons.nth(i)
            if btn.is_visible():
                text = btn.inner_text()[:40]
                logger.info(f"  按钮[{i}]: '{text}'")
        except:
            pass
    
    # 2. 查找file input
    file_inputs = page.locator('input[type="file"]')
    logger.info(f"file input数量: {file_inputs.count()}")
    
    # 3. 查找upload相关元素
    for sel in ['[class*=upload]', '[class*=publish]', '[class*=file]', '[class*=video]']:
        els = page.locator(sel)
        if els.count() > 0:
            logger.info(f"选择器 '{sel}': {els.count()} 个元素")
            for i in range(min(els.count(), 5)):
                try:
                    el = els.nth(i)
                    if el.is_visible():
                        logger.info(f"  [{i}] visible, tag={el.evaluate('e=>e.tagName')}")
                except:
                    pass
    
    # 4. 检查是否有"发布视频"大按钮（上传入口）
    for text in ["发布视频", "上传视频", "上传", "选择文件", "点击上传"]:
        el = page.get_by_text(text).first
        if el.count() > 0 and el.is_visible():
            logger.info(f"✅ 找到可见文本: '{text}'")
    
    # 5. 截图
    page.screenshot(path=os.path.join(DIR, "page_analyze.png"))
    
    # 6. 保存页面HTML
    html = page.content()
    with open(os.path.join(DIR, "page_debug.html"), 'w') as f:
        f.write(html)
    logger.info(f"HTML已保存 ({len(html)} chars)")
    
    # 7. 尝试点击"发布视频"按钮
    for btn_text in ["发布视频", "上传视频"]:
        try:
            btn = page.get_by_text(btn_text, exact=False).first
            if btn.count() > 0:
                logger.info(f"尝试点击'{btn_text}'...")
                btn.click()
                time.sleep(3)
                break
        except Exception as e:
            logger.warning(f"点击'{btn_text}'失败: {e}")
    
    # 8. 再次检查file input
    file_inputs = page.locator('input[type="file"]')
    logger.info(f"点击后file input数量: {file_inputs.count()}")
    
    if file_inputs.count() > 0:
        file_inputs.first.set_input_files(VIDEO)
        logger.info("✅ 文件已上传")
    else:
        logger.warning("⚠️ 仍找不到file input，尝试其他方式")
        # 尝试点击上传区域
        for sel in ['.upload', '.publish', '[class*=upload]', '[class*=publish]']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.click()
                    logger.info(f"点击了 {sel}")
                    time.sleep(2)
                    fi = page.locator('input[type="file"]')
                    if fi.count() > 0:
                        fi.first.set_input_files(VIDEO)
                        logger.info("✅ 文件已上传")
                        break
            except:
                pass
    
    input("\n按 Enter 关闭...")
    
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
