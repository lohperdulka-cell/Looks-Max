# syntax=docker/dockerfile:1
# FaceMetric — образ для Render (free tier) и локального docker-compose.
#
# Профиль сборки задаётся ARG INSTALL_EXTRAS:
#   core — веб-приложение + numpy/OpenCV (они в ядре). Демо-провайдер считает
#          метрики по синтетической сетке, поэтому конвейер работает целиком.
#   mp   — + MediaPipe и PDF: реальные измерения, ≈240 МБ RSS, PDF-экспорт.
#          Влезает в 512 МБ Render free (замер: 321 МБ пик с PDF). Дефолт.
#   full — + InsightFace. Даёт 106 точек и пол/возраст из модели, но качает
#          280 МБ весов; на free влезает только с обрезкой моделей (274 МБ).
#
# Render free даёт 512 МБ RAM. Замеры пика RSS: MediaPipe — 237 МБ,
# InsightFace с обрезкой до трёх моделей — 274 МБ, полный buffalo_l — 920 МБ
# (OOM-kill), MediaPipe + PDF — 321 МБ. Поэтому на free собирайте mp
# (или core для демо-режима).
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
# libegl1/libgles2 — MediaPipe: без них загрузка падает с
# "libEGL.so.1: cannot open shared object file" и провайдер молча уходит
# в демо-сетку, то есть метрики перестают измерять реальное лицо.
# pango/cairo/gdk-pixbuf/harfbuzz — WeasyPrint (PDF); fonts-dejavu-core —
# кириллица в PDF, иначе текст выйдет пустыми квадратами.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libegl1 \
        libgles2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libharfbuzz0b \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — кэшируем слой.
# mail добавлен в оба профиля: aiosmtplib — чистый Python, RSS не растёт,
# а без него на проде не работает восстановление пароля.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip \
    && case "$INSTALL_EXTRAS" in \
         full) pip install ".[ml,pdf,mail]" ;; \
         mp)   pip install ".[mp,pdf,mail]" ;; \
         *)    pip install ".[mail]" ;; \
       esac

COPY . .

# Модель MediaPipe запекается в образ: data/models исключён через .dockerignore,
# и без этого шага контейнер качает 3.6 МБ при каждом холодном старте Render.
# Если сеть при сборке недоступна, шаг не валит сборку — модель догрузится в
# рантайме (_ensure_mediapipe_task_model), просто первый запрос будет медленнее.
ARG MP_MODEL_URL="https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
RUN mkdir -p data/media data/models \
    && { curl -fsSL --retry 3 --retry-delay 2 -o data/models/face_landmarker.task.tmp "$MP_MODEL_URL" \
         && mv data/models/face_landmarker.task.tmp data/models/face_landmarker.task \
         || echo "WARN: модель MediaPipe не скачалась при сборке — догрузится при старте"; }

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Миграции применяются на старте, затем поднимается сервер.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
