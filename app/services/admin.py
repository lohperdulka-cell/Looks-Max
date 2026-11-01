"""Сервис админки: дашборд, модерация, веса метрик, логи, фидбек, контент.

Все действия администратора пишутся в AdminLog. Чтение весов метрик
кэшируется в процессе: при изменении из админки кэш сбрасывается.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.battle import Battle, Vote
from app.models.content import AdminLog, Article, ErrorLog, FaqItem, Feedback, MetricWeight
from app.models.exercise import ExerciseCompletion
from app.models.photo import Photo
from app.models.report import Report
from app.models.user import User

logger = get_logger(__name__)


@dataclass(slots=True)
class DashboardStats:
    """Сводка для главной страницы админки."""

    users_total: int
    users_new_7d: int
    users_banned: int
    photos_total: int
    reports_total: int
    reports_7d: int
    battles_total: int
    votes_total: int
    exercises_done: int
    feedback_unread: int
    errors_24h: int


async def _count(db: AsyncSession, model, *conditions) -> int:
    """COUNT(*) по модели с условиями."""
    stmt = select(func.count()).select_from(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    return int((await db.execute(stmt)).scalar_one())


async def dashboard(db: AsyncSession) -> DashboardStats:
    """Собирает сводную статистику платформы."""
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)

    return DashboardStats(
        users_total=await _count(db, User),
        users_new_7d=await _count(db, User, User.created_at >= week_ago),
        users_banned=await _count(db, User, User.is_banned.is_(True)),
        photos_total=await _count(db, Photo),
        reports_total=await _count(db, Report),
        reports_7d=await _count(db, Report, Report.created_at >= week_ago),
        battles_total=await _count(db, Battle),
        votes_total=await _count(db, Vote),
        exercises_done=await _count(db, ExerciseCompletion),
        feedback_unread=await _count(db, Feedback, Feedback.is_processed.is_(False)),
        errors_24h=await _count(db, ErrorLog, ErrorLog.created_at >= day_ago),
    )


async def daily_activity(db: AsyncSession, days: int = 14) -> list[dict]:
    """Регистрации и отчёты по дням за период."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days - 1)

    user_rows = (
        await db.execute(
            select(func.date(User.created_at), func.count())
            .where(User.created_at >= since)
            .group_by(func.date(User.created_at))
        )
    ).all()
    report_rows = (
        await db.execute(
            select(func.date(Report.created_at), func.count())
            .where(Report.created_at >= since)
            .group_by(func.date(Report.created_at))
        )
    ).all()

    users_by_day = {str(day): int(count) for day, count in user_rows}
    reports_by_day = {str(day): int(count) for day, count in report_rows}

    result: list[dict] = []
    for offset in range(days):
        day = (now - timedelta(days=days - 1 - offset)).date()
        key = day.isoformat()
        result.append(
            {
                "day": key,
                "users": users_by_day.get(key, 0),
                "reports": reports_by_day.get(key, 0),
            }
        )
    return result


async def list_users(
    db: AsyncSession, query: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[User], int]:
    """Список пользователей с поиском по логину/email."""
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)
    if query:
        pattern = f"%{query.strip().lower()}%"
        condition = func.lower(User.username).like(pattern) | func.lower(
            func.coalesce(User.email, "")
        ).like(pattern)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int((await db.execute(count_stmt)).scalar_one())
    users = (
        (await db.execute(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(users), total


async def log_action(
    db: AsyncSession,
    admin: User,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    details: dict | None = None,
) -> AdminLog:
    """Пишет действие администратора в журнал."""
    entry = AdminLog(
        admin_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    logger.info("admin.action", admin=admin.username, action=action, target_type=target_type)
    return entry


async def recent_admin_logs(db: AsyncSession, limit: int = 100) -> list[AdminLog]:
    """Последние действия администраторов."""
    return list(
        (await db.execute(select(AdminLog).order_by(AdminLog.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )


async def recent_errors(db: AsyncSession, limit: int = 100) -> list[ErrorLog]:
    """Последние ошибки приложения."""
    return list(
        (await db.execute(select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )


async def log_error(
    db: AsyncSession,
    level: str,
    message: str,
    path: str | None = None,
    traceback_text: str | None = None,
) -> ErrorLog:
    """Сохраняет ошибку для отображения в админке (обрезая длинный traceback)."""
    entry = ErrorLog(
        level=level,
        path=path,
        message=message[:4000],
        traceback=(traceback_text or "")[:8000] or None,
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_feedback(
    db: AsyncSession, only_unread: bool = False, limit: int = 100
) -> list[Feedback]:
    """Сообщения обратной связи."""
    stmt = select(Feedback)
    if only_unread:
        stmt = stmt.where(Feedback.is_processed.is_(False))
    return list(
        (await db.execute(stmt.order_by(Feedback.created_at.desc()).limit(limit))).scalars().all()
    )


async def mark_feedback_processed(db: AsyncSession, feedback: Feedback) -> Feedback:
    """Помечает обращение обработанным."""
    feedback.is_processed = True
    feedback.processed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(feedback)
    return feedback


async def list_metric_weights(db: AsyncSession) -> list[MetricWeight]:
    """Переопределённые веса метрик."""
    return list((await db.execute(select(MetricWeight).order_by(MetricWeight.key))).scalars().all())


async def upsert_metric_weight(
    db: AsyncSession,
    key: str,
    weight: float,
    reference_value: float | None = None,
    is_enabled: bool = True,
    note: str | None = None,
) -> MetricWeight:
    """Создаёт или обновляет переопределение веса метрики."""
    existing = (
        await db.execute(select(MetricWeight).where(MetricWeight.key == key))
    ).scalar_one_or_none()
    if existing is None:
        existing = MetricWeight(key=key, weight=weight)
        db.add(existing)

    existing.weight = weight
    existing.reference_value = reference_value
    existing.is_enabled = is_enabled
    existing.note = note
    await db.commit()
    await db.refresh(existing)
    return existing


async def delete_metric_weight(db: AsyncSession, key: str) -> bool:
    """Удаляет переопределение (метрика возвращается к значению из кода)."""
    existing = (
        await db.execute(select(MetricWeight).where(MetricWeight.key == key))
    ).scalar_one_or_none()
    if existing is None:
        return False
    await db.delete(existing)
    await db.commit()
    return True


async def active_weight_overrides(db: AsyncSession) -> dict[str, float]:
    """Активные переопределения весов в виде {key: weight}."""
    rows = (
        (await db.execute(select(MetricWeight).where(MetricWeight.is_enabled.is_(True))))
        .scalars()
        .all()
    )
    return {row.key: row.weight for row in rows}


async def list_articles(db: AsyncSession, limit: int = 200) -> list[Article]:
    """Статьи блога (включая черновики)."""
    return list(
        (await db.execute(select(Article).order_by(Article.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )


async def list_faq(db: AsyncSession) -> list[FaqItem]:
    """Элементы FAQ по порядку сортировки."""
    return list(
        (await db.execute(select(FaqItem).order_by(FaqItem.sort_order, FaqItem.id))).scalars().all()
    )
