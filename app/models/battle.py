"""Модели моггбатлов: пары, голоса, сезоны (батл недели), рейтинг ELO."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import TimestampMixin


class Battle(TimestampMixin, Base):
    """Пара фото для голосования. winner_photo_id = None, пока никто не победил."""

    __tablename__ = "battles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    left_photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    right_photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("battle_seasons.id", ondelete="SET NULL"), index=True, default=None
    )

    votes_left: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    votes_right: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    winner_photo_id: Mapped[int | None] = mapped_column(Integer, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    votes: Mapped[list[Vote]] = relationship(
        lazy="raise_on_sql", back_populates="battle", cascade="all, delete-orphan"
    )


class Vote(TimestampMixin, Base):
    """Голос пользователя в батле. Один голос на пользователя на батл."""

    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("battle_id", "voter_id", name="uq_vote_battle_voter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    battle_id: Mapped[int] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    voter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chosen_photo_id: Mapped[int] = mapped_column(Integer, nullable=False)

    battle: Mapped[Battle] = relationship(lazy="raise_on_sql", back_populates="votes")


class BattleSeason(TimestampMixin, Base):
    """Сезон (например, «батл недели»). Турнир по накопленным победам."""

    __tablename__ = "battle_seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
