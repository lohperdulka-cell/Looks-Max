"""Тесты этапа 3: загрузка фото, landmarks, предпросмотр метрик, доступ."""

from __future__ import annotations

import io

import numpy as np
import pytest
from app.models.photo import Photo
from httpx import AsyncClient
from PIL import Image

REGISTER = {"username": "photouser", "password": "Passw0rd1", "biometric_consent": True}


def _png_bytes(width: int = 300, height: int = 300, noise: bool = True) -> bytes:
    """Готовит PNG-байты. По умолчанию — шум (лицо не детектируется)."""
    rng = np.random.default_rng(0)
    if noise:
        arr = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    else:
        arr = np.full((height, width, 3), 128, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


async def _register(client: AsyncClient, username: str = "photouser") -> None:
    resp = await client.post("/api/auth/register", json={**REGISTER, "username": username})
    assert resp.status_code == 201


async def test_upload_requires_auth(client: AsyncClient) -> None:
    files = {"file": ("x.png", _png_bytes(), "image/png")}
    resp = await client.post("/api/photos", files=files)
    assert resp.status_code == 401


async def test_upload_rejects_non_image(client: AsyncClient) -> None:
    await _register(client)
    files = {"file": ("x.txt", b"not an image at all", "text/plain")}
    resp = await client.post("/api/photos", files=files)
    assert resp.status_code == 400


async def test_upload_rejects_image_without_face(client: AsyncClient) -> None:
    await _register(client)
    files = {"file": ("noise.png", _png_bytes(noise=True), "image/png")}
    resp = await client.post("/api/photos", files=files)
    # Лицо не найдено — 422, и файл не остаётся на диске.
    assert resp.status_code == 422
    assert "Лицо не найдено" in resp.json()["detail"]


async def test_photo_list_empty(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.get("/api/photos")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


@pytest.fixture
async def seeded_photo(client: AsyncClient, db_session) -> dict:
    """Создаёт фото с валидными landmarks напрямую в БД (без ML-детекции)."""
    await _register(client)
    from app.models.user import User
    from sqlalchemy import select

    user = (await db_session.execute(select(User))).scalar_one()

    # Синтетическое «лицо»: овал + характерные точки. Координаты нормированы 0..1.
    rng = np.random.default_rng(3)
    points = rng.uniform(0.2, 0.8, (106, 2)).tolist()

    photo = Photo(
        user_id=user.id,
        storage_path=f"users/{user.id}/seed.png",
        content_type="image/png",
        size_bytes=1024,
        width=300,
        height=300,
        landmarks={"points": points, "provider": "manual", "bbox": [0.2, 0.2, 0.8, 0.8]},
        landmarks_provider="manual",
        is_aligned=True,
        quality_score=0.9,
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)
    return {"photo_id": photo.id, "points": points}


async def test_preview_returns_metrics(client: AsyncClient, seeded_photo: dict) -> None:
    resp = await client.post(f"/api/photos/{seeded_photo['photo_id']}/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert 0.0 <= body["overall"] <= 10.0
    assert 0.0 <= body["harmony"] <= 10.0
    assert body["metrics"]
    assert body["strengths"]
    assert body["disclaimer"]


async def test_preview_forbidden_for_other_user(client: AsyncClient, seeded_photo: dict) -> None:
    await client.post("/api/auth/logout")
    await _register(client, "intruder")
    resp = await client.post(f"/api/photos/{seeded_photo['photo_id']}/preview")
    assert resp.status_code == 404


async def test_update_landmarks_marks_manual(client: AsyncClient, seeded_photo: dict) -> None:
    points = [[0.3, 0.3], [0.7, 0.3], [0.5, 0.6], [0.4, 0.7], [0.6, 0.7]]
    resp = await client.put(
        f"/api/photos/{seeded_photo['photo_id']}/landmarks",
        json={"points": points, "provider": "manual"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["landmarks_provider"] == "manual"
    assert len(body["landmarks"]["points"]) == len(points)


async def test_update_landmarks_rejects_out_of_range(
    client: AsyncClient, seeded_photo: dict
) -> None:
    points = [[5.0, 5.0], [0.2, 0.2], [0.3, 0.3], [0.4, 0.4], [0.5, 0.5]]
    resp = await client.put(
        f"/api/photos/{seeded_photo['photo_id']}/landmarks",
        json={"points": points, "provider": "manual"},
    )
    assert resp.status_code == 400


async def test_photo_visibility_toggle(client: AsyncClient, seeded_photo: dict) -> None:
    resp = await client.patch(
        f"/api/photos/{seeded_photo['photo_id']}",
        json={"is_public_in_battles": True, "is_hidden": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_public_in_battles"] is True


async def test_delete_photo(client: AsyncClient, seeded_photo: dict) -> None:
    photo_id = seeded_photo["photo_id"]
    resp = await client.delete(f"/api/photos/{photo_id}")
    assert resp.status_code == 204
    assert (await client.get(f"/api/photos/{photo_id}")).status_code == 404


async def test_rate_page_redirects_guest_to_login(client: AsyncClient) -> None:
    resp = await client.get("/rate", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=/rate"


async def test_rate_page_renders_for_user(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.get("/rate")
    assert resp.status_code == 200
    assert "Точки лица" in resp.text


async def test_harmony_is_not_stuck_at_zero(client: AsyncClient, seeded_photo: dict) -> None:
    """Гармония должна быть непрерывной, а не обнуляться при типичном разбросе."""
    resp = await client.post(f"/api/photos/{seeded_photo['photo_id']}/preview")
    body = resp.json()
    assert 0.0 < body["harmony"] <= 10.0


def test_index_map_selected_by_point_count() -> None:
    """Карта точек выбирается по числу точек, а не по провайдеру (регрессия)."""
    import numpy as np
    from app.ml.landmarks import FaceLandmarks
    from app.ml.metrics import _index_map

    rng = np.random.default_rng(1)
    if_map = _index_map(FaceLandmarks(points=rng.random((106, 2)), provider="insightface"))
    manual_map = _index_map(FaceLandmarks(points=rng.random((106, 2)), provider="manual"))
    mp_map = _index_map(FaceLandmarks(points=rng.random((478, 2)), provider="mediapipe"))

    assert if_map == manual_map
    assert if_map["nose_tip"] == 86
    assert mp_map["nose_tip"] != 86
    assert max(if_map.values()) < 106
