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


def _png_header_bomb(width: int, height: int) -> bytes:
    """PNG с крошечным телом, но гигантскими размерами в заголовке."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * (width * 3 + 1)))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("side", [12000, 20000])
async def test_upload_rejects_decompression_bomb(client: AsyncClient, side: int) -> None:
    """Маленький файл с гигантским кадром не должен занимать память.

    115 байт разворачиваются в сотни МБ RGB при декодировании. Оба порога
    Pillow (предупреждение и исключение) отсекаются как 400, а не 500.
    """
    await _register(client, username=f"bomb{side}")
    data = _png_header_bomb(side, side)
    assert len(data) < 4096  # «бомба» действительно маленькая
    files = {"file": ("bomb.png", data, "image/png")}
    resp = await client.post("/api/photos", files=files)
    assert resp.status_code == 400
    assert "большое" in resp.json()["detail"]


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


def _synthetic_mediapipe_points() -> object:
    """Симметричный «идеальный» анфас в схеме MediaPipe (478 точек).

    Все зеркальные пары расставлены строго симметрично относительно x=0.5, так
    что любая ненулевая асимметрия — дефект расчёта, а не лица.
    """
    import numpy as np

    pts = np.full((478, 2), 0.5, dtype=np.float64)
    # (индекс, x, y) — левые и правые точки заданы зеркально.
    explicit = {
        33: (0.35, 0.50),
        133: (0.40, 0.50),
        362: (0.60, 0.50),
        263: (0.65, 0.50),
        10: (0.50, 0.10),
        152: (0.50, 0.90),
        172: (0.30, 0.75),
        397: (0.70, 0.75),
        234: (0.28, 0.50),
        454: (0.72, 0.50),
        61: (0.45, 0.72),
        291: (0.55, 0.72),
        0: (0.50, 0.70),
        17: (0.50, 0.74),
        1: (0.50, 0.60),
        98: (0.47, 0.60),
        327: (0.53, 0.60),
        2: (0.50, 0.66),
    }
    for i, (x, y) in explicit.items():
        pts[i] = (x, y)
    # Прочие зеркальные пары — на равных расстояниях от оси.
    for left, right, d, y in [
        (159, 386, 0.10, 0.46),
        (145, 374, 0.12, 0.44),
        (46, 276, 0.08, 0.55),
        (53, 283, 0.10, 0.52),
        (70, 300, 0.14, 0.40),
        (63, 293, 0.09, 0.42),
        (132, 361, 0.11, 0.47),
        (58, 288, 0.13, 0.53),
        (136, 365, 0.12, 0.45),
        (150, 379, 0.10, 0.48),
    ]:
        pts[left] = (0.5 - d, y)
        pts[right] = (0.5 + d, y)
    return pts


def test_mediapipe_symmetry_axis_uses_own_contour() -> None:
    """Ось симметрии берёт контур схемы, а не срез 0..32 (регрессия).

    Раньше для MediaPipe срез rp[0:33] — это не контур, а произвольные точки,
    и ось уезжала на десятки пикселей: у симметричного лица выходила
    асимметрия ~0.23 и балл 0.0.
    """
    from app.ml.landmarks import FaceLandmarks
    from app.ml.metrics import compute_raw_metrics

    lm = FaceLandmarks(
        points=_synthetic_mediapipe_points(), provider="mediapipe", image_size=(1000, 1000)
    )
    raw = compute_raw_metrics(lm)
    assert raw.values["facial_symmetry"] < 0.02


def test_mediapipe_lip_height_is_not_zero() -> None:
    """Высота губ считается по внешним точкам, а не по совпадающим 13/14."""
    from app.ml.landmarks import FaceLandmarks
    from app.ml.metrics import compute_raw_metrics

    lm = FaceLandmarks(
        points=_synthetic_mediapipe_points(), provider="mediapipe", image_size=(1000, 1000)
    )
    raw = compute_raw_metrics(lm)
    assert raw.values["mouth_proportion"] < 30.0
    assert raw.values["mouth_proportion"] > 0.5


# --- Безопасность путей к медиафайлам ----------------------------------------


def test_resolve_media_path_rejects_sibling_prefix_dir() -> None:
    """Путь с выходом в соседний каталог с общим префиксом отклоняется.

    Строковая проверка startswith('/media') пропускала '/media_evil': такой
    относительный путь увёл бы файл за пределы media_root.
    """
    from app.core.storage import resolve_media_path
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        resolve_media_path("../media_evil/x.png")
    assert exc.value.status_code == 400


def test_resolve_media_path_accepts_inside_root() -> None:
    """Обычный путь внутри media_root резолвится без ошибки."""
    from app.core.storage import resolve_media_path

    resolved = resolve_media_path("users/1/photo.png")
    assert resolved.name == "photo.png"
