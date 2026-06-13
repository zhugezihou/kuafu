# ============================================================
# Dockerfile — Python 应用生产级镜像
# 架构: 多阶段构建 | 非 root 用户 | 健康检查 | 最小镜像
# ============================================================

# ---- Stage 1: 构建阶段 ----
FROM python:3.12-slim AS builder

# 安全：设置环境变量避免 pyc 和 pip 缓存膨胀
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# 分层缓存：先复制依赖文件，再安装依赖
# 这样 requirements.txt 不变时，这一层会命中缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --target=/deps

# ---- Stage 2: 运行阶段 ----
FROM python:3.12-slim AS runtime

# 元数据标签
LABEL maintainer="kuafu" \
      description="Python web application" \
      version="1.0.0"

# 安全：创建非 root 用户
RUN groupadd -r appuser && \
    useradd -r -g appuser -d /app -s /sbin/nologin appuser && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

# 从构建阶段拷贝已安装的依赖
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages

# 拷贝应用代码
COPY --chown=appuser:appuser app.py /app/

WORKDIR /app
USER appuser

# 安全：默认端口，可在运行时覆盖
EXPOSE 8080

# 健康检查：Kubernetes 就绪探针兼容
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# 启动命令
CMD ["python", "app.py"]
