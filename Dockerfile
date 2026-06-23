# ============================================================
# Dockerfile — 夸父 (Kuafu) 多阶段构建镜像
# 架构: 多阶段构建 | 非 root 用户 | 健康检查 | 最小镜像
# ============================================================

# ---- Stage 1: 构建阶段 ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# 安装夸父（零外部依赖，只有 pyyaml）
COPY requirements.txt pyproject.toml README.md ./
COPY core/ ./core/
RUN pip install --no-cache-dir pyyaml --target=/deps && \
    mkdir -p /deps/kuafu && \
    cp -r core /deps/kuafu/

# ---- Stage 2: 运行阶段 ----
FROM python:3.12-slim AS runtime

LABEL maintainer="kuafu" \
      description="夸父 (Kuafu) — 自我进化的 AI Agent 框架" \
      version="1.1.0"

# 安全：创建非 root 用户
RUN groupadd -r appuser && \
    useradd -r -g appuser -d /app -s /sbin/nologin appuser && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

# 从构建阶段拷贝依赖和核心代码
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages

WORKDIR /app

# 启动 GW 模式（默认）
# 覆盖为交互模式: docker compose run --rm kuafu bash
EXPOSE 8765

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; r=urllib.request.urlopen('http://localhost:8765/healthz'); assert r.status==200" || exit 1

# 默认命令：Gateway
CMD ["python", "-m", "core.cli", "gateway", "start", "--host", "0.0.0.0", "--port", "8765"]
