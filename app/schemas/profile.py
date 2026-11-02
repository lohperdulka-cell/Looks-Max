"""Pydantic-схемы профиля, прогресса и достижений."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileStatsOut(BaseModel):
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
    wins: int = 0
    losses: int = 0


class ProgressPointOut(BaseModel):
    """Точка графика прогресса."""

    day: date
    overall_score: float | None = None
    reports_count: int = 0


class ProgressOut(BaseModel):
    """Ряд прогресса."""

    days: int
    points: list[ProgressPointOut]
    delta: float


class AchievementOut(BaseModel):
    """Достижение (полученное или доступное)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    description: str
    icon: str | None = None
    threshold: int | None = None
    awarded_at: datetime | None = None
    earned: bool = False
    progress: float = 0.0


class ProfileOut(BaseModel):
    """Профиль пользователя с публичной статистикой."""

    username: str
    display_name: str | None = None
    gender: str | None = None
    birth_year: int | None = None
    created_at: datetime
    elo: int
    streak_days: int
    show_in_battles: bool
    stats: ProfileStatsOut


class CompareMetric(BaseModel):
    """Сравнение метрики «до/после»."""

    key: str
    label: str
    before: float
    after: float
    delta: float


class BeforeAfterOut(BaseModel):
    """Сравнение первого и последнего отчёта."""

    before_report_id: int
    after_report_id: int
    before_date: datetime
    after_date: datetime
    before_overall: float
    after_overall: float
    overall_delta: float
    metrics: list[CompareMetric] = Field(default_factory=list)


class LevelOut(BaseModel):
    """Уровень и прогресс до следующего."""

    level: int
    title: str
    xp: int
    xp_into_level: int
    xp_for_next: int
    progress: float


class XpSourceOut(BaseModel):
    """Источник опыта: название, количество действий и начисленный XP."""

    code: str
    label: str
    count: int
    xp: int


class QuestOut(BaseModel):
    """Ежедневное задание с прогрессом."""

    code: str
    title: str
    description: str
    target: int
    done: int
    completed: bool
    xp_reward: int


class ActivityDayOut(BaseModel):
    """Активность за день для карты вклада."""

    day: date
    total: int
    intensity: int
    breakdown: dict[str, int] = Field(default_factory=dict)


class GamificationOut(BaseModel):
    """Сводка геймификации для профиля."""

    level: LevelOut
    total_xp: int
    sources: list[XpSourceOut]
    quests: list[QuestOut]
    quests_done: int
    activity: list[ActivityDayOut]
    active_days: int


class ProfileUpdateRequest(BaseModel):
    """Обновление профиля."""

    display_name: str | None = Field(default=None, max_length=64)
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    birth_year: int | None = Field(default=None, ge=1920, le=2020)
    show_in_battles: bool | None = None
