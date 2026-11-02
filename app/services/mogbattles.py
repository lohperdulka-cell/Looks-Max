"""Сервис PvP-моггбатлов: очередь, подбор соперника, готовность, результат.

Дуэль 1x1 в отличие от арены зрительских голосов (`battles.py`):
  * подбор по близким PvP-очкам (окно расширяется, если никто не ждёт);
  * пока оба не нажали «Готов», фото соперника не отдаётся клиенту;
  * когда оба готовы, оценки считаются на сервере, победитель получает очки,
    проигравший теряет (но не уходит в минус);
  * «ничья» при равных баллах — оба получают утешительные очки.

Очки PvP держим отдельно от ELO арены: там рейтинг двигают зрители, здесь —
личный исход, смешивать их значило бы наказывать за одно и то же дважды.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mogbattle import (
    ROOM_CANCELLED,
    ROOM_FINISHED,
    ROOM_REVEALED,
    ROOM_WAITING,
    MogRoom,
)
from app.models.photo import Photo
from app.models.user import User
from app.services.battles import is_battle_eligible

# Окно подбора по PvP-очкам: начинаем с узкого, расширяем при пустой очереди.
MATCH_POINT_WINDOW = 200
ROOM_TTL_MINUTES = 15
# Очки за исход. Победа/поражение — база, дальше масштаб по разнице рейтингов.
WIN_POINTS = 25
LOSS_POINTS = -15
DRAW_POINTS = 5


class MogError(Exception):
    """Нарушение правил моггбатла (преобразуется в HTTP-ответ)."""


@dataclass(slots=True)
class RoomView:
    """Представление комнаты для API: что можно показывать на текущем шаге."""

    room: MogRoom
    opponent_ready: bool
    both_ready: bool


def _now() -> datetime:
    return datetime.now(UTC)


async def _pick_photo(db: AsyncSession, user: User) -> Photo:
    """Берёт самое свежее публичное фото пользователя, годное для дуэли."""
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
    if photo is None or not is_battle_eligible(photo):
        raise MogError("Нужно публичное фото хорошего качества — загрузите его на странице оценки")
    return photo


async def _expire_stale(db: AsyncSession, now: datetime) -> None:
    """Закрывает просроченные комнаты, чтобы они не засоряли очередь."""
    stale = (
        (
            await db.execute(
                select(MogRoom).where(
                    MogRoom.status.in_([ROOM_WAITING, ROOM_REVEALED]),
                    MogRoom.expires_at < now,
                )
            )
        )
        .scalars()
        .all()
    )
    for room in stale:
        room.status = ROOM_CANCELLED
    if stale:
        await db.commit()


async def _find_opponent(db: AsyncSession, user: User, now: datetime) -> MogRoom | None:
    """Ищет чужую комнату в очереди с близкими очками и занимает её."""
    window = MATCH_POINT_WINDOW
    for _ in range(3):
        opponent = (
            await db.execute(
                select(User)
                .join(MogRoom, MogRoom.player_a_id == User.id)
                .where(
                    MogRoom.status == ROOM_WAITING,
                    MogRoom.player_b_id.is_(None),
                    MogRoom.player_a_id != user.id,
                    MogRoom.expires_at >= now,
                    User.pvp_points.between(user.pvp_points - window, user.pvp_points + window),
                )
                .order_by(MogRoom.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if opponent is not None:
            room = (
                await db.execute(
                    select(MogRoom)
                    .where(
                        MogRoom.player_a_id == opponent.id,
                        MogRoom.status == ROOM_WAITING,
                        MogRoom.player_b_id.is_(None),
                    )
                    .order_by(MogRoom.created_at.asc())
                    .limit(1)
                )
            ).scalar_one()
            return room
        window *= 4
    return None


async def join_queue(db: AsyncSession, user: User) -> MogRoom:
    """Встаёт в очередь: занимает чужую комнату либо создаёт свою.

    Возвращает комнату, где пользователь — игрок B (если нашёлся соперник) или
    игрок A (если очередь была пуста и он первый).
    """
    if not user.show_in_battles:
        raise MogError("Участие в моггбатлах отключено в профиле")

    now = _now()
    await _expire_stale(db, now)

    # Уже есть активная комната — возвращаем её (идемпотентность повторного входа).
    existing = (
        await db.execute(
            select(MogRoom)
            .where(
                MogRoom.status == ROOM_WAITING,
                MogRoom.expires_at >= now,
                or_(MogRoom.player_a_id == user.id, MogRoom.player_b_id == user.id),
            )
            .order_by(MogRoom.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    photo = await _pick_photo(db, user)
    opponent_room = await _find_opponent(db, user, now)
    if opponent_room is not None:
        opponent_room.player_b_id = user.id
        opponent_room.photo_b_id = photo.id
        # Готовность НЕ ставим автоматически: соперник должен подтвердить её
        # сам, иначе фото раскрылось бы без его согласия.
        await _maybe_reveal(db, opponent_room)
        await db.commit()
        await db.refresh(opponent_room)
        return opponent_room

    room = MogRoom(
        status=ROOM_WAITING,
        player_a_id=user.id,
        photo_a_id=photo.id,
        expires_at=now + timedelta(minutes=ROOM_TTL_MINUTES),
    )
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return room


async def get_room(db: AsyncSession, room_id: int, user: User) -> MogRoom:
    """Возвращает комнату участнику (чужую не отдаём — не раскрываем чужие фото)."""
    room = await db.get(MogRoom, room_id)
    if room is None or user.id not in (room.player_a_id, room.player_b_id):
        raise MogError("Комната не найдена")
    return room


async def mark_ready(db: AsyncSession, room: MogRoom, user: User) -> MogRoom:
    """Отмечает готовность стороны. Когда готовы оба — считает результат."""
    if room.status not in (ROOM_WAITING, ROOM_REVEALED):
        raise MogError("Комната уже завершена")

    if user.id == room.player_a_id:
        room.ready_a = True
    elif user.id == room.player_b_id:
        room.ready_b = True
    else:
        raise MogError("Вы не участник этой комнаты")

    await _maybe_reveal(db, room)
    await db.commit()
    await db.refresh(room)
    return room


async def _maybe_reveal(db: AsyncSession, room: MogRoom) -> None:
    """Если оба готовы и соперник есть — считает оценки и очки."""
    if room.player_b_id is None or not (room.ready_a and room.ready_b):
        return
    if room.status != ROOM_WAITING:
        return

    photo_a = await db.get(Photo, room.photo_a_id)
    photo_b = await db.get(Photo, room.photo_b_id) if room.photo_b_id else None
    player_a = await db.get(User, room.player_a_id)
    player_b = await db.get(User, room.player_b_id) if room.player_b_id else None
    if not (photo_a and photo_b and player_a and player_b):
        return

    score_a = round(_score_photo(photo_a, player_a), 2)
    score_b = round(_score_photo(photo_b, player_b), 2)
    room.score_a = score_a
    room.score_b = score_b

    # Ничья — только если совпадают показанные баллы. Иначе пользователь видел
    # бы «4.74 против 4.78» и надпись «Ничья», что выглядит как ошибка.
    if score_a == score_b:
        room.is_draw = True
        room.winner_id = None
        room.points_a = DRAW_POINTS
        room.points_b = DRAW_POINTS
    elif score_a > score_b:
        room.winner_id = player_a.id
        room.points_a, room.points_b = _points_for(player_a, player_b, won=True)
    else:
        room.winner_id = player_b.id
        room.points_b, room.points_a = _points_for(player_b, player_a, won=True)

    player_a.pvp_points = max(0, player_a.pvp_points + room.points_a)
    player_b.pvp_points = max(0, player_b.pvp_points + room.points_b)
    room.status = ROOM_REVEALED


def _points_for(winner: User, loser: User, *, won: bool) -> tuple[int, int]:
    """Очки победителю и проигравшему с поправкой на разницу в силе.

    Победа над более сильным соперником стоит дороже, поражение от слабого —
    дешевле: иначе рейтинг быстро расслаивается и новичкам не с кем играть.
    """
    gap = winner.pvp_points - loser.pvp_points
    bonus = max(-10, min(10, gap // 50))
    win = WIN_POINTS - bonus
    loss = LOSS_POINTS + bonus
    return win, max(-WIN_POINTS, loss)


def _score_photo(photo: Photo, owner: User) -> float:
    """Общий балл фото (0..10) — та же модель, что и в отчётах."""
    from app.services.reports import compute_report

    try:
        return float(compute_report(photo, owner).overall)
    except Exception:
        return 0.0


async def finish_room(db: AsyncSession, room: MogRoom, user: User) -> MogRoom:
    """Фиксирует, что участник увидел результат (для аналитики и очистки)."""
    if room.status == ROOM_REVEALED:
        room.status = ROOM_FINISHED
        await db.commit()
        await db.refresh(room)
    return room


async def leave_room(db: AsyncSession, room: MogRoom, user: User) -> None:
    """Выход из комнаты. Пока соперник не найден — просто закрываем её."""
    if room.status in (ROOM_WAITING, ROOM_REVEALED):
        room.status = ROOM_CANCELLED
        await db.commit()


async def leaderboard(db: AsyncSession, limit: int = 50) -> list[dict]:
    """Лидерборд моггбатлов по PvP-очкам."""
    limit = max(1, min(limit, 200))
    users = (
        (
            await db.execute(
                select(User)
                .where(
                    User.is_active.is_(True),
                    User.is_banned.is_(False),
                    User.pvp_points > 0,
                )
                .order_by(User.pvp_points.desc(), User.id.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "rank": index,
            "username": user.username,
            "display_name": user.display_name,
            "pvp_points": user.pvp_points,
        }
        for index, user in enumerate(users, start=1)
    ]


async def user_record(db: AsyncSession, user_id: int) -> tuple[int, int]:
    """Победы и поражения пользователя в завершённых дуэлях."""
    rooms = (
        (
            await db.execute(
                select(MogRoom).where(
                    MogRoom.status.in_([ROOM_REVEALED, ROOM_FINISHED]),
                    or_(MogRoom.player_a_id == user_id, MogRoom.player_b_id == user_id),
                )
            )
        )
        .scalars()
        .all()
    )
    wins = sum(1 for r in rooms if r.winner_id == user_id)
    losses = sum(1 for r in rooms if r.winner_id is not None and r.winner_id != user_id)
    return wins, losses
