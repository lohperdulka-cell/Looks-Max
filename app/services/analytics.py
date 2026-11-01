"""Аналитика просмотров: запись визитов и агрегаты для админки.

Данные обезличены: путь, тип страницы, необязательный id пользователя и хост
referer. Ни IP, ни user-agent не сохраняются.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import PageView

MAX_PATH_LENGTH = 255
MAX_HOST_LENGTH = 128


def classify(path: str) -> str:
    """Определяет тип страницы по пути (для группировки в отчёте)."""
    if path.startswith("/blog/"):
        return "article"
    if path.startswith("/r/"):
        return "public_report"
    if path in {"/", ""}:
        return "landing"
    return "page"


def normalize_path(path: str) -> str:
    """Оставляет только путь без query-строки, обрезая до длины колонки."""
    clean = urlparse(path).path or "/"
    return clean[:MAX_PATH_LENGTH]


def referer_host(referer: str | None) -> str | None:
    """Извлекает хост из referer, отбрасывая свои переходы."""
    if not referer:
        return None
    host = urlparse(referer).hostname or ""
    return host[:MAX_HOST_LENGTH] or None


async def record_view(
    db: AsyncSession, path: str, user_id: int | None = None, referer: str | None = None
) -> PageView:
    """Сохраняет просмотр страницы."""
    view = PageView(
        path=normalize_path(path),
        kind=classify(path),
        user_id=user_id,
        referer_host=referer_host(referer),
        created_at=datetime.now(UTC),
    )
    db.add(view)
    await db.commit()
    await db.refresh(view)
    return view


async def views_total(db: AsyncSession, days: int = 30) -> int:
    """Число просмотров за период."""
    since = datetime.now(UTC) - timedelta(days=days)
    return int(
        (
            await db.execute(
                select(func.count()).select_from(PageView).where(PageView.created_at >= since)
            )
        ).scalar_one()
    )


async def top_pages(db: AsyncSession, days: int = 30, limit: int = 20) -> list[dict]:
    """Самые просматриваемые пути за период."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(PageView.path, func.count().label("views"))
            .where(PageView.created_at >= since)
            .group_by(PageView.path)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [{"path": path, "views": int(views)} for path, views in rows]


async def views_by_kind(db: AsyncSession, days: int = 30) -> list[dict]:
    """Разбивка просмотров по типу страницы."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(PageView.kind, func.count().label("views"))
            .where(PageView.created_at >= since)
            .group_by(PageView.kind)
            .order_by(func.count().desc())
        )
    ).all()
    return [{"kind": kind, "views": int(views)} for kind, views in rows]


async def top_referers(db: AsyncSession, days: int = 30, limit: int = 10) -> list[dict]:
    """Внешние источники переходов."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(PageView.referer_host, func.count().label("views"))
            .where(PageView.created_at >= since, PageView.referer_host.is_not(None))
            .group_by(PageView.referer_host)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [{"host": host, "views": int(views)} for host, views in rows]
