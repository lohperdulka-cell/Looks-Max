"""Тесты этапа 2: мастер ручной разметки и RBF-интерполяция меша."""

from __future__ import annotations

import numpy as np
import pytest
from app.models.photo import Photo
from httpx import AsyncClient

REGISTER = {"username": "wizarduser", "password": "Passw0rd1", "biometric_consent": True}


async def _register(client: AsyncClient, username: str = "wizarduser") -> None:
    resp = await client.post("/api/auth/register", json={**REGISTER, "username": username})
    assert resp.status_code == 201


@pytest.fixture
async def seeded_photo(client: AsyncClient, db_session) -> dict:
    """Фото с 106 синтетическими точками (создаётся напрямую в БД)."""
    await _register(client)
    from app.models.user import User
    from sqlalchemy import select

    user = (await db_session.execute(select(User))).scalar_one()
    rng = np.random.default_rng(5)
    points = rng.uniform(0.2, 0.8, (106, 2)).tolist()
    photo = Photo(
        user_id=user.id,
        storage_path=f"users/{user.id}/wiz.png",
        content_type="image/png",
        size_bytes=1024,
        width=400,
        height=500,
        landmarks={"points": points, "provider": "insightface", "bbox": [0.2, 0.2, 0.8, 0.8]},
        landmarks_provider="insightface",
        is_aligned=True,
        quality_score=0.9,
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)
    return {"photo_id": photo.id, "points": points}


# --- Интерполяция (чистая математика) ----------------------------------------


def test_interpolation_is_identity_on_canonical_anchors() -> None:
    """Канонические якоря -> интерполяция возвращает исходный шаблон.

    Это главный инвариант: если пользователь поставил точки «как в шаблоне»,
    деформация не должна ничего сдвигать (иначе появилось бы систематическое
    искажение всех метрик).
    """
    from app.ml.demo_landmarks import _TEMPLATE
    from app.ml.wizard import canonical_anchor_positions, interpolate_mesh

    mesh = interpolate_mesh(canonical_anchor_positions())
    err = float(np.abs(mesh - np.asarray(_TEMPLATE)).max())
    assert err < 1e-9


def test_interpolation_follows_global_shift() -> None:
    """Общий сдвиг якорей переносится на весь меш ровно (аффинная часть TPS)."""
    from app.ml.demo_landmarks import _TEMPLATE
    from app.ml.wizard import canonical_anchor_positions, interpolate_mesh

    shifted = {k: [x + 0.03, y - 0.02] for k, (x, y) in canonical_anchor_positions().items()}
    mesh = interpolate_mesh(shifted)
    dx = mesh[:, 0] - np.asarray(_TEMPLATE)[:, 0]
    dy = mesh[:, 1] - np.asarray(_TEMPLATE)[:, 1]
    assert dx == pytest.approx(0.03, abs=1e-9)
    assert dy == pytest.approx(-0.02, abs=1e-9)


def test_interpolation_local_change_stays_local() -> None:
    """Сдвиг одной точки не должен двигать противоположный край лица.

    Иначе правка подбородка «тянула» бы лоб: RBF с аффинной частью держит
    дальние точки почти на месте.
    """
    from app.ml import metrics as m
    from app.ml.demo_landmarks import _TEMPLATE
    from app.ml.wizard import canonical_anchor_positions, interpolate_mesh

    anchors = canonical_anchor_positions()
    # Поднимаем подбородок вверх (короче нижняя треть): y уменьшаем.
    anchors["chin"] = [anchors["chin"][0], anchors["chin"][1] - 0.06]
    mesh = interpolate_mesh(anchors)
    template = np.asarray(_TEMPLATE)

    # Верхние точки шаблона (лоб/брови, y < 0.35) почти не смещаются.
    top = template[:, 1] < 0.35
    top_shift = float(np.abs(mesh[top] - template[top]).max())
    assert top_shift < 0.02

    # Сам подбородок сместился заметно (вверх).
    chin_idx = m.IF_CHIN
    assert abs(mesh[chin_idx, 1] - template[chin_idx, 1]) > 0.01


def test_interpolation_requires_all_anchors() -> None:
    """Неполный набор якорей — понятная ошибка, а не мусорный меш."""
    from app.ml.wizard import canonical_anchor_positions, interpolate_mesh

    anchors = canonical_anchor_positions()
    anchors.pop("chin")
    with pytest.raises(ValueError, match="Не заданы ключевые точки"):
        interpolate_mesh(anchors)


