#!/usr/bin/env bash
# ============================================================
# 夸父重启脚本 (restart.sh)
# 用法:
#   bash restart.sh              # 重启交互模式
#   bash restart.sh "写个脚本"   # 重启并执行单次任务
#   bash restart.sh gateway      # 重启 Gateway 模式
# ============================================================

set -e

KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$KUAFFU_DIR"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════${NC}"
echo -e "${CYAN}  🌞 夸父重启中...                     ${NC}"
echo -e "${CYAN}═══════════════════════════════════════${NC}"

# 1. 检测并停止正在运行的夸父进程
KUAFU_PIDS=$(pgrep -f "python.*core\.main" 2>/dev/null || true)
GATEWAY_PIDS=$(pgrep -f "kuafu.*gateway" 2>/dev/null || true)

if [ -n "$KUAFU_PIDS" ] || [ -n "$GATEWAY_PIDS" ]; then
    echo -e "${YELLOW}⏹  正在停止夸父进程...${NC}"
    
    # 先优雅停止 Gateway（如果有）
    if [ -n "$GATEWAY_PIDS" ]; then
        echo -e "   → 停止 Gateway..."
        bash "$KUAFFU_DIR/kuafu.sh" gateway stop 2>/dev/null || true
        sleep 1
    fi
    
    # 停止主进程
    if [ -n "$KUAFU_PIDS" ]; then
        for pid in $KUAFU_PIDS; do
            echo -e "   → 停止 PID $pid"
            kill "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
    
    # 检查是否还有残留
    REMAINING=$(pgrep -f "python.*core\.main" 2>/dev/null || true)
    if [ -n "$REMAINING" ]; then
        echo -e "${YELLOW}   → 强制停止残留进程...${NC}"
        kill -9 $REMAINING 2>/dev/null || true
        sleep 0.5
    fi
    
    echo -e "${GREEN}✅ 夸父已停止${NC}"
else
    echo -e "   ℹ️  夸父当前未运行"
fi

# 2. 检查虚拟环境
if [ ! -d "$KUAFFU_DIR/venv" ]; then
    echo -e "${RED}❌ 虚拟环境不存在: $KUAFFU_DIR/venv${NC}"
    echo -e "   请先运行: python3 -m venv venv && pip install -e ."
    exit 1
fi

# 3. 启动
echo ""
echo -e "${GREEN}🚀 正在启动夸父...${NC}"

# 根据参数决定启动模式
if [ "$1" = "gateway" ]; then
    # Gateway 模式
    shift
    echo -e "   模式: Gateway $*"
    exec bash "$KUAFFU_DIR/kuafu.sh" gateway "$@"
elif [ "$1" = "status" ]; then
    # 仅查看状态
    exec bash "$KUAFFU_DIR/kuafu.sh" status
else
    # 交互模式 / 单次任务
    if [ $# -eq 0 ]; then
        echo -e "   模式: 交互式"
    else
        echo -e "   模式: 单次任务"
    fi
    exec bash "$KUAFFU_DIR/kuafu.sh" "$@"
fi
