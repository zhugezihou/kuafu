#!/data/data/com.termux/files/usr/bin/sh
# ============================================================================
# 夸父 (Kuafu) — 手机版快速启动（Termux 内一键启动）
#
# 这是最简单的启动方式，适合手机端使用。
# 不需要任何参数，自动检测环境并启动。
# ============================================================================

set -e

KUAFFU_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KUAFFU_DIR"

# 加载 .env
[ -f "$KUAFFU_DIR/.env" ] && set -a && source "$KUAFFU_DIR/.env" && set +a

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok() { echo -e "  ${GRN}✓${NC} $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YLW}⚠${NC} $1"; }
inf() { echo -e "  ${CYN}→${NC} $1"; }

echo ""
echo -e "${CYN}   夸父 · 手机版${NC}"
echo -e "   ${BOLD}逐日不息 · 在指尖${NC}"
echo ""

# 1. 检查 Python
PYTHON="python3"
if ! command -v python3 &>/dev/null; then
    if command -v python &>/dev/null; then
        PYTHON="python"
    else
        err "Python 未安装！"
        err "请先运行: pkg install python"
        exit 1
    fi
fi
ok "Python: $($PYTHON --version 2>&1)"

# 2. 检查 Web UI 文件
if [ ! -f "$KUAFFU_DIR/mobile/web_server.py" ]; then
    err "找不到 mobile/web_server.py"
    err "请确保在夸父根目录运行此脚本"
    exit 1
fi

# 3. 强制云端模式（手机版不跑本地模型）
export KUAFFU_BACKEND=cloud
warn "云端模式（DeepSeek）"

# 4. 获取 IP
DEVICE_IP="$(ip route get 1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2 || echo '127.0.0.1')"
PORT="${KUAFFU_PORT:-8080}"

# 5. 启动
echo ""
echo -e "  ${BOLD}启动夸父...${NC}"
echo ""

exec "$PYTHON" "$KUAFFU_DIR/mobile/web_server.py" --port "$PORT" --host "0.0.0.0"
