#!/data/data/com.termux/files/usr/bin/sh
# ============================================================================
# 夸父 — Termux 后台服务守护进程
#
# 功能：
#   1. 启动 Web UI（HTTP 服务）
#   2. 心跳保活（Android 后台限制下保持运行）
#   3. 崩溃自动重启
#   4. 日志管理
#
# 注意：手机版仅云端模式，不启动本地 llama-server。
#
# 用法:
#   bash mobile/termux-daemon.sh start    # 启动
#   bash mobile/termux-daemon.sh stop     # 停止
#   bash mobile/termux-daemon.sh restart  # 重启
#   bash mobile/termux-daemon.sh status   # 查看状态
#   bash mobile/termux-daemon.sh logs     # 查看日志
# ============================================================================

set -e

# ─── 路径 ────────────────────────────────────────────────────────────────────
KUAFFU_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOGS_DIR="$KUAFFU_DIR/logs"
WEB_LOG="$LOGS_DIR/web.log"
DAEMON_LOG="$LOGS_DIR/daemon.log"
PID_DIR="$KUAFFU_DIR/.pids"
mkdir -p "$LOGS_DIR" "$PID_DIR"

# ─── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GRN}✓${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YLW}⚠${NC} $1"; }
inf()  { echo -e "  ${CYN}→${NC} $1"; }

# ─── 加载 .env ───────────────────────────────────────────────────────────────
[ -f "$KUAFFU_DIR/.env" ] && set -a && source "$KUAFFU_DIR/.env" && set +a
PORT="${KUAFU_PORT:-8080}"

# ─── 检测 Termux 后台限制 ──────────────────────────────────────────────────
check_termux() {
    if [ ! -d "/data/data/com.termux" ]; then
        warn "不在 Termux 环境中，部分保活功能不可用"
        echo "0"
        return
    fi

    if command -v tsudo &>/dev/null; then
        if [ "$(id -u)" = "0" ]; then
            ok "已提权运行 (root)，后台存活更稳定"
            echo "2"
            return
        fi
    fi

    if command -v termux-wake-lock &>/dev/null; then
        echo "1"
        return
    fi

    echo "0"
}

# ─── 获取 PID ────────────────────────────────────────────────────────────────
get_pid() {
    local name="$1"
    local pid_file="$PID_DIR/${name}.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return
        fi
    fi
    local pgrep_pid
    pgrep_pid=$(pgrep -f "$name" 2>/dev/null | head -1)
    echo "${pgrep_pid:-}"
}

# ─── 停止 ────────────────────────────────────────────────────────────────────
do_stop() {
    echo ""
    echo -e "${BOLD}🛑 停止夸父服务...${NC}"

    WEB_PID=$(get_pid "web_server.py")
    if [ -n "$WEB_PID" ]; then
        kill "$WEB_PID" 2>/dev/null || true
        ok "Web UI 已停止 (PID $WEB_PID)"
        rm -f "$PID_DIR/web.pid"
    else
        inf "Web UI 未运行"
    fi

    DAEMON_PID=$(get_pid "termux-daemon")
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        ok "守护进程已停止"
        rm -f "$PID_DIR/daemon.pid"
    fi

    if command -v termux-wake-unlock &>/dev/null; then
        termux-wake-unlock 2>/dev/null || true
    fi

    echo ""
    ok "夸父服务已停止"
}

# ─── 启动 Web UI ─────────────────────────────────────────────────────────────
start_web() {
    if get_pid "web_server.py" > /dev/null; then
        ok "Web UI 已在运行"
        return 0
    fi

    inf "启动夸父 Web UI (端口 $PORT)..."

    cd "$KUAFFU_DIR"
    KUAFU_LLM_BACKEND="${KUAFU_LLM_BACKEND:-cloud}" \
    KUAFU_PROVIDERS="${KUAFU_PROVIDERS:-deepseek}" \
    KUAFU_PORT="$PORT" \
    python "$KUAFFU_DIR/mobile/web_server.py" --port "$PORT" --host "0.0.0.0" \
        > "$WEB_LOG" 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_DIR/web.pid"

    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
        ok "Web UI 已启动 (PID $pid)"
    else
        err "Web UI 启动失败"
        tail -5 "$WEB_LOG"
        return 1
    fi
}

