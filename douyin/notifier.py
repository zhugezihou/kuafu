"""
抖音发布通知模块
════════════════
发布结果通知：控制台输出、文件日志、可扩展支持 Webhook 等。
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional


class PublishNotifier:
    """发布结果通知器"""

    def __init__(self, log_dir: str = "douyin/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def notify(self, result: dict):
        """
        发送通知

        Args:
            result: 发布结果字典
                - success: bool
                - message: str
                - video: str (optional)
                - title: str (optional)
                - error: str (optional)
        """
        # 1. 记录日志文件
        self._log_to_file(result)

        # 2. 控制台输出
        self._log_to_console(result)

    def _log_to_file(self, result: dict):
        """写入日志文件"""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"publish_{today}.log"

        entry = {
            "timestamp": datetime.now().isoformat(),
            "success": result.get("success", False),
            "message": result.get("message", ""),
            "video": result.get("video", ""),
            "title": result.get("title", ""),
            "error": result.get("error", ""),
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _log_to_console(self, result: dict):
        """控制台输出"""
        status_icon = "✅" if result.get("success") else "❌"
        print(f"{status_icon} [{datetime.now().strftime('%H:%M:%S')}] {result.get('message', '')}")

        if result.get("error"):
            print(f"  ⚠️  错误: {result['error']}")

    def get_today_log(self) -> list:
        """获取今天的发布日志"""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"publish_{today}.log"

        if not log_file.exists():
            return []

        entries = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def get_recent_logs(self, days: int = 7) -> list:
        """获取最近几天的发布日志"""
        all_entries = []
        for i in range(days):
            date = (datetime.now() - __import__("datetime").timedelta(days=i)).strftime("%Y-%m-%d")
            log_file = self.log_dir / f"publish_{date}.log"
            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                all_entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        return all_entries
