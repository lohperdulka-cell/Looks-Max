"""Точка входа FastAPI-приложения FaceMetric.

Этап 1: каркас приложения — конфигурация, логирование, БД, health-check,
централизованный обработчик ошибок и security headers. Роутеры подключаются
по мере реализации этапов.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, engine
from app.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default] if settings.rate_limit_enabled else [],
    enabled=settings.rate_limit_enabled,
)


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
        allow_origins=[settings.base_url],
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
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Ошибка валидации данных", "errors": exc.errors()},
        )

    @application.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Централизованный обработчик: не отдаём внутренности наружу."""
        logger.exception("app.unhandled_error", path=request.url.path, error=str(exc))
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

        ml_status: dict[str, object] = {"enabled": settings.ml_enabled}
        if settings.ml_enabled:
            try:
                from app.ml.landmarks import _load_insightface, _load_mediapipe

                ml_status["insightface"] = _load_insightface() is not None
                ml_status["mediapipe"] = _load_mediapipe() is not None
            except Exception as exc:
                ml_status["error"] = str(exc)

        healthy = db_ok
        return {
            "status": "ok" if healthy else "degraded",
            "database": db_ok,
            "ml": ml_status,
            "version": "0.1.0",
        }

    # Статика (CSS/JS) подключается сразу; шаблоны — по мере этапов.
    application.mount(
        "/static",
        StaticFiles(directory=str(settings.media_root.parent.parent / "app" / "static")),
        name="static",
    )

    return application


app = create_app()
