"""Геймификация профиля: уровни, опыт, ежедневные задания, карта активности.

Опыт (XP) выводится из реальных действий, а не хранится счётчиком: так он не
разъезжается с фактами (удалили отчёт — XP пересчитался) и не требует миграции
при каждом изменении правил. Формулы уровней — чистые функции, их проверяют
тесты без БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.battle import Vote
from app.models.exercise import ExerciseCompletion
from app.models.mogbattle import ROOM_FINISHED, ROOM_REVEALED, MogRoom
from app.models.photo import Photo
from app.models.report import Report
from app.models.user import User

# Опыт за действия.
XP_PHOTO = 10
XP_REPORT = 25
XP_EXERCISE = 15
XP_VOTE = 5
XP_MOG_WIN = 40
XP_MOG_LOSS = 10
XP_MOG_DRAW = 20
XP_STREAK_DAY = 2

# Кривая уровней: шаг растёт линейно, чтобы первые уровни брались быстро,
# а дальние требовали реальной работы (без «бесконечного» гринда).
LEVEL_BASE_STEP = 100
LEVEL_STEP_GROWTH = 25


def xp_for_level(level: int) -> int:
    """Суммарный XP, необходимый чтобы ДОСТИЧЬ уровня (уровень 1 — это 0)."""
    if level <= 1:
        return 0
    n = level - 1
    return LEVEL_BASE_STEP * n + LEVEL_STEP_GROWTH * n * (n - 1)


def level_from_xp(xp: int) -> int:
    """Уровень по накопленному XP."""
    xp = max(0, int(xp))
    level = 1
    while xp_for_level(level + 1) <= xp:
        level += 1
    return level


# Звания по уровню, от старшего к младшему.
RANK_TITLES: tuple[tuple[int, str], ...] = (
    (12, "Мастер"),
    (8, "Эксперт"),
    (5, "Продвинутый"),
    (3, "Практик"),
    (1, "Новичок"),
)


def rank_title(level: int) -> str:
    """Название звания для уровня."""
    for threshold, title in RANK_TITLES:
        if level >= threshold:
            return title
    return "Новичок"


@dataclass(slots=True)
class LevelInfo:
    """Уровень пользователя и прогресс до следующего."""

    level: int
    xp: int
    xp_into_level: int
    xp_for_next: int
    progress: float
    title: str


def level_info(xp: int) -> LevelInfo:
    """Считает уровень, звание и прогресс до следующего уровня."""
    xp = max(0, int(xp))
    level = level_from_xp(xp)
    floor_xp = xp_for_level(level)
    next_xp = xp_for_level(level + 1)
    span = next_xp - floor_xp
    into = xp - floor_xp
    return LevelInfo(
        level=level,
        xp=xp,
        xp_into_level=into,
        xp_for_next=max(0, span - into),
        progress=round(into / span, 4) if span > 0 else 0.0,
        title=rank_title(level),
    )


@dataclass(slots=True)
class XpBreakdown:
    """Разложение опыта по источникам (для объяснения «откуда XP»)."""

    photos: int = 0
    reports: int = 0
    exercises: int = 0
    votes: int = 0
    mog_wins: int = 0
    mog_losses: int = 0
    mog_draws: int = 0
    streak_days: int = 0

    @property
    def total(self) -> int:
        return (
            self.photos * XP_PHOTO
            + self.reports * XP_REPORT
            + self.exercises * XP_EXERCISE
            + self.votes * XP_VOTE
            + self.mog_wins * XP_MOG_WIN
            + self.mog_losses * XP_MOG_LOSS
            + self.mog_draws * XP_MOG_DRAW
            + self.streak_days * XP_STREAK_DAY
        )


@dataclass(slots=True)
class ActivityDay:
    """Активность за один день (для карты вклада)."""

    day: date
    photos: int = 0
    reports: int = 0
    exercises: int = 0
    votes: int = 0
    mog: int = 0

    @property
    def total(self) -> int:
        return self.photos + self.reports + self.exercises + self.votes + self.mog

    @property
    def intensity(self) -> int:
        """Ступень заливки 0..4 — для heatmap без вычислений на клиенте."""
        total = self.total
        if total <= 0:
            return 0
        if total <= 2:
            return 1
        if total <= 5:
            return 2
        if total <= 9:
            return 3
        return 4


@dataclass(frozen=True, slots=True)
class QuestSpec:
    """Ежедневное задание: цель по метрике за сегодня."""

    code: str
    title: str
    description: str
    metric: str
    target: int
    xp_reward: int


DAILY_QUESTS: tuple[QuestSpec, ...] = (
    QuestSpec(
        "photo_today", "Новый кадр", "Загрузите фото для оценки", "photos_today", 1, XP_PHOTO
    ),
    QuestSpec("report_today", "Свежий отчёт", "Создайте отчёт", "reports_today", 1, XP_REPORT),
    QuestSpec(
        "exercises_today",
        "Разминка",
        "Выполните 2 упражнения",
        "exercises_today",
        2,
        2 * XP_EXERCISE,
    ),
    QuestSpec(
        "votes_today", "Судья", "Проголосуйте 3 раза на арене", "votes_today", 3, 3 * XP_VOTE
    ),
    QuestSpec("mog_today", "Дуэлянт", "Сыграйте моггбатл", "mog_today", 1, XP_MOG_WIN),
)


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def collect_breakdown(db: AsyncSession, user: User) -> XpBreakdown:
    """Считает разложение XP по источникам."""
    photos = (
        await db.execute(select(func.count()).select_from(Photo).where(Photo.user_id == user.id))
    ).scalar_one()
    reports = (
        await db.execute(select(func.count()).select_from(Report).where(Report.user_id == user.id))
    ).scalar_one()
    exercises = (
        await db.execute(
            select(func.count())
            .select_from(ExerciseCompletion)
            .where(ExerciseCompletion.user_id == user.id)
        )
    ).scalar_one()
    votes = (
        await db.execute(select(func.count()).select_from(Vote).where(Vote.voter_id == user.id))
    ).scalar_one()

    rooms = (
        (
            await db.execute(
                select(MogRoom).where(
                    MogRoom.status.in_([ROOM_REVEALED, ROOM_FINISHED]),
                    (MogRoom.player_a_id == user.id) | (MogRoom.player_b_id == user.id),
                )
            )
        )
        .scalars()
        .all()
    )
    wins = sum(1 for r in rooms if r.winner_id == user.id)
    draws = sum(1 for r in rooms if r.is_draw)
    losses = sum(1 for r in rooms if r.winner_id is not None and r.winner_id != user.id)

    return XpBreakdown(
        photos=int(photos),
        reports=int(reports),
        exercises=int(exercises),
        votes=int(votes),
        mog_wins=wins,
        mog_losses=losses,
        mog_draws=draws,
        streak_days=max(0, user.streak_days - 1),  # первый день серии бонуса не даёт
    )


async def _count_since(db: AsyncSession, column, model, user_field, user_id: int, since) -> int:
    """Число строк модели с датой >= since (общий счётчик для heatmap и заданий)."""
    value = (
        await db.execute(
            select(func.count()).select_from(model).where(user_field == user_id, column >= since)
        )
    ).scalar_one()
    return int(value)


async def activity_series(db: AsyncSession, user: User, days: int = 30) -> list[ActivityDay]:
    """Активность по дням за период (для карты вклада)."""
    days = max(7, min(days, 365))
    now = datetime.now(UTC)
    since = _day_start(now) - timedelta(days=days - 1)
    today = now.date()

    async def per_day(model, column, user_field, user_id: int) -> dict[date, int]:
        # Группировать по колонке-времени нельзя: у каждой строки своя секунда,
        # поэтому «группа» давала бы по одной строке на запись и счётчик за день
        # схлопывался бы до единицы. Считаем по календарной дате в Python.
        stamps = (
            (await db.execute(select(column).where(user_field == user_id, column >= since)))
            .scalars()
            .all()
        )
        counts: dict[date, int] = {}
        for stamp in stamps:
            day = stamp.date()
            counts[day] = counts.get(day, 0) + 1
        return counts

    photos = await per_day(Photo, Photo.created_at, Photo.user_id, user.id)
    reports = await per_day(Report, Report.created_at, Report.user_id, user.id)
    exercises = await per_day(
        ExerciseCompletion, ExerciseCompletion.created_at, ExerciseCompletion.user_id, user.id
    )
    votes = await per_day(Vote, Vote.created_at, Vote.voter_id, user.id)

    mog_rows = (
        (
            await db.execute(
                select(MogRoom.updated_at).where(
                    MogRoom.status.in_([ROOM_REVEALED, ROOM_FINISHED]),
                    (MogRoom.player_a_id == user.id) | (MogRoom.player_b_id == user.id),
                    MogRoom.updated_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    mog: dict[date, int] = {}
    for ts in mog_rows:
        mog[ts.date()] = mog.get(ts.date(), 0) + 1

    series: list[ActivityDay] = []
    for offset in range(days):
        day = today - timedelta(days=days - 1 - offset)
        series.append(
            ActivityDay(
                day=day,
                photos=photos.get(day, 0),
                reports=reports.get(day, 0),
                exercises=exercises.get(day, 0),
                votes=votes.get(day, 0),
                mog=mog.get(day, 0),
            )
        )
    return series


async def today_counts(db: AsyncSession, user: User) -> dict[str, int]:
    """Счётчики активности за сегодня — основа прогресса ежедневных заданий."""
    since = _day_start(datetime.now(UTC))
    mog = (
        await db.execute(
            select(func.count())
            .select_from(MogRoom)
            .where(
                MogRoom.status.in_([ROOM_REVEALED, ROOM_FINISHED]),
                (MogRoom.player_a_id == user.id) | (MogRoom.player_b_id == user.id),
                MogRoom.updated_at >= since,
            )
        )
    ).scalar_one()
    return {
        "photos_today": await _count_since(
            db, Photo.created_at, Photo, Photo.user_id, user.id, since
        ),
        "reports_today": await _count_since(
            db, Report.created_at, Report, Report.user_id, user.id, since
        ),
        "exercises_today": await _count_since(
            db,
            ExerciseCompletion.created_at,
            ExerciseCompletion,
            ExerciseCompletion.user_id,
            user.id,
            since,
        ),
        "votes_today": await _count_since(db, Vote.created_at, Vote, Vote.voter_id, user.id, since),
        "mog_today": int(mog),
    }
