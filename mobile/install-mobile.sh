#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 手机端一键安装脚本 (Termux)
# ============================================================================
#
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/zhugezihou/kuafu/main/mobile/install-mobile.sh | bash
#   # 或本地:
#   bash mobile/install-mobile.sh
#
# 作用:
#   1. 安装 Termux 依赖 (Python, git, curl)
#   2. 克隆/更新夸父代码库
#   3. 安装 Python 依赖
#   4. 创建启动快捷方式
#   5. 启动 Web UI
#
# 注意：手机版仅云端模式（DeepSeek），不下载本地模型。
#       需要 DeepSeek API Key 才能使用。
#
# 支持的设备:
#   - Android (Termux)
#   - 任意 Android 设备
#
# ============================================================================

set -euo pipefail

# ─── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
BLU='\033[0;34m'; CYN='\033[0;36m'; MAG='\033[0;35m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GRN}✓${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YLW}⚠${NC} $1"; }
inf()  { echo -e "  ${BLU}→${NC} $1"; }
title(){ echo -e "\n${BOLD}${MAG}$1${NC}\n"; }
sep()  { echo -e "  ${CYN}──────────────────────────────────────────${NC}"; }

# ─── Banner ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYN}"
echo '    _                     __'
echo '   | |                   / _|'
echo '   | | __ ___   ____ _  | |_ _   _ _ __'
echo '   | |/ _` \ \ / / _` | |  _| | | | `__|'
echo '   | | (_| |\ V / (_| | | | | |_| | |  | |'
echo '   |_|\__,_| \_/ \__,_| |_|  \__,_|_|  |_|'
echo ""
echo -e "   逐日不息 · 手机版 (云端模式)${NC}"
echo ""

# ─── 检测 Termux ─────────────────────────────────────────────────────────────
title "📱 检测环境"

if [ ! -d "/data/data/com.termux" ] && [ ! -f "/system/bin/sh" ]; then
    warn "不在 Termux 环境中运行"
    warn "请安装 Termux: https://f-droid.org/packages/com.termux/"
fi

# ─── 安装系统依赖 ────────────────────────────────────────────────────────────
title "📦 安装系统依赖"

inf "更新包列表..."
pkg update -y -q 2>/dev/null || true

DEPS="python git curl wget openssh"
for dep in $DEPS; do
    if pkg list-installed 2>/dev/null | grep -q "^$dep "; then
        ok "$dep 已安装"
    else
        inf "安装 $dep..."
        pkg install -y "$dep" 2>/dev/null || warn "$dep 安装失败（可跳过）"
    fi
done

# ─── 创建必要目录 ────────────────────────────────────────────────────────────
title "📁 创建目录结构"

TERMUX_HOME="/data/data/com.termux/files/home"
KUAFFU_DIR="${KUAFFU_DIR:-$TERMUX_HOME/kuafu}"
mkdir -p "$KUAFFU_DIR/mobile"
mkdir -p "$KUAFFU_DIR/logs"
ok "目录已创建: $KUAFFU_DIR"

# ─── 克隆/更新代码 ───────────────────────────────────────────────────────────
title "📥 获取夸父代码"

if [ -d "$KUAFFU_DIR/.git" ]; then
    inf "夸父目录已存在，更新中..."
    cd "$KUAFFU_DIR"
    git pull --ff-only 2>/dev/null || warn "git pull 失败，跳过"
    ok "代码已更新"
else
    if [ -n "$(ls -A "$KUAFFU_DIR" 2>/dev/null)" ]; then
        warn "目录 $KUAFFU_DIR 非空且不是 git 仓库，跳过克隆"
    else
        inf "克隆夸父代码库..."
        git clone https://github.com/zhugezihou/kuafu.git "$KUAFFU_DIR" 2>/dev/null || {
            err "克隆失败"
            warn "请手动克隆: git clone https://github.com/zhugezihou/kuafu.git $KUAFFU_DIR"
        }
        ok "代码已克隆"
    fi
fi

cd "$KUAFFU_DIR"

# ─── Python 环境 ─────────────────────────────────────────────────────────────
title "🐍 配置 Python 环境"

PYTHON="python3"
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    err "Python 未安装！"
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
inf "Python 版本: $PY_VER"
if [ "$(echo "$PY_VER" | cut -d. -f1)" -lt 3 ] || { [ "$(echo "$PY_VER" | cut -d. -f1)" -eq 3 ] && [ "$(echo "$PY_VER" | cut -d. -f2)" -lt 10 ]; }; then
    err "需要 Python 3.10+，当前是 $PY_VER"
    exit 1
fi

ok "Python 环境就绪"

# ─── 创建 .env ───────────────────────────────────────────────────────────────
title "⚙️ 配置 (云端模式)"

if [ ! -f "$KUAFFU_DIR/.env" ]; then
    # 尝试从环境变量读取 DeepSeek API Key
    DS_KEY="${DEEPSEEK_API_KEY:-}"
    if [ -z "$DS_KEY" ]; then
        echo ""
        warn "手机版使用云端 DeepSeek，需要 API Key"
        warn "获取: https://platform.deepseek.com/usage"
        echo ""
        read -r -p "  请输入 DeepSeek API Key (留空跳过): " input_key
        DS_KEY="${input_key:-}"
    fi

    cat > "$KUAFFU_DIR/.env" << EOF
