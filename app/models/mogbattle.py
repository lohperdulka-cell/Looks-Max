"""Модели PvP-моггбатлов: комнаты дуэлей и очередь на подбор соперника.

Отличие от арены (`battle.py`): там голосуют зрители, здесь два участника
сражаются один на один. Комната живёт от создания до раскрытия результата;
пока оба не подтвердили готовность, фото соперника скрыто — иначе смысл дуэли
теряется (можно подобрать ракурс под конкретного соперника).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import TimestampMixin

# Статусы комнаты. waiting — ждём соперника или его готовность; revealed —
# оба готовы, результат посчитан; finished — результат просмотрен и начислены
# очки; cancelled — участник вышел или комната устарела.
ROOM_WAITING = "waiting"
ROOM_REVEALED = "revealed"
ROOM_FINISHED = "finished"
ROOM_CANCELLED = "cancelled"


class MogRoom(TimestampMixin, Base):
    """Комната дуэли 1x1. player_b/photo_b появляются после подбора соперника."""

    __tablename__ = "mog_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ROOM_WAITING, nullable=False, index=True
    )

    player_a_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    player_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    photo_a_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), nullable=False
    )
    photo_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("photos.id", ondelete="SET NULL"), default=None
    )

    # Готовность сторон. Готовность A выставляется сразу при входе в очередь.
    ready_a: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ready_b: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Баллы (0..10) и победитель — заполняются в момент раскрытия.
    score_a: Mapped[float | None] = mapped_column(Float, default=None)
    score_b: Mapped[float | None] = mapped_column(Float, default=None)
    winner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    is_draw: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Начисленные очки PvP (для показа «+18» в интерфейсе).
    points_a: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points_b: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("battle_seasons.id", ondelete="SET NULL"), index=True, default=None
    )
    # Комнату-«висяк» переиспользуем/закрываем по этому времени.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
