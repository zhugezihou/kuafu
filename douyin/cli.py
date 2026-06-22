"""
夸父抖音自动发布 - 命令行入口
══════════════════════════════

用法:
    python douyin/cli.py login          # 首次登录（扫码）
    python douyin/cli.py publish --video demo.mp4 --title "我的视频"
    python douyin/cli.py batch --dir douyin/templates
    python douyin/cli.py today          # 发布今日推荐视频
    python douyin/cli.py status         # 查看发布状态
    python douyin/cli.py add --video /path/to/video.mp4  # 添加到素材库
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# 确保项目根目录在路径中
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from douyin.publisher import DouyinPublisher
from douyin.content_manager import ContentManager


def cmd_login():
    """登录抖音创作者平台"""
    publisher = DouyinPublisher()
    try:
        publisher.start_browser()
        publisher.ensure_logged_in()
        print("\n✅ 登录成功！下次可自动发布。")
    finally:
        publisher.close_browser()


def cmd_publish(video_path: str, title: str = "", tags: list = None, schedule: str = ""):
    """发布单个视频"""
    publisher = DouyinPublisher()
    try:
        publisher.start_browser()
        publisher.ensure_logged_in()
        ok = publisher.publish_video(video_path, title=title, tags=tags, schedule_time=schedule)
        if ok:
            print("\n✅ 发布成功！")
        else:
            print("\n❌ 发布失败")
    finally:
        publisher.close_browser()


def cmd_batch(video_dir: str, title_template: str = None):
    """批量发布目录下所有视频"""
    publisher = DouyinPublisher()
    try:
        publisher.start_browser()
        publisher.ensure_logged_in()
        result = publisher.publish_batch(video_dir, title_template=title_template)
        print(f"\n📊 批量发布结果: 成功 {len(result['success'])} | 失败 {len(result['failed'])}")
    finally:
        publisher.close_browser()


def cmd_today():
    """发布今日推荐视频"""
    config = _load_config()
    content_mgr = ContentManager(config)
    video = content_mgr.get_video_for_today()

    if not video:
        print("📹 没有待发布的视频素材。请先添加视频到 douyin/templates/")
        return

    print(f"📹 今日推荐发布: {video.name}")
    confirm = input("确认发布？(y/n): ").strip().lower()
    if confirm != "y":
        print("已取消")
        return

    publisher = DouyinPublisher(config)
    try:
        publisher.start_browser()
        publisher.ensure_logged_in()
        ok = publisher.publish_video(str(video), title=video.stem)
        if ok:
            print(f"\n✅ 今日视频已发布: {video.name}")
        else:
            print("\n❌ 发布失败")
    finally:
        publisher.close_browser()


def cmd_status():
    """查看发布状态"""
    config = _load_config()
    content_mgr = ContentManager(config)
    stats = content_mgr.get_publish_stats()

    print("📊 抖音发布状态")
    print(f"  总素材数:   {stats['total']}")
    print(f"  已发布:     {stats['published']}")
    print(f"  待发布:     {stats['unpublished']}")
    if stats['pending']:
        print(f"  待发布列表: {', '.join(stats['pending'])}")

    # 检查 Cookie
    from douyin.cookie_manager import CookieManager
    cookie_mgr = CookieManager(config.get("cookie", {}).get("file", "douyin/cookies.json"))
    print(f"  登录状态:   {'✅ 已登录' if cookie_mgr.is_valid() else '❌ 未登录'}")

    # 检查发布历史
    history_file = Path(config.get("history_file", "douyin/publish_history.json"))
    if history_file.exists():
        history = json.loads(history_file.read_text())
        if history:
            last = history[-1]
            print(f"  上次发布:   {last.get('title', '未知')} @ {last.get('published_at', '未知')}")


def cmd_add(video_path: str):
    """添加视频到素材库"""
    config = _load_config()
    content_mgr = ContentManager(config)
    ok = content_mgr.add_to_templates(video_path)
    if ok:
        print(f"✅ 已添加到素材库")
    else:
        print(f"❌ 添加失败: 文件不存在 {video_path}")


def _load_config() -> dict:
    """加载配置"""
    import yaml
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        return yaml.safe_load(config_path.read_text())
    return {}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    commands = {
        "login": cmd_login,
        "today": cmd_today,
        "status": cmd_status,
    }

    if cmd in commands:
        commands[cmd]()
    elif cmd == "publish":
        args = _parse_publish_args()
        cmd_publish(**args)
    elif cmd == "batch":
        args = _parse_batch_args()
        cmd_batch(**args)
    elif cmd == "add":
        if len(sys.argv) < 3:
            print("用法: python douyin/cli.py add --video <路径>")
            return
        video_path = sys.argv[2] if not sys.argv[2].startswith("--") else None
        if "--video" in sys.argv:
            idx = sys.argv.index("--video")
            video_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        if video_path:
            cmd_add(video_path)
        else:
            print("请指定视频路径: python douyin/cli.py add --video <路径>")
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


def _parse_publish_args() -> dict:
    args = {"video_path": "", "title": "", "tags": None, "schedule": ""}
    if "--video" in sys.argv:
        idx = sys.argv.index("--video")
        args["video_path"] = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        args["title"] = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    if "--tags" in sys.argv:
        idx = sys.argv.index("--tags")
        args["tags"] = sys.argv[idx + 1].split(",") if idx + 1 < len(sys.argv) else None
    if "--schedule" in sys.argv:
        idx = sys.argv.index("--schedule")
        args["schedule"] = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    return args


def _parse_batch_args() -> dict:
    args = {"video_dir": "", "title_template": None}
    if "--dir" in sys.argv:
        idx = sys.argv.index("--dir")
        args["video_dir"] = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        args["title_template"] = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
    return args


if __name__ == "__main__":
    main()
