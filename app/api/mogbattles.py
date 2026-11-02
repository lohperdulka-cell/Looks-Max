"""Роутер PvP-моггбатлов: очередь, готовность, результат, лидерборд.

Ключевая деталь приватности: пока соперник не подтвердил готовность, его фото
клиенту не отдаётся (ни URL, ни id). Иначе участник мог бы подобрать ракурс
под конкретного соперника, и дуэль теряла бы смысл.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.mogbattle import ROOM_REVEALED, ROOM_WAITING, MogRoom
from app.models.user import User
from app.schemas.mogbattles import (
    STEP_OPPONENT_FOUND,
    STEP_REVEALED,
    STEP_WAITING,
    MogLeaderboardEntryOut,
    MogProfileOut,
    MogRoomOut,
    MogSideOut,
)
from app.services import mogbattles as mog
from app.services.mogbattles import MogError

router = APIRouter(prefix="/api/mogbattles", tags=["mogbattles"])


async def _side_out(
    db: DbSession,
    user_id: int,
    photo_id: int | None,
    *,
    ready: bool,
    reveal: bool,
    score: float | None,
    points: int,
) -> MogSideOut:
    """Собирает сторону дуэли. Фото раскрывается только на шаге результата."""
    user = await db.get(User, user_id)
    name = (user.display_name or user.username) if user else "участник"
    out = MogSideOut(
        user_id=user_id,
        name=name,
        ready=ready,
        score=score if reveal else None,
        points=points if reveal else 0,
    )
    if photo_id and reveal:
        out.photo_id = photo_id
        out.image_url = f"/api/photos/{photo_id}/image"
        out.blurred = False
    elif photo_id:
        # Соперник уже найден, но не готов — показываем силуэт без картинки.
        out.photo_id = None
        out.image_url = None
        out.blurred = True
    return out


async def _serialize(db: DbSession, room: MogRoom, user: User) -> MogRoomOut:
    is_a = user.id == room.player_a_id
    my_id = room.player_a_id if is_a else (room.player_b_id or user.id)
    opp_id = room.player_b_id if is_a else room.player_a_id
    my_photo = room.photo_a_id if is_a else room.photo_b_id
    opp_photo = room.photo_b_id if is_a else room.photo_a_id
    my_ready = room.ready_a if is_a else room.ready_b
    opp_ready = room.ready_b if is_a else room.ready_a
    my_score = room.score_a if is_a else room.score_b
    opp_score = room.score_b if is_a else room.score_a
    my_points = room.points_a if is_a else room.points_b
    opp_points = room.points_b if is_a else room.points_a

    reveal = room.status in (ROOM_REVEALED, "finished")
    me = await _side_out(
        db, my_id, my_photo, ready=my_ready, reveal=True, score=my_score, points=my_points
    )

    opponent = None
    if opp_id is not None:
        opponent = await _side_out(
            db,
            opp_id,
            opp_photo,
            ready=opp_ready,
            reveal=reveal,
            score=opp_score,
            points=opp_points,
        )

    if room.status == ROOM_WAITING and opp_id is None:
        step = STEP_WAITING
    elif not reveal:
        step = STEP_OPPONENT_FOUND
    else:
        step = STEP_REVEALED

    return MogRoomOut(
        room_id=room.id,
        status=room.status,
        step=step,
        is_player_a=is_a,
        me=me,
        opponent=opponent,
        winner_id=room.winner_id,
        is_draw=room.is_draw,
        result_text=_result_text(room, my_id),
        expires_at=room.expires_at.isoformat(),
    )


def _result_text(room: MogRoom, my_id: int | None) -> str:
    """Текст исхода для текущего игрока."""
    if room.status not in (ROOM_REVEALED, "finished"):
        return ""
    if room.is_draw:
        return "Ничья — равные баллы"
    if room.winner_id == my_id:
        return "Вы победили. MOGGED!"
    if room.winner_id is None:
        return "Дуэль завершена"
    return "Вас переиграли"


async def _room_or_404(db: DbSession, room_id: int, user: User) -> MogRoom:
    try:
        return await mog.get_room(db, room_id, user)
    except MogError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/queue", response_model=MogRoomOut)
@limiter.limit("30/minute")
async def join_queue(request: Request, user: CurrentUser, db: DbSession) -> MogRoomOut:
    """Встаёт в очередь моггбатла: занимает чужую комнату или создаёт свою."""
    try:
        room = await mog.join_queue(db, user)
    except MogError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await _serialize(db, room, user)


@router.get("/rooms/{room_id}", response_model=MogRoomOut)
async def room_state(room_id: int, user: CurrentUser, db: DbSession) -> MogRoomOut:
    """Состояние комнаты для опроса (готовность соперника, результат)."""
    room = await _room_or_404(db, room_id, user)
    return await _serialize(db, room, user)


@router.post("/rooms/{room_id}/ready", response_model=MogRoomOut)
@limiter.limit("60/minute")
async def mark_ready(
    request: Request, room_id: int, user: CurrentUser, db: DbSession
) -> MogRoomOut:
    """Подтверждает готовность. Когда готовы оба — считаются оценки и очки."""
    room = await _room_or_404(db, room_id, user)
    try:
        room = await mog.mark_ready(db, room, user)
    except MogError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await _serialize(db, room, user)


@router.post("/rooms/{room_id}/finish", response_model=MogRoomOut)
async def finish_room(room_id: int, user: CurrentUser, db: DbSession) -> MogRoomOut:
    """Фиксирует, что участник увидел результат."""
    room = await _room_or_404(db, room_id, user)
    room = await mog.finish_room(db, room, user)
    return await _serialize(db, room, user)


@router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def leave_room(room_id: int, user: CurrentUser, db: DbSession) -> None:
    """Выход из комнаты (отмена ожидания)."""
    room = await _room_or_404(db, room_id, user)
    await mog.leave_room(db, room, user)


@router.get("/leaderboard", response_model=list[MogLeaderboardEntryOut])
async def leaderboard(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MogLeaderboardEntryOut]:
    """Лидерборд моггбатлов по PvP-очкам."""
    rows = await mog.leaderboard(db, limit=limit)
    return [MogLeaderboardEntryOut(**row) for row in rows]


@router.get("/me", response_model=MogProfileOut)
async def my_pvp(user: CurrentUser, db: DbSession) -> MogProfileOut:
    """Мой PvP-профиль: очки, победы/поражения, место в лидерборде."""
    wins, losses = await mog.user_record(db, user.id)
    rank: int | None = None
    if user.pvp_points > 0:
        higher = (
            await db.execute(
                select(func.count()).select_from(User).where(User.pvp_points > user.pvp_points)
            )
        ).scalar_one()
        rank = int(higher) + 1
    return MogProfileOut(pvp_points=user.pvp_points, wins=wins, losses=losses, rank=rank)
