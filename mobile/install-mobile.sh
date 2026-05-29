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
#   3. 安装 Python 依赖 (pyyaml)
#   4. 下载推荐 GGUF 模型 (Qwen3-8B)
#   5. 下载 llama.cpp 预编译二进制 (arm64)
#   6. 创建 termux-services 配置 (后台运行)
#   7. 创建启动快捷方式
#   8. 启动 Web UI
#
# 支持的设备:
#   - Android (Termux)
#   - 骁龙 8 系列 / 天玑 9000+ / 苹果 A17+
#   推荐: 骁龙 8 Elite (Snapdragon 8 Elite Gen 5)
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
echo -e "   逐日不息 · 手机版${NC}"
echo ""

# ─── 检测 Termux ─────────────────────────────────────────────────────────────
title "📱 检测环境"

if [ ! -d "/data/data/com.termux" ] && [ ! -f "/system/bin/sh" ]; then
    warn "不在 Termux 环境中运行"
    warn "请安装 Termux: https://f-droid.org/packages/com.termux/"
    warn "然后重新运行本脚本"
fi

# ADB/无线调试检测：小米 17 Pro 建议用无线 ADB
if command -v adb &>/dev/null; then
    ok "ADB 已安装（可用于无线调试）"
fi

# ─── 安装系统依赖 ────────────────────────────────────────────────────────────
title "📦 安装系统依赖"

# 更新包列表
inf "更新包列表..."
pkg update -y -q 2>/dev/null || true

# 安装必要包
DEPS="python git curl wget tsu termux-services openssh"
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
mkdir -p "$KUAFFU_DIR/models"
mkdir -p "$KUAFFU_DIR/strategy"
mkdir -p "$KUAFFU_DIR/skills"
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
        # 目录非空但不是 git 仓库
        warn "目录 $KUAFFU_DIR 非空且不是 git 仓库，跳过克隆"
        warn "请确保 mobile/ 目录下有 web_server.py 和 static/chat.html"
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

# ─── 复制 mobile 文件（如果从非 git 方式获取）────────────────────────────────
if [ ! -f "$KUAFFU_DIR/mobile/web_server.py" ]; then
    warn "mobile/web_server.py 不存在"
    warn "请确保夸父代码包含 mobile/ 目录"
fi

# ─── Python 依赖 ─────────────────────────────────────────────────────────────
title "🐍 配置 Python 环境"

# 检查 Python
PYTHON="python3"
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    err "Python 未安装！"
    exit 1
fi

# 检查 Python 版本
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
inf "Python 版本: $PY_VER"
if [ "$(echo "$PY_VER" | cut -d. -f1)" -lt 3 ] || { [ "$(echo "$PY_VER" | cut -d. -f1)" -eq 3 ] && [ "$(echo "$PY_VER" | cut -d. -f2)" -lt 10 ]; }; then
    err "需要 Python 3.10+，当前是 $PY_VER"
    exit 1
fi

# 安装 pyyaml（唯一外部依赖）
inf "安装 pyyaml..."
pip install pyyaml 2>/dev/null || pkg install python-pyaml 2>/dev/null || {
    warn "pyyaml 安装失败，部分功能不可用"
}
ok "Python 环境就绪"

# ─── 下载 llama.cpp (arm64) ─────────────────────────────────────────────────
title "🦙 安装 llama.cpp (arm64)"

LLAMA_DIR="$KUAFFU_DIR/llama.cpp"
mkdir -p "$LLAMA_DIR"

if [ -f "$LLAMA_DIR/llama-server" ] || [ -f "$LLAMA_DIR/llama-cli" ]; then
    ok "llama.cpp 已存在"
else
    inf "下载预编译 arm64 二进制..."

    # 从 GitHub releases 下载
    LLAMA_VERSION="b4728"
    LLAMA_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_VERSION}/llama-${LLAMA_VERSION}-bin-ubuntu-arm64.zip"

    warn "下载 llama.cpp (~200MB) 可能需要几分钟..."
    wget -q --show-progress "$LLAMA_URL" -O /tmp/llama.zip 2>/dev/null || {
        err "下载失败"
        warn "请手动下载:"
        warn "  wget $LLAMA_URL"
        warn "  解压到 $LLAMA_DIR"
        warn "  或使用 Termux 编译: pkg install llama.cpp"
        LLAMA_SKIPPED=1
    }

    if [ -z "${LLAMA_SKIPPED:-}" ]; then
        cd "$LLAMA_DIR"
        unzip -o /tmp/llama.zip 2>/dev/null
        rm -f /tmp/llama.zip
        chmod +x llama-server llama-cli 2>/dev/null || true

        if [ -f "$LLAMA_DIR/llama-server" ]; then
            ok "llama-server 已安装: $(ls -lh $LLAMA_DIR/llama-server | awk '{print $5}')"
        else
            warn "llama-server 未找到，尝试 pkg 安装..."
            pkg install -y llama.cpp 2>/dev/null || true
        fi
    fi
