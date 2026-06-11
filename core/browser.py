"""
夸父浏览器工具 — 基于 Playwright 的无头浏览器自动化

功能：
1. browser_navigate(url) → 加载页面，返回交互元素快照
2. browser_click(ref) → 点击元素
3. browser_type(ref, text) → 输入文本
4. browser_snapshot() → 获取页面快照
5. browser_js(expression) → 执行 JavaScript
6. browser_screenshot() → 截图保存

设计原则：
- 单浏览器实例，多页面标签管理
- 零新增依赖（Playwright 已是 夸父 依赖）
- 30s 超时自动清理（防止残留）
- 截图存到 screenshots/ 目录
- 快照只返回文本/交互元素（节省 token）
"""

import base64
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 模块级单例 ──────────────────────────────────────────────────

_playwright_instance = None
_browser = None
_page = None
_last_active = 0
_SESSION_TIMEOUT = 300  # 5分钟无操作自动关闭

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

MAX_SNAPSHOT_LENGTH = 4000
MAX_SCREENSHOT_HISTORY = 50


# ── 会话管理 ───────────────────────────────────────────────────—

def _get_browser():
    """获取或创建浏览器实例（惰性初始化 + 自动超时回收）。"""
    global _playwright_instance, _browser, _page, _last_active

    now = time.time()

    # 检查是否超时
    if _browser and (now - _last_active) > _SESSION_TIMEOUT:
        _cleanup()
        return None, None

    if _browser is not None and _page is not None:
        try:
            _page.title()  # 简单健康检查
            _last_active = now
            return _browser, _page
        except Exception:
            _cleanup()

    return None, None


def _ensure_browser():
    """确保浏览器可用，必要时启动。"""
    global _playwright_instance, _browser, _page, _last_active

    browser, page = _get_browser()
    if browser is not None:
        return browser, page

    # 启动新浏览器
    try:
        from playwright.sync_api import sync_playwright

        _playwright_instance = sync_playwright().start()
        _browser = _playwright_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
            ],
        )
        _page = _browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        _page.set_default_timeout(15000)  # 15秒默认超时
        _last_active = time.time()
        return _browser, _page
    except Exception as e:
        return None, None


def _cleanup():
    """清理浏览器资源。"""
    global _playwright_instance, _browser, _page

    try:
        if _page:
            _page.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright_instance:
            _playwright_instance.stop()
    except Exception:
        pass

    _browser = None
    _page = None
    _playwright_instance = None


def _manage_screenshots():
    """限制截图历史数量。"""
    screenshots = sorted(SCREENSHOTS_DIR.glob("*.png"))
    while len(screenshots) > MAX_SCREENSHOT_HISTORY:
        try:
            screenshots[0].unlink()
            screenshots = screenshots[1:]
        except Exception:
            break


# ── 快照生成 ───────────────────────────────────────────────────—