# ─── 主启动 ──────────────────────────────────────────────────────────────────
do_start() {
    echo ""
    echo -e "${BOLD}${CYN}   夸父 · Termux 守护进程${NC}"
    echo ""

    local env_level
    env_level=$(check_termux)

    if [ "$env_level" -ge 1 ] && command -v termux-wake-lock &>/dev/null; then
        inf "获取 Wakelock（防止后台休眠）..."
        termux-wake-lock
        ok "Wakelock 已获取"
    fi

    warn "云端模式（DeepSeek）—— 不启动本地模型"

    start_web || {
        err "Web UI 启动失败"
        do_stop
        exit 1
    }

    # 守护进程（心跳保活 + 崩溃自启）
    (
        trap '' HUP INT QUIT TERM

        while true; do
            if ! get_pid "web_server.py" > /dev/null; then
                warn "[$(date '+%H:%M:%S')] Web UI 崩溃，重启..."
                cd "$KUAFFU_DIR"
                KUAFU_LLM_BACKEND="${KUAFU_LLM_BACKEND:-cloud}" \
                KUAFU_PROVIDERS="${KUAFU_PROVIDERS:-deepseek}" \
                KUAFU_PORT="$PORT" \
                python "$KUAFFU_DIR/mobile/web_server.py" --port "$PORT" --host "0.0.0.0" \
                    > "$WEB_LOG" 2>&1 &
                echo $! > "$PID_DIR/web.pid"
            fi
            sleep 15
        done
    ) &

    local daemon_pid=$!
    echo "$daemon_pid" > "$PID_DIR/daemon.pid"

    DEVICE_IP="$(ip route get 1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2 || echo '127.0.0.1')"

    echo ""
    echo -e "${GRN}  ✅ 夸父手机版已启动！${NC}"
    echo ""
    echo -e "  ${BOLD}访问地址:${NC}"
    echo -e "    ${CYN}•${NC} 手机: ${BOLD}http://127.0.0.1:$PORT/${NC}"
    echo -e "    ${CYN}•${NC} 电脑: ${BOLD}http://$DEVICE_IP:$PORT/${NC}"
    echo -e "         （确保在同一 WiFi）"
    echo ""
    echo -e "  ${BOLD}管理命令:${NC}"
    echo -e "    ${CYN}•${NC} bash mobile/termux-daemon.sh status"
    echo -e "    ${CYN}•${NC} bash mobile/termux-daemon.sh stop"
    echo ""

    if [ "$env_level" -lt 2 ]; then
        echo -e "  ${YLW}⚠ 提示:${NC}"
        echo -e "  在 Termux 设置中启用『后台运行』权限"
        echo -e "  避免夸父被 Android 系统杀死"
        echo ""
    fi
}

# ─── 状态 ────────────────────────────────────────────────────────────────────
do_status() {
    echo -e "\n${BOLD}📊 夸父服务状态${NC}\n"

    LOCAL_IP="$(ip route get 1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2)"

    WEB_PID=$(get_pid "web_server.py")
    if [ -n "$WEB_PID" ]; then
        WEB_UPTIME=$(ps -o etime= -p "$WEB_PID" 2>/dev/null | xargs)
        ok "Web UI: 运行中 (PID $WEB_PID, 运行 $WEB_UPTIME)"
    else
        err "Web UI: 未运行"
    fi

    DAEMON_PID=$(get_pid "termux-daemon")
    if [ -n "$DAEMON_PID" ]; then
        ok "守护进程: 运行中 (PID $DAEMON_PID)"
    else
        warn "守护进程: 未运行"
    fi

    if [ -n "$LOCAL_IP" ]; then
        ok "网络: http://$LOCAL_IP:$PORT/"
    fi

    MEM_TOTAL=$(free -m 2>/dev/null | grep Mem | awk '{print $2}')
    MEM_AVAIL=$(free -m 2>/dev/null | grep Mem | awk '{print $7}')
    if [ -n "$MEM_AVAIL" ]; then
        ok "内存: ${MEM_AVAIL}MB 可用 / ${MEM_TOTAL}MB 总量"
    fi

    echo ""
}

# ─── 日志 ────────────────────────────────────────────────────────────────────
do_logs() {
    local target="${1:-all}"
    case "$target" in
        web|ui)
            echo -e "${CYN}═══ Web UI 日志 (最近 50 行) ═══${NC}"
            tail -50 "$WEB_LOG" 2>/dev/null || echo "（无日志）"
            ;;
        daemon)
            echo -e "${CYN}═══ 守护进程日志 (最近 50 行) ═══${NC}"
            tail -50 "$DAEMON_LOG" 2>/dev/null || echo "（无日志）"
            ;;
        all|*)
            do_logs web
            echo ""
            do_logs daemon
            ;;
    esac
}

# ─── 主命令路由 ──────────────────────────────────────────────────────────────
case "${1:-start}" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        sleep 2
        do_start
        ;;
    status)
        do_status
        ;;
    logs)
        do_logs "${2:-all}"
        ;;
    help|--help|-h)
        echo ""
        echo "用法: bash mobile/termux-daemon.sh <命令>"
        echo ""
        echo "  命令:"
        echo "    start    启动夸父服务（默认）"
        echo "    stop     停止夸父服务"
        echo "    restart  重启夸父服务"
        echo "    status   查看服务状态"
        echo "    logs     查看日志 (web/daemon/all)"
        echo ""
        ;;
    *)
        err "未知命令: $1"
        echo "用法: bash mobile/termux-daemon.sh {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
