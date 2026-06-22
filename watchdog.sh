#!/usr/bin/env bash
# ============================================================
# 夸父守护程序 (watchdog.sh)
# 功能：监控夸父进程，如果被关闭则自动重启
# 用法：
#   bash watchdog.sh start    # 启动守护
#   bash watchdog.sh stop     # 停止守护
#   bash watchdog.sh status   # 查看状态
# ============================================================

KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCHDOG_PID_FILE="$KUAFFU_DIR/.watchdog.pid"
KUAFU_PID_FILE="$KUAFFU_DIR/.kuafu.pid"
LOG_FILE="$KUAFFU_DIR/logs/watchdog.log"

mkdir -p "$KUAFFU_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

start_kuafu() {
    log "🚀 启动夸父..."
    cd "$KUAFFU_DIR"
    nohup bash kuafu.sh gateway start > "$KUAFFU_DIR/logs/kuafu.log" 2>&1 &
    KUAFU_PID=$!
    echo $KUAFU_PID > "$KUAFU_PID_FILE"
    log "✅ 夸父已启动 (PID: $KUAFU_PID)"
}

stop_kuafu() {
    if [ -f "$KUAFU_PID_FILE" ]; then
        KUAFU_PID=$(cat "$KUAFU_PID_FILE")
        if kill -0 "$KUAFU_PID" 2>/dev/null; then
            log "⏹ 停止夸父 (PID: $KUAFU_PID)..."
            kill "$KUAFU_PID" 2>/dev/null
            sleep 2
            # 强制杀
            kill -9 "$KUAFU_PID" 2>/dev/null || true
        fi
        rm -f "$KUAFU_PID_FILE"
    fi
    # 也杀 gateway 进程
    GATEWAY_PID=$(pgrep -f "kuafu.*gateway" 2>/dev/null || true)
    if [ -n "$GATEWAY_PID" ]; then
        kill -9 $GATEWAY_PID 2>/dev/null || true
    fi
    log "⏹ 夸父已停止"
}

watchdog_loop() {
    log "🛡 守护程序启动 (PID: $$)"
    echo $$ > "$WATCHDOG_PID_FILE"
    
    # 先启动夸父
    if [ ! -f "$KUAFU_PID_FILE" ] || ! kill -0 $(cat "$KUAFU_PID_FILE") 2>/dev/null; then
        start_kuafu
    fi
    
    # 监控循环
    while true; do
        if [ -f "$KUAFU_PID_FILE" ]; then
            KUAFU_PID=$(cat "$KUAFU_PID_FILE")
            if ! kill -0 "$KUAFU_PID" 2>/dev/null; then
                log "⚠️ 夸父进程 (PID: $KUAFU_PID) 已关闭，正在重启..."
                start_kuafu
            fi
        else
            log "⚠️ PID 文件丢失，重新启动夸父..."
            start_kuafu
        fi
        sleep 5
    done
}

case "${1:-start}" in
    start)
        # 检查是否已有守护在运行
        if [ -f "$WATCHDOG_PID_FILE" ] && kill -0 $(cat "$WATCHDOG_PID_FILE") 2>/dev/null; then
            log "⚠️ 守护程序已在运行 (PID: $(cat $WATCHDOG_PID_FILE))"
            exit 0
        fi
        # 后台运行守护
        nohup bash "$0" _daemon > /dev/null 2>&1 &
        echo "🛡 夸父守护程序已启动 (PID: $!)"
        ;;
    stop)
        if [ -f "$WATCHDOG_PID_FILE" ]; then
            WATCHDOG_PID=$(cat "$WATCHDOG_PID_FILE")
            log "⏹ 停止守护程序 (PID: $WATCHDOG_PID)..."
            kill "$WATCHDOG_PID" 2>/dev/null || true
            rm -f "$WATCHDOG_PID_FILE"
        fi
        stop_kuafu
        echo "⏹ 夸父守护程序已停止"
        ;;
    status)
        echo "═══ 夸父守护状态 ═══"
        if [ -f "$WATCHDOG_PID_FILE" ] && kill -0 $(cat "$WATCHDOG_PID_FILE") 2>/dev/null; then
            echo "🛡 守护程序: 运行中 (PID: $(cat $WATCHDOG_PID_FILE))"
        else
            echo "🛡 守护程序: 未运行"
        fi
        if [ -f "$KUAFU_PID_FILE" ] && kill -0 $(cat "$KUAFU_PID_FILE") 2>/dev/null; then
            echo "🚀 夸父进程: 运行中 (PID: $(cat $KUAFU_PID_FILE))"
        else
            echo "🚀 夸父进程: 未运行"
        fi
        echo "📋 最近日志:"
        tail -5 "$LOG_FILE" 2>/dev/null || echo "   (无日志)"
        ;;
    _daemon)
        # 内部模式：实际运行守护循环
        watchdog_loop
        ;;
    *)
        echo "用法: bash watchdog.sh {start|stop|status}"
        ;;
esac