# 夸父手机端配置 (云端模式)
KUAFU_LLM_BACKEND=cloud
KUAFU_PROVIDERS=deepseek
DEEPSEEK_API_KEY=${DS_KEY}
DEEPSEEK_MODEL=deepseek-chat
KUAFU_PORT=8080
KUAFU_HOST=0.0.0.0
EOF
    ok ".env 已创建（云端模式）"
    if [ -n "$DS_KEY" ]; then
        ok "DeepSeek API Key 已配置"
    else
        warn "DeepSeek API Key 未配置！请编辑 $KUAFFU_DIR/.env 后启动"
    fi
else
    ok ".env 已存在"
fi

# ─── Termux Boot 开机自启 ──────────────────────────────────────────────────
title "🚀 配置后台服务"

SERVICE_DIR="$TERMUX_HOME/.termux/boot"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/kuafu" << 'SERVICEEOF'
#!/data/data/com.termux/files/usr/bin/sh
# 夸父手机端自启脚本 — 启动 Web UI (云端模式)

KUAFFU_DIR="/data/data/com.termux/files/home/kuafu"
cd "$KUAFFU_DIR" || exit 1

# 加载 .env
[ -f "$KUAFFU_DIR/.env" ] && set -a && source "$KUAFFU_DIR/.env" && set +a

# 启动 Web UI
export KUAFU_LLM_BACKEND=cloud
export KUAFU_PROVIDERS=deepseek
python mobile/web_server.py --port "${KUAFU_PORT:-8080}" > "$KUAFFU_DIR/logs/web.log" 2>&1 &
echo "✅ 夸父 Web UI 已启动 (端口 ${KUAFU_PORT:-8080})"
SERVICEEOF

chmod +x "$SERVICE_DIR/kuafu"
ok "自启服务已配置: $SERVICE_DIR/kuafu"

# ─── 创建快捷方式 ────────────────────────────────────────────────────────────
title "🔗 创建快捷方式"

ALIAS_CMD="alias kuafu='bash ~/kuafu/mobile/start-mobile.sh'"
if [ -f "$TERMUX_HOME/.bashrc" ]; then
    if ! grep -q "alias kuafu=" "$TERMUX_HOME/.bashrc"; then
        echo "$ALIAS_CMD" >> "$TERMUX_HOME/.bashrc"
        ok "别名已添加: kuafu → start-mobile.sh"
    else
        ok "别名已存在: kuafu"
    fi
else
    echo "$ALIAS_CMD" > "$TERMUX_HOME/.bashrc"
    ok "已创建 .bashrc 并添加别名: kuafu"
fi

# Termux 快捷方式（长按图标）
cat > "$TERMUX_HOME/.shortcuts/kuafu.sh" << 'SHORTEOF'
#!/data/data/com.termux/files/usr/bin/sh
exec /data/data/com.termux/files/home/kuafu/mobile/start-mobile.sh
SHORTEOF
chmod +x "$TERMUX_HOME/.shortcuts/kuafu.sh" 2>/dev/null || true

# start-mobile.sh （从模板复制，确保对齐当前版本）
cp "$KUAFFU_DIR/mobile/start-mobile.sh" "$KUAFFU_DIR/mobile/start-mobile.sh"
chmod +x "$KUAFFU_DIR/mobile/start-mobile.sh"
ok "启动脚本: $KUAFFU_DIR/mobile/start-mobile.sh"

# ─── 完成 ────────────────────────────────────────────────────────────────────
title "✅ 安装完成！"

echo ""
echo "  ${BOLD}夸父手机版已安装到:${NC}"
echo "    $KUAFFU_DIR"
echo ""
echo "  ${BOLD}启动方式:${NC}"
echo "    ${CYN}1)${NC} 在 Termux 中运行:"
echo "       bash mobile/start-mobile.sh"
echo "    ${CYN}2)${NC} 快捷方式（长按 Termux 桌面图标）:"
echo "       夸父"
echo "    ${CYN}3)${NC} 开机自启（需安装 Termux:Boot）:"
echo "       ~/.termux/boot/kuafu"
echo ""
echo "  ${BOLD}使用方式:${NC}"
echo "    ${CYN}•${NC} 手机浏览器打开: ${BOLD}http://127.0.0.1:8080/${NC}"
echo "    ${CYN}•${NC} 电脑浏览器打开: ${BOLD}http://<手机IP>:8080/${NC}"
echo "       （确保在同一 WiFi 下）"
echo ""
echo "  ${BOLD}常用命令:${NC}"
echo "    ${CYN}•${NC} 启动: bash mobile/start-mobile.sh"
echo "    ${CYN}•${NC} 停止: pkill -f web_server"
echo "    ${CYN}•${NC} 日志: cat logs/web.log"
echo "    ${CYN}•${NC} 更新: git pull"
echo ""
echo -e "${GRN}  逐日不息 · 在指尖${NC}"
echo ""
