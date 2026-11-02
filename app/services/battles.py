"""Сервис моггбатлов: подбор пар, голосование, ELO, лидерборд, анти-чит.

Логика:
  * пара собирается из публичных фото разных пользователей с близким ELO;
  * каждый пользователь голосует один раз за батл (уникальный индекс в БД);
  * победитель определяется по большинству после VOTES_TO_WIN голосов, затем
    пересчитывается ELO обоих владельцев (классическая формула Эло);
  * анти-чит: нельзя голосовать за своё фото, нельзя голосовать в батле, где
    участвуют только свои фото, есть суточный лимит голосов и минимальный
    порог качества фото.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.battle import Battle, BattleSeason, Vote
from app.models.photo import Photo
from app.models.user import User

VOTES_TO_WIN = 3
ELO_K = 32
MATCH_ELO_WINDOW = 250
DAILY_VOTE_LIMIT = 100
MIN_QUALITY_FOR_BATTLE = 0.35
MIN_BATTLE_PHOTO_SIDE = 300


class BattleError(Exception):
    """Нарушение правил моггбатлов (для преобразования в HTTP-ответ)."""


@dataclass(slots=True)
class EloDelta:
    """Изменение рейтинга после батла."""

    winner_delta: int
    loser_delta: int


def expected_score(rating_a: int, rating_b: int) -> float:
    """Ожидаемый результат игрока A против B (формула Эло)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def elo_update(winner: int, loser: int, k: int = ELO_K) -> EloDelta:
    """Считает новые рейтинги победителя и проигравшего."""
    expected_winner = expected_score(winner, loser)
    expected_loser = expected_score(loser, winner)
    return EloDelta(
        winner_delta=round(k * (1.0 - expected_winner)),
        loser_delta=round(k * (0.0 - expected_loser)),
    )


def is_battle_eligible(photo: Photo) -> bool:
    """Проверяет, годится ли фото для батла (качество, размер, видимость)."""
    if photo.is_hidden or not photo.is_public_in_battles:
        return False
    if not photo.landmarks:
        return False
    if photo.quality_score is not None and photo.quality_score < MIN_QUALITY_FOR_BATTLE:
        return False
    if photo.width is not None and photo.width < MIN_BATTLE_PHOTO_SIDE:
        return False
    return not (photo.height is not None and photo.height < MIN_BATTLE_PHOTO_SIDE)


