"""Тесты этапа 6: мобатлы — ELO, подбор пар, голосование, анти-чит, лидерборд."""

from __future__ import annotations

import numpy as np
import pytest
from app.models.photo import Photo
from app.models.user import User
from app.services.battles import (
    DAILY_VOTE_LIMIT,
    VOTES_TO_WIN,
    BattleError,
    elo_update,
    expected_score,
    is_battle_eligible,
)
from httpx import AsyncClient
from sqlalchemy import select


async def _register(client: AsyncClient, username: str) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201


async def _make_photo(db_session, user_id: int, public: bool = True) -> Photo:
    rng = np.random.default_rng(user_id)
    photo = Photo(
        user_id=user_id,
        storage_path=f"users/{user_id}/b{user_id}.png",
        content_type="image/png",
        size_bytes=2048,
        width=600,
        height=800,
        landmarks={
            "points": rng.uniform(0.2, 0.8, (106, 2)).tolist(),
            "provider": "manual",
            "bbox": [0.2, 0.2, 0.8, 0.8],
        },
        landmarks_provider="manual",
        is_aligned=True,
        quality_score=0.9,
        is_public_in_battles=public,
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)
    return photo


@pytest.fixture
async def two_users_with_photos(client: AsyncClient, db_session):
    """Два пользователя с публичными фото — основа для батла."""
    await _register(client, "fighter_one")
    await client.post("/api/auth/logout")
    await _register(client, "fighter_two")
    await client.post("/api/auth/logout")

    users = (await db_session.execute(select(User).order_by(User.id))).scalars().all()
    for u in users:
        u.show_in_battles = True
    await db_session.commit()
    photo_one = await _make_photo(db_session, users[0].id)
    photo_two = await _make_photo(db_session, users[1].id)
    return users, photo_one, photo_two


def test_elo_math() -> None:
    assert expected_score(1200, 1200) == 0.5
    delta = elo_update(1200, 1200)
    assert delta.winner_delta == 16
    assert delta.loser_delta == -16

    # Андердог получает больше за победу, чем фаворит.
    upset = elo_update(1000, 1600)
    expected_win = elo_update(1600, 1000)
    assert upset.winner_delta > expected_win.winner_delta
    assert upset.loser_delta < 0


async def test_battle_eligibility_rules(db_session, two_users_with_photos) -> None:
    _, photo_one, _ = two_users_with_photos
    assert is_battle_eligible(photo_one)

    photo_one.is_hidden = True
    assert not is_battle_eligible(photo_one)
    photo_one.is_hidden = False

    photo_one.quality_score = 0.1
    assert not is_battle_eligible(photo_one)
    photo_one.quality_score = 0.9

    photo_one.width = 100
    assert not is_battle_eligible(photo_one)


async def test_next_battle_requires_public_photo(client: AsyncClient, db_session) -> None:
    await _register(client, "solo_user")
    user = (await db_session.execute(select(User))).scalar_one()
    user.show_in_battles = True
    await db_session.commit()
    await _make_photo(db_session, user.id, public=False)
    resp = await client.get("/api/battles/next")
    assert resp.status_code == 409


async def test_next_battle_matches_two_users(client: AsyncClient, two_users_with_photos) -> None:
    _, _, _ = two_users_with_photos
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    resp = await client.get("/api/battles/next")
    assert resp.status_code == 200
    body = resp.json()
    assert body["left"]["id"] != body["right"]["id"]
    assert body["total_votes"] == 0