def _extract_snapshot(page, full: bool = False) -> str:
    """从页面提取可交互元素的文本快照。"""
    try:
        if full:
            # 完整内容：获取 body 文本
            text = page.evaluate("""() => {
                const body = document.body;
                if (!body) return '';
                const clone = body.cloneNode(true);
                // 移除 script/style
                clone.querySelectorAll('script, style, svg, noscript').forEach(el => el.remove());
                return clone.innerText || '';
            }""")
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > MAX_SNAPSHOT_LENGTH * 2:
                text = text[:MAX_SNAPSHOT_LENGTH * 2] + "\n\n...(内容已截断)"
            return text[:MAX_SNAPSHOT_LENGTH * 2]

        # 紧凑模式：提取可交互元素
        elements = page.evaluate("""() => {
            const results = [];
            const tags = 'a, button, input, select, textarea, [role="button"], '
                       + '[role="link"], [role="checkbox"], [role="radio"], '
                       + '[role="tab"], [role="menuitem"], [onclick]';

            const els = document.querySelectorAll(tags);
            let id = 1;
            for (const el of els) {
                // 跳过隐藏元素
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;

                // 提取有意义的属性
                const tag = el.tagName.toLowerCase();
                const text = (el.textContent || '').trim().slice(0, 80);
                const href = el.getAttribute('href') || '';
                const name = el.getAttribute('name') || '';
                const placeholder = el.getAttribute('placeholder') || '';
                const aria_label = el.getAttribute('aria-label') || '';

                // 跳过纯装饰性元素
                if (!text && !href && !name && !placeholder && !aria_label) continue;
                if (text.length === 0 && tag === 'a') continue;

                results.push({
                    id: id++,
                    tag,
                    text: text.slice(0, 60),
                    href: href.slice(0, 120),
                    name,
                    placeholder: placeholder.slice(0, 40),
                    aria_label: aria_label.slice(0, 60),
                    type: el.getAttribute('type') || '',
                    checked: el.checked !== undefined ? el.checked : undefined,
                });
            }
            return results;
        }""")

        lines = []
        for el in elements:
            parts = [f"[@e{el['id']}]"]
            parts.append(f"<{el['tag']}>")
            if el['text']:
                parts.append(f"「{el['text']}」")
            if el['href']:
                href_short = el['href'][:80]
                parts.append(f"→ {href_short}")
            if el['placeholder']:
                parts.append(f"[placeholder={el['placeholder']}]")
            if el['aria_label']:
                parts.append(f"[label={el['aria_label']}]")
            if el['checked'] is not None:
                parts.append("✓" if el['checked'] else "□")
            lines.append(" ".join(parts))

        # 获取标题和 URL
        title = page.evaluate("document.title") or ""
        current_url = page.url

        header = f"标题: {title}\nURL: {current_url}\n"

        if not lines:
            header += "\n(页面无交互元素)\n"
            # 至少获取一些纯文本
            text_content = page.evaluate("""() => {
                const body = document.body;
                if (!body) return '';
                const text = (body.innerText || '').trim();
                return text.slice(0, 500);
            }""")
            if text_content:
                header += f"\n页面内容摘要:\n{text_content[:800]}\n"

            return header

        snapshot = header + "\n".join(lines)
        if len(snapshot) > MAX_SNAPSHOT_LENGTH:
            snapshot = snapshot[:MAX_SNAPSHOT_LENGTH] + "\n\n...(快照已截断)"
        return snapshot

    except Exception as e:
        return f"(获取页面快照失败: {e})"


# ── 工具函数 ───────────────────────────────────────────────────—

def navigate(url: str) -> dict:
    """导航到 URL，返回页面快照。"""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = _ensure_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器启动失败，请检查 Playwright 安装"}

    _, page = result

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 等待网络空闲（最多额外等 5 秒）
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        snapshot = _extract_snapshot(page, full=False)

        return {
            "success": True,
            "output": f"已加载页面\n\n{snapshot}",
            "url": page.url,
            "title": page.evaluate("document.title") or "",
        }
    except Exception as e:
        return {"success": False, "output": f"页面加载失败: {e}"}


def snapshot(full: bool = False) -> dict:
    """获取当前页面快照。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    try:
        snapshot_text = _extract_snapshot(page, full=full)
        title = page.evaluate("document.title") or ""
        current_url = page.url
        return {
            "success": True,
            "output": snapshot_text,
            "url": current_url,
            "title": title,
        }
    except Exception as e:
        return {"success": False, "output": f"获取快照失败: {e}"}


def click(ref: str) -> dict:
    """点击元素（通过 ref ID 如 @e5）。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    # 解析 ref ID
    m = re.match(r"@?e(\d+)", ref)
    if not m:
        return {"success": False, "output": f"无效的 ref ID: {ref}，格式应为 @e5"}

    target_id = int(m.group(1))

    try:
        # 从 dom 中找到对应元素
        clicked = page.evaluate(f"""({target_id}) => {{
            const tags = 'a, button, input, select, textarea, [role="button"], '
                       + '[role="link"], [role="checkbox"], [role="radio"], '
                       + '[role="tab"], [role="menuitem"], [onclick]';
            const els = document.querySelectorAll(tags);
            let id = 1;
            for (const el of els) {{
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                const text = (el.textContent || '').trim().slice(0, 80);
                const href = el.getAttribute('href') || '';
                const aria_label = el.getAttribute('aria-label') || '';
                if (!text && !href && !aria_label) continue;
                if (id === {target_id}) {{
                    el.scrollIntoView({{block: 'center'}});
                    el.click();
                    return {{ok: true, tag: el.tagName.toLowerCase(), text: text.slice(0, 60)}};
                }}
                id++;
            }}
            return {{ok: false}};
        }}, {target_id})""")

        if not clicked.get("ok"):
            return {"success": False, "output": f"未找到 ref @e{target_id}，页面可能已变化，请重新获取快照"}

        time.sleep(0.5)  # 等待可能的页面更新

        # 如果点击后页面变化，重新获取快照
        new_snapshot = _extract_snapshot(page, full=False)
        return {
            "success": True,
            "output": f"已点击 <{clicked.get('tag', '?')}>「{clicked.get('text', '')}」\n\n{new_snapshot}",
        }
    except Exception as e:
        return {"success": False, "output": f"点击失败: {e}"}


