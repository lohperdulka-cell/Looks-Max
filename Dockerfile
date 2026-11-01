# syntax=docker/dockerfile:1
# FaceMetric — образ для Render (free tier) и локального docker-compose.
#
# Профиль сборки задаётся ARG INSTALL_EXTRAS:
#   core — веб-приложение + numpy/OpenCV (они в ядре). Демо-провайдер считает
#          метрики по синтетической сетке, поэтому конвейер работает целиком.
#   full — + PDF (weasyprint). Провайдеры InsightFace/MediaPipe подключаются
#          отдельно через extras [ml].
#
# Render free даёт 512 МБ RAM: InsightFace (onnxruntime + buffalo_l) и
# MediaPipe в этот лимит не влезают, поэтому на free собирайте core и
# оставляйте демо-провайдер (ML_PROVIDER_DEMO=true).
FROM python:3.12-slim AS base

ARG INSTALL_EXTRAS=core

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# libpq-dev — asyncpg; libgl/libglib/libgomp — OpenCV/numpy; curl — healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — кэшируем слой.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip \
    && if [ "$INSTALL_EXTRAS" = "full" ]; then pip install ".[ml,pdf]"; else pip install .; fi

COPY . .

RUN mkdir -p data/media data/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Миграции применяются на старте, затем поднимается сервер.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
