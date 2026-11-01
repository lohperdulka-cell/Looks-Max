"""Роутер мобатлов: подбор пары, голосование, лидерборд, батл недели."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.battle import Battle, Vote
from app.models.photo import Photo
from app.models.user import User
from app.schemas.battles import (
    BattleOut,
    BattlePhotoOut,
    LeaderboardEntryOut,
    SeasonOut,
    VoteRequest,
    VoteResultOut,
)
from app.services import battles as battles_service
from app.services.battles import BattleError

router = APIRouter(prefix="/api/battles", tags=["battles"])


def _photo_out(photo: Photo, owner: User | None) -> BattlePhotoOut:
    return BattlePhotoOut(
        id=photo.id,
        image_url=f"/api/photos/{photo.id}/image",
        width=photo.width,
        height=photo.height,
        quality_score=photo.quality_score,
        owner=(owner.display_name or owner.username) if owner else "",
    )


async def _serialize_battle(db: DbSession, battle: Battle, voter_id: int) -> BattleOut:
    left = await db.get(Photo, battle.left_photo_id)
    right = await db.get(Photo, battle.right_photo_id)
    if left is None or right is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фото батла недоступны")

    left_owner = await db.get(User, left.user_id)
    right_owner = await db.get(User, right.user_id)

    season_title = None
    if battle.season_id:
        from app.models.battle import BattleSeason

        season = await db.get(BattleSeason, battle.season_id)
        season_title = season.title if season else None

    my_vote = (
        await db.execute(
            select(Vote.chosen_photo_id).where(
                Vote.battle_id == battle.id, Vote.voter_id == voter_id
            )
        )
    ).scalar_one_or_none()

    return BattleOut(
        id=battle.id,
        left=_photo_out(left, left_owner),
        right=_photo_out(right, right_owner),
        votes_left=battle.votes_left,
        votes_right=battle.votes_right,
        total_votes=battle.votes_left + battle.votes_right,
        is_active=battle.is_active,
        winner_photo_id=battle.winner_photo_id,
        season_title=season_title,
        my_vote_photo_id=my_vote,
    )


@router.get("/next", response_model=BattleOut)
@limiter.limit("60/minute")
async def next_battle(request: Request, user: CurrentUser, db: DbSession) -> BattleOut:
    """Подбирает (или возвращает активный) батл для одного из публичных фото."""
    photo = (
        await db.execute(
            select(Photo)
            .where(
                Photo.user_id == user.id,
                Photo.is_public_in_battles.is_(True),
                Photo.is_hidden.is_(False),
            )
            .order_by(Photo.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нет фото, участвующих в батлах. Отметьте фото как публичное.",
        )

    try:
        battle, _, _ = await battles_service.create_or_get_battle(db, photo, user)
    except BattleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await _serialize_battle(db, battle, user.id)


@router.post("/{battle_id}/vote", response_model=VoteResultOut)
@limiter.limit("60/minute")
async def vote(
    request: Request, battle_id: int, payload: VoteRequest, user: CurrentUser, db: DbSession
) -> VoteResultOut:
    """Голосует в батле и при необходимости завершает его и пересчитывает ELO."""
    try:
        battle, finished = await battles_service.cast_vote(
            db, battle_id, payload.chosen_photo_id, user
        )
    except BattleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    detail = "Голос учтён"
    if finished:
        winner = await db.get(Photo, battle.winner_photo_id) if battle.winner_photo_id else None
        detail = "Батл завершён: рейтинг обновлён"
        if winner is not None:
            owner = await db.get(User, winner.user_id)
            if owner is not None:
                detail = f"Победил @{owner.username} — рейтинг обновлён"

    return VoteResultOut(
        battle=await _serialize_battle(db, battle, user.id),
        finished=finished,
        detail=detail,
    )


@router.get("/leaderboard", response_model=list[LeaderboardEntryOut])
async def leaderboard(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    season_id: int | None = Query(default=None),
) -> list[LeaderboardEntryOut]:
    """Лидерборд по ELO (или по победам в сезоне)."""
    rows = await battles_service.leaderboard(db, limit=limit, season_id=season_id)
    return [LeaderboardEntryOut(**row) for row in rows]


@router.get("/season", response_model=SeasonOut)
async def current_season(user: CurrentUser, db: DbSession) -> SeasonOut:
    """Текущий батл недели (создаётся при необходимости)."""
    season = await battles_service.ensure_current_season(db)
    return SeasonOut.model_validate(season)
