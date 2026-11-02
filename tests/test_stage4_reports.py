"""Тесты этапа 4: отчёты, агрегация, рекомендации, публичные ссылки, экспорт."""

from __future__ import annotations

import numpy as np
import pytest
from app.models.photo import Photo
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy import select


async def _register(client: AsyncClient, username: str = "reportuser") -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201


@pytest.fixture
async def seeded_photo(client: AsyncClient, db_session) -> int:
    """Фото с landmarks, распределёнными так, чтобы метрики имели разброс."""
    await _register(client)
    user = (await db_session.execute(select(User))).scalar_one()
    rng = np.random.default_rng(11)
    points = rng.uniform(0.15, 0.85, (106, 2)).tolist()
    photo = Photo(
        user_id=user.id,
        storage_path=f"users/{user.id}/r.png",
        content_type="image/png",
        size_bytes=2048,
        width=400,
        height=400,
        landmarks={"points": points, "provider": "manual", "bbox": [0.15, 0.15, 0.85, 0.85]},
        landmarks_provider="manual",
        is_aligned=True,
        quality_score=0.85,
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)
    return photo.id


async def test_create_report_returns_full_payload(client: AsyncClient, seeded_photo: int) -> None:
    resp = await client.post(f"/api/reports?photo_id={seeded_photo}")
    assert resp.status_code == 201
    body = resp.json()

    assert 0.0 <= body["overall_score"] <= 10.0
    assert body["categories_detail"]
    assert len(body["metrics"]) >= 10
    assert body["strengths"]
    assert body["growth"]
    assert body["disclaimer"]
    assert body["model_version"]


async def test_report_metrics_have_units_and_sources(
    client: AsyncClient, seeded_photo: int
) -> None:
    resp = await client.post(f"/api/reports?photo_id={seeded_photo}")
    metrics = resp.json()["metrics"]
    assert all(m["source"] for m in metrics)
    assert all(m["category_label"] for m in metrics)
    assert all("unit" in m for m in metrics)


async def test_report_recommendations_are_prioritized(
    client: AsyncClient, seeded_photo: int
) -> None:
    resp = await client.post(f"/api/reports?photo_id={seeded_photo}")
    recs = resp.json()["recommendations"]
    assert recs
    priorities = [r["priority"] for r in recs]
    assert priorities == sorted(priorities)
    assert priorities[0] == 1
    for rec in recs:
        assert rec["text"]
        assert rec["severity"] in {"low", "medium", "high"}


async def test_report_for_foreign_photo_returns_404(client: AsyncClient, seeded_photo: int) -> None:
    await client.post("/api/auth/logout")
    await _register(client, "intruder2")
    resp = await client.post(f"/api/reports?photo_id={seeded_photo}")
    assert resp.status_code == 404


async def test_report_history_and_detail(client: AsyncClient, seeded_photo: int) -> None:
    created = await client.post(f"/api/reports?photo_id={seeded_photo}")
    report_id = created.json()["id"]

    history = await client.get("/api/reports")
    assert history.status_code == 200
    assert history.json()["total"] == 1

    detail = await client.get(f"/api/reports/{report_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == report_id


async def test_report_detail_hidden_from_other_user(client: AsyncClient, seeded_photo: int) -> None:
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]
    await client.post("/api/auth/logout")
    await _register(client, "intruder3")
    assert (await client.get(f"/api/reports/{report_id}")).status_code == 404


async def test_public_share_flow(client: AsyncClient, seeded_photo: int) -> None:
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]

    shared = await client.patch(f"/api/reports/{report_id}/share", json={"is_public": True})
    assert shared.status_code == 200
    token = shared.json()["public_token"]
    assert token

    public = await client.get(f"/api/public/reports/{token}")
    assert public.status_code == 200
    body = public.json()
    # Публичный отчёт не содержит id/photo_id — персональные данные не утекают.
    assert "id" not in body
    assert "photo_id" not in body
    assert body["overall_score"] >= 0

    # Отзыв доступа закрывает публичную ссылку.
    await client.patch(f"/api/reports/{report_id}/share", json={"is_public": False})
    assert (await client.get(f"/api/public/reports/{token}")).status_code == 404


async def test_public_report_unknown_token(client: AsyncClient) -> None:
    assert (await client.get("/api/public/reports/does-not-exist")).status_code == 404


