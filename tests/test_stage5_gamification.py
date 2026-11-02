"""Тесты этапа 5: геймификация профиля — уровни, XP, задания, карта активности."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from app.models.photo import Photo
from app.models.report import Report
from app.models.user import User
from app.services import gamification as g
from httpx import AsyncClient
from sqlalchemy import select

PASSWORD = "Passw0rd1"


async def _register(client: AsyncClient, username: str) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD, "biometric_consent": True},
    )
    assert resp.status_code == 201


# --- Чистая математика уровней ------------------------------------------------


def test_level_one_at_zero_xp() -> None:
    info = g.level_info(0)
    assert info.level == 1
    assert info.title == "Новичок"
    assert info.progress == 0.0


def test_xp_for_level_is_monotonic() -> None:
    previous = -1
    for level in range(1, 40):
        value = g.xp_for_level(level)
        assert value > previous
        previous = value


def test_level_boundaries() -> None:
    """Ровно на пороге уровень повышается, на единицу меньше — ещё нет."""
    for level in range(2, 15):
        need = g.xp_for_level(level)
        assert g.level_from_xp(need) == level
        assert g.level_from_xp(need - 1) == level - 1


def test_level_progress_within_unit() -> None:
    need = g.xp_for_level(5)
    info = g.level_info(need)
    assert info.level == 5
    assert 0.0 <= info.progress <= 1.0
    assert info.xp_for_next > 0


def test_negative_xp_clamped() -> None:
    assert g.level_from_xp(-100) == 1
    assert g.level_info(-5).xp == 0


def test_rank_titles_ordered() -> None:
    assert g.rank_title(1) == "Новичок"
    assert g.rank_title(3) == "Практик"
    assert g.rank_title(5) == "Продвинутый"
    assert g.rank_title(8) == "Эксперт"
    assert g.rank_title(50) == "Мастер"


def test_xp_breakdown_total() -> None:
    b = g.XpBreakdown(photos=2, reports=1, exercises=3, votes=4, mog_wins=1)
    assert b.total == (
        2 * g.XP_PHOTO + 1 * g.XP_REPORT + 3 * g.XP_EXERCISE + 4 * g.XP_VOTE + 1 * g.XP_MOG_WIN
    )


def test_activity_intensity_bands() -> None:
    assert g.ActivityDay(day=datetime.now(UTC).date()).intensity == 0
    assert g.ActivityDay(day=datetime.now(UTC).date(), reports=1).intensity == 1
    assert g.ActivityDay(day=datetime.now(UTC).date(), reports=5).intensity == 2
    assert g.ActivityDay(day=datetime.now(UTC).date(), reports=9).intensity == 3
    assert g.ActivityDay(day=datetime.now(UTC).date(), reports=30).intensity == 4


# --- API ---------------------------------------------------------------------


@pytest.fixture
async def player(client: AsyncClient, db_session):
    """Пользователь с одним фото и одним отчётом."""
    await _register(client, "gamer")
    user = (await db_session.execute(select(User))).scalar_one()
    rng = np.random.default_rng(3)
    photo = Photo(
        user_id=user.id,
        storage_path="users/1/g.png",
        content_type="image/png",
        size_bytes=1024,
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
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)
    report = Report(
        user_id=user.id,
        photo_id=photo.id,
        overall_score=6.5,
        harmony_score=5.0,
        is_public=False,
    )
    db_session.add(report)
    await db_session.commit()
    return user


async def test_gamification_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/profile/gamification")
    assert resp.status_code in (401, 403)


async def test_gamification_shape(client: AsyncClient, player) -> None:
    await client.post("/api/auth/login", json={"username": "gamer", "password": PASSWORD})
    resp = await client.get("/api/profile/gamification")
    assert resp.status_code == 200
    body = resp.json()
    assert body["level"]["level"] >= 1
    assert body["total_xp"] == g.XP_PHOTO + g.XP_REPORT
    assert len(body["quests"]) == len(g.DAILY_QUESTS)
    assert len(body["activity"]) == 30
    # Загрузка фото и отчёт были сегодня — оба дневных задания выполнены.
    done = {q["code"]: q["completed"] for q in body["quests"]}
    assert done["photo_today"] is True
    assert done["report_today"] is True


async def test_xp_sources_match_actions(client: AsyncClient, player) -> None:
    await client.post("/api/auth/login", json={"username": "gamer", "password": PASSWORD})
    body = (await client.get("/api/profile/gamification")).json()
    by_code = {s["code"]: s for s in body["sources"]}
    assert by_code["photos"]["xp"] == g.XP_PHOTO
    assert by_code["reports"]["xp"] == g.XP_REPORT
    # Пустые источники в ответ не попадают.
    assert "votes" not in by_code


async def test_activity_reflects_today(client: AsyncClient, player) -> None:
    await client.post("/api/auth/login", json={"username": "gamer", "password": PASSWORD})
    body = (await client.get("/api/profile/gamification")).json()
    today = body["activity"][-1]
    assert today["breakdown"]["photos"] == 1
    assert today["breakdown"]["reports"] == 1
    assert today["total"] == 2
    assert body["active_days"] == 1


async def test_quest_progress_clamped_to_target(client: AsyncClient, player, db_session) -> None:
    """Прогресс задания не превышает цель, даже если действий больше."""
    user = player
    rng = np.random.default_rng(9)
    for index in range(4):
        db_session.add(
            Photo(
                user_id=user.id,
                storage_path=f"users/{user.id}/extra{index}.png",
                content_type="image/png",
                size_bytes=1024,
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
            )
        )
    await db_session.commit()

    await client.post("/api/auth/login", json={"username": "gamer", "password": PASSWORD})
    body = (await client.get("/api/profile/gamification")).json()
    for quest in body["quests"]:
        assert quest["done"] <= quest["target"]
    # Фото сегодня больше цели задания — прогресс упирается в 1/1, а не в 5/1.
    photo_quest = next(q for q in body["quests"] if q["code"] == "photo_today")
    assert photo_quest["done"] == photo_quest["target"] == 1
    # А опыт при этом растёт от всех фото, не только от одного.
    assert body["total_xp"] == 5 * g.XP_PHOTO + g.XP_REPORT


async def test_same_day_actions_counted_separately(client: AsyncClient, player, db_session) -> None:
    """Несколько действий за один день дают счётчик > 1 (регрессия: было 1).

    Раньше heatmap группировал по колонке-времени, а у записей разные секунды,
    поэтому «группа» совпадала с записью и день всегда показывал единицу.
    """
    user = player
    rng = np.random.default_rng(21)
    for index in range(2):
        db_session.add(
            Photo(
                user_id=user.id,
                storage_path=f"users/{user.id}/same{index}.png",
                content_type="image/png",
                size_bytes=1024,
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
            )
        )
    await db_session.commit()

    await client.post("/api/auth/login", json={"username": "gamer", "password": PASSWORD})
    body = (await client.get("/api/profile/gamification")).json()
    today = body["activity"][-1]
    # 1 фото из фикстуры + 2 добавленных = 3 за сегодня.
    assert today["breakdown"]["photos"] == 3
    assert today["total"] == 4  # плюс отчёт из фикстуры


async def test_gamification_is_per_user(client: AsyncClient, player) -> None:
    """У нового пользователя нет чужого опыта."""
    await _register(client, "fresh")
    body = (await client.get("/api/profile/gamification")).json()
    assert body["total_xp"] == 0
    assert body["level"]["level"] == 1
    assert body["sources"] == []
    assert body["active_days"] == 0


async def test_profile_page_has_gamification_blocks(client: AsyncClient, player) -> None:
    await client.post("/api/auth/login", json={"username": "gamer", "password": PASSWORD})
    resp = await client.get("/profile")
    assert resp.status_code == 200
    for marker in ("data-level-card", "data-quests-card", "data-heatmap", "data-xp-sources"):
        assert marker in resp.text


def test_day_start_truncates_to_midnight() -> None:
    """Начало дня обнуляет время — иначе счётчики «за сегодня» захватят вчера."""
    start = g._day_start(datetime(2026, 1, 2, 15, 30, 45, tzinfo=UTC))
    assert (start.hour, start.minute, start.second, start.microsecond) == (0, 0, 0, 0)


def test_streak_first_day_no_bonus() -> None:
    """Первый день серии не даёт XP — иначе «серия» награждала бы за пустоту."""
    assert g.XpBreakdown(streak_days=0).total == 0
    assert g.XpBreakdown(streak_days=3).total == 3 * g.XP_STREAK_DAY


def test_quests_have_unique_codes() -> None:
    codes = [q.code for q in g.DAILY_QUESTS]
    assert len(codes) == len(set(codes))
    assert all(q.target > 0 and q.xp_reward > 0 for q in g.DAILY_QUESTS)


def test_level_info_next_xp_consistency() -> None:
    """xp_into_level + xp_for_next = полный интервал уровня."""
    info = g.level_info(g.xp_for_level(4) + 30)
    span = g.xp_for_level(5) - g.xp_for_level(4)
    assert info.xp_into_level + info.xp_for_next == span
