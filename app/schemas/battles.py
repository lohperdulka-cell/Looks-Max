"""Pydantic-схемы мобатлов."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BattlePhotoOut(BaseModel):
    """Фото-участник батла."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    image_url: str
    width: int | None = None
    height: int | None = None
    quality_score: float | None = None
    owner: str = ""


class BattleOut(BaseModel):
    """Батл с двумя фото и текущим счётом."""

    id: int
    left: BattlePhotoOut
    right: BattlePhotoOut
    votes_left: int
    votes_right: int
    total_votes: int
    is_active: bool
    winner_photo_id: int | None = None
    season_title: str | None = None
    my_vote_photo_id: int | None = None


class VoteRequest(BaseModel):
    """Голос за одно из фото батла."""

    chosen_photo_id: int


class VoteResultOut(BaseModel):
    """Результат голосования."""

    battle: BattleOut
    finished: bool
    detail: str = ""


class LeaderboardEntryOut(BaseModel):
    """Строка лидерборда."""

    rank: int
    username: str
    display_name: str | None = None
    elo: int
    wins: int
    losses: int


class SeasonOut(BaseModel):
    """Сезон (батл недели)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    starts_at: datetime
    ends_at: datetime
    is_active: bool
