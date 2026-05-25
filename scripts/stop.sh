#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 停止脚本
#
# 停止夸父后台进程（launcher / 飞书机器人等）。
#
# 用法:
#   bash scripts/stop.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PID_FILE="$ROOT_DIR/kuafu.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "⏹  停止夸父 (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            echo "  强制停止..."
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
        echo "✅ 夸父已停止"
    else
        echo "  PID $PID 不存在，清理 pid 文件"
        rm -f "$PID_FILE"
    fi
fi

# 也停止所有夸父进程
PIDS=$(pgrep -f "python.*core\\.main" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "⏹  停止 launcher 进程..."
    kill $PIDS 2>/dev/null || true
fi

echo "✅ 完成"
