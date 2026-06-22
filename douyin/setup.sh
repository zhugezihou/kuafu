#!/bin/bash
# ═══════════════════════════════════════════════
# 抖音自动发布工作流 - 环境初始化脚本
# ═══════════════════════════════════════════════
# 用法: bash douyin/setup.sh

set -e

echo "🛠️  抖音自动发布工作流 - 环境初始化"
echo "═══════════════════════════════════════"

# 1. 安装 Python 依赖
echo ""
echo "📦 安装 Python 依赖..."
pip3 install playwright pyyaml 2>/dev/null || pip install playwright pyyaml

# 2. 安装 Playwright 浏览器
echo ""
echo "🌐 安装 Playwright Chromium 浏览器..."
python3 -m playwright install chromium 2>/dev/null || playwright install chromium

# 3. 创建必要目录
echo ""
echo "📂 创建目录结构..."
mkdir -p douyin/templates
mkdir -p douyin/logs

# 4. 初始化 .gitkeep
touch douyin/templates/.gitkeep
touch douyin/logs/.gitkeep

# 5. 检查配置
echo ""
echo "✅ 检查配置..."
if [ -f douyin/config.yaml ]; then
    echo "  配置已存在: douyin/config.yaml"
else
    echo "  ⚠️  配置文件不存在，将使用默认配置"
fi

# 6. 检查是否可运行
echo ""
echo "🔍 运行检查..."
python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from douyin.publisher import DouyinPublisher
    print('  ✅ publisher 模块 OK')
except Exception as e:
    print(f'  ⚠️  publisher 模块: {e}')

try:
    from douyin.content_manager import ContentManager
    print('  ✅ content_manager 模块 OK')
except Exception as e:
    print(f'  ⚠️  content_manager 模块: {e}')

try:
    from douyin.scheduler import PublishScheduler
    print('  ✅ scheduler 模块 OK')
except Exception as e:
    print(f'  ⚠️  scheduler 模块: {e}')

try:
    from douyin.notifier import PublishNotifier
    print('  ✅ notifier 模块 OK')
except Exception as e:
    print(f'  ⚠️  notifier 模块: {e}')
"

# 7. 使用说明
echo ""
echo "═══════════════════════════════════════"
echo "🎉 环境初始化完成！"
echo ""
echo "📖 使用说明:"
echo ""
echo "  【首次使用】先登录一次（会打开浏览器扫码）:"
echo "    python douyin/cli.py login"
echo ""
echo "  【发布单个视频】:"
echo "    python douyin/cli.py publish --video douyin/templates/我的视频.mp4 --title '视频标题'"
echo ""
echo "  【批量发布全部待发视频】:"
echo "    python douyin/scheduler.py all"
echo ""
echo "  【定时自动发布（每小时检查）】:"
echo "    python douyin/scheduler.py loop --interval 60"
echo ""
echo "  【查看发布状态】:"
echo "    python douyin/cli.py status"
echo ""
echo "  【添加视频到素材库】:"
echo "    python douyin/cli.py add --video /path/to/video.mp4"
echo ""
echo "  【通过工作流触发】:"
echo "    在夸父平台执行 workflows/douyin-auto-publish.yaml"
echo "═══════════════════════════════════════"
