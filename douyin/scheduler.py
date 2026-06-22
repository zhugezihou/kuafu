"""
抖音自动发布调度器
══════════════════
定时检查视频素材目录，自动发布未发布的视频。
支持单次发布和循环调度模式。
"""

import time
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable

# 确保项目根目录在路径中
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from douyin.publisher import DouyinPublisher
from douyin.content_manager import ContentManager


class PublishScheduler:
    """自动发布调度器"""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.content_mgr = ContentManager(self.config)
        self.publisher = DouyinPublisher(self.config)
        self.running = False
        self._on_publish_callback: Optional[Callable] = None

    def _load_config(self, config_path: str = None) -> dict:
        """加载配置"""
        import yaml
        path = Path(config_path) if config_path else Path(__file__).parent / "config.yaml"
        if path.exists():
            return yaml.safe_load(path.read_text())
        return {}

    def on_publish(self, callback: Callable):
        """设置发布回调"""
        self._on_publish_callback = callback

    def _notify(self, result: dict):
        """通知发布结果"""
        if self._on_publish_callback:
            self._on_publish_callback(result)

        # 控制台输出
        status = "✅" if result.get("success") else "❌"
        print(f"\n{status} [{datetime.now().strftime('%H:%M:%S')}] {result.get('message', '')}")
        if result.get("error"):
            print(f"  错误: {result['error']}")

    def publish_once(self, video_dir: str = None) -> dict:
        """
        执行一次发布：检查并发布一个视频

        Args:
            video_dir: 视频素材目录，默认使用配置中的目录

        Returns:
            发布结果字典
        """
        # 获取待发布视频
        unpublished = self.content_mgr.get_unpublished_videos()

        if not unpublished:
            return {
                "success": False,
                "message": "没有待发布的视频",
                "video": None,
            }

        # 选择第一个待发布视频
        video_path = unpublished[0]
        video_name = video_path.stem

        # 生成标题（从文件名或配置模板）
        title = self._generate_title(video_name)

        print(f"\n📹 准备发布: {video_path.name}")
        print(f"📝 标题: {title}")

        # 发布
        try:
            self.publisher.start_browser()
            self.publisher.ensure_logged_in()

            ok = self.publisher.publish_video(
                str(video_path),
                title=title,
                tags=self.config.get("publish", {}).get("default_tags", ["日常", "生活"]),
                schedule_time=self.config.get("publish", {}).get("default_schedule", ""),
            )

            if ok:
                result = {
                    "success": True,
                    "message": f"发布成功: {video_path.name}",
                    "video": str(video_path),
                    "title": title,
                }
            else:
                result = {
                    "success": False,
                    "message": f"发布失败: {video_path.name}",
                    "video": str(video_path),
                    "error": "发布接口返回失败",
                }

        except Exception as e:
            result = {
                "success": False,
                "message": f"发布异常: {video_path.name}",
                "video": str(video_path),
                "error": str(e),
            }
        finally:
            self.publisher.close_browser()

        self._notify(result)
        return result

    def _generate_title(self, video_name: str) -> str:
        """根据视频文件名生成标题"""
        # 从配置中获取标题模板
        template = self.config.get("publish", {}).get("title_template", "{name}")
        return template.replace("{name}", video_name).replace("{date}", datetime.now().strftime("%Y-%m-%d"))

    def publish_all(self, video_dir: str = None) -> list:
        """
        发布所有待发布视频（逐个发布，带间隔）

        Args:
            video_dir: 视频素材目录

        Returns:
            发布结果列表
        """
        unpublished = self.content_mgr.get_unpublished_videos()
        if not unpublished:
            print("📭 没有待发布的视频")
            return []

        min_interval = self.config.get("publish", {}).get("min_interval", 300)
        results = []

        print(f"\n📦 发现 {len(unpublished)} 个待发布视频")
        print(f"⏱️  发布间隔: {min_interval}秒\n")

        for i, video_path in enumerate(unpublished):
            print(f"\n{'='*50}")
            print(f"📹 [{i+1}/{len(unpublished)}] {video_path.name}")
            print(f"{'='*50}")

            result = self.publish_once(str(video_path))
            results.append(result)

            # 如果不是最后一个，等待间隔时间
            if i < len(unpublished) - 1:
                print(f"\n⏳ 等待 {min_interval} 秒后发布下一个...")
                time.sleep(min_interval)

        # 汇总
        success_count = sum(1 for r in results if r["success"])
        fail_count = len(results) - success_count
        print(f"\n{'='*50}")
        print(f"📊 发布完成: ✅ {success_count} 成功 | ❌ {fail_count} 失败")
        print(f"{'='*50}")

        return results

    def run_loop(self, interval_minutes: int = 60, max_runs: int = None):
        """
        循环运行：每隔一段时间检查并发布

        Args:
            interval_minutes: 检查间隔（分钟）
            max_runs: 最大运行次数（None=无限）
        """
        self.running = True
        run_count = 0

        print(f"\n🔄 自动发布调度器已启动")
        print(f"⏱️  检查间隔: {interval_minutes} 分钟")
        print(f"📂 视频目录: {self.config.get('video_sources', ['douyin/templates'])}")
        print(f"{'='*50}\n")

        try:
            while self.running:
                run_count += 1
                now = datetime.now()
                print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 第 {run_count} 次检查...")

                result = self.publish_once()

                if result["success"]:
                    print(f"✅ 已发布，下次检查在 {interval_minutes} 分钟后")
                else:
                    print(f"📭 {result['message']}，下次检查在 {interval_minutes} 分钟后")

                # 检查最大运行次数
                if max_runs and run_count >= max_runs:
                    print(f"\n🛑 已达到最大运行次数 ({max_runs})，停止")
                    break

                # 等待
                for _ in range(interval_minutes * 60):
                    if not self.running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n🛑 调度器已手动停止")
        finally:
            self.running = False

    def stop(self):
        """停止调度器"""
        self.running = False


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="抖音自动发布调度器")
    parser.add_argument("action", choices=["once", "all", "loop", "status"],
                        help="once=发布一个 | all=发布全部 | loop=循环调度 | status=查看状态")
    parser.add_argument("--interval", type=int, default=60, help="循环检查间隔（分钟）")
    parser.add_argument("--max-runs", type=int, default=None, help="最大运行次数")
    parser.add_argument("--video-dir", type=str, default=None, help="视频素材目录")

    args = parser.parse_args()

    scheduler = PublishScheduler()

    if args.action == "once":
        result = scheduler.publish_once(args.video_dir)
        sys.exit(0 if result["success"] else 1)

    elif args.action == "all":
        results = scheduler.publish_all(args.video_dir)
        success = sum(1 for r in results if r["success"])
        sys.exit(0 if success == len(results) else 1)

    elif args.action == "loop":
        scheduler.run_loop(interval_minutes=args.interval, max_runs=args.max_runs)

    elif args.action == "status":
        stats = scheduler.content_mgr.get_publish_stats()
        print(f"\n📊 发布状态")
        print(f"{'='*30}")
        print(f"📹 视频总数:    {stats['total']}")
        print(f"✅ 已发布:      {stats['published']}")
        print(f"⏳ 待发布:      {stats['unpublished']}")
        print(f"📂 素材目录:    {stats['source_dir']}")
        print(f"📋 历史记录:    {stats['history_file']}")


if __name__ == "__main__":
    main()
