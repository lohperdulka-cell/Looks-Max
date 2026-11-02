"""Модели отчёта об оценке и значений отдельных метрик."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.photo import Photo
    from app.models.user import User


class Report(TimestampMixin, Base):
    """Отчёт об оценке: агрегат + разбивка по метрикам."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), index=True, nullable=False
    )

    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    harmony_score: Mapped[float | None] = mapped_column(Float, default=None)
    categories: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    recommendations: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    model_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)

    # Публичная анонимная ссылка (opt-in).
    public_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(lazy="raise_on_sql", back_populates="reports")
    photo: Mapped[Photo] = relationship(lazy="raise_on_sql", back_populates="reports")
    metrics: Mapped[list[MetricValue]] = relationship(
        lazy="raise_on_sql", back_populates="report", cascade="all, delete-orphan"
    )


class MetricValue(Base):
    """Значение конкретной метрики в рамках отчёта (0..10 с отклонением от эталона)."""

    __tablename__ = "metric_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), index=True, nullable=False
    )

    key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)

    raw_value: Mapped[float | None] = mapped_column(Float, default=None)
    reference_value: Mapped[float | None] = mapped_column(Float, default=None)
    deviation: Mapped[float | None] = mapped_column(Float, default=None)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    report: Mapped[Report] = relationship(lazy="raise_on_sql", back_populates="metrics")
