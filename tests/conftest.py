"""Общие фикстуры pytest.

Каждый тест получает чистую схему БД: таблицы пересоздаются перед тестом,
что исключает протечку состояния между тестами.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Настройки должны быть выставлены ДО импорта app.*, т.к. settings кэшируется.
_TMP = Path(tempfile.mkdtemp(prefix="facemetric-test-"))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-0123456789abcdef")
os.environ.setdefault("ML_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402


@pytest.fixture(autouse=True)
async def _clean_schema() -> AsyncIterator[None]:
    """Пересоздаёт схему перед каждым тестом (изоляция состояния)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
async def db_session() -> AsyncIterator:
    """Сессия к той же БД, что использует приложение."""
    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP-клиент поверх ASGI-транспорта (без реального сокета)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
