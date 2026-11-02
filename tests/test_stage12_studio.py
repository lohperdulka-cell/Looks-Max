"""Тесты переработки модуля оценки: студия разметки, нижняя навигация, звук.

Проверяем то, что видно снаружи и что легко сломать незаметно: состав нижней
навигации, отсутствие её в админке, наличие студии на странице оценки и то, что
старый «сканирующий» редактор с широкими точками действительно удалён.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

REGISTER = {"username": "studio", "password": "Passw0rd1", "biometric_consent": True}


async def _register(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/register", json=REGISTER)
    assert resp.status_code == 201


# --- Нижняя навигация ---------------------------------------------------------


async def test_bottom_nav_has_five_product_sections(client: AsyncClient) -> None:
    """Ровно пять пунктов: Оценка, Моггбатлы, ИИ-Бот, Упражнения, Профиль."""
    html = (await client.get("/")).text
    assert 'class="bottom-nav"' in html
    for href in ("/mogbattles", "/chat", "/rate", "/exercises", "/profile"):
        assert f'href="{href}"' in html
    assert html.count("bottom-nav-label") == 5


async def test_bottom_nav_uses_svg_icons(client: AsyncClient) -> None:
    """Иконки — SVG, а не эмодзи: эмодзи рисуются разными шрифтами и «плывут»."""
    html = (await client.get("/")).text
    nav = html[
        html.index('class="bottom-nav"') : html.index("</nav>", html.index('class="bottom-nav"'))
    ]
    assert nav.count("<svg") == 5
    # Старые эмодзи-глифы не должны вернуться.
    for glyph in ("⌂", "⚔", "◉", "✚", "☺"):
        assert glyph not in nav


async def test_bottom_nav_marks_active_section(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.get("/rate")
    assert resp.status_code == 200
    assert 'aria-current="page"' in resp.text


async def test_bottom_nav_absent_in_admin(client: AsyncClient) -> None:
    """В админке навигация продукта не нужна: там свои вкладки."""
    await _register(client)
    # Первому пользователю выдаём права администратора.
    from app.db import SessionLocal
    from app.models.user import User
    from sqlalchemy import update

    async with SessionLocal() as session:
        await session.execute(update(User).values(is_admin=True))
        await session.commit()

    html = (await client.get("/admin")).text
    assert 'class="bottom-nav"' not in html


# --- Студия разметки ----------------------------------------------------------


async def test_rate_page_loads_studio_not_old_editor(client: AsyncClient) -> None:
    """Страница оценки подключает студию и больше не грузит старый редактор."""
    await _register(client)
    html = (await client.get("/rate")).text
    assert "/static/js/studio.js" in html
    assert "data-open-studio" in html
    # Старые скрипты с широкими точками и «сканированием» удалены.
    assert "wizard.js" not in html
    assert "scan-anim.js" not in html
    assert "face-overlay.js" not in html


async def test_old_editor_assets_are_gone(client: AsyncClient) -> None:
    """Файлы старого редактора должны отвечать 404, а не висеть мёртвым грузом."""
    for path in ("/static/js/wizard.js", "/static/js/scan-anim.js"):
        assert (await client.get(path)).status_code == 404


async def test_studio_script_defines_studio_api(client: AsyncClient) -> None:
    resp = await client.get("/static/js/studio.js")
    assert resp.status_code == 200
    assert "window.fmStudio" in resp.text
    # Три шага мастера: совмещение, разметка, результат.
    for step in ("align", "mark", "done"):
        assert f"'{step}'" in resp.text


async def test_studio_points_are_small_circles(client: AsyncClient) -> None:
    """Точки разметки — маленькие кружки, а не широкие кляксы.

    Радиус задаётся от ширины кадра (nw / 210), а не фиксированным числом:
    иначе на большом фото точки выглядят крошечными, а на маленьком — огромными.
    """
    js = (await client.get("/static/js/studio.js")).text
    assert "nw / 210" in js


async def test_studio_has_no_text_over_photo(client: AsyncClient) -> None:
    """Подсказки живут в панели под сценой, а не поверх лица."""
    html = (await client.get("/rate")).text
    assert "canvas-hint" not in html


async def test_studio_css_is_shipped(client: AsyncClient) -> None:
    css = (await client.get("/static/css/main.css")).text
    for token in (".studio-stage", ".studio-overlay", ".studio-nudge", ".studio-progress"):
        assert token in css
    # Старый оверлей мастера удалён вместе с редактором.
    assert ".wizard-dialog" not in css


async def test_wizard_api_returns_sixteen_anchors(client: AsyncClient, seeded_photo: int) -> None:
    """Студия по-прежнему получает 16 шагов разметки от бэкенда."""
    resp = await client.get(f"/api/photos/{seeded_photo}/wizard")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["anchors"]) == 16
    assert all("canonical" in a for a in body["anchors"])


# --- Звук и плашка «MOGGED!» --------------------------------------------------


async def test_mogged_sound_is_local_and_served(client: AsyncClient) -> None:
    """Звук лежит локально: CSP default-src 'self' блокирует внешние медиа."""
    await _register(client)
    html = (await client.get("/mogbattles")).text
    assert "/static/audio/mogged.wav" in html
    resp = await client.get("/static/audio/mogged.wav")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/")


async def test_mogged_stamp_is_red_text_with_black_outline(client: AsyncClient) -> None:
    """Плашка — красный текст с чёрной обводкой и без фона, с анимацией."""
    css = (await client.get("/static/css/main.css")).text
    assert ".mog-stamp" in css
    assert "-webkit-text-stroke" in css
    assert "mog-stamp-in" in css and "mog-stamp-shake" in css
    # Фона у плашки быть не должно.
    stamp_block = css[css.index(".mog-stamp {") : css.index(".mog-stamp {") + 400]
    assert "background" not in stamp_block


async def test_mogged_stamp_marks_the_loser(client: AsyncClient) -> None:
    """Плашка ставится на проигравшего, а не на победителя."""
    js = (await client.get("/static/js/mogbattles.js")).text
    assert "mog-stamp" in js
    assert "'lost'" in js


@pytest.fixture
async def seeded_photo(client: AsyncClient, db_session) -> int:
    """Фото с 106 точками — по нему студия отдаёт 16 шагов разметки."""
    import numpy as np
    from app.models.photo import Photo
    from app.models.user import User
    from sqlalchemy import select

    await _register(client)
    user = (await db_session.execute(select(User))).scalar_one()
    rng = np.random.default_rng(9)
    points = rng.uniform(0.2, 0.8, (106, 2)).tolist()
    photo = Photo(
        user_id=user.id,
        storage_path=f"users/{user.id}/studio.png",
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
    return photo.id


async def test_studio_mesh_and_preview_pipeline(client: AsyncClient, seeded_photo: int) -> None:
    """Полный путь студии: 16 точек -> меш -> сохранение -> предпросмотр.

    Это то, что делает шаг 3 в браузере: POST /wizard восстанавливает 106 точек,
    PUT /landmarks их сохраняет, POST /preview считает метрики. Если ломается
    любой шаг, мастер «не работает» — именно эта жалоба и была.
    """
    anchors = (await client.get(f"/api/photos/{seeded_photo}/wizard")).json()["anchors"]
    payload = {a["key"]: a["auto"] or a["canonical"] for a in anchors}

    mesh = await client.post(f"/api/photos/{seeded_photo}/wizard", json={"anchors": payload})
    assert mesh.status_code == 200
    points = mesh.json()["points"]
    assert len(points) == 106
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in points)

    saved = await client.put(
        f"/api/photos/{seeded_photo}/landmarks",
        json={"points": points, "provider": "manual"},
    )
    assert saved.status_code == 200
    assert saved.json()["landmarks"]["provider"] == "manual"

    preview = await client.post(f"/api/photos/{seeded_photo}/preview")
    assert preview.status_code == 200
    assert preview.json()["overall"] > 0


async def test_studio_mesh_requires_at_least_five_anchors(
    client: AsyncClient, seeded_photo: int
) -> None:
    """Меньше пяти точек — меш не восстановить: бэкенд честно ругается."""
    resp = await client.post(
        f"/api/photos/{seeded_photo}/wizard",
        json={"anchors": {"left_pupil": [0.3, 0.4], "right_pupil": [0.7, 0.4]}},
    )
    assert resp.status_code == 422
