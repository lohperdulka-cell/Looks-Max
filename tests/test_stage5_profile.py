"""Тесты этапа 5: профиль, статистика, прогресс, до/после, достижения, streak."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from app.models.photo import Photo
from app.models.user import User
from app.services.achievements import (
    ACHIEVEMENT_CATALOG,
    AchievementStats,
    progress_ratio,
)
from app.services.profile import compute_streak
from httpx import AsyncClient
from sqlalchemy import select


async def _register(client: AsyncClient, username: str = "profileuser") -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201


@pytest.fixture
async def photo_with_landmarks(client: AsyncClient, db_session) -> int:
    await _register(client)
    user = (await db_session.execute(select(User))).scalar_one()
    rng = np.random.default_rng(5)
    photo = Photo(
        user_id=user.id,
        storage_path=f"users/{user.id}/p.png",
        content_type="image/png",
        size_bytes=1024,
        width=400,
        height=400,
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
    return photo.id


async def test_profile_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/profile")).status_code == 401


async def test_profile_stats_after_registration(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "profileuser"
    assert body["stats"]["reports_count"] == 0
    assert body["stats"]["photos_count"] == 0


async def test_profile_update(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.patch(
        "/api/profile",
        json={"display_name": "Иван", "gender": "male", "birth_year": 1995},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Иван"
    assert body["gender"] == "male"
    assert body["birth_year"] == 1995


async def test_profile_update_rejects_bad_gender(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.patch("/api/profile", json={"gender": "martian"})
    assert resp.status_code == 422


async def test_stats_track_reports(client: AsyncClient, photo_with_landmarks: int) -> None:
    for _ in range(2):
        created = await client.post(f"/api/reports?photo_id={photo_with_landmarks}")
        assert created.status_code == 201

    resp = await client.get("/api/profile")
    stats = resp.json()["stats"]
    assert stats["reports_count"] == 2
    assert stats["best_score"] > 0
    assert stats["latest_score"] > 0


async def test_progress_series_has_gaps_not_zeros(
    client: AsyncClient, photo_with_landmarks: int
) -> None:
    await client.post(f"/api/reports?photo_id={photo_with_landmarks}")
    resp = await client.get("/api/profile/progress?days=14")
    assert resp.status_code == 200
    points = resp.json()["points"]
    assert len(points) == 14
    # Сегодня есть отчёт, прошлые дни — None (разрыв), а не 0.
    assert points[-1]["overall_score"] is not None
    assert any(p["overall_score"] is None for p in points)


async def test_before_after_requires_two_reports(
    client: AsyncClient, photo_with_landmarks: int
) -> None:
    await client.post(f"/api/reports?photo_id={photo_with_landmarks}")
    resp = await client.get("/api/profile/before-after")
    assert resp.status_code == 409


async def test_before_after_with_two_reports(
    client: AsyncClient, photo_with_landmarks: int
) -> None:
    first = (await client.post(f"/api/reports?photo_id={photo_with_landmarks}")).json()
    second = (await client.post(f"/api/reports?photo_id={photo_with_landmarks}")).json()
    resp = await client.get("/api/profile/before-after")
    assert resp.status_code == 200
    body = resp.json()
    assert body["before_report_id"] == first["id"]
    assert body["after_report_id"] == second["id"]
    assert body["metrics"]


async def test_achievements_listed_with_progress(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.get("/api/profile/achievements")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == len(ACHIEVEMENT_CATALOG)
    assert all("progress" in item for item in items)
    assert all(item["earned"] is False for item in items)


async def test_achievement_awarded_after_report(
    client: AsyncClient, photo_with_landmarks: int
) -> None:
    await client.post(f"/api/reports?photo_id={photo_with_landmarks}")
    resp = await client.get("/api/profile/achievements")
    earned = {item["code"] for item in resp.json() if item["earned"]}
    assert "first_report" in earned


async def test_streak_increments_on_activity(client: AsyncClient) -> None:
    await _register(client)
    await client.patch("/api/profile", json={"display_name": "Тест"})
    resp = await client.get("/api/profile")
    assert resp.json()["streak_days"] == 1


def test_compute_streak_rules() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assert compute_streak(None, 0, now) == 1
    assert compute_streak(now - timedelta(hours=2), 3, now) == 3
    assert compute_streak(now - timedelta(days=1), 3, now) == 4
    assert compute_streak(now - timedelta(days=2), 9, now) == 1


def test_achievement_progress_ratio() -> None:
    stats = AchievementStats(reports_count=5)
    assert progress_ratio("reports_5", stats) == 1.0
    assert progress_ratio("reports_20", stats) == 0.25
    assert progress_ratio("unknown_code", stats) == 0.0


def test_catalog_codes_are_unique() -> None:
    codes = [spec.code for spec in ACHIEVEMENT_CATALOG]
    assert len(codes) == len(set(codes))
