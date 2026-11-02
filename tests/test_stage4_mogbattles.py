"""Тесты этапа 4: PvP-моггбатлы — очередь, блюр до готовности, очки, лидерборд."""

from __future__ import annotations

import numpy as np
import pytest
from app.models.mogbattle import ROOM_FINISHED, ROOM_REVEALED, ROOM_WAITING, MogRoom
from app.models.photo import Photo
from app.models.user import User
from app.services import mogbattles as mog
from httpx import AsyncClient
from sqlalchemy import select

PASSWORD = "Passw0rd1"


async def _register(client: AsyncClient, username: str) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD, "biometric_consent": True},
    )
    assert resp.status_code == 201


async def _login(client: AsyncClient, username: str) -> None:
    resp = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200


async def _make_photo(db_session, user_id: int, *, quality: float = 0.9) -> Photo:
    """Фото с валидными landmarks и разными точками у каждого владельца."""
    rng = np.random.default_rng(user_id * 7 + 1)
    photo = Photo(
        user_id=user_id,
        storage_path=f"users/{user_id}/mog{user_id}.png",
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
        quality_score=quality,
        is_public_in_battles=True,
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)
    return photo


@pytest.fixture
async def two_players(client: AsyncClient, db_session):
    """Два игрока с публичными фото и включённым участием в моггбатлах."""
    await _register(client, "mog_one")
    await client.post("/api/auth/logout")
    await _register(client, "mog_two")
    await client.post("/api/auth/logout")

    users = (await db_session.execute(select(User).order_by(User.id))).scalars().all()
    for u in users:
        u.show_in_battles = True
    await db_session.commit()
    for u in users:
        await _make_photo(db_session, u.id)
    return users


# --- Очки ---------------------------------------------------------------------


def test_points_favor_underdog() -> None:
    """Победа над сильным соперником даёт больше очков, чем над слабым."""
    weak = User(username="w", password_hash="x", pvp_points=1000)
    strong = User(username="s", password_hash="x", pvp_points=1600)
    upset_win, _ = mog._points_for(weak, strong, won=True)
    expected_win, _ = mog._points_for(strong, weak, won=True)
    assert upset_win > expected_win


def test_points_loss_is_bounded() -> None:
    """Проигравший не теряет больше WIN_POINTS — нет «обвала» рейтинга."""
    a = User(username="a", password_hash="x", pvp_points=2000)
    b = User(username="b", password_hash="x", pvp_points=0)
    _, loss = mog._points_for(a, b, won=True)
    assert loss >= -mog.WIN_POINTS


# --- API: очередь и приватность ------------------------------------------------


async def test_queue_requires_public_photo(client: AsyncClient, db_session) -> None:
    await _register(client, "mog_solo")
    user = (await db_session.execute(select(User))).scalar_one()
    user.show_in_battles = True
    await db_session.commit()
    resp = await client.post("/api/mogbattles/queue")
    assert resp.status_code == 409


async def test_queue_requires_opt_in(client: AsyncClient, db_session) -> None:
    """Без галочки «Показывать в моггбатлах» в очередь не пускаем."""
    await _register(client, "mog_noopt")
    user = (await db_session.execute(select(User))).scalar_one()
    await _make_photo(db_session, user.id)
    resp = await client.post("/api/mogbattles/queue")
    assert resp.status_code == 409


