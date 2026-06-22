"""
抖音视频内容管理器
══════════════════
管理视频素材的扫描、筛选、排序，以及已发布记录的去重。
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional


class ContentManager:
    """视频内容管理器"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.history_file = Path(self.config.get("history_file", "douyin/publish_history.json"))
        self.video_sources = self.config.get("video_sources", ["douyin/templates"])

    def scan_videos(self) -> List[Path]:
        """扫描所有视频源目录，返回视频文件列表"""
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"}
        videos = []

        for source in self.video_sources:
            source_path = Path(source)
            if not source_path.exists():
                continue
            for f in source_path.iterdir():
                if f.is_file() and f.suffix.lower() in video_exts:
                    videos.append(f)

        # 按修改时间排序（最新的在前）
        videos.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return videos

    def get_published_set(self) -> set:
        """获取已发布视频的文件名集合（用于去重）"""
        if not self.history_file.exists():
            return set()

        try:
            history = json.loads(self.history_file.read_text())
            return {entry.get("video", "") for entry in history}
        except (json.JSONDecodeError, KeyError):
            return set()

    def get_unpublished_videos(self) -> List[Path]:
        """获取未发布的视频列表"""
        all_videos = self.scan_videos()
        published = self.get_published_set()

        unpublished = [v for v in all_videos if str(v) not in published]
        return unpublished

    def get_video_for_today(self) -> Optional[Path]:
        """获取今天要发布的视频（取最旧未发布的）"""
        unpublished = self.get_unpublished_videos()
        if not unpublished:
            return None

        # 取最早未发布的
        unpublished.sort(key=lambda f: f.stat().st_mtime)
        return unpublished[0]

    def get_publish_stats(self) -> dict:
        """获取发布统计"""
        published = self.get_published_set()
        unpublished = self.get_unpublished_videos()
        total = len(self.scan_videos())

        return {
            "total": total,
            "published": len(published),
            "unpublished": len(unpublished),
            "pending": [v.name for v in unpublished[:5]],  # 最多显示5个
        }

    def add_to_templates(self, video_path: str) -> bool:
        """将视频文件添加到素材目录"""
        src = Path(video_path)
        if not src.exists():
            return False

        target_dir = Path("douyin/templates")
        target_dir.mkdir(parents=True, exist_ok=True)

        target = target_dir / src.name
        if target.exists():
            # 重名则加时间戳
            stem = target.stem
            suffix = target.suffix
            target = target_dir / f"{stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}"

        import shutil
        shutil.copy2(src, target)
        print(f"[Content] ✅ 已添加视频素材: {target}")
        return True


if __name__ == "__main__":
    # 测试
    mgr = ContentManager()
    stats = mgr.get_publish_stats()
    print(f"📊 发布统计: {stats}")

    today_video = mgr.get_video_for_today()
    if today_video:
        print(f"📹 今日推荐发布: {today_video.name}")
    else:
        print("📹 没有待发布的视频")
