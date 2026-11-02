"""Тесты этапа 6: аккордеоны на странице отчёта.

Секции длинного отчёта свёрнуты в аккордеоны, кроме «Рекомендаций». Проверяем
структуру разметки и то, что состояние кнопки (aria-expanded) и класс секции
(is-open) согласованы: расхождение объявляло бы скринридеру открытую секцию
свёрнутой.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from app.models.photo import Photo
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy import select

PAGE = "/report"
EXPECTED_SECTIONS = {"gauges", "gallery", "table", "computation", "recommendations"}


@pytest.fixture
async def seeded_photo(client: AsyncClient, db_session) -> int:
    """Фото с landmarks — отчёт по нему строит полный набор секций."""
    resp = await client.post(
        "/api/auth/register",
        json={"username": "accuser", "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201
    user = (await db_session.execute(select(User))).scalar_one()
    rng = np.random.default_rng(31)
    points = rng.uniform(0.15, 0.85, (106, 2)).tolist()
    photo = Photo(
        user_id=user.id,
        storage_path=f"users/{user.id}/acc.png",
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


@pytest.fixture
async def report_page(client: AsyncClient, seeded_photo: int) -> str:
    resp = await client.get(PAGE)
    assert resp.status_code == 200
    return resp.text


async def test_report_page_has_all_accordions(report_page: str) -> None:
    found = set(re.findall(r'data-acc-id="([a-z]+)"', report_page))
    assert found == EXPECTED_SECTIONS


async def test_each_accordion_has_control_and_body(report_page: str) -> None:
    """У каждой секции ровно одна кнопка-заголовок и одно тело."""
    sections = re.findall(r"<section[^>]*data-accordion.*?</section>", report_page, re.S)
    assert len(sections) == len(EXPECTED_SECTIONS)
    for section in sections:
        assert section.count("data-acc-toggle") == 1
        assert section.count("data-acc-body") == 1
        # Приём анимации grid-template-rows требует одного ребёнка в теле.
        assert section.count("acc-body-inner") == 1


async def test_recommendations_open_by_default(report_page: str) -> None:
    """Рекомендации видны сразу — это главный вывод отчёта."""
    section = re.search(
        r'<section[^>]*data-acc-id="recommendations".*?</section>', report_page, re.S
    )
    assert section is not None
    assert "is-open" in section.group(0)
    assert 'aria-expanded="true"' in section.group(0)


async def test_other_sections_collapsed_by_default(report_page: str) -> None:
    for acc_id in ("gauges", "gallery", "table", "computation"):
        section = re.search(rf'<section[^>]*data-acc-id="{acc_id}".*?</section>', report_page, re.S)
        assert section is not None
        assert 'aria-expanded="false"' in section.group(0)
        assert "is-open" not in section.group(0).split(">", 1)[0]


async def test_aria_expanded_matches_is_open(report_page: str) -> None:
    """Состояние кнопки и класс секции не должны расходиться."""
    sections = re.findall(r"<section[^>]*data-accordion.*?</section>", report_page, re.S)
    for section in sections:
        header = section.split("</button>", 1)[0]
        open_by_class = "is-open" in section.split(">", 1)[0]
        open_by_aria = 'aria-expanded="true"' in header
        assert open_by_class == open_by_aria


async def test_toggle_is_a_button_for_keyboard(report_page: str) -> None:
    """Заголовок — настоящая кнопка: Enter/Space работают без своего JS."""
    toggles = re.findall(r"<button[^>]*data-acc-toggle[^>]*>", report_page)
    assert len(toggles) == len(EXPECTED_SECTIONS)
    for toggle in toggles:
        assert 'type="button"' in toggle


async def test_accordion_body_wrapped_once(report_page: str) -> None:
    """Тело аккордеона содержит ровно одну обёртку (иначе анимация не работает)."""
    bodies = re.findall(r'<div class="acc-body"[^>]*>(.*?)</div></div>', report_page, re.S)
    assert len(bodies) == len(EXPECTED_SECTIONS)
    assert all(body.count("acc-body-inner") == 1 for body in bodies)


async def test_accordion_assets_served(client: AsyncClient) -> None:
    js = await client.get("/static/js/report.js")
    assert js.status_code == 200
    assert "initAccordions" in js.text
    assert "data-accordion" in js.text

    css = await client.get("/static/css/main.css")
    assert css.status_code == 200
    assert ".acc.is-open" in css.text


async def test_public_report_uses_same_accordions(client: AsyncClient, seeded_photo: int) -> None:
    """Публичная страница отчёта отдаёт ту же разметку аккордеонов."""
    report_id = (await client.post(f"/api/reports?photo_id={seeded_photo}")).json()["id"]
    shared = await client.patch(f"/api/reports/{report_id}/share", json={"is_public": True})
    assert shared.status_code == 200
    token = shared.json()["public_token"]

    resp = await client.get(f"/r/{token}")
    assert resp.status_code == 200
    assert set(re.findall(r'data-acc-id="([a-z]+)"', resp.text)) == EXPECTED_SECTIONS
