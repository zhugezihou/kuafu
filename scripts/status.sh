#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 状态查看脚本
#
# 用法:
#   bash scripts/status.sh            # 查看全部状态
#   bash scripts/status.sh --quick    # 快速检查（简洁输出）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok(){ echo -e "  ${GRN}✓${NC} $1"; }
err(){ echo -e "  ${RED}✗${NC} $1"; }
warn(){ echo -e "  ${YLW}⚠${NC} $1"; }
inf(){ echo -e "  ${CYN}→${NC} $1"; }

echo ""
echo "╔══════════════════════════════════════╗"
echo "║       夸父 (Kuafu) 状态              ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ─── 进程状态 ───
if [ -f "$ROOT_DIR/kuafu.pid" ]; then
    PID=$(cat "$ROOT_DIR/kuafu.pid")
    if kill -0 "$PID" 2>/dev/null; then
        ok "运行中 (PID: $PID)"
        ELAPSED=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
        MEM=$(ps -o rss= -p "$PID" 2>/dev/null | xargs)
        [ -n "$MEM" ] && MEM_MB=$((MEM / 1024)) || MEM_MB="-"
        inf "运行时间: ${ELAPSED:-unknown}"
        inf "内存占用: ${MEM_MB}MB"
    else
        warn "PID 文件存在但进程已死 (PID: $PID)"
        rm -f "$ROOT_DIR/kuafu.pid"
    fi
else
    # 查找所有夸父进程
    KUA_PIDS=$(pgrep -f "python.*kuafu\|python.*core\.main\|python.*launcher" 2>/dev/null || true)
    if [ -n "$KUA_PIDS" ]; then
        warn "无 pid 文件，但有进程: $KUA_PIDS"
    else
        inf "夸父未运行"
    fi
fi

# ─── 配置状态 ───
if [ -f "$ROOT_DIR/.env" ]; then
    ok "配置文件存在 (.env)"
    BACKEND=$(grep "^KUAFFU_BACKEND" "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "cloud")
    inf "后端: $BACKEND"

    if [ "$BACKEND" = "local" ]; then
        # 检查 llama-server
        if pgrep -f "llama-server" &>/dev/null; then
            ok "llama-server 运行中"
        else
            warn "llama-server 未运行 (本地模式需要)"
        fi
        # 检查模型
        MODEL=$(grep "^KUAFFU_API_KEY" "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "")
        if [ -d "$ROOT_DIR/models" ]; then
            GGUF_COUNT=$(ls "$ROOT_DIR/models"/*.gguf 2>/dev/null | wc -l || echo 0)
            if [ "$GGUF_COUNT" -gt 0 ]; then
                ok "模型文件: $GGUF_COUNT 个"
                ls "$ROOT_DIR/models"/*.gguf 2>/dev/null | while read f; do
                    size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || echo 0)
                    inf "  $(basename $f) ($((size/1024/1024))MB)"
                done
            else
                warn "模型目录为空，需要下载模型"
            fi
        else
            warn "models/ 目录不存在"
        fi
    else
        # 云端模式检查 API key
        KEY=$(grep "^KUAFFU_API_KEY" "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "")
        if [ -n "$KEY" ] && [ "$KEY" != "***" ]; then
            ok "API Key 已配置"
        else
            warn "API Key 未配置"
        fi
    fi
else
    err ".env 配置文件不存在"
    inf "请运行: python setup_wizard.py"
fi

# ─── 虚拟环境 ───
if [ -d "$ROOT_DIR/venv" ]; then
    ok "虚拟环境存在 (venv/)"
else
    warn "虚拟环境不存在"
fi

# ─── 记忆状态 ───
MEM_COUNT=$(ls "$ROOT_DIR/memory"/*.json 2>/dev/null | wc -l || echo 0)
if [ "$MEM_COUNT" -gt 0 ]; then
    ok "记忆数据: $MEM_COUNT 个文件"
fi

# ─── 系统资源 ───
echo ""
echo "── 系统资源 ──"
if command -v nvidia-smi &>/dev/null; then
    GPU=$(nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "N/A")
    inf "GPU: $GPU"
fi
FREE_MEM=$(free -h | grep Mem | awk '{print $3 "/" $2}')
inf "内存: $FREE_MEM"

echo ""
if [ "${1:-}" != "--quick" ]; then
    echo "  快速命令:"
    echo "    bash scripts/start.sh      # 启动"
    echo "    bash scripts/status.sh --quick  # 简洁输出"
fi
echo ""
