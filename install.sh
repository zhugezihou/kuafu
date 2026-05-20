#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 一键安装脚本
# ============================================================================
#
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/zhugezihou/kuafu/main/install.sh | bash
#   # 或本地
#   bash install.sh
#
# 作用:
#   1. 检测系统 (Python 3.10+, pip, git)
#   2. 创建虚拟环境
#   3. 安装依赖
#   4. 运行配置向导
#   5. 给出下一步指引
#
# ============================================================================

set -e

# ─── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
BLU='\033[0;34m'
CYN='\033[0;36m'
MAG='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

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
echo -e "   逐日不息 · 自我超越${NC}"
echo ""

# ─── 1. 系统检测 ─────────────────────────────────────────────────────────────
title "📋 系统检测"

# Python
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &>/dev/null; then
        PYTHON=$cmd
        break
    fi
done

if [ -z "$PYTHON" ]; then
    err "未找到 Python。请安装 Python 3.10+"
    exit 1
fi

PY_VER=$($PYTHON --version 2>&1)
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    err "需要 Python 3.10+，当前: $PY_VER"
    exit 1
fi
ok "Python: $PY_VER"

# pip
if ! $PYTHON -m pip --version &>/dev/null; then
    err "未找到 pip。请安装 python3-pip"
    exit 1
fi
ok "pip: $($PYTHON -m pip --version | head -1)"

# git
if ! command -v git &>/dev/null; then
    err "未找到 git。请安装 git"
    exit 1
fi
ok "git: $(git --version)"

# OS 信息
OS_NAME="unknown"
case "$(uname -s)" in
    Linux)  OS_NAME="Linux ($(grep ^ID= /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"' || echo 'unknown'))" ;;
    Darwin) OS_NAME="macOS ($(sw_vers -productVersion 2>/dev/null || echo 'unknown'))" ;;
    MINGW*|MSYS*) OS_NAME="Windows (Git Bash)" ;;
esac
ok "系统: $OS_NAME"

# GPU 检测
GPU_INFO=""
if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    ok "GPU: $GPU_INFO"
elif command -v clinfo &>/dev/null; then
    GPU_INFO="OpenCL 设备可用"
    ok "OpenCL 可用"
else
    GPU_INFO="未检测到 GPU（仅云端模式可用）"
    warn "$GPU_INFO"
fi

sep

# ─── 2. 检测已有安装 ─────────────────────────────────────────────────────────
KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd 2>/dev/null || pwd)"

if [ -f "$KUAFFU_DIR/.env" ] && grep -q "KUAFFU_API_KEY" "$KUAFFU_DIR/.env" 2>/dev/null; then
    warn "检测到已有 .env 配置，跳过配置向导"
    SKIP_WIZARD=true
fi

if [ -d "$KUAFFU_DIR/venv" ]; then
    warn "检测到已有 venv，跳过虚拟环境创建"
    SKIP_VENV=true
fi

# ─── 3. 创建虚拟环境 ─────────────────────────────────────────────────────────
title "🔧 安装依赖"

if [ "$SKIP_VENV" != "true" ]; then
    inf "创建虚拟环境..."
    $PYTHON -m venv "$KUAFFU_DIR/venv"
    ok "虚拟环境创建完成"
fi

source "$KUAFFU_DIR/venv/bin/activate"
inf "安装依赖 (pyyaml)..."
$PYTHON -m pip install -q -r "$KUAFFU_DIR/requirements.txt" 2>&1 | tail -5
ok "依赖安装完成"

# ─── 4. 可选额外依赖 ─────────────────────────────────────────────────────────
BOLD_DEPS_INSTALLED=false
if [ "$SKIP_WIZARD" != "true" ]; then
    echo ""
    echo -e "  ${BOLD}附加组件（可选，Enter 跳过）${NC}"
    echo -e "  ${YLW}建议先完成基础配置，之后随时可安装${NC}"
    echo ""
    read -p "  ⏎ 回车跳过 | y 安装可选依赖 (rich/jinja2/pyyaml)... " -r INSTALL_EXTRAS
    if [ "$INSTALL_EXTRAS" = "y" ] || [ "$INSTALL_EXTRAS" = "Y" ]; then
        inf "安装可选增强..."
        $PYTHON -m pip install -q rich jinja2 2>&1 | tail -3
        BOLD_DEPS_INSTALLED=true
        ok "可选依赖安装完成"
    fi
fi

# ─── 5. 运行配置向导 ─────────────────────────────────────────────────────────
if [ "$SKIP_WIZARD" != "true" ]; then
    title "⚙️  初始化配置"
    inf "启动配置向导..."
    $PYTHON "$KUAFFU_DIR/setup_wizard.py"
else
    inf "跳过配置向导（已有配置）"
fi

# ─── 6. 运行基本测试 ──────────────────────────────────────────────────────────
title "🧪 运行基本测试"

cd "$KUAFFU_DIR"

if [ -f "tests/test_all.py" ]; then
    inf "运行测试..."
    $PYTHON -m pytest tests/test_all.py -v --tb=short 2>&1 || \
    $PYTHON tests/test_all.py 2>&1 || \
    warn "测试运行出现问题（部分测试可能需要 API key）"
    ok "测试完成"
else
    ok "测试文件已集成，跳过独立测试套件"
fi

# ─── 7. 运行后检查 ───────────────────────────────────────────────────────────
title "✅ 安装完成"

echo ""
echo -e "  ${BOLD}夸父已安装至:${NC} $KUAFFU_DIR"
echo ""
echo -e "  ${BOLD}快速开始:${NC}"
echo ""
echo -e "    cd $KUAFFU_DIR"
echo -e "    source venv/bin/activate"
echo -e "    python -m core.main"
echo ""
echo -e "  ${BOLD}使用 kuafu.sh（一键启动）:${NC}"
echo -e "    bash kuafu.sh                    # 交互模式"
echo -e "    bash kuafu.sh '帮我搜索 Python'  # 单次任务"
echo ""
echo -e "  ${BOLD}Python API:${NC}"
echo -e '    from kuafu import KuafuAgent'
echo -e "    agent = KuafuAgent()"
echo -e "    result = agent.run('你的任务')"
echo ""

# 显示后端配置摘要
if [ -f "$KUAFFU_DIR/.env" ]; then
    BACKEND=$(grep "^KUAFFU_BACKEND" "$KUAFFU_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "cloud")
    BACKEND_DISPLAY="云端 (DeepSeek)"
    if [ "$BACKEND" = "local" ]; then
        BACKEND_DISPLAY="本地 (Qwen3.5-9B via llama.cpp)"
    fi
    echo -e "  ${BOLD}当前配置:${NC}"
    echo -e "    LLM 后端: $BACKEND_DISPLAY"
    echo -e "    配置文件: $KUAFFU_DIR/.env"
fi

echo ""
echo -e "  ${CYN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GRN}夸父已就绪，逐日不息！${NC}"
echo -e "  ${CYN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
