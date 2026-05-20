#!/usr/bin/env bash
# ============================================================================
# 夸父 (Kuafu) — 下载本地模型
#
# 用法:
#   bash scripts/download_model.sh              # 交互选择
#   bash scripts/download_model.sh --auto       # 自动下载推荐模型
#   bash scripts/download_model.sh Qwen-14B     # 指定型号
#
# 支持的模型:
#   - Qwen3.5-9B-UD-Q4_K_XL.gguf (推荐, 5.8GB, 8K上下文, MTP加速)
#   - Qwen3.5-9B-Q4_K_M.gguf     (标准版, 5.5GB, 4K上下文)
#   - Qwen3.5-14B-Q4_K_M.gguf    (性能版, 8.5GB)
# ============================================================================
set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok(){ echo -e "  ${GRN}✓${NC} $1"; }
err(){ echo -e "  ${RED}✗${NC} $1"; }
warn(){ echo -e "  ${YLW}⚠${NC} $1"; }
inf(){ echo -e "  ${CYN}→${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODELS_DIR="$ROOT_DIR/models"
mkdir -p "$MODELS_DIR"

declare -A MODELS
MODELS["qwen-9b-ud"]="Qwen3.5-9B-UD-Q4_K_XL.gguf|https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-UD-Q4_K_XL.gguf|推荐 (5.8GB, MTP加速, 8K上下文)"
MODELS["qwen-9b"]="Qwen3.5-9B-Q4_K_M.gguf|https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf|标准 (5.5GB)"
MODELS["qwen-14b"]="Qwen3.5-14B-Q4_K_M.gguf|https://huggingface.co/unsloth/Qwen3.5-14B-GGUF/resolve/main/Qwen3.5-14B-Q4_K_M.gguf|性能 (8.5GB)"

if [ $# -ge 1 ] && [ "$1" != "--auto" ]; then
    SELECTED="$1"
    for key in "${!MODELS[@]}"; do
        IFS='|' read -r filename url desc <<< "${MODELS[$key]}"
        if [[ "$key" == *"${SELECTED}"* ]] || [[ "$filename" == *"${SELECTED}"* ]]; then
            TARGET_FILE="$MODELS_DIR/$filename"
            TARGET_URL="$url"
            SELECTED_DESC="$desc"
            break
        fi
    done
fi

if [ -z "${TARGET_FILE:-}" ]; then
    echo ""
    echo -e "  ${BOLD}选择要下载的模型:${NC}"
    echo ""
    local keys=()
    local i=0
    for key in "${!MODELS[@]}"; do
        IFS='|' read -r filename url desc <<< "${MODELS[$key]}"
        keys+=("$key")
        echo -e "  $((i+1)). ${filename}  — ${desc}"
        ((i++))
    done
    echo ""
    read -p "  输入编号 (1-${#keys[@]}, 默认 1): " choice
    choice=${choice:-1}
    idx=$((choice-1))
    if [ "$idx" -lt 0 ] || [ "$idx" -ge "${#keys[@]}" ]; then
        idx=0
    fi
    selected_key="${keys[$idx]}"
    IFS='|' read -r TARGET_FILE TARGET_URL SELECTED_DESC <<< "${MODELS[$selected_key]}"
fi

echo ""
echo -e "  ${BOLD}文件:${NC}  $(basename "$TARGET_FILE")"
echo -e "  ${BOLD}大小:${NC}  $SELECTED_DESC"
echo -e "  ${BOLD}目录:${NC}  $MODELS_DIR"
echo ""

if [ -f "$TARGET_FILE" ]; then
    local_size=$(stat -c%s "$TARGET_FILE" 2>/dev/null || stat -f%z "$TARGET_FILE" 2>/dev/null || echo "0")
    if [ "$local_size" -gt 100000000 ]; then
        ok "模型已存在 ($(numfmt --to=iec $local_size 2>/dev/null || echo "$local_size bytes"))"
        exit 0
    fi
fi

# 检查可用工具
if command -v aria2c &>/dev/null; then
    DL_CMD="aria2c -x 4 -s 4 --continue=true"
    inf "使用 aria2c 多线程下载..."
elif command -v curl &>/dev/null; then
    DL_CMD="curl -L -C -"
    inf "使用 curl 下载..."
elif command -v wget &>/dev/null; then
    DL_CMD="wget -c"
    inf "使用 wget 下载..."
else
    err "未找到下载工具，请安装 curl、wget 或 aria2c"
    exit 1
fi

cd "$MODELS_DIR"
$DL_CMD -o "$(basename "$TARGET_FILE")" "$TARGET_URL"

if [ -f "$TARGET_FILE" ]; then
    size=$(stat -c%s "$TARGET_FILE" 2>/dev/null || stat -f%z "$TARGET_FILE" 2>/dev/null || echo "0")
    ok "模型下载完成: $(numfmt --to=iec $size 2>/dev/null || echo "$size bytes")"
    echo ""
    echo -e "  ${BOLD}下一步:${NC}"
    echo "  1. 编译/安装 llama-server"
    echo "  2. 运行: llama-server -m $TARGET_FILE -c 8192 --port 8080"
    echo "  3. 设置 .env: KUAFFU_BACKEND=local"
    echo "  4. 启动夸父: bash kuafu.sh"
else
    err "下载失败"
    exit 1
fi