def type_text(ref: str, text: str) -> dict:
    """向输入框输入文本（先清空再输入）。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    m = re.match(r"@?e(\d+)", ref)
    if not m:
        return {"success": False, "output": f"无效的 ref ID: {ref}，格式应为 @e5"}

    target_id = int(m.group(1))

    try:
        typed = page.evaluate(f"""({target_id}, text) => {{
            const tags = 'input, textarea, select, [contenteditable="true"], '
                       + '[role="textbox"], [role="searchbox"]';
            const els = document.querySelectorAll(tags);
            let id = 1;
            for (const el of els) {{
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                if (id === {target_id}) {{
                    el.scrollIntoView({{block: 'center'}});
                    el.focus();
                    if (el.tagName.toLowerCase() === 'input' || el.tagName.toLowerCase() === 'textarea') {{
                        el.value = text;
                    }} else {{
                        el.textContent = text;
                    }}
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{ok: true, tag: el.tagName.toLowerCase(), placeholder: el.getAttribute('placeholder') || ''}};
                }}
                id++;
            }}
            return {{ok: false}};
        }}, {target_id}, {json.dumps(text)})""")

        if not typed.get("ok"):
            return {"success": False, "output": f"未找到输入框 ref @e{target_id}"}

        return {
            "success": True,
            "output": f"已输入到 <{typed.get('tag', '?')}>「{text}」",
        }
    except Exception as e:
        return {"success": False, "output": f"输入失败: {e}"}


def press_key(key: str) -> dict:
    """按下键盘按键（Enter, Escape, Tab 等）。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    key_map = {
        "enter": "Enter",
        "return": "Enter",
        "escape": "Escape",
        "esc": "Escape",
        "tab": "Tab",
        "space": " ",
        "up": "ArrowUp",
        "down": "ArrowDown",
        "left": "ArrowLeft",
        "right": "ArrowRight",
        "home": "Home",
        "end": "End",
        "pageup": "PageUp",
        "pagedown": "PageDown",
        "backspace": "Backspace",
        "delete": "Delete",
    }

    mapped_key = key_map.get(key.lower(), key)

    try:
        page.keyboard.press(mapped_key)
        time.sleep(0.3)

        new_snapshot = _extract_snapshot(page, full=False)
        return {
            "success": True,
            "output": f"已按下 {mapped_key}\n\n{new_snapshot}",
        }
    except Exception as e:
        return {"success": False, "output": f"按键失败: {e}"}


def scroll(direction: str) -> dict:
    """滚动页面。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    delta = 500 if direction == "down" else -500

    try:
        page.evaluate(f"window.scrollBy(0, {delta})")
        time.sleep(0.3)
        new_snapshot = _extract_snapshot(page, full=False)
        return {
            "success": True,
            "output": f"已滚动{direction}\n\n{new_snapshot}",
        }
    except Exception as e:
        return {"success": False, "output": f"滚动失败: {e}"}


def execute_js(expression: str) -> dict:
    """在页面中执行 JavaScript。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    try:
        value = page.evaluate(expression)
        output = str(value) if value is not None else "(返回 undefined)"
        if len(output) > 2000:
            output = output[:2000] + "\n\n...(输出已截断)"
        return {"success": True, "output": output}
    except Exception as e:
        return {"success": False, "output": f"JS 执行失败: {e}"}


def screenshot(filename: Optional[str] = None) -> dict:
    """截取页面截图，保存到 screenshots/ 目录。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动，请先调用 browser_navigate"}

    _, page = result

    try:
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"

        path = SCREENSHOTS_DIR / filename
        page.screenshot(path=str(path), full_page=False)
        _manage_screenshots()

        return {
            "success": True,
            "output": f"截图已保存: {path}",
            "path": str(path),
        }
    except Exception as e:
        return {"success": False, "output": f"截图失败: {e}"}


def close() -> dict:
    """关闭当前浏览器会话。"""
    _cleanup()
    return {"success": True, "output": "浏览器会话已关闭"}


def get_console_logs(clear: bool = False) -> dict:
    """获取浏览器控制台日志。"""
    result = _get_browser()
    if result[0] is None:
        return {"success": False, "output": "浏览器未启动"}

    # 暂不实现控制台日志监听（需要 page.on 注册，复杂）
    return {"success": True, "output": "(控制台日志功能暂未实现)"}


# ── 模块加载时注册清理 ──────────────────────────────────────────

import atexit
atexit.register(_cleanup)


# JSON 序列化辅助（用于 type_text 中）
import json