async def test_first_player_waits(client: AsyncClient, two_players) -> None:
    await _login(client, "mog_one")
    resp = await client.post("/api/mogbattles/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["step"] == "waiting"
    assert body["opponent"] is None


async def test_opponent_photo_hidden_until_ready(
    client: AsyncClient, two_players, db_session
) -> None:
    """Главный инвариант приватности: до обоюдной готовности фото не отдаётся."""
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    await client.post("/api/auth/logout")

    await _login(client, "mog_two")
    joined = await client.post("/api/mogbattles/queue")
    assert joined.status_code == 200
    body = joined.json()
    # Соперник найден, но ни он, ни я ещё не подтвердили готовность.
    assert body["step"] == "opponent_found"
    assert body["opponent"]["image_url"] is None
    assert body["opponent"]["photo_id"] is None
    assert body["opponent"]["blurred"] is True
    # Своё фото видно всегда — скрывать его от самого себя смысла нет.
    assert body["me"]["image_url"] is not None

    # Подтверждаю готовность первым — соперник ещё нет, фото всё ещё скрыто.
    partial = (await client.post(f"/api/mogbattles/rooms/{room_id}/ready")).json()
    assert partial["step"] == "opponent_found"
    assert partial["opponent"]["image_url"] is None


async def test_both_ready_reveals_result(client: AsyncClient, two_players) -> None:
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    await client.post("/api/auth/logout")

    await _login(client, "mog_two")
    await client.post("/api/mogbattles/queue")
    await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    await client.post("/api/auth/logout")

    # Первый игрок подтверждает — теперь оба готовы, результат раскрывается.
    await _login(client, "mog_one")
    revealed = (await client.post(f"/api/mogbattles/rooms/{room_id}/ready")).json()
    assert revealed["step"] == "revealed"
    assert revealed["me"]["image_url"] is not None
    assert revealed["opponent"]["image_url"] is not None
    assert revealed["me"]["score"] is not None
    assert revealed["opponent"]["score"] is not None


async def test_winner_gets_points_loser_loses(client: AsyncClient, two_players, db_session) -> None:
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    await client.post("/api/auth/logout")
    await _login(client, "mog_two")
    await client.post("/api/mogbattles/queue")
    await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    await client.post("/api/auth/logout")
    await _login(client, "mog_one")
    revealed = (await client.post(f"/api/mogbattles/rooms/{room_id}/ready")).json()

    db_session.expire_all()  # HTTP-запросы писали в БД из другой сессии
    users = (await db_session.execute(select(User))).scalars().all()
    if revealed["is_draw"]:
        assert sum(u.pvp_points for u in users) == 2 * mog.DRAW_POINTS
    else:
        assert revealed["winner_id"] is not None
        winner_points = next(u.pvp_points for u in users if u.id == revealed["winner_id"])
        loser_points = next(u.pvp_points for u in users if u.id != revealed["winner_id"])
        assert winner_points > 0
        # Проигравший теряет очки, но не уходит в минус.
        assert loser_points >= 0


async def test_room_hidden_from_outsider(client: AsyncClient, two_players, db_session) -> None:
    """Посторонний не видит чужую комнату — 404, без раскрытия чужих фото."""
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    await client.post("/api/auth/logout")
    await _register(client, "mog_intruder")
    resp = await client.get(f"/api/mogbattles/rooms/{room_id}")
    assert resp.status_code == 404


async def test_leave_room_cancels(client: AsyncClient, two_players, db_session) -> None:
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    resp = await client.delete(f"/api/mogbattles/rooms/{room_id}")
    assert resp.status_code == 204
    room = await db_session.get(MogRoom, room_id)
    await db_session.refresh(room)
    assert room.status == "cancelled"


async def test_finish_marks_room_finished(client: AsyncClient, two_players) -> None:
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    await client.post("/api/auth/logout")
    await _login(client, "mog_two")
    await client.post("/api/mogbattles/queue")
    await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    await client.post("/api/auth/logout")
    await _login(client, "mog_one")
    await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    done = (await client.post(f"/api/mogbattles/rooms/{room_id}/finish")).json()
    assert done["status"] == ROOM_FINISHED


async def test_leaderboard_and_me(client: AsyncClient, two_players, db_session) -> None:
    users = (await db_session.execute(select(User))).scalars().all()
    users[0].pvp_points = 120
    await db_session.commit()
    await _login(client, "mog_one")
    board = await client.get("/api/mogbattles/leaderboard")
    assert board.status_code == 200
    rows = board.json()
    assert any(r["pvp_points"] == 120 for r in rows)
    me = await client.get("/api/mogbattles/me")
    assert me.status_code == 200
    assert me.json()["pvp_points"] == 120
    assert me.json()["rank"] == 1


async def test_ready_after_finish_rejected(client: AsyncClient, two_players) -> None:
    await _login(client, "mog_one")
    room_id = (await client.post("/api/mogbattles/queue")).json()["room_id"]
    await client.post("/api/auth/logout")
    await _login(client, "mog_two")
    await client.post("/api/mogbattles/queue")
    await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    await client.post("/api/auth/logout")
    await _login(client, "mog_one")
    await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    await client.post(f"/api/mogbattles/rooms/{room_id}/finish")
    resp = await client.post(f"/api/mogbattles/rooms/{room_id}/ready")
    assert resp.status_code == 409


async def test_mogbattles_page_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/mogbattles")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=/mogbattles"


async def test_mogbattles_page_renders_for_user(client: AsyncClient, two_players) -> None:
    await _login(client, "mog_one")
    resp = await client.get("/mogbattles")
    assert resp.status_code == 200
    assert "Моггбатлы" in resp.text
    assert "/static/js/mogbattles.js" in resp.text


async def test_room_status_constants() -> None:
    """Константы статусов не должны разъезжаться с логикой сервиса."""
    assert (ROOM_WAITING, ROOM_REVEALED, ROOM_FINISHED) == ("waiting", "revealed", "finished")
