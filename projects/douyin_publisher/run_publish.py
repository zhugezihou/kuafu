#!/home/asus/kuafu/venv/bin/python
"""抖音发布 - 扫码登录 + 上传 + 填标题"""
import sys, os, time, json, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from playwright.sync_api import sync_playwright

DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.join(os.path.dirname(os.path.dirname(DIR)), "douyin/templates/test_video.mp4")
COOKIE = os.path.join(DIR, "douyin_cookies.json")
TITLE = "AI自动生成的测试视频 🚀 夸父逐日"

if not os.path.exists(VIDEO):
    logger.error(f"❌ 视频不存在: {VIDEO}")
    sys.exit(1)

size_mb = os.path.getsize(VIDEO) / 1024 / 1024
logger.info(f"📹 视频文件: {VIDEO} ({size_mb:.1f}MB)")

p = sync_playwright().start()
b = p.chromium.launch(
    executable_path='/home/asus/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome',
    headless=False,
    args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
)
ctx = b.new_context(viewport={'width': 1280, 'height': 800})
page = ctx.new_page()

try:
    # 加载cookies
    if os.path.exists(COOKIE):
        with open(COOKIE) as f:
            cookies = json.load(f)
        ctx.add_cookies(cookies)
        logger.info(f"已加载 {len(cookies)} 条cookies")

    # 访问上传页
    logger.info("访问上传页面...")
    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
    time.sleep(8)
    logger.info(f"URL: {page.url}")

    # 检查登录
    if 'login' in page.url.lower() or 'passport' in page.url.lower():
        logger.info("=" * 50)
        logger.info("📱 请在浏览器中扫码登录（120秒内）")
        logger.info("=" * 50)
        for i in range(120):
            time.sleep(2)
            if 'login' not in page.url.lower() and 'passport' not in page.url.lower():
                logger.info("✅ 登录成功！")
                cookies = ctx.cookies()
                with open(COOKIE, 'w') as f:
                    json.dump(cookies, f)
                logger.info(f"已保存 {len(cookies)} 条cookies")
                break
            if i % 10 == 0:
                logger.info(f"等待扫码... {i*2}s")
        else:
            logger.error("❌ 扫码超时")
            sys.exit(1)

    page.screenshot(path=os.path.join(DIR, "step_login.png"))
    logger.info("✅ 已进入上传页面")

    # ---- 上传视频 ----
    logger.info("开始上传视频...")

    # 先尝试找"发布视频"按钮并点击
    for text in ["发布视频", "上传视频", "选择文件"]:
        try:
            btn = page.get_by_text(text, exact=False).first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                logger.info(f"点击了'{text}'按钮")
                time.sleep(3)
                break
        except:
            pass

    # 查找file input
    file_input = None
    for i in range(10):
        fi = page.locator('input[type="file"]')
        if fi.count() > 0:
            file_input = fi.first
            logger.info(f"找到file input (尝试{i+1})")
            break
        time.sleep(1)

    if file_input:
        logger.info("上传文件中...")
        file_input.set_input_files(VIDEO)
        logger.info("✅ 文件已上传，等待处理...")
    else:
        # 最后手段：JS注入创建file input
        logger.warning("未找到file input，尝试JS注入...")
        page.evaluate("""
            const container = document.querySelector('[class*="upload"]') || document.body;
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = 'video/*';
            input.style.position = 'absolute';
            input.style.left = '10px';
            input.style.top = '10px';
            input.style.zIndex = '9999';
            container.appendChild(input);
        """)
        time.sleep(2)
        fi = page.locator('input[type="file"]').last
        if fi.count() > 0:
            fi.set_input_files(VIDEO)
            logger.info("✅ 通过JS注入上传文件")
        else:
            logger.error("❌ 无法上传文件")
            sys.exit(1)

    # 等待上传完成
    logger.info("⏳ 等待上传处理（约3分钟）...")
    for i in range(90):
        time.sleep(2)
        if i % 15 == 0:
            logger.info(f"处理中... {i*2}s")
    time.sleep(30)

    page.screenshot(path=os.path.join(DIR, "after_upload.png"))
    logger.info("✅ 上传阶段完成")

    # ---- 填写标题 ----
    logger.info("填写标题...")
    filled = False
    for placeholder in ["标题", "title", "Title", "添加标题", "请输入标题"]:
        try:
            inp = page.locator(f'input[placeholder*="{placeholder}"]').first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                time.sleep(0.3)
                inp.fill('')
                time.sleep(0.3)
                inp.fill(TITLE)
                filled = True
                logger.info(f"✅ 通过placeholder填写标题")
                break
        except:
            pass

    if not filled:
        # 尝试contenteditable
        try:
            ed = page.locator('[contenteditable="true"]').first
            if ed.count() > 0:
                ed.click()
                time.sleep(0.3)
                ed.fill(TITLE)
                filled = True
                logger.info("✅ 通过contenteditable填写标题")
        except:
            pass

    if not filled:
        # 尝试所有输入框
        try:
            inputs = page.locator('input')
            for i in range(inputs.count()):
                try:
                    inp = inputs.nth(i)
                    if inp.is_visible():
                        inp.click()
                        inp.fill('')
                        time.sleep(0.3)
                        inp.fill(TITLE)
                        filled = True
                        logger.info(f"✅ 通过索引{i}填写标题")
                        break
                except:
                    pass
        except:
            pass

    time.sleep(2)
    page.screenshot(path=os.path.join(DIR, "final.png"))

    print("\n" + "="*50)
    print("✅ 抖音发布流程执行完成！")
    print(f"   视频: {VIDEO} ({size_mb:.1f}MB)")
    print(f"   标题: {TITLE}")
    print("="*50)
    print("\n⚠️ 请在浏览器中手动检查并点击发布按钮完成最终发布")

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
