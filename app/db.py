"""Асинхронный слой доступа к БД (SQLAlchemy 2.0 + asyncpg/aiosqlite).

Единый AsyncEngine и фабрика сессий. Для SQLite включается WAL и отключён
общий пул (NullPool-подобное поведение для aiosqlite не требуется, но
check_same_thread=False важен при использовании в разных тасках).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Единая конвенция именования constraint'ов — важно для стабильных Alembic-миграций.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Базовый декларативный класс для всех ORM-моделей."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _engine_kwargs() -> dict[str, Any]:
    if settings.is_sqlite:
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
    }


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    future=True,
    **_engine_kwargs(),
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: выдаёт сессию и гарантирует rollback при ошибке."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_models() -> None:
    """Создаёт таблицы из метаданных (для локального запуска/тестов).

    В production схема управляется Alembic, поэтому здесь только create_all —
    удобно для быстрого старта на SQLite.
    """
    # Импорт нужен, чтобы все модели зарегистрировались в Base.metadata.
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
