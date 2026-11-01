"""Pydantic-схемы упражнений."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class ExerciseOut(BaseModel):
    """Упражнение из библиотеки."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    category: str
    category_label: str
    description: str
    instructions: str | None = None
    duration_seconds: int | None = None
    difficulty: str
    targets: list[str] = Field(default_factory=list)
    completed_today: bool = False
    completions_count: int = 0


class ExerciseListOut(BaseModel):
    """Список упражнений с агрегатами прогресса."""

    items: list[ExerciseOut]
    total: int
    streak: int
    total_completions: int
    by_category: dict[str, int] = Field(default_factory=dict)


class CompleteRequest(BaseModel):
    """Отметка выполнения упражнения."""

    note: str | None = Field(default=None, max_length=500)


class CompletionOut(BaseModel):
    """Результат отметки выполнения."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    exercise_id: int
    completed_on: date
    note: str | None = None
    created: bool = False
    streak: int = 0
    total_completions: int = 0


class ProgressDayOut(BaseModel):
    """День в календаре прогресса."""

    day: date
    completed: int


class ExerciseProgressOut(BaseModel):
    """Прогресс упражнений по дням."""

    days: int
    streak: int
    total_completions: int
    calendar: list[ProgressDayOut]
    by_category: dict[str, int] = Field(default_factory=dict)


class RecommendationExercisesOut(BaseModel):
    """Подборка упражнений под зоны роста последнего отчёта."""

    report_id: int | None = None
    growth_keys: list[str] = Field(default_factory=list)
    items: list[ExerciseOut] = Field(default_factory=list)
