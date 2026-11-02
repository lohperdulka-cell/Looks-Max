"""Точка входа FastAPI-приложения FaceMetric.

Этап 1: каркас приложения — конфигурация, логирование, БД, health-check,
централизованный обработчик ошибок и security headers. Роутеры подключаются
по мере реализации этапов.
"""

from __future__ import annotations

import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.config import BASE_DIR, settings
from app.core.limiter import limiter
from app.db import SessionLocal, engine
from app.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Инициализация и корректное завершение приложения."""
    logger.info("app.startup", env=settings.app_env, db=settings.database_url.split("://")[0])
    settings.media_root.mkdir(parents=True, exist_ok=True)
    yield
    await engine.dispose()
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    """Создаёт и конфигурирует экземпляр приложения."""
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Платформа объективной оценки внешности (looks-max).",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    application.state.limiter = limiter
    application.add_exception_handler(
        RateLimitExceeded,
        _rate_limit_exceeded_handler,  # type: ignore[arg-type]
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        """Добавляет security-заголовки к каждому ответу."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' https://cdn.jsdelivr.net "
            "https://cdnjs.cloudflare.com; font-src 'self' data:; connect-src 'self'",
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # exc.errors() может содержать несериализуемые объекты (например, ValueError
        # в ctx), поэтому прогоняем через jsonable_encoder с fallback на str.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Ошибка валидации данных",
                "errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str}),
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Централизованный обработчик: не отдаём внутренности наружу.

        Ошибка дополнительно сохраняется в error_logs — админка показывает
        последние сбои. Запись best-effort: её сбой не должен ломать ответ.
        """
        logger.exception("app.unhandled_error", path=request.url.path, error=str(exc))
        try:
            from app.services.admin import log_error

            async with SessionLocal() as session:
                await log_error(
                    session,
                    "error",
                    str(exc),
                    path=request.url.path,
                    traceback_text=traceback.format_exc(),
                )
        except Exception:
            logger.warning("app.error_log_failed", path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Внутренняя ошибка сервера"},
        )

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        """Health-check: доступность БД и ML-провайдеров."""
        db_ok = True
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            db_ok = False
            logger.error("health.db.failed", error=str(exc))

        ml_status: dict[str, object] = {
            "enabled": settings.ml_enabled,
            "primary": settings.ml_provider_primary,
            "fallback": settings.ml_provider_fallback,
            "demo": settings.ml_provider_demo,
        }
        if settings.ml_enabled:
            try:
                from app.ml.landmarks import _load_insightface, _load_mediapipe

                # Загружаем только реально сконфигурированные провайдеры:
                # на демо-режиме тяжёлые веса не трогаем вовсе.
                if settings.ml_provider_primary in ("insightface", "mediapipe"):
                    ml_status["insightface"] = _load_insightface() is not None
                    ml_status["mediapipe"] = _load_mediapipe() is not None
                else:
                    ml_status["detectors"] = "demo"
            except Exception as exc:
                ml_status["error"] = str(exc)

        # Детектор считается рабочим, если загрузился основной или резервный.
        # Если ни один не поднялся и демо выключено, загрузка фото упадёт —
        # это деградация, а не «ok»: иначе поломка вида «нет libEGL.so.1»
        # проходит незамеченной, пока пользователь не попробует загрузить фото.
        detector_ok = True
        if settings.ml_enabled:
            detector_ok = bool(
                ml_status.get("insightface")
                or ml_status.get("mediapipe")
                or ml_status.get("detectors") == "demo"
                or settings.ml_provider_demo
            )

        healthy = db_ok and detector_ok
        return {
            "status": "ok" if healthy else "degraded",
            "database": db_ok,
            "ml": ml_status,
            "version": "0.1.0",
        }

    from app.api.admin import router as admin_router
    from app.api.auth import router as auth_router
    from app.api.battles import router as battles_router
    from app.api.chat import router as chat_router
    from app.api.content import router as content_router
    from app.api.exercises import router as exercises_router
    from app.api.mogbattles import router as mogbattles_router
    from app.api.pages import router as pages_router
    from app.api.photos import router as photos_router
    from app.api.profile import router as profile_router
    from app.api.reports import public_router as public_reports_router
    from app.api.reports import router as reports_router
    from app.core.deps import csrf_origin_guard

    # Мутирующие роутеры идут с глобальной проверкой Origin: cookie
    # уходит автоматически, поэтому чужой сайт не должен мочь дёрнуть API от
    # имени пользователя. Публичный роутер (только GET) — без проверки.
    guard = [Depends(csrf_origin_guard)]
    application.include_router(auth_router, dependencies=guard)
    application.include_router(photos_router, dependencies=guard)
    application.include_router(reports_router, dependencies=guard)
    application.include_router(profile_router, dependencies=guard)
    application.include_router(battles_router, dependencies=guard)
    application.include_router(mogbattles_router, dependencies=guard)
    application.include_router(exercises_router, dependencies=guard)
    application.include_router(chat_router, dependencies=guard)
    application.include_router(admin_router, dependencies=guard)
    application.include_router(content_router, dependencies=guard)
    application.include_router(public_reports_router)
    application.include_router(pages_router)

    # Статика (CSS/JS) подключается сразу; шаблоны — по мере этапов.
    application.mount(
        "/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static"
    )

    return application


app = create_app()
