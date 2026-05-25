# ============================================================================
# 夸父 (Kuafu) — Docker 镜像
#
# 构建:
#   docker build -t kuafu .
#
# 运行（交互模式）:
#   docker run -it --rm \
#     -v $(pwd)/.env:/app/.env \
#     -v $(pwd)/memory:/app/memory \
#     -v $(pwd)/models:/app/models \
#     kuafu
#
# 运行（后台模式）:
#   docker run -d --name kuafu \
#     -v $(pwd)/.env:/app/.env \
#     -v $(pwd)/memory:/app/memory \
#     kuafu
#   docker logs -f kuafu
# ============================================================================

FROM python:3.11-slim

LABEL maintainer="zhugezihou"
LABEL description="夸父 (Kuafu) — 自我进化的 AI Agent"

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制并安装项目
COPY . .
RUN pip install --no-cache-dir -e .

# 创建数据目录
RUN mkdir -p /app/memory /app/models /app/skills

# 默认命令：交互模式
CMD ["python", "-m", "core.main"]
