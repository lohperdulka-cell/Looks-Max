"""Сервис профиля: статистика, история прогресса, streak, до/после.

Собирает данные из фото, отчётов, упражнений и батлов и готовит их для
страницы профиля: сводка, line-график прогресса, сравнение «до/после»,
серия активности и достижения.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.battle import Battle, Vote
from app.models.exercise import ExerciseCompletion, ProgressSnapshot
from app.models.photo import Photo
from app.models.report import Report
from app.models.user import User
from app.services.achievements import AchievementStats


@dataclass(slots=True)
class ProfileStats:
    """Сводная статистика профиля."""

    photos_count: int
    reports_count: int
    exercises_count: int
    battle_votes: int
    best_score: float
    latest_score: float
    average_score: float
    improvement: float
    streak_days: int
    elo: int
    public_reports: int


@dataclass(slots=True)
class ProgressPoint:
    """Точка графика прогресса."""

    day: date
    overall_score: float | None
    reports_count: int


async def collect_stats(db: AsyncSession, user: User) -> ProfileStats:
    """Считает сводную статистику пользователя."""
    photos_count = (
        await db.execute(select(func.count()).select_from(Photo).where(Photo.user_id == user.id))
    ).scalar_one()
    reports_count = (
        await db.execute(select(func.count()).select_from(Report).where(Report.user_id == user.id))
    ).scalar_one()
    exercises_count = (
        await db.execute(
            select(func.count())
            .select_from(ExerciseCompletion)
            .where(ExerciseCompletion.user_id == user.id)
        )
    ).scalar_one()
    battle_votes = (
        await db.execute(select(func.count()).select_from(Vote).where(Vote.voter_id == user.id))
    ).scalar_one()
    public_reports = (
        await db.execute(
            select(func.count())
            .select_from(Report)
            .where(Report.user_id == user.id, Report.is_public.is_(True))
        )
    ).scalar_one()

    scores = (
        (
            await db.execute(
                select(Report.overall_score)
                .where(Report.user_id == user.id)
                .order_by(Report.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    scores_list = [float(s) for s in scores]

    best_score = max(scores_list) if scores_list else 0.0
    latest_score = scores_list[-1] if scores_list else 0.0
    average_score = sum(scores_list) / len(scores_list) if scores_list else 0.0
    improvement = (scores_list[-1] - scores_list[0]) if len(scores_list) >= 2 else 0.0

    return ProfileStats(
        photos_count=int(photos_count),
        reports_count=int(reports_count),
        exercises_count=int(exercises_count),
        battle_votes=int(battle_votes),
        best_score=round(best_score, 2),
        latest_score=round(latest_score, 2),
        average_score=round(average_score, 2),
        improvement=round(improvement, 2),
        streak_days=user.streak_days,
        elo=user.elo,
        public_reports=int(public_reports),
    )


def stats_to_achievement_stats(stats: ProfileStats) -> AchievementStats:
    """Переводит статистику профиля в метрики для проверки достижений."""
    return AchievementStats(
        photos_count=stats.photos_count,
        reports_count=stats.reports_count,
        streak_days=stats.streak_days,
        exercise_completions=stats.exercises_count,
        battle_votes=stats.battle_votes,
        best_score=stats.best_score,
        public_reports=stats.public_reports,
    )


async def progress_series(db: AsyncSession, user_id: int, days: int = 30) -> list[ProgressPoint]:
    """Ряд прогресса по дням: средний балл и число отчётов.

    Дни без отчётов возвращаются с overall_score=None — на графике это разрыв,
    а не ноль (ноль означал бы «оценка упала»).
    """
    days = max(7, min(days, 365))
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(Report.created_at, Report.overall_score)
            .where(Report.user_id == user_id, Report.created_at >= since)
            .order_by(Report.created_at.asc())
        )
    ).all()

    by_day: dict[date, list[float]] = {}
    for created_at, score in rows:
        day = created_at.date()
        by_day.setdefault(day, []).append(float(score))

    today = datetime.now(UTC).date()
    series: list[ProgressPoint] = []
    for offset in range(days):
        day = today - timedelta(days=days - 1 - offset)
        scores = by_day.get(day, [])
        series.append(
            ProgressPoint(
                day=day,
                overall_score=round(sum(scores) / len(scores), 2) if scores else None,
                reports_count=len(scores),
            )
        )
    return series


async def before_after(db: AsyncSession, user_id: int) -> tuple[Report | None, Report | None]:
    """Первый и последний отчёт пользователя (для сравнения «до/после»)."""
    first = (
        await db.execute(
            select(Report)
            .where(Report.user_id == user_id)
            .order_by(Report.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    last = (
        await db.execute(
            select(Report)
            .where(Report.user_id == user_id)
            .order_by(Report.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return first, last


def compute_streak(last_activity: datetime | None, current_streak: int, now: datetime) -> int:
    """Пересчитывает серию активности (streak).

    Правила: активность сегодня или вчера продолжает серию, пропуск дня
    (более одного календарного дня) сбрасывает её в 1.
    """
    if last_activity is None:
        return 1
    last_day = last_activity.date()
    today = now.date()
    delta = (today - last_day).days
    if delta <= 0:
        return max(1, current_streak)
    if delta == 1:
        return max(1, current_streak) + 1
    return 1


async def touch_activity(db: AsyncSession, user: User) -> int:
    """Отмечает активность пользователя и обновляет streak. Возвращает новый streak."""
    now = datetime.now(UTC)
    new_streak = compute_streak(user.last_activity_date, user.streak_days, now)
    user.streak_days = new_streak
    user.last_activity_date = now
    await db.commit()
    return new_streak


async def upsert_snapshot(db: AsyncSession, user: User, stats: ProfileStats) -> None:
    """Обновляет дневной снимок прогресса (для быстрых графиков)."""
    today = datetime.now(UTC).date()
    snapshot = (
        await db.execute(
            select(ProgressSnapshot).where(
                ProgressSnapshot.user_id == user.id, ProgressSnapshot.day == today
            )
        )
    ).scalar_one_or_none()

    if snapshot is None:
        snapshot = ProgressSnapshot(user_id=user.id, day=today)
        db.add(snapshot)

    snapshot.overall_score = stats.latest_score
    snapshot.reports_count = stats.reports_count
    snapshot.battles_count = stats.battle_votes
    snapshot.elo = stats.elo
    await db.commit()


async def battle_record(db: AsyncSession, user_id: int) -> tuple[int, int]:
    """Счёт побед/поражений в батлах по фото пользователя."""
    wins = (
        await db.execute(
            select(func.count())
            .select_from(Battle)
            .join(Photo, Photo.id == Battle.winner_photo_id)
            .where(Photo.user_id == user_id)
        )
    ).scalar_one()
    total = (
        await db.execute(
            select(func.count())
            .select_from(Battle)
            .join(Photo, Photo.id.in_([Battle.left_photo_id, Battle.right_photo_id]))
            .where(Photo.user_id == user_id, Battle.winner_photo_id.is_not(None))
        )
    ).scalar_one()
    losses = max(0, int(total) - int(wins))
    return int(wins), losses
