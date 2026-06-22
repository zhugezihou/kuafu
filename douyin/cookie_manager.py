"""
抖音 Cookie 管理器
══════════════════
管理抖音创作者平台的登录态 Cookie，支持保存、加载、刷新。
"""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional


class CookieManager:
    """管理抖音创作者平台的 Cookie"""

    def __init__(self, cookie_path: str = "douyin/cookies.json"):
        self.cookie_path = Path(cookie_path)
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, cookies: list) -> None:
        """保存 Cookie 到文件"""
        data = {
            "cookies": cookies,
            "saved_at": datetime.now().isoformat(),
        }
        self.cookie_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"[Cookie] ✅ 已保存 {len(cookies)} 条 Cookie → {self.cookie_path}")

    def load(self) -> Optional[list]:
        """加载 Cookie，如果过期则返回 None"""
        if not self.cookie_path.exists():
            print("[Cookie] ⚠️ 未找到 Cookie 文件，需要重新登录")
            return None

        try:
            data = json.loads(self.cookie_path.read_text())
            cookies = data.get("cookies", [])

            # 检查是否过期
            saved_at = data.get("saved_at", "")
            if saved_at:
                saved_time = datetime.fromisoformat(saved_at)
                age = datetime.now() - saved_time
                if age > timedelta(days=7):
                    print(f"[Cookie] ⚠️ Cookie 已保存 {age.days} 天，可能已过期")
                    return None

            print(f"[Cookie] ✅ 已加载 {len(cookies)} 条 Cookie（保存于 {saved_at}）")
            return cookies

        except (json.JSONDecodeError, KeyError) as e:
            print(f"[Cookie] ❌ Cookie 文件损坏: {e}")
            return None

    def is_valid(self) -> bool:
        """检查 Cookie 是否有效（文件存在且未过期）"""
        return self.load() is not None

    def delete(self) -> None:
        """删除 Cookie 文件（用于重新登录）"""
        if self.cookie_path.exists():
            self.cookie_path.unlink()
            print("[Cookie] 🗑️ 已删除 Cookie 文件")


if __name__ == "__main__":
    # 测试
    mgr = CookieManager()
    print(f"Cookie 有效: {mgr.is_valid()}")
