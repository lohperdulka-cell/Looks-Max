"""Каталог достижений: коды, условия выдачи и проверка.

Достижения выдаются автоматически при действиях пользователя (загрузка фото,
создание отчёта, выполнение упражнений, победы в батлах). Условие задаётся
предикатом по метрикам активности, а не «ручным» присвоением.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Achievement, UserAchievement
from app.models.user import User


@dataclass(frozen=True, slots=True)
class AchievementSpec:
    """Описание достижения и условие его выдачи."""

    code: str
    title: str
    description: str
    icon: str
    threshold: int
    metric: str  # ключ метрики активности, по которой проверяется условие


# Каталог. metric — имя поля из AchievementStats.
ACHIEVEMENT_CATALOG: tuple[AchievementSpec, ...] = (
    AchievementSpec(
        code="first_photo",
        title="Первый кадр",
        description="Загружено первое фото для оценки.",
        icon="camera",
        threshold=1,
        metric="photos_count",
    ),
    AchievementSpec(
        code="first_report",
        title="Первый отчёт",
        description="Создан первый отчёт об оценке.",
        icon="clipboard",
        threshold=1,
        metric="reports_count",
    ),
    AchievementSpec(
        code="reports_5",
        title="Аналитик",
        description="Создано пять отчётов — видна динамика.",
        icon="chart",
        threshold=5,
        metric="reports_count",
    ),
    AchievementSpec(
        code="reports_20",
        title="Марафонец",
        description="Двадцать отчётов: системная работа над собой.",
        icon="medal",
        threshold=20,
        metric="reports_count",
    ),
    AchievementSpec(
        code="streak_3",
        title="Три дня подряд",
        description="Активность три дня подряд.",
        icon="flame",
        threshold=3,
        metric="streak_days",
    ),
    AchievementSpec(
        code="streak_7",
        title="Неделя дисциплины",
        description="Активность семь дней подряд.",
        icon="flame",
        threshold=7,
        metric="streak_days",
    ),
    AchievementSpec(
        code="streak_30",
        title="Месяц дисциплины",
        description="Активность тридцать дней подряд.",
        icon="flame",
        threshold=30,
        metric="streak_days",
    ),
    AchievementSpec(
        code="exercises_10",
        title="Практик",
        description="Выполнено десять упражнений.",
        icon="dumbbell",
        threshold=10,
        metric="exercise_completions",
    ),
    AchievementSpec(
        code="exercises_50",
        title="Тренер самому себе",
        description="Выполнено пятьдесят упражнений.",
        icon="dumbbell",
        threshold=50,
        metric="exercise_completions",
    ),
    AchievementSpec(
        code="battles_10",
        title="Участник батлов",
        description="Десять голосов в моггбатлах.",
        icon="swords",
        threshold=10,
        metric="battle_votes",
    ),
    AchievementSpec(
        code="score_above_7",
        title="Высокий балл",
        description="Общий балл отчёта достиг 7.0 и выше.",
        icon="star",
        threshold=7,
        metric="best_score",
    ),
    AchievementSpec(
        code="public_share",
        title="Публичный профиль",
        description="Включена публичная ссылка на отчёт.",
        icon="share",
        threshold=1,
        metric="public_reports",
    ),
)

CATALOG_BY_CODE: dict[str, AchievementSpec] = {spec.code: spec for spec in ACHIEVEMENT_CATALOG}


@dataclass(slots=True)
class AchievementStats:
    """Метрики активности пользователя для проверки условий."""

    photos_count: int = 0
    reports_count: int = 0
    streak_days: int = 0
    exercise_completions: int = 0
    battle_votes: int = 0
    best_score: float = 0.0
    public_reports: int = 0


async def sync_catalog(db: AsyncSession) -> None:
    """Синхронизирует справочник достижений в БД с каталогом в коде."""
    existing = {row.code: row for row in (await db.execute(select(Achievement))).scalars().all()}
    changed = False
    for spec in ACHIEVEMENT_CATALOG:
        row = existing.get(spec.code)
        if row is None:
            db.add(
                Achievement(
                    code=spec.code,
                    title=spec.title,
                    description=spec.description,
                    icon=spec.icon,
                    threshold=spec.threshold,
                )
            )
            changed = True
        elif (row.title, row.description, row.threshold) != (
            spec.title,
            spec.description,
            spec.threshold,
        ):
            row.title = spec.title
            row.description = spec.description
            row.threshold = spec.threshold
            row.icon = spec.icon
            changed = True
    if changed:
        await db.commit()


def _satisfied(spec: AchievementSpec, stats: AchievementStats) -> bool:
    value = getattr(stats, spec.metric, 0)
    return float(value) >= float(spec.threshold)


async def award_achievements(
    db: AsyncSession, user: User, stats: AchievementStats
) -> list[Achievement]:
    """Выдаёт новые достижения по текущей статистике. Возвращает список новых."""
    await sync_catalog(db)

    catalog = {row.code: row for row in (await db.execute(select(Achievement))).scalars().all()}
    owned = {
        row.achievement_id
        for row in (
            await db.execute(select(UserAchievement).where(UserAchievement.user_id == user.id))
        )
        .scalars()
        .all()
    }

    awarded: list[Achievement] = []
    now = datetime.now(UTC)
    for spec in ACHIEVEMENT_CATALOG:
        row = catalog.get(spec.code)
        if row is None or row.id in owned:
            continue
        if not _satisfied(spec, stats):
            continue
        db.add(UserAchievement(user_id=user.id, achievement_id=row.id, awarded_at=now))
        awarded.append(row)

    if awarded:
        await db.commit()
    return awarded


async def list_user_achievements(
    db: AsyncSession, user_id: int
) -> list[tuple[Achievement, datetime]]:
    """Достижения пользователя с датами выдачи."""
    rows = (
        await db.execute(
            select(UserAchievement, Achievement)
            .join(Achievement, Achievement.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .order_by(UserAchievement.awarded_at.desc())
        )
    ).all()
    return [(achievement, ua.awarded_at) for ua, achievement in rows]


def catalog_preview() -> list[dict]:
    """Полный каталог достижений (для страницы «что можно получить»)."""
    return [
        {
            "code": spec.code,
            "title": spec.title,
            "description": spec.description,
            "icon": spec.icon,
            "threshold": spec.threshold,
        }
        for spec in ACHIEVEMENT_CATALOG
    ]


def progress_ratio(spec_code: str, stats: AchievementStats) -> float:
    """Доля выполнения условия достижения (0..1)."""
    spec = CATALOG_BY_CODE.get(spec_code)
    if spec is None or spec.threshold <= 0:
        return 0.0
    value = float(getattr(stats, spec.metric, 0))
    return max(0.0, min(1.0, value / float(spec.threshold)))


Predicate = Callable[[AchievementStats], bool]
