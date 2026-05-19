#!/usr/bin/env bash
# 夸父 (Kuafu) 一键启动
# 自动检查/启动 llama-server，然后运行夸父

set -e

KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER="$HOME/llama.cpp/build/bin/llama-server"
MODEL="$HOME/models/Qwen3.5-9B-Q4_K_M.gguf"

# 1. 检查本地模型服务是否在跑
if ! curl -s http://localhost:8080/v1/models > /dev/null 2>&1; then
    echo "🚀 启动本地大模型 (Qwen3.5-9B) — 约 5-30 秒..."
    if [ ! -f "$LLAMA_SERVER" ]; then
        echo "❌ 未找到 llama-server: $LLAMA_SERVER"
        exit 1
    fi
    if [ ! -f "$MODEL" ]; then
        echo "❌ 未找到模型文件: $MODEL"
        exit 1
    fi
    nohup "$LLAMA_SERVER" \
        -m "$MODEL" \
        -c 32768 -ngl 99 --host 127.0.0.1 --port 8080 \
        -t 10 --flash-attn on --reasoning off \
        > /dev/null 2>&1 &
    # 等待服务就绪（最多 60s）
    echo -n "⌛ 加载中..."
    for i in $(seq 1 60); do
        sleep 1
        if curl -s http://localhost:8080/v1/models > /dev/null 2>&1; then
            echo ""
            echo "✅ 模型就绪 ($((i))s)"
            break
        fi
        if [ "$i" -eq 60 ]; then
            echo ""
            echo "❌ 模型启动超时（60s），请检查 GPU 驱动 / 内存"
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
