"""Pydantic-схемы PvP-моггбатлов (дуэли 1x1)."""

from __future__ import annotations

from pydantic import BaseModel

# Статусы шага для клиента.
STEP_WAITING = "waiting"  # ждём соперника
STEP_OPPONENT_FOUND = "opponent_found"  # соперник есть, он ещё не готов
STEP_REVEALED = "revealed"  # оба готовы, показываем результат


class MogSideOut(BaseModel):
    """Сторона дуэли. Фото соперника не отдаём, пока он не подтвердил готовность."""

    user_id: int
    name: str
    photo_id: int | None = None
    image_url: str | None = None
    ready: bool = False
    blurred: bool = False
    score: float | None = None
    points: int = 0


class MogRoomOut(BaseModel):
    """Состояние комнаты для текущего пользователя."""

    room_id: int
    status: str
    step: str
    is_player_a: bool
    me: MogSideOut
    opponent: MogSideOut | None = None
    winner_id: int | None = None
    is_draw: bool = False
    result_text: str = ""
    expires_at: str


class MogLeaderboardEntryOut(BaseModel):
    """Строка лидерборда моггбатлов."""

    rank: int
    username: str
    display_name: str | None = None
    pvp_points: int


class MogProfileOut(BaseModel):
    """Сводка PvP для профиля/сайдбара."""

    pvp_points: int
    wins: int
    losses: int
    rank: int | None = None
