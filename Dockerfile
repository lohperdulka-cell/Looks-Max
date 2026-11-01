# syntax=docker/dockerfile:1
# FaceMetric — образ для Render (free tier) и локального docker-compose.
#
# Профиль сборки задаётся ARG INSTALL_EXTRAS:
#   core — веб-приложение + numpy/OpenCV (они в ядре). Демо-провайдер считает
#          метрики по синтетической сетке, поэтому конвейер работает целиком.
#   mp   — + MediaPipe: реальные измерения, ≈240 МБ RSS. Влезает в 512 МБ
#          Render free. Рекомендуется для деплоя.
#   full — + MediaPipe, InsightFace и PDF (weasyprint). InsightFace качает
#          280 МБ весов и грузит их в память; на free не влезает.
#
# Render free даёт 512 МБ RAM. Замеры пика RSS: MediaPipe — 237 МБ,
# InsightFace с обрезкой до трёх моделей — 274 МБ, полный buffalo_l — 920 МБ
# (OOM-kill). Поэтому на free собирайте mp (или core для демо-режима).
#
# Дефолт — mp: Render Blueprint не умеет передавать build args, поэтому образ
# на Render собирается с этим значением. Локально профиль переопределяется
# через `--build-arg INSTALL_EXTRAS=...` или docker-compose.
FROM python:3.12-slim AS base

ARG INSTALL_EXTRAS=mp

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
    && case "$INSTALL_EXTRAS" in \
         full) pip install ".[ml,pdf]" ;; \
         mp)   pip install ".[mp]" ;; \
         *)    pip install . ;; \
       esac

COPY . .

RUN mkdir -p data/media data/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Миграции применяются на старте, затем поднимается сервер.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
