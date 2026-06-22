#!/home/asus/kuafu/venv/bin/python
"""抖音发布 - 简化版"""
import sys, os, time, json, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from playwright.sync_api import sync_playwright

DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.join(DIR, "test_video.mp4")
COOKIE = os.path.join(DIR, "douyin_cookies.json")
TITLE = "AI自动生成的测试视频 🚀 夸父逐日"

p = sync_playwright().start()
b = p.chromium.launch(
    executable_path='/home/asus/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome',
    headless=False,
    args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
)
ctx = b.new_context(viewport={'width': 1280, 'height': 800})
page = ctx.new_page()

# 加载 cookies
if os.path.exists(COOKIE):
    with open(COOKIE) as f:
        cookies = json.load(f)
    ctx.add_cookies(cookies)
    logger.info(f"已加载 {len(cookies)} 条 cookies")

try:
    # 访问上传页
    logger.info("访问上传页面...")
    page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
    time.sleep(8)
    
    url_lower = page.url.lower()
    logger.info(f"URL: {page.url}")
    
    if 'login' in url_lower or 'passport' in url_lower:
        logger.info("❌ 需要扫码登录")
        page.screenshot(path=os.path.join(DIR, "need_login.png"))
        print("\n⚠️ 请用抖音APP扫码登录，有60秒时间...\n")
        
        for i in range(60):
            time.sleep(3)
            current = page.url.lower()
            if 'login' not in current and 'passport' not in current:
                logger.info("✅ 登录成功！")
                # 保存新 cookies
                new_cookies = ctx.cookies()
                with open(COOKIE, 'w') as f:
                    json.dump(new_cookies, f)
                logger.info(f"已保存 {len(new_cookies)} 条 cookies")
                break
            if i % 5 == 0:
                print(f"  等待扫码... {i*3}s/180s")
        else:
            logger.error("❌ 扫码超时")
            raise Exception("扫码超时")
        
        # 重新进入上传页
        page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
        time.sleep(8)
    
    logger.info("✅ 已登录")
    page.screenshot(path=os.path.join(DIR, "logged_in.png"))
    
    # 等待页面加载
    logger.info("等待页面加载...")
    time.sleep(5)
    
    # 查找并点击发布视频按钮
    logger.info("查找发布视频按钮...")
    clicked = False
    
    # 方法1: get_by_text
    try:
        btn = page.get_by_text("发布视频")
        if btn.count() > 0:
            btn.first.click()
            clicked = True
            logger.info("✅ 点击发布视频按钮成功")
            time.sleep(3)
    except:
        pass
    
    # 方法2: CSS
    if not clicked:
        for sel in ['button:has-text("发布视频")', '.semi-button:has-text("发布视频")']:
            try:
                el = page.locator(sel)
                if el.count() > 0:
                    el.first.click()
                    clicked = True
                    logger.info(f"✅ 通过 {sel} 点击成功")
                    time.sleep(3)
                    break
            except:
                pass
    
    # 方法3: 所有button
    if not clicked:
        try:
            buttons = page.locator('button')
            for i in range(buttons.count()):
                try:
                    text = buttons.nth(i).text_content() or ""
                    if "发布" in text or "上传" in text:
                        buttons.nth(i).click()
                        clicked = True
                        logger.info(f"✅ 点击按钮[{i}]: {text.strip()}")
                        time.sleep(3)
                        break
                except:
                    pass
        except:
            pass
    
    if not clicked:
        logger.warning("⚠️ 没有找到发布视频按钮，尝试直接上传")
    
    page.screenshot(path=os.path.join(DIR, "after_click.png"))
    
    # 上传视频
    logger.info("上传视频...")
    try:
        file_input = page.locator('input[type="file"]').first
        file_input.wait_for(state="attached", timeout=15000)
        file_input.set_input_files(VIDEO)
        logger.info("✅ 文件已选择")
    except Exception as e:
        logger.warning(f"上传文件失败: {e}")
        # 尝试点击上传区域
        try:
            upload_area = page.locator('.semi-upload, [class*="upload"]').first
            if upload_area.count() > 0:
                upload_area.first.click()
                time.sleep(2)
                file_input = page.locator('input[type="file"]').first
                file_input.set_input_files(VIDEO)
                logger.info("✅ 通过点击上传区域后选择文件")
        except Exception as e2:
            logger.error(f"第二次上传也失败: {e2}")
    
    # 等待上传
    logger.info("等待上传完成（最多3分钟）...")
    time.sleep(60)  # 先等1分钟
    
    page.screenshot(path=os.path.join(DIR, "after_upload.png"))
    
    # 填写标题
    logger.info("填写标题...")
    filled = False
    for placeholder in ["标题", "title", "Title", "添加标题"]:
        try:
            inp = page.locator(f'input[placeholder*="{placeholder}"]').first
            if inp.count() > 0:
                inp.click()
                inp.fill('')
                time.sleep(0.5)
                inp.fill(TITLE)
                filled = True
                logger.info(f"✅ 标题已填写: {TITLE}")
                break
        except:
            pass
    
    if not filled:
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
    print("✅ 流程执行完成！")
    print(f"   视频: {VIDEO}")
    print(f"   标题: {TITLE}")
    print(f"   截图: {DIR}/final.png")
    print("="*50)
    print("\n⚠️ 请在浏览器中手动点击发布按钮完成最终发布")
    
    input("\n按 Enter 关闭浏览器...")

except Exception as e:
    logger.error(f"❌ 失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    try:
        b.close()
        p.stop()
    except:
        pass
