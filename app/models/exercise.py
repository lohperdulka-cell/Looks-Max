"""Модели упражнений, прогресса выполнения и достижений."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import TimestampMixin


class Exercise(TimestampMixin, Base):
    """Упражнение из библиотеки (мимика, осанка, уход, jawline)."""

    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, default=None)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    difficulty: Mapped[str] = mapped_column(String(16), default="easy", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ExerciseCompletion(TimestampMixin, Base):
    """Факт выполнения упражнения пользователем (для streak и прогресс-бара)."""

    __tablename__ = "exercise_completions"
    __table_args__ = (
        UniqueConstraint("user_id", "exercise_id", "completed_on", name="uq_completion_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="CASCADE"), index=True, nullable=False
    )
    completed_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, default=None)


class Achievement(TimestampMixin, Base):
    """Справочник достижений (бейджей)."""

    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64), default=None)
    threshold: Mapped[int | None] = mapped_column(Integer, default=None)


class UserAchievement(Base):
    """Выданное пользователю достижение."""

    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    achievement_id: Mapped[int] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE"), index=True, nullable=False
    )
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProgressSnapshot(TimestampMixin, Base):
    """Снимок прогресса пользователя по дням (для line-графика)."""

    __tablename__ = "progress_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    overall_score: Mapped[float | None] = mapped_column(Float, default=None)
    reports_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    battles_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    elo: Mapped[int | None] = mapped_column(Integer, default=None)