def test_interpolation_handles_coincident_points() -> None:
    """Совпавшие якоря не роняют solve (регуляризация TPS)."""
    from app.ml.wizard import canonical_anchor_positions, interpolate_mesh

    anchors = canonical_anchor_positions()
    anchors["nose_left"] = list(anchors["nose_right"])
    mesh = interpolate_mesh(anchors)
    assert mesh.shape == (106, 2)
    assert np.isfinite(mesh).all()


def test_detect_anchor_points_returns_none_for_mediapipe_scheme() -> None:
    """Для 468-точечной схемы автопозиции не отдаём: индексы другие."""
    from app.ml.wizard import detect_anchor_points

    assert detect_anchor_points(np.zeros((468, 2))) is None
    assert detect_anchor_points(np.zeros((68, 2))) is None
    assert detect_anchor_points(np.zeros((106, 2))) is not None


def test_interpolated_mesh_produces_metrics() -> None:
    """Меш из мастера пригоден для конвейера метрик без изменений."""
    from app.ml.landmarks import FaceLandmarks
    from app.ml.metrics import compute_raw_metrics
    from app.ml.wizard import canonical_anchor_positions, interpolate_mesh

    mesh = interpolate_mesh(canonical_anchor_positions())
    lm = FaceLandmarks(points=mesh, provider="manual", image_size=(1000, 1000))
    raw = compute_raw_metrics(lm, gender="male")
    assert raw.values
    assert all(np.isfinite(v) for v in raw.values.values())


# --- API мастера --------------------------------------------------------------


async def test_wizard_start_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/photos/1/wizard")
    assert resp.status_code == 401


async def test_wizard_start_returns_16_anchors(client: AsyncClient, seeded_photo: dict) -> None:
    resp = await client.get(f"/api/photos/{seeded_photo['photo_id']}/wizard")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["anchors"]) == 16
    keys = {a["key"] for a in body["anchors"]}
    assert {"left_pupil", "chin", "left_gonion", "right_gonion"} <= keys
    for anchor in body["anchors"]:
        assert anchor["label"] and anchor["hint"]
        assert len(anchor["canonical"]) == 2
    assert body["image_url"].endswith("/image")


async def test_wizard_start_rejects_foreign_photo(client: AsyncClient, seeded_photo: dict) -> None:
    await _register(client, "wizardintruder")
    resp = await client.get(f"/api/photos/{seeded_photo['photo_id']}/wizard")
    assert resp.status_code == 404


async def test_wizard_interpolate_returns_full_mesh(
    client: AsyncClient, seeded_photo: dict
) -> None:
    from app.ml.wizard import canonical_anchor_positions

    anchors = canonical_anchor_positions()
    resp = await client.post(
        f"/api/photos/{seeded_photo['photo_id']}/wizard",
        json={"anchors": anchors},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["points"]) == 106
    assert body["provider"] == "manual"


async def test_wizard_interpolate_rejects_unknown_anchor(
    client: AsyncClient, seeded_photo: dict
) -> None:
    from app.ml.wizard import canonical_anchor_positions

    anchors = canonical_anchor_positions()
    anchors["left_ear"] = [0.1, 0.1]
    resp = await client.post(
        f"/api/photos/{seeded_photo['photo_id']}/wizard",
        json={"anchors": anchors},
    )
    assert resp.status_code == 400
    assert "Неизвестные ключевые точки" in resp.json()["detail"]


async def test_wizard_interpolate_rejects_out_of_frame_point(
    client: AsyncClient, seeded_photo: dict
) -> None:
    from app.ml.wizard import canonical_anchor_positions

    anchors = canonical_anchor_positions()
    anchors["chin"] = [5.0, 5.0]
    resp = await client.post(
        f"/api/photos/{seeded_photo['photo_id']}/wizard",
        json={"anchors": anchors},
    )
    assert resp.status_code == 400


async def test_wizard_interpolate_rejects_foreign_photo(
    client: AsyncClient, seeded_photo: dict
) -> None:
    from app.ml.wizard import canonical_anchor_positions

    await _register(client, "wizardintruder2")
    resp = await client.post(
        f"/api/photos/{seeded_photo['photo_id']}/wizard",
        json={"anchors": canonical_anchor_positions()},
    )
    assert resp.status_code == 404
