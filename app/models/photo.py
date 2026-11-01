"""Модели фото и landmarks (точек лица)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.report import Report
    from app.models.user import User


class Photo(TimestampMixin, Base):
    """Загруженное фото пользователя. Файл хранится на диске, в БД — путь и метаданные."""

    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)

    # Результат preprocessing + landmarks (нормализованные координаты 0..1).
    landmarks: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    landmarks_provider: Mapped[str | None] = mapped_column(String(32), default=None)
    is_aligned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, default=None)

    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_public_in_battles: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="photos")
    reports: Mapped[list[Report]] = relationship(back_populates="photo")
