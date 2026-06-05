# ============================================================================
# 夸父 (Kuafu) — 多阶段 Docker 镜像
#
# 构建:
#   docker build -t kuafu .
#
# 运行（交互模式）:
#   docker run -it --rm \
#     -v $(pwd)/.env:/app/.env \
#     -v $(pwd)/memory:/app/memory \
#     kuafu
#
# 运行（Gateway / 飞书/微信）:
#   docker run -d --name kuafu-gateway \
#     -v $(pwd)/.env:/app/.env \
#     -v $(pwd)/memory:/app/memory \
#     -p 8765:8765 \
#     kuafu gateway start --port 8765
#
# 镜像大小: ~180MB (基于 python:3.11-slim)
# ============================================================================

# ─── 阶段 1: 依赖安装 ─────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

LABEL maintainer="zhugezihou"
LABEL description="夸父 (Kuafu) — 自我进化的 AI Agent"

# 安装编译依赖和运行时工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制 pyproject.toml 和 setup 相关文件（利用 Docker 缓存）
# 这样只要依赖不变，pip install 就不会重复执行
COPY pyproject.toml README.md ./
COPY core/__init__.py core/__init__.py

# 安装项目依赖（此时还没有源码，只安装 pyproject.toml 中声明的基础依赖）
RUN pip install --no-cache-dir pyyaml

# ─── 阶段 2: 最终镜像 ─────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="zhugezihou"
LABEL description="夸父 (Kuafu) — 自我进化的 AI Agent"

# 仅安装运行时必需的系统包
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从 builder 阶段复制已安装的依赖
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# 复制项目代码（排除了 .dockerignore 中的文件）
COPY . .

# 安装项目的可执行入口（不重复安装依赖）
RUN pip install --no-cache-dir --no-deps -e .

# 创建运行时数据目录（不做持久化的目录，容器重启后不保留）
RUN mkdir -p /app/memory /app/models /app/skills

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV KUAFFU_DOCKER=1

# 健康检查（检查 Python 进程存活）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)" || exit 1

# 默认命令：交互模式
ENTRYPOINT ["python", "-m", "core.cli"]
CMD []
