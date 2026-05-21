#!/usr/bin/env python3
"""
Scrapling 示例脚本
展示 Scrapling 库的基本用法
"""

from scrapling import ScraplingEngine


def example_single_page():
    """抓取单个页面"""
    print("=" * 50)
    print("示例 1: 抓取单个页面")
    print("=" * 50)
    
    engine = ScraplingEngine()
    
    # 抓取单个页面
    try:
        data = engine.scrape(
            "https://example.com",
            wait_selector="body",  # 等待 body 标签加载
            wait_timeout=10
        )
        
        print(f"页面标题: {data.title}")
        print(f"URL: {data.url}")
        print(f"HTML 长度: {len(data.html)} 字符")
        
        # 提取标题
        if data.css("h1.title"):
            title = data.css("h1.title")[0].text
            print(f"标题: {title}")
            
    except Exception as e:
        print(f"抓取失败: {e}")


def example_async():
    """异步抓取多个页面"""
    print("\n" + "=" * 50)
    print("示例 2: 异步抓取多个页面")
    print("=" * 50)
    
    engine = ScraplingEngine()
    
    urls = [
        "https://example.com",
        "https://httpbin.org/get",
        "https://httpbin.org/html"
    ]
    
    try:
        results = engine.scrape_async(urls)
        
        for i, result in enumerate(results, 1):
            print(f"\n页面 {i}:")
            print(f"  URL: {result.url}")
            print(f"  状态码: {result.status_code}")
            print(f"  HTML 长度: {len(result.html)} 字符")
            
    except Exception as e:
        print(f"抓取失败: {e}")


def example_batch():
    """批量抓取"""
    print("\n" + "=" * 50)
    print("示例 3: 批量抓取")
    print("=" * 50)
    
    engine = ScraplingEngine()
    
    urls = [
        "https://httpbin.org/html",
        "https://httpbin.org/html",
        "https://httpbin.org/html"
    ]
    
    try:
        results = engine.scrape_all(urls)
        
        for i, result in enumerate(results, 1):
            print(f"\n结果 {i}:")
            print(f"  状态码: {result.status_code}")
            print(f"  是否成功: {result.success}")
            if result.success:
                print(f"  HTML 长度: {len(result.html)}")
                
    except Exception as e:
        print(f"批量抓取失败: {e}")


def example_html_parsing():
    """解析 HTML 字符串"""
    print("\n" + "=" * 50)
    print("示例 4: 解析 HTML 字符串")
    print("=" * 50)
    
    engine = ScraplingEngine()
    
    # 简单的 HTML 示例
    html = """
    <html>
    <head><title>Test Page</title></head>
    <body>
        <h1 class="title">Hello World</h1>
        <p>Some content here</p>
        <div class="container">
            <span>More content</span>
        </div>
    </body>
    </html>
    """
    
    try:
        data = engine.parse_html(html)
        
        print(f"页面标题: {data.title}")
        
        # 使用 CSS 选择器
        titles = data.css(".title")
        if titles:
            print(f"标题文本: {titles[0].text}")
        
        # 使用 XPath
        paragraphs = data.xpath("//p")
        if paragraphs:
            print(f"段落文本: {paragraphs[0].text}")
            
    except Exception as e:
        print(f"解析失败: {e}")


def example_selector_group():
    """使用选择器组"""
    print("\n" + "=" * 50)
    print("示例 5: 使用选择器组")
    print("=" * 50)
    
    engine = ScraplingEngine()
    
    # 创建选择器组
    selectors = {
        "title": "h1.title",
        "content": ".container p",
        "links": "a[href]"
    }
    
    try:
        engine = ScraplingEngine()
        data = engine.scrape("https://example.com", wait_selector="body")
        
        # 使用选择器组提取
        for name, selector in selectors.items():
            elements = data.css(selector)
            if elements:
                print(f"{name}: {elements[0].text[:50]}...")
            else:
                print(f"{name}: 未找到")
                
    except Exception as e:
        print(f"选择器组示例失败: {e}")


def example_with_headers():
    """使用自定义请求头"""
    print("\n" + "=" * 50)
    print("示例 6: 使用自定义请求头")
    print("=" * 50)
    
    engine = ScraplingEngine()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br"
    }
    
    try:
        data = engine.scrape(
            "https://httpbin.org/headers",
            headers=headers
        )
        
        print(f"请求头: {data.headers}")
        print(f"状态码: {data.status_code}")
        
    except Exception as e:
        print(f"请求失败: {e}")


if __name__ == "__main__":
    print("🚀 Scrapling 示例脚本")
    print("=" * 60)
    
    # 运行示例
    try:
        example_single_page()
        example_async()
        example_batch()
        example_html_parsing()
        example_selector_group()
        example_with_headers()
        
        print("\n" + "=" * 60)
        print("✅ 所有示例执行完成！")
        print("=" * 60)
        
    except ImportError as e:
        print(f"\n❌ 错误: 需要先安装 scrapling 库")
        print(f"运行命令: pip install scrapling")
        print(f"\n错误详情: {e}")
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