fi

# 尝试从 pkg 安装（如果上面失败）
if ! command -v llama-server &>/dev/null && [ ! -f "$LLAMA_DIR/llama-server" ]; then
    inf "尝试 pkg install llama.cpp..."
    pkg install -y llama.cpp 2>/dev/null && ok "llama.cpp 已安装 (pkg)" || warn "llama.cpp 安装跳过"
fi

# 添加到 PATH
if [ -d "$LLAMA_DIR" ] && ! echo "$PATH" | grep -q "$LLAMA_DIR"; then
    export PATH="$LLAMA_DIR:$PATH"
    echo 'export PATH="$HOME/kuafu/llama.cpp:$PATH"' >> "$TERMUX_HOME/.bashrc"
fi

# ─── 下载模型 ────────────────────────────────────────────────────────────────
title "📦 下载模型"

MODEL_FILE="$KUAFFU_DIR/models/qwen3-8b-q4_k_m.gguf"

if [ -f "$MODEL_FILE" ]; then
    MODEL_SIZE=$(ls -lh "$MODEL_FILE" | awk '{print $5}')
    ok "模型已存在: $MODEL_SIZE"
else
    echo ""
    echo "  请选择要下载的模型："
    echo "    ${BOLD}1)${NC} Qwen3-8B-Q4_K_M   (4.7GB, 推荐) ${GRN}★${NC}"
    echo "    ${BOLD}2)${NC} Qwen3-4B-Q4_K_M   (2.5GB, 轻量)"
    echo "    ${BOLD}3)${NC} Qwen3-14B-Q4_K_M  (8.5GB, 性能)"
    echo "    ${BOLD}s)${NC} 跳过模型下载"
    echo ""
    read -r -p "  请选择 [1/2/3/s]: " model_choice

    case "$model_choice" in
        1|"")
            MODEL_URL="https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf"
            MODEL_FILE="$KUAFFU_DIR/models/qwen3-8b-q4_k_m.gguf"
            ;;
        2)
            MODEL_URL="https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
            MODEL_FILE="$KUAFFU_DIR/models/qwen3-4b-q4_k_m.gguf"
            ;;
        3)
            MODEL_URL="https://huggingface.co/unsloth/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf"
            MODEL_FILE="$KUAFFU_DIR/models/qwen3-14b-q4_k_m.gguf"
            ;;
        s|S)
            warn "跳过模型下载"
            warn "稍后可用: bash $KUAFFU_DIR/scripts/download_model.sh"
            MODEL_SKIPPED=1
            ;;
        *)
            warn "无效选择，跳过"
            MODEL_SKIPPED=1
            ;;
    esac

    if [ -z "${MODEL_SKIPPED:-}" ] && [ -n "${MODEL_URL:-}" ]; then
        inf "下载模型 (~4.7GB, 可能需要较长时间)..."
        warn "请确保手机连接 WiFi"
        warn "下载过程中请勿关闭 Termux"
        wget -q --show-progress "$MODEL_URL" -O "$MODEL_FILE" 2>/dev/null || {
            err "下载失败"
            # 尝试使用 curl 重试
            warn "重试使用 curl..."
            curl -L -o "$MODEL_FILE" "$MODEL_URL" 2>/dev/null || {
                err "下载失败，请稍后重试"
                warn "手动下载: wget $MODEL_URL"
                rm -f "$MODEL_FILE" 2>/dev/null
            }
        }
        if [ -f "$MODEL_FILE" ]; then
            ok "模型已下载: $(ls -lh "$MODEL_FILE" | awk '{print $5}')"
        fi
    fi
fi

# ─── 创建 .env ───────────────────────────────────────────────────────────────
title "⚙️ 配置"

if [ ! -f "$KUAFFU_DIR/.env" ]; then
    cat > "$KUAFFU_DIR/.env" << 'EOF'
# 夸父手机端配置
KUAFFU_BACKEND=local
KUAFFU_LOCAL_BASE_URL=http://127.0.0.1:8080
KUAFFU_PORT=8080
KUAFFU_HOST=0.0.0.0
EOF
    ok ".env 已创建（本地模型模式）"
else
    ok ".env 已存在"
fi

# ─── Termux Boot 开机自启 ──────────────────────────────────────────────────
title "🚀 配置后台服务"

