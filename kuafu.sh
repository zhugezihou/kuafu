#!/usr/bin/env bash
# 夸父 (Kuafu) 一键启动
# 自动检查/启动 llama-server，然后运行夸父

set -e

KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER="/mnt/c/Users/asus/llama-server/llama-server.exe"
MODEL="/mnt/c/Users/asus/models/Qwen3.5-9B-UD-Q4_K_XL.gguf"
LLAMA_DIR="C:\\Users\\asus\\llama-server"
MODEL_DIR="C:\\Users\\asus\\models\\Qwen3.5-9B-UD-Q4_K_XL.gguf"
# ⚠️ llama-server 运行在 Windows 侧，使用 Windows 内存/GPU 资源
# 不消耗 WSL 内存，根治 OOM-kill 崩溃

# 1. 检查本地模型服务是否在跑
if ! curl -s http://localhost:8080/v1/models > /dev/null 2>&1; then
    echo "🚀 启动本地大模型 (Qwen3.5-9B) — 约 5-30 秒..."
    if [ ! -f "$LLAMA_SERVER" ]; then
        echo "❌ 未找到 llama-server: $LLAMA_SERVER（Windows侧）"
        echo "  使用: cmd.exe /c start $LLAMA_DIR\\llama-server.exe ..."
        exit 1
    fi
    if [ ! -f "$MODEL" ]; then
        echo "❌ 未找到模型文件: $MODEL（Windows侧）"
        exit 1
    fi
    # 从 WSL 调用 Windows 侧 llama-server.exe（不消耗 WSL 内存/swap）
    # -ngl 99 由 Windows 侧 CUDA 驱动处理，与 WSL 无关
    # --host 0.0.0.0 使 Windows 侧监听所有接口，WSL 可通过 localhost 访问
    "$LLAMA_SERVER" \
        -m "$MODEL" \
        -c 8192 --host 0.0.0.0 --port 8080 \
        --cache-type-k q8_0 --cache-type-v q8_0 \
        --spec-type draft-mtp \
        --reasoning off -ngl 99 \
        > /dev/null 2>&1 &
    LLAMA_PID=$!
    # 等待服务就绪（最多 90s，Windows 侧 CUDA 首次加载较慢）
    echo -n "⌛ 加载中..."
    for i in $(seq 1 90); do
        sleep 1
        if curl -s http://localhost:8080/v1/models > /dev/null 2>&1; then
            echo ""
            echo "✅ 模型就绪 ($((i))s)"
            break
        fi
        if [ "$i" -eq 90 ]; then
            echo ""
            echo "❌ 模型启动超时（90s），请检查 Windows 侧 llama-server 是否正常"
            exit 1
        fi
    done
fi

# 2. 运行夸父
source "$KUAFFU_DIR/venv/bin/activate"
if [ $# -eq 0 ]; then
    # 无参数 → 交互模式
    python3 "$KUAFFU_DIR/run.py"
else
    # 有参数 → 命令式
    python3 "$KUAFFU_DIR/run.py" "$*"
fi
