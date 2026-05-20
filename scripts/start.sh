#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 启动脚本
#
# 默认启动交互式 CLI 模式，可配置启动飞书机器人或 cron 模式。
#
# 用法:
#   bash scripts/start.sh              # 交互模式
#   bash scripts/start.sh --feishu     # 启动飞书机器人
#   bash scripts/start.sh --cron       # 仅启动 cron 调度器
#   bash scripts/start.sh --daemon     # 后台运行（nohup）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 自动激活虚拟环境
if [ -d "$ROOT_DIR/venv" ]; then
    source "$ROOT_DIR/venv/bin/activate"
fi

cd "$ROOT_DIR"

MODE="${1:-interactive}"

case "$MODE" in
    --feishu|-f)
        echo "🚀 启动夸父飞书机器人..."
        exec python -m core.feishu_bot
        ;;
    --cron|-c)
        echo "🚀 启动夸父 Cron 调度器..."
        exec python -m core.cron_scheduler
        ;;
    --daemon|-d)
        echo "🚀 后台启动夸父..."
        nohup python launcher.py > "$ROOT_DIR/kuafu.log" 2>&1 &
        PID=$!
        echo $PID > "$ROOT_DIR/kuafu.pid"
        echo "   PID: $PID"
        echo "   日志: $ROOT_DIR/kuafu.log"
        echo "   停止: bash scripts/stop.sh"
        ;;
    *)
        # 默认交互模式
        if [ -f "$ROOT_DIR/kuafu.sh" ]; then
            exec bash "$ROOT_DIR/kuafu.sh"
        else
            exec python -m core.main
        fi
        ;;
esac
