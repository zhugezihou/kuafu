#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 安装脚本 v1.0
# ============================================================================
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/zhugezihou/kuafu/main/install.sh | bash
#   bash install.sh
# ============================================================================

set -e

REPO="https://github.com/zhugezihou/kuafu.git"
INSTALL_DIR="${KUAFFU_DIR:-$HOME/kuafu}"
PYTHON="${PYTHON:-python3}"

echo "============================================"
echo "  夸父 (Kuafu) v1.1 — 安装脚本"
echo "============================================"
echo ""

# ── 检查 Python 版本 ──
echo "📋 检查 Python 版本..."
if ! command -v "$PYTHON" &>/dev/null; then
    echo "❌ 未找到 Python3，请先安装 Python 3.10+"
    exit 1
fi
PY_VERSION=$($PYTHON --version 2>&1 | grep -oP '\d+\.\d+')
if [ "$(echo "$PY_VERSION < 3.10" | bc -l 2>/dev/null || echo 1)" = "1" ]; then
    echo "❌ Python $PY_VERSION 版本过低，需要 3.10+"
    exit 1
fi
echo "✅ Python $PY_VERSION"

# ── Clone 仓库 ──
if [ -d "$INSTALL_DIR" ]; then
    echo "📂 目录已存在: $INSTALL_DIR"
    echo "   更新中..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin main 2>/dev/null || true
else
    echo "📥 克隆仓库..."
    git clone --depth 1 "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 创建虚拟环境 ──
echo "🔧 创建虚拟环境..."
cd "$INSTALL_DIR"
if [ ! -d "venv" ]; then
    $PYTHON -m venv venv
fi
source venv/bin/activate

# ── 安装依赖 ──
echo "📦 安装依赖..."
pip install -e . --quiet 2>/dev/null || pip install -e .

# ── 设置向导 ──
echo ""
echo "============================================"
echo "  ✅ 安装完成！"
echo "============================================"
echo ""
echo "快速开始："
echo "  cd $INSTALL_DIR"
echo "  source venv/bin/activate"
echo "  bash kuafu.sh"
echo ""
echo "首次运行建议先执行初始化向导："
echo "  python setup_wizard.py"
echo ""
echo "文档：$INSTALL_DIR/README.md"
