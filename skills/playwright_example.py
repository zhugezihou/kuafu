#!/usr/bin/env python3
"""
Playwright 浏览器自动化示例脚本
用于演示如何使用 Playwright 进行网页自动化操作
"""

from playwright.sync_api import sync_playwright


def example_screenshot():
    """示例：访问网页并截图"""
    print("示例 1: 访问网页并截图")
    
    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # 访问网页
        page.goto('https://httpbin.org/html')
        
        # 截图
        page.screenshot(path='screenshot.png')
        print("✓ 截图已保存为 screenshot.png")
        
        browser.close()


def example_click_interaction():
    """示例：点击交互"""
    print("\n示例 2: 点击交互")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # 访问页面
        page.goto('https://httpbin.org/')
        
        # 等待元素并点击
        page.wait_for_selector('.btn-primary')
        page.click('.btn-primary')
        
        print("✓ 元素点击完成")
        
        browser.close()


def example_web_scraping():
    """示例：网页内容抓取"""
    print("\n示例 3: 网页内容抓取")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # 访问页面
        page.goto('https://httpbin.org/html')
        
        # 抓取内容
        content = page.inner_text('body')
        print(f"✓ 抓取到 {len(content)} 字符内容")
        
        browser.close()


def example_login_flow():
    """示例：模拟登录流程"""
    print("\n示例 4: 模拟登录流程")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # 非 headless 模式可以看到浏览器
        page = browser.new_page()
        
        # 访问登录页
        page.goto('https://example.com/login')
        
        # 填写表单
        page.fill('#username', 'testuser')
        page.fill('#password', 'testpass')
        
        # 点击登录按钮
        page.click('#login-btn')
        
        # 等待登录成功
        page.wait_for_selector('.welcome')
        
        print("✓ 登录流程完成")
        
        browser.close()


if __name__ == '__main__':
    print("=" * 50)
    print("Playwright 自动化测试示例")
    print("=" * 50)
    
    # 根据需求选择运行哪些示例
    example_screenshot()
    # example_click_interaction()
    # example_web_scraping()
    # example_login_flow()
    
    print("\n" + "=" * 50)
    print("所有示例运行完成")
    print("=" * 50)
