"""Общие фикстуры pytest.

Тесты используют отдельную SQLite-БД в памяти (файл во временной директории,
чтобы async-движок работал предсказуемо между соединениями).
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
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ML_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.db import Base, SessionLocal, engine, init_models  # noqa: E402
from app.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db_session() -> AsyncIterator:
    """Готовит схему и отдаёт сессию, откатывая изменения после теста."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP-клиент поверх ASGI-транспорта (без реального сокета)."""
    await init_models()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