async def test_export_html(client: AsyncClient, seeded_photo: int) -> None:
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]
    resp = await client.get(f"/api/reports/{report_id}/export/html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "FaceMetric" in resp.text
    assert "Рекомендации" in resp.text


async def test_export_html_has_metric_gauges(client: AsyncClient, seeded_photo: int) -> None:
    """Печатная версия должна повторять веб: шкала на каждую метрику и категорию."""
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]
    resp = await client.get(f"/api/reports/{report_id}/export/html")
    assert resp.status_code == 200
    html = resp.text
    assert "linear-gradient" in html
    # Маркер значения рисуется по одному на метрику и на категорию.
    assert html.count("gauge-marker") >= 2
    assert "эталон:" in html


async def test_export_pdf_or_503(client: AsyncClient, seeded_photo: int) -> None:
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]
    resp = await client.get(f"/api/reports/{report_id}/export/pdf")
    # PDF доступен при установленном WeasyPrint, иначе честный 503.
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert resp.content.startswith(b"%PDF")


async def test_report_exposes_computation_variables(client: AsyncClient, seeded_photo: int) -> None:
    """Отчёт отдаёт переменные расчёта и вклад категорий для проверки вручную."""
    payload = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()
    computation = payload["computation"]

    variables = {v["key"]: v for v in computation["variables"]}
    assert "face_width" in variables
    assert variables["face_width"]["value"] > 0
    # У каждой переменной есть человекочитаемое название, не сырой ключ.
    assert variables["face_width"]["label"] != "face_width"

    contributions = computation["category_contributions"]
    assert contributions
    assert all(c["weight"] > 0 for c in contributions)


async def test_overall_score_equals_weighted_categories(
    client: AsyncClient, seeded_photo: int
) -> None:
    """Итог в отчёте сходится с суммой вкладов категорий, делённой на сумму весов."""
    payload = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()
    computation = payload["computation"]
    numerator = sum(c["contribution"] for c in computation["category_contributions"])
    denominator = computation["overall_denominator"]
    assert denominator > 0
    assert numerator / denominator == pytest.approx(payload["overall_score"], abs=0.01)


async def test_metrics_expose_tolerance_and_formula(client: AsyncClient, seeded_photo: int) -> None:
    """Каждая метрика объяснима: допуск, z-оценка и формула заполнены."""
    payload = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()
    metrics = {m["key"]: m for m in payload["metrics"]}

    canthal = metrics["canthal_tilt"]
    assert canthal["tolerance"] == pytest.approx(4.0)
    assert canthal["formula"]
    # z = |отклонение| / допуск.
    assert canthal["z_score"] == pytest.approx(
        abs(canthal["deviation"]) / canthal["tolerance"], abs=0.01
    )
    # Формула есть у всех метрик, включая композит гармонии.
    assert all(m["formula"] for m in payload["metrics"])


async def test_export_html_has_computation_block(client: AsyncClient, seeded_photo: int) -> None:
    """Печатная версия объясняет расчёт: вклад категорий и переменные."""
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]
    html = (await client.get(f"/api/reports/{report_id}/export/html")).text
    assert "Как посчитан итог" in html
    assert "Переменные расчёта" in html
    assert "face_width" in html
    assert "weight_category" in html


async def test_report_page_renders_for_user(client: AsyncClient, seeded_photo: int) -> None:
    resp = await client.get("/report")
    assert resp.status_code == 200
    assert "Отчёт об оценке" in resp.text


async def test_export_html_escapes_display_name(client: AsyncClient, seeded_photo: int) -> None:
    """Имя пользователя не должно становиться разметкой в экспортируемом отчёте.

    Отчёт собирается строковой интерполяцией, поэтому незаэкранированный
    display_name — это stored XSS в HTML-экспорте и инъекция в PDF.
    """
    payload = "<img src=x onerror=alert(1)>"
    assert (await client.patch("/api/profile", json={"display_name": payload})).status_code == 200
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]

    html = (await client.get(f"/api/reports/{report_id}/export/html")).text
    assert payload not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html

    pdf = await client.get(f"/api/reports/{report_id}/export/pdf")
    assert pdf.status_code in (200, 503)


async def test_report_page_redirects_guest(client: AsyncClient) -> None:
    resp = await client.get("/report", follow_redirects=False)
    assert resp.status_code == 302


def test_recommendations_are_deterministic() -> None:
    """Один и тот же ScoreResult даёт одинаковые рекомендации."""
    from app.ml.metrics import RawMetrics
    from app.ml.recommendations import build_recommendations
    from app.ml.scoring import score_metrics

    raw = RawMetrics()
    raw.values = {"facial_symmetry": 0.12, "canthal_tilt": -3.0}
    result = score_metrics(raw)
    first = build_recommendations(result)
    second = build_recommendations(result)
    assert [r.key for r in first] == [r.key for r in second]
