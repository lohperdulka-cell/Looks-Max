"""Точка входа FastAPI-приложения FaceMetric.

Этап 1: каркас приложения — конфигурация, логирование, БД, health-check,
централизованный обработчик ошибок и security headers. Роутеры подключаются
по мере реализации этапов.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
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

    from app.api.auth import router as auth_router
    from app.api.pages import router as pages_router

    application.include_router(auth_router)
    application.include_router(pages_router)

    # Статика (CSS/JS) подключается сразу; шаблоны — по мере этапов.
    application.mount(
        "/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static"
    )

    return application


app = create_app()
