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
#   bash kuafu.sh gateway start       # 启动 Gateway
#   bash kuafu.sh gateway stop        # 停止 Gateway
#   bash kuafu.sh gateway status      # Gateway 状态（systemctl）
#   bash kuafu.sh --help              # 全部命令

set -e

KUAFFU_DIR="$(cd "$(dirname "$0")" && pwd)"

# 加载 .env 环境变量（用 set -a + source 把变量导出）
# 先过滤掉带 $ 或特殊字符的行避免 shell 解析报错
if [ -f "$KUAFFU_DIR/.env" ]; then
    while IFS= read -r line; do
        [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            export "$line" 2>/dev/null || true
        fi
    done < "$KUAFFU_DIR/.env"
fi

source "$KUAFFU_DIR/venv/bin/activate"
export PYTHONPATH="$KUAFFU_DIR${PYTHONPATH:+:$PYTHONPATH}"

# gateway 子命令不需要检测本地模型，直接走 cli
if [ "$1" = "gateway" ]; then
    exec python -c "
import sys, core.cli
sys.argv = ['kuafu'] + sys.argv[1:]
sys.exit(core.cli.main())
" "$@"
fi

LLAMA_SERVER_PORT=8080

# 1. 检测 llama-server（仅交互模式需要）
LLAMA_BASE_URL="http://localhost:${LLAMA_SERVER_PORT}"
if curl -s --connect-timeout 1 "${LLAMA_BASE_URL}/v1/models" > /dev/null 2>&1; then
    echo "✅ 本地大模型 (Qwen3.5-9B @ localhost) 就绪"
    export KUAFFU_BACKEND=local
elif WIN_IP=$(ip route | grep default | awk '{print $3}') && \
     [ -n "$WIN_IP" ] && \
     curl -s --connect-timeout 1 "http://${WIN_IP}:${LLAMA_SERVER_PORT}/v1/models" > /dev/null 2>&1; then
    LLAMA_BASE_URL="http://${WIN_IP}:${LLAMA_SERVER_PORT}"
    echo "✅ 本地大模型 (Qwen3.5-9B @ ${WIN_IP}) 就绪"
    export KUAFFU_LOCAL_BASE_URL="${LLAMA_BASE_URL}"
    export KUAFFU_BACKEND=local
else
    export KUAFFU_BACKEND=cloud
fi

export KUAFFU_INTERACTIVE=1

# 2. 路由
if [ $# -eq 0 ]; then
    exec python -m core.main
else
    case "$1" in
        cron|sessions|status|model|gateway|setup|skill|tools)
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
