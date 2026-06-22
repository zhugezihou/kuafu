#!/home/asus/kuafu/venv/bin/python
"""抖音发布 - 完整流程（含扫码登录）"""
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
    
    logger.info(f"URL: {page.url}")
    
    # 检查是否在登录页
    if 'login' in page.url.lower() or 'passport' in page.url.lower():
        logger.info("❌ 需要重新登录")
        page.screenshot(path=os.path.join(DIR, "need_login.png"))
        
        # 点击"扫码登录"标签确保二维码显示
        try:
            scan_tab = page.get_by_text("扫码登录")
            if scan_tab.count() > 0:
                scan_tab.first.click()
                time.sleep(2)
                logger.info("已切换到扫码登录")
        except:
            pass
        
        print("\n" + "="*60)
        print("⚠️  请用抖音APP扫码登录（有60秒时间）")
        print("="*60 + "\n")
        
        for i in range(60):
            time.sleep(3)
            current_url = page.url.lower()
            if 'login' not in current_url and 'passport' not in current_url:
                logger.info("✅ 登录成功！")
                # 保存新 cookies
                new_cookies = ctx.cookies()
                with open(COOKIE, 'w') as f:
                    json.dump(new_cookies, f)
                logger.info(f"已保存 {len(new_cookies)} 条 cookies")
                break
            if i % 5 == 0:
                print(f"  等待扫码... {i*3+3}s/180s")
        else:
            logger.error("❌ 扫码超时")
            raise Exception("扫码超时")
        
        # 重新进入上传页
        page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded")
        time.sleep(10)
    
    logger.info(f"✅ 已登录，当前URL: {page.url}")
    page.screenshot(path=os.path.join(DIR, "logged_in.png"))
    
    # 等待页面完全加载
    logger.info("等待页面加载...")
    time.sleep(8)
    
    # 检查页面内容确认是上传页
    body_text = page.locator('body').inner_text()
    logger.info(f"页面文本片段: {body_text[:200]}")
    
    # 查找发布视频按钮 - 多种策略
    logger.info("查找发布视频按钮...")
    clicked = False
    
    # 策略1: 文本匹配
    for btn_text in ["发布视频", "上传视频", "发布作品", "新建发布"]:
        try:
            btn = page.get_by_text(btn_text)
            if btn.count() > 0:
                btn.first.click()
                clicked = True
                logger.info(f"✅ 点击: {btn_text}")
                time.sleep(5)
                break
        except:
            pass
    
    # 策略2: CSS + 文本
    if not clicked:
        for sel in [
            'button:has-text("发布视频")',
            'button:has-text("上传")',
            'button:has-text("发布")',
            '.semi-button:has-text("发布")',
            '[class*="upload"]',
            '[class*="publish"]'
        ]:
            try:
                el = page.locator(sel)
                if el.count() > 0:
                    el.first.click()
                    clicked = True
                    logger.info(f"✅ 点击: {sel}")
                    time.sleep(5)
                    break
            except:
                pass
    
    # 策略3: 遍历所有按钮
    if not clicked:
        try:
            buttons = page.locator('button')
            for i in range(buttons.count()):
                try:
                    text = buttons.nth(i).inner_text()
                    if text.strip() and any(kw in text for kw in ["发布", "上传", "新建"]):
                        buttons.nth(i).click()
                        clicked = True
                        logger.info(f"✅ 点击按钮[{i}]: {text.strip()}")
                        time.sleep(5)
                        break
                except:
                    pass
        except:
            pass
    
    if not clicked:
        logger.warning("⚠️ 未找到发布按钮，保存页面HTML分析")
        html = page.content()
        with open(os.path.join(DIR, "page_debug.html"), "w") as f:
            f.write(html)
    
    page.screenshot(path=os.path.join(DIR, "after_click.png"))
    
    # 等待上传组件出现
    logger.info("等待上传组件...")
    time.sleep(5)
    
    # 上传视频
    logger.info("上传视频文件...")
    uploaded = False
    
    # 先检查是否有file input
    file_input = page.locator('input[type="file"]')
    if file_input.count() > 0:
        file_input.first.set_input_files(VIDEO)
        uploaded = True
        logger.info("✅ 文件已通过input上传")
    else:
        # 尝试点击上传区域
        for upload_sel in [
            '.semi-upload',
            '[class*="upload"]',
            '[class*="Upload"]',
            '.upload-component',
            'div[class*="dragger"]',
            'div[class*="uploader"]'
        ]:
            try:
                el = page.locator(upload_sel)
                if el.count() > 0:
                    el.first.click()
                    time.sleep(2)
                    # 点击后再次检查file input
                    file_input = page.locator('input[type="file"]')
                    if file_input.count() > 0:
                        file_input.first.set_input_files(VIDEO)
                        uploaded = True
                        logger.info(f"✅ 通过点击{upload_sel}上传文件")
                        break
            except:
                pass
    
    if not uploaded:
        # 最后手段：JS注入
        try:
            page.evaluate("""
                const input = document.createElement('input');
                input.type = 'file';
                input.style.display = 'none';
                document.body.appendChild(input);
                return true;
            """)
            file_input = page.locator('input[type="file"]').last
            file_input.set_input_files(VIDEO)
            uploaded = True
            logger.info("✅ 通过JS注入上传文件")
        except Exception as e:
            logger.error(f"❌ 所有上传方式均失败: {e}")
    
    if uploaded:
        logger.info("等待上传完成（约2分钟）...")
        for i in range(60):
            time.sleep(2)
            if i % 10 == 0:
                logger.info(f"上传中... {i*2}s")
        time.sleep(30)  # 额外等待
        
        page.screenshot(path=os.path.join(DIR, "upload_done.png"))
        
        # 填写标题
        logger.info("填写标题...")
        filled = False
        for placeholder in ["标题", "title", "Title", "添加标题", "请输入标题"]:
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
                # 找所有可见输入框
                for tag in ['input', 'textarea']:
                    els = page.locator(tag)
                    for i in range(els.count()):
                        try:
                            el = els.nth(i)
                            if el.is_visible():
                                rect = el.bounding_box()
                                if rect and rect['width'] > 200:  # 宽输入框很可能是标题
                                    el.click()
                                    el.fill('')
                                    time.sleep(0.3)
                                    el.fill(TITLE)
                                    filled = True
                                    logger.info(f"✅ 通过宽输入框[{i}]填写标题")
                                    break
                        except:
                            pass
                    if filled:
                        break
            except:
                pass
        
        if not filled:
            logger.warning("⚠️ 未能填写标题")
        
        time.sleep(3)
        page.screenshot(path=os.path.join(DIR, "final.png"))
    
    print("\n" + "="*50)
    print("✅ 发布流程执行完成！")
    print(f"   视频: {VIDEO}")
    print(f"   标题: {TITLE}")
    print("="*50)
    print("\n⚠️ 请在浏览器中手动检查并点击发布按钮")
    
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
