"""Модель пользователя и связанных сущностей аутентификации."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.photo import Photo
    from app.models.report import Report


class User(TimestampMixin, Base):
    """Пользователь платформы. Вход только по логину и паролю."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, default=None)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Явное согласие на обработку биометрии (GDPR-подобное требование).
    biometric_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Профиль
    display_name: Mapped[str | None] = mapped_column(String(64), default=None)
    avatar_path: Mapped[str | None] = mapped_column(String(255), default=None)
    gender: Mapped[str | None] = mapped_column(String(16), default=None)  # male | female | other
    birth_year: Mapped[int | None] = mapped_column(Integer, default=None)

    # Геймификация
    elo: Mapped[int] = mapped_column(Integer, default=1200, nullable=False)
    streak_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_activity_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    show_in_battles: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    photos: Mapped[list[Photo]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reports: Mapped[list[Report]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """Refresh-токены: храним хэш, чтобы отзывать сессии (logout, смена пароля)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(64), default=None)


class PasswordResetToken(Base):
    """Токены восстановления пароля. Хранится только хеш токена."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