SERVICE_DIR="$TERMUX_HOME/.termux/boot"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/kuafu" << 'SERVICEEOF'
#!/data/data/com.termux/files/usr/bin/sh
# 夸父手机端自启脚本
# 启动 llama-server + Web UI

KUAFFU_DIR="/data/data/com.termux/files/home/kuafu"
cd "$KUAFFU_DIR" || exit 1

# 加载 .env
[ -f "$KUAFFU_DIR/.env" ] && set -a && source "$KUAFFU_DIR/.env" && set +a

# 启动 llama-server（如果模型存在）
MODEL="$KUAFFU_DIR/models/qwen3-8b-q4_k_m.gguf"
LLAMA_PATH="$(command -v llama-server 2>/dev/null || echo "$KUAFFU_DIR/llama.cpp/llama-server")"

if [ -f "$MODEL" ] && [ -n "$LLAMA_PATH" ] && [ -f "$LLAMA_PATH" ]; then
    $LLAMA_PATH \
        -m "$MODEL" \
        -c 4096 \
        --port 8080 \
        --host 127.0.0.1 \
        -ngl 99 \
        --mlock \
        > "$KUAFFU_DIR/logs/llama.log" 2>&1 &
    echo "✅ llama-server 已启动"
else
    echo "⚠️ 模型或 llama-server 未就绪，跳过"
fi

# 启动 Web UI
sleep 2
cd "$KUAFFU_DIR"
KUAFFU_BACKEND=local python mobile/web_server.py --port 8080 > "$KUAFFU_DIR/logs/web.log" 2>&1 &
echo "✅ 夸父 Web UI 已启动 (端口 8080)"
SERVICEEOF

chmod +x "$SERVICE_DIR/kuafu"
ok "自启服务已配置: $SERVICE_DIR/kuafu"

# ─── Termux:Services 配置 ────────────────────────────────────────────────────
SERVICES_DIR="$PREFIX/var/lib/termux-services"
mkdir -p "$SERVICES_DIR"

cat > "$SERVICES_DIR/kuafu/run" << 'RUNEOF'
#!/data/data/com.termux/files/usr/bin/sh
exec 2>&1

KUAFFU_DIR="/data/data/com.termux/files/home/kuafu"
cd "$KUAFFU_DIR" || exit 1

# 加载 .env
[ -f "$KUAFFU_DIR/.env" ] && set -a && source "$KUAFFU_DIR/.env" && set +a
export KUAFFU_BACKEND=local

echo "🚀 启动夸父 Web UI..."
exec python mobile/web_server.py --port "${KUAFFU_PORT:-8080}"
RUNEOF

chmod +x "$SERVICES_DIR/kuafu/run" 2>/dev/null || true
ok "termux-services 已配置（如有安装）"

# ─── 启动快捷方式 ────────────────────────────────────────────────────────────
title "🔗 创建快捷方式"

# 别名：输入 kuafu 直接启动
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

# 创建启动脚本
cat > "$KUAFFU_DIR/mobile/start-mobile.sh" << 'STARTEOF'
#!/data/data/com.termux/files/usr/bin/sh
# ============================================================================
# 夸父手机端启动脚本
# ============================================================================
set -e

KUAFFU_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KUAFFU_DIR"

# 加载 .env
[ -f "$KUAFFU_DIR/.env" ] && set -a && source "$KUAFFU_DIR/.env" && set +a

# 颜色
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok() { echo -e "  ${GRN}✓${NC} $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YLW}⚠${NC} $1"; }
inf() { echo -e "  ${CYN}→${NC} $1"; }

echo ""
echo -e "${CYN}   夸父 · 手机版${NC}"
echo ""

# 强制云端模式（手机版不跑本地模型）
export KUAFFU_BACKEND=cloud
warn "云端模式（DeepSeek）"

# 启动 Web UI
PORT="\${KUAFFU_PORT:-8080}"
HOST="\${KUAFFU_HOST:-0.0.0.0}"

inf "启动夸父 Web UI..."
export KUAFFU_BACKEND="\${KUAFFU_BACKEND:-cloud}"

python "\$KUAFFU_DIR/mobile/web_server.py" --port "\$PORT" --host "\$HOST" 2>&1
STARTEOF

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
echo "    ${CYN}•${NC} 停止: pkill -f llama-server; pkill -f web_server"
echo "    ${CYN}•${NC} 日志: cat logs/llama.log"
echo "    ${CYN}•${NC} 更新: git pull"
echo ""
echo -e "${GRN}  逐日不息 · 在指尖${NC}"
echo ""