async def active_season(db: AsyncSession) -> BattleSeason | None:
    """Текущий активный сезон (батл недели)."""
    now = datetime.now(UTC)
    return (
        await db.execute(
            select(BattleSeason)
            .where(
                BattleSeason.is_active.is_(True),
                BattleSeason.starts_at <= now,
                BattleSeason.ends_at >= now,
            )
            .order_by(BattleSeason.starts_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ensure_current_season(db: AsyncSession) -> BattleSeason:
    """Создаёт сезон на текущую ISO-неделю, если его ещё нет."""
    now = datetime.now(UTC)
    year, week, _ = now.isocalendar()
    slug = f"week-{year}-{week:02d}"

    season = (
        await db.execute(select(BattleSeason).where(BattleSeason.slug == slug))
    ).scalar_one_or_none()
    if season is not None:
        return season

    monday = now - timedelta(days=now.weekday())
    starts_at = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    season = BattleSeason(
        title=f"Батл недели {year}-W{week:02d}",
        slug=slug,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        is_active=True,
    )
    db.add(season)
    await db.commit()
    await db.refresh(season)
    return season


async def _recent_opponents(db: AsyncSession, photo_id: int, limit: int = 20) -> set[int]:
    """Фото, с которыми уже был батл за последние сутки (чтобы не повторяться)."""
    since = datetime.now(UTC) - timedelta(days=1)
    rows = (
        await db.execute(
            select(Battle.left_photo_id, Battle.right_photo_id).where(
                Battle.created_at >= since,
                or_(Battle.left_photo_id == photo_id, Battle.right_photo_id == photo_id),
            )
        )
    ).all()
    opponents: set[int] = set()
    for left, right in rows:
        opponents.add(right if left == photo_id else left)
    return opponents


async def find_opponent(db: AsyncSession, photo: Photo, owner_elo: int) -> Photo | None:
    """Подбирает соперника: другое фото, другой владелец, близкое ELO."""
    recent = await _recent_opponents(db, photo.id)
    window = MATCH_ELO_WINDOW

    for _ in range(3):
        stmt = (
            select(Photo)
            .join(User, User.id == Photo.user_id)
            .where(
                Photo.id != photo.id,
                Photo.user_id != photo.user_id,
                Photo.is_hidden.is_(False),
                Photo.is_public_in_battles.is_(True),
                Photo.landmarks.is_not(None),
                Photo.quality_score >= MIN_QUALITY_FOR_BATTLE,
                User.is_active.is_(True),
                User.is_banned.is_(False),
                # Opt-out владельца: галочка «Показывать в моггбатлах» должна
                # реально исключать фото из подбора, а не только храниться.
                User.show_in_battles.is_(True),
                User.elo.between(owner_elo - window, owner_elo + window),
            )
            .order_by(func.random())
            .limit(1)
        )
        if recent:
            stmt = stmt.where(Photo.id.not_in(recent))
        candidate = (await db.execute(stmt)).scalar_one_or_none()
        if candidate is not None:
            return candidate
        window *= 3  # расширяем окно, если не нашлось соперника

    return None


async def create_or_get_battle(
    db: AsyncSession, photo: Photo, user: User
) -> tuple[Battle, Photo, Photo]:
    """Возвращает активный батл с участием фото или создаёт новый."""
    if not user.show_in_battles:
        raise BattleError("Участие в батлах отключено в профиле")
    if not is_battle_eligible(photo):
        raise BattleError("Фото не допущено к батлам: нужно публичное фото хорошего качества")

    existing = (
        await db.execute(
            select(Battle)
            .where(
                Battle.is_active.is_(True),
                Battle.winner_photo_id.is_(None),
                or_(Battle.left_photo_id == photo.id, Battle.right_photo_id == photo.id),
            )
            .order_by(Battle.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        left = await db.get(Photo, existing.left_photo_id)
        right = await db.get(Photo, existing.right_photo_id)
        return existing, left, right  # type: ignore[return-value]

    opponent = await find_opponent(db, photo, user.elo)
    if opponent is None:
        raise BattleError("Пока нет достойного соперника — попробуйте позже")

    season = await ensure_current_season(db)
    battle = Battle(
        left_photo_id=photo.id,
        right_photo_id=opponent.id,
        season_id=season.id,
        is_active=True,
    )
    db.add(battle)
    await db.commit()
    await db.refresh(battle)
    return battle, photo, opponent


async def votes_today(db: AsyncSession, voter_id: int) -> int:
    """Сколько голосов пользователь отдал за последние 24 часа."""
    since = datetime.now(UTC) - timedelta(days=1)
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Vote)
                .where(Vote.voter_id == voter_id, Vote.created_at >= since)
            )
        ).scalar_one()
    )


async def cast_vote(
    db: AsyncSession, battle_id: int, chosen_photo_id: int, voter: User
) -> tuple[Battle, bool]:
    """Голосует в батле. Возвращает (батл, завершён ли батл).

    Анти-чит проверки: батл существует и активен, голос за одну из двух фотографий,
    голосующий не владелец ни одной из них, повторный голос запрещён, суточный
    лимит голосов не превышен.
    """
    battle = await db.get(Battle, battle_id)
    if battle is None:
        raise BattleError("Батл не найден")
    if not battle.is_active or battle.winner_photo_id is not None:
        raise BattleError("Этот батл уже завершён")

    if chosen_photo_id not in (battle.left_photo_id, battle.right_photo_id):
        raise BattleError("Голос можно отдать только за одно из двух фото")

    left = await db.get(Photo, battle.left_photo_id)
    right = await db.get(Photo, battle.right_photo_id)
    if left is None or right is None:
        raise BattleError("Фото батла недоступны")

    if voter.id in (left.user_id, right.user_id):
        raise BattleError("Нельзя голосовать в батле со своим фото")

    already = (
        await db.execute(select(Vote).where(Vote.battle_id == battle_id, Vote.voter_id == voter.id))
    ).scalar_one_or_none()
    if already is not None:
        raise BattleError("Вы уже голосовали в этом батле")

    if await votes_today(db, voter.id) >= DAILY_VOTE_LIMIT:
        raise BattleError("Суточный лимит голосов исчерпан")

    db.add(Vote(battle_id=battle_id, voter_id=voter.id, chosen_photo_id=chosen_photo_id))
    if chosen_photo_id == battle.left_photo_id:
        battle.votes_left += 1
    else:
        battle.votes_right += 1

    finished = False
    total = battle.votes_left + battle.votes_right
    if total >= VOTES_TO_WIN:
        if battle.votes_left == battle.votes_right:
            finished = False  # ничья — ждём следующий голос
        else:
            battle.winner_photo_id = (
                battle.left_photo_id
                if battle.votes_left > battle.votes_right
                else battle.right_photo_id
            )
            battle.is_active = False
            await _apply_elo(db, left, right, battle.winner_photo_id)
            finished = True

    await db.commit()
    await db.refresh(battle)
    return battle, finished


async def _apply_elo(db: AsyncSession, left: Photo, right: Photo, winner_photo_id: int) -> None:
    """Пересчитывает ELO владельцев фото по итогу батла."""
    winner_photo = left if left.id == winner_photo_id else right
    loser_photo = right if winner_photo.id == left.id else left

    winner = await db.get(User, winner_photo.user_id)
    loser = await db.get(User, loser_photo.user_id)
    if winner is None or loser is None or winner.id == loser.id:
        return

    delta = elo_update(winner.elo, loser.elo)
    winner.elo = max(100, winner.elo + delta.winner_delta)
    loser.elo = max(100, loser.elo + delta.loser_delta)


async def leaderboard(
    db: AsyncSession, limit: int = 50, season_id: int | None = None
) -> list[dict]:
    """Лидерборд по ELO. При season_id — победы только в рамках сезона."""
    limit = max(1, min(limit, 200))

    if season_id is None:
        users = (
            (
                await db.execute(
                    select(User)
                    .where(User.is_active.is_(True), User.is_banned.is_(False))
                    .order_by(User.elo.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        rows = []
        for index, user in enumerate(users, start=1):
            wins, losses = await win_loss(db, user.id)
            rows.append(
                {
                    "rank": index,
                    "username": user.username,
                    "display_name": user.display_name,
                    "elo": user.elo,
                    "wins": wins,
                    "losses": losses,
                }
            )
        return rows

    win_counts = (
        select(Photo.user_id.label("user_id"), func.count().label("wins"))
        .join(Battle, Photo.id == Battle.winner_photo_id)
        .where(Battle.season_id == season_id)
        .group_by(Photo.user_id)
        .subquery()
    )
    season_rows = (
        await db.execute(
            select(User, win_counts.c.wins)
            .join(win_counts, win_counts.c.user_id == User.id)
            .order_by(win_counts.c.wins.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "rank": index,
            "username": user.username,
            "display_name": user.display_name,
            "elo": user.elo,
            "wins": int(wins),
            "losses": 0,
        }
        for index, (user, wins) in enumerate(season_rows, start=1)
    ]


async def win_loss(db: AsyncSession, user_id: int) -> tuple[int, int]:
    """Победы и поражения пользователя по завершённым батлам."""
    left = aliased(Photo)
    right = aliased(Photo)
    rows = (
        await db.execute(
            select(
                Battle.left_photo_id,
                Battle.right_photo_id,
                Battle.winner_photo_id,
                left.user_id,
                right.user_id,
            )
            .join(left, left.id == Battle.left_photo_id)
            .join(right, right.id == Battle.right_photo_id)
            .where(
                Battle.winner_photo_id.is_not(None),
                or_(left.user_id == user_id, right.user_id == user_id),
            )
        )
    ).all()

    wins = 0
    losses = 0
    for left_id, right_id, winner_id, left_owner, right_owner in rows:
        if left_owner == right_owner:
            continue  # батл между фото одного владельца не влияет на счёт
        user_photo_id = left_id if left_owner == user_id else right_id
        if winner_id == user_photo_id:
            wins += 1
        else:
            losses += 1
    return wins, losses
