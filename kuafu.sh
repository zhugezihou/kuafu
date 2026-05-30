#!/usr/bin/env bash
# 夸父 (Kuafu) 一键启动
# 检查 llama-server 是否就绪（Windows 侧），然后运行夸父
# ⚠️ llama-server 运行在 Windows 侧（GPU/CUDA），不消耗 WSL 内存
#
# 用法:
#   bash kuafu.sh                       # 交互模式
#   bash kuafu.sh "写个脚本"            # 直接执行
#   bash kuafu.sh cron list             # 定时任务
#   bash kuafu.sh sessions list         # 会话管理
#   bash kuafu.sh status                # 状态查看
#   bash kuafu.sh --help                # 全部命令

set -e

KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER_PORT=8080

# 1. 动态检测 llama-server（先 localhost，再 Windows IP）
LLAMA_BASE_URL="http://localhost:${LLAMA_SERVER_PORT}"
if curl -s "${LLAMA_BASE_URL}/v1/models" > /dev/null 2>&1; then
    echo "✅ 本地大模型 (Qwen3.5-9B @ localhost) 就绪"
    export KUAFFU_BACKEND=local
elif WIN_IP=$(ip route | grep default | awk '{print $3}') && \
     [ -n "$WIN_IP" ] && \
     curl -s "http://${WIN_IP}:${LLAMA_SERVER_PORT}/v1/models" > /dev/null 2>&1; then
    LLAMA_BASE_URL="http://${WIN_IP}:${LLAMA_SERVER_PORT}"
    echo "✅ 本地大模型 (Qwen3.5-9B @ ${WIN_IP}) 就绪"
    export KUAFFU_LOCAL_BASE_URL="${LLAMA_BASE_URL}"
    export KUAFFU_BACKEND=local
else
    echo "ℹ️  本地大模型 (Qwen3.5-9B) 未运行，自动切换 DeepSeek 云端"
    echo "   如需本地 GPU 加速，运行 Windows 侧: start-llama.bat"
    export KUAFFU_BACKEND=cloud
fi

# 2. 运行夸父
source "$KUAFFU_DIR/venv/bin/activate"
export PYTHONPATH="$KUAFFU_DIR${PYTHONPATH:+:$PYTHONPATH}"
export KUAFFU_INTERACTIVE=1

# 3. 路由
if [ $# -eq 0 ]; then
    exec python -m core.main
else
    case "$1" in
        cron|sessions|status|model|gateway|setup|skill|tools)
            # 通过 cli.py 处理子命令
            exec python -c "
import sys, core.cli
sys.argv = ['kuafu'] + sys.argv[1:]
sys.exit(core.cli.main())
" "$@"
            ;;
        *)
            exec python -m core.main "$@"
            ;;
    esac
fi