async def test_vote_flow_and_elo_update(client: AsyncClient, two_users_with_photos) -> None:
    _, photo_one, photo_two = two_users_with_photos

    # Игрок 1 открывает батл.
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    battle = (await client.get("/api/battles/next")).json()
    battle_id = battle["id"]

    # Игрок 1 не может голосовать в своём батле.
    own_photo = next(p for p in (battle["left"], battle["right"]) if p["id"] == photo_one.id)
    other_photo = next(p for p in (battle["left"], battle["right"]) if p["id"] != photo_one.id)
    self_vote = await client.post(
        f"/api/battles/{battle_id}/vote", json={"chosen_photo_id": own_photo["id"]}
    )
    assert self_vote.status_code == 409

    # Голосуют сторонние зрители (нужно 3 голоса для завершения).
    voters = ["watcher_a", "watcher_b", "watcher_c"]
    for index, name in enumerate(voters):
        await client.post("/api/auth/logout")
        await _register(client, name)
        resp = await client.post(
            f"/api/battles/{battle_id}/vote", json={"chosen_photo_id": other_photo["id"]}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["battle"]["votes_left"] + body["battle"]["votes_right"] == index + 1

    assert body["finished"] is True
    assert body["battle"]["winner_photo_id"] == other_photo["id"]

    # ELO победителя вырос, проигравшего — упал.
    leaderboard = (await client.get("/api/battles/leaderboard")).json()
    winner_name = next(
        p for p in (battle["left"], battle["right"]) if p["id"] == other_photo["id"]
    )["owner"]
    elo_by_name = {row["username"]: row["elo"] for row in leaderboard}
    winner_user = "fighter_two" if other_photo["id"] == photo_two.id else "fighter_one"
    loser_user = "fighter_one" if winner_user == "fighter_two" else "fighter_two"
    assert elo_by_name[winner_user] > 1200
    assert elo_by_name[loser_user] < 1200
    assert winner_name


async def test_double_vote_rejected(client: AsyncClient, two_users_with_photos) -> None:
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    battle = (await client.get("/api/battles/next")).json()
    target = battle["right"]["id"]

    await client.post("/api/auth/logout")
    await _register(client, "one_voter")
    first = await client.post(f"/api/battles/{battle['id']}/vote", json={"chosen_photo_id": target})
    assert first.status_code == 200
    second = await client.post(
        f"/api/battles/{battle['id']}/vote", json={"chosen_photo_id": target}
    )
    assert second.status_code == 409


async def test_invalid_choice_rejected(client: AsyncClient, two_users_with_photos) -> None:
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    battle = (await client.get("/api/battles/next")).json()

    await client.post("/api/auth/logout")
    await _register(client, "bad_voter")
    resp = await client.post(f"/api/battles/{battle['id']}/vote", json={"chosen_photo_id": 999_999})
    assert resp.status_code == 409


async def test_leaderboard_has_rank_and_elo(client: AsyncClient, two_users_with_photos) -> None:
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    resp = await client.get("/api/battles/leaderboard")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows
    assert rows[0]["rank"] == 1
    assert all("elo" in row for row in rows)


async def test_season_is_created_and_returned(client: AsyncClient, two_users_with_photos) -> None:
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    resp = await client.get("/api/battles/season")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"].startswith("week-")
    assert body["is_active"] is True
    assert body["starts_at"] < body["ends_at"]


async def test_season_leaderboard(client: AsyncClient, two_users_with_photos) -> None:
    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    season = (await client.get("/api/battles/season")).json()
    resp = await client.get(f"/api/battles/leaderboard?season_id={season['id']}")
    assert resp.status_code == 200


async def test_battles_page_redirects_guest(client: AsyncClient) -> None:
    resp = await client.get("/battles", follow_redirects=False)
    assert resp.status_code == 302


async def test_constants_are_sane() -> None:
    assert VOTES_TO_WIN >= 1
    assert DAILY_VOTE_LIMIT > 0
    assert issubclass(BattleError, Exception)


async def test_opponent_photo_image_is_reachable(
    client: AsyncClient, db_session, two_users_with_photos
) -> None:
    """Фото соперника должно открываться в батле.

    Раньше /api/photos/{id}/image отдавал файл только владельцу, поэтому
    картинка чужого участника батла возвращала 404 и батл был неиграбельным
    (на экране оставалась битая картинка).
    """
    _users, _photo_one, photo_two = two_users_with_photos
    from app.core.storage import resolve_media_path

    path = resolve_media_path(photo_two.storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    await _register(client, "voter_view")  # логинимся третьим
    resp = await client.get(f"/api/photos/{photo_two.id}/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")


async def test_private_photo_image_still_hidden(
    client: AsyncClient, db_session, two_users_with_photos
) -> None:
    """Приватное чужое фото по-прежнему отдаёт 404 (защита от IDOR)."""
    _users, _photo_one, photo_two = two_users_with_photos
    photo_two.is_public_in_battles = False
    await db_session.commit()
    await _register(client, "voter_priv")
    resp = await client.get(f"/api/photos/{photo_two.id}/image")
    assert resp.status_code == 404


async def test_opted_out_user_not_shown_as_opponent(
    client: AsyncClient, db_session, two_users_with_photos
) -> None:
    """Галочка «Показывать в мобатлах» реально исключает фото из подбора.

    Раньше поле хранилось и рисовалось в профиле, но нигде не проверялось:
    пользователь снимал галочку, а его фото всё равно показывали другим.
    """
    users, _photo_one, _photo_two = two_users_with_photos
    users[1].show_in_battles = False  # владелец фото-соперника уходит в opt-out
    await db_session.commit()

    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    resp = await client.get("/api/battles/next")
    assert resp.status_code == 409  # соперников больше нет


async def test_opted_out_user_cannot_start_battle(
    client: AsyncClient, db_session, two_users_with_photos
) -> None:
    """С opt-out нельзя и выставить своё фото в батл."""
    users, _photo_one, _photo_two = two_users_with_photos
    users[0].show_in_battles = False
    await db_session.commit()

    await client.post("/api/auth/login", json={"username": "fighter_one", "password": "Passw0rd1"})
    resp = await client.get("/api/battles/next")
    assert resp.status_code == 409
