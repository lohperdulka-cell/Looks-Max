"""Тесты этапа 8: админка — доступ, дашборд, юзеры, модерация, веса, контент, логи."""

from __future__ import annotations

import pytest
from app.models.content import Feedback, MetricWeight
from app.models.user import User
from app.services import admin as admin_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select


async def _register_via_new_client(username: str, db) -> User:
    """Регистрирует пользователя отдельным клиентом (не трогая cookie текущего)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as fresh:
        return await _register(fresh, username, db=db)


async def _register(client: AsyncClient, username: str, admin: bool = False, db=None) -> User:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201
    user = (await db.execute(select(User).where(User.username == username))).scalar_one()
    if admin:
        user.is_admin = True
        await db.commit()
        await db.refresh(user)
    return user


@pytest.fixture
async def admin_client(client: AsyncClient, db_session):
    """Клиент, залогиненный как администратор."""
    await _register(client, "boss", admin=True, db=db_session)
    return client


@pytest.fixture
async def plain_client(client: AsyncClient, db_session):
    """Клиент обычного пользователя (для проверки запретов)."""
    await _register(client, "pleb", db=db_session)
    return client


async def test_admin_endpoints_require_auth(client: AsyncClient) -> None:
    for path in ("/api/admin/dashboard", "/api/admin/users", "/api/admin/logs"):
        resp = await client.get(path)
        assert resp.status_code == 401


async def test_admin_endpoints_forbid_non_admin(plain_client: AsyncClient) -> None:
    for path in ("/api/admin/dashboard", "/api/admin/users", "/api/admin/metric-weights"):
        resp = await plain_client.get(path)
        assert resp.status_code == 403


async def test_admin_page_redirects_non_admin(plain_client: AsyncClient) -> None:
    resp = await plain_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


async def test_admin_page_renders_for_admin(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/admin")
    assert resp.status_code == 200
    assert "Админка" in resp.text
    assert "/static/js/admin.js" in resp.text


async def test_dashboard_counts_reflect_data(admin_client: AsyncClient, db_session) -> None:
    await _register_via_new_client("extra_user", db_session)
    resp = await admin_client.get("/api/admin/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["users_total"] == 2
    assert body["users_new_7d"] == 2
    assert body["users_banned"] == 0
    assert body["reports_total"] == 0


async def test_activity_series_length_and_fields(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/api/admin/activity?days=14")
    assert resp.status_code == 200
    points = resp.json()
    assert len(points) == 14
    assert {"day", "users", "reports"} <= set(points[0])
    assert points[-1]["users"] >= 1


async def test_users_search_filters(admin_client: AsyncClient, db_session) -> None:
    await _register_via_new_client("findme", db_session)
    found = (await admin_client.get("/api/admin/users?query=findme")).json()
    assert found["total"] == 1
    assert found["items"][0]["username"] == "findme"

    empty = (await admin_client.get("/api/admin/users?query=zzzz-nobody")).json()
    assert empty["total"] == 0


async def test_user_ban_and_unban_writes_log(admin_client: AsyncClient, db_session) -> None:
    victim = await _register_via_new_client("victim", db_session)

    banned = await admin_client.patch(
        f"/api/admin/users/{victim.id}",
        json={"is_banned": True, "ban_reason": "спам"},
    )
    assert banned.status_code == 200
    assert banned.json()["is_banned"] is True

    logs = (await admin_client.get("/api/admin/logs")).json()
    entry = next(row for row in logs if row["action"] == "user.update")
    assert entry["target_id"] == victim.id
    assert entry["details"]["ban_reason"] == "спам"

    unbanned = await admin_client.patch(f"/api/admin/users/{victim.id}", json={"is_banned": False})
    assert unbanned.json()["is_banned"] is False


async def test_admin_cannot_demote_self(admin_client: AsyncClient, db_session) -> None:
    admin = (await db_session.execute(select(User).where(User.username == "boss"))).scalar_one()
    resp = await admin_client.patch(f"/api/admin/users/{admin.id}", json={"is_admin": False})
    assert resp.status_code == 409


async def test_update_unknown_user_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.patch("/api/admin/users/999999", json={"is_banned": True})
    assert resp.status_code == 404


async def test_photo_moderation_hide_and_restore(admin_client: AsyncClient, db_session) -> None:
    from datetime import UTC, datetime

    from app.models.photo import Photo

    owner = (await db_session.execute(select(User).where(User.username == "boss"))).scalar_one()
    photo = Photo(
        user_id=owner.id,
        storage_path="photos/a.jpg",
        original_filename="a.jpg",
        content_type="image/jpeg",
        size_bytes=1024,
        is_public_in_battles=True,
        is_hidden=False,
        created_at=datetime.now(UTC),
    )
    db_session.add(photo)
    await db_session.commit()
    await db_session.refresh(photo)

    listing = (await admin_client.get("/api/admin/photos")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["username"] == "boss"

    hidden = await admin_client.post(f"/api/admin/photos/{photo.id}/hide?hidden=true")
    assert hidden.status_code == 200
    assert hidden.json()["is_hidden"] is True
    # Скрытие снимает участие в батлах.
    assert hidden.json()["is_public_in_battles"] is False

    restored = await admin_client.post(f"/api/admin/photos/{photo.id}/hide?hidden=false")
    assert restored.json()["is_hidden"] is False


async def test_hide_unknown_photo_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.post("/api/admin/photos/424242/hide")
    assert resp.status_code == 404


async def test_metric_weight_upsert_requires_known_key(admin_client: AsyncClient) -> None:
    resp = await admin_client.put(
        "/api/admin/metric-weights", json={"key": "no_such_metric", "weight": 1.0}
    )
    assert resp.status_code == 422


async def test_metric_weight_upsert_and_delete(admin_client: AsyncClient, db_session) -> None:
    from app.ml.constants import METRIC_SPECS

    key = METRIC_SPECS[0].key
    created = await admin_client.put(
        "/api/admin/metric-weights",
        json={"key": key, "weight": 2.5, "note": "важнее прочего", "is_enabled": True},
    )
    assert created.status_code == 200
    assert created.json()["weight"] == 2.5

    # Повторный upsert обновляет, а не дублирует.
    updated = await admin_client.put(
        "/api/admin/metric-weights", json={"key": key, "weight": 3.5, "is_enabled": True}
    )
    assert updated.json()["weight"] == 3.5
    rows = (await db_session.execute(select(MetricWeight))).scalars().all()
    assert len(rows) == 1

    listed = (await admin_client.get("/api/admin/metric-weights")).json()
    assert len(listed) == 1

    deleted = await admin_client.delete(f"/api/admin/metric-weights/{key}")
    assert deleted.status_code == 204
    assert (await admin_client.get("/api/admin/metric-weights")).json() == []


async def test_weight_overrides_reach_scoring(admin_client: AsyncClient, db_session) -> None:
    """Переопределение веса из админки видно сервису расчёта отчёта."""
    from app.ml.constants import METRIC_SPECS

    key = METRIC_SPECS[0].key
    await admin_client.put(
        "/api/admin/metric-weights", json={"key": key, "weight": 4.0, "is_enabled": True}
    )
    overrides = await admin_service.active_weight_overrides(db_session)
    assert overrides[key] == 4.0

    # Отключённое переопределение не попадает в активные.
    await admin_client.put(
        "/api/admin/metric-weights", json={"key": key, "weight": 4.0, "is_enabled": False}
    )
    assert await admin_service.active_weight_overrides(db_session) == {}


async def test_delete_unknown_weight_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.delete("/api/admin/metric-weights/nope")
    assert resp.status_code == 404


async def test_feedback_listing_and_processing(admin_client: AsyncClient, db_session) -> None:
    from datetime import UTC, datetime

    db_session.add(
        Feedback(
            subject="Не грузится фото",
            message="При загрузке HEIC ошибка",
            email="user@example.com",
            is_processed=False,
            created_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    rows = (await admin_client.get("/api/admin/feedback")).json()
    assert len(rows) == 1
    assert rows[0]["is_processed"] is False

    unread = (await admin_client.get("/api/admin/feedback?only_unread=true")).json()
    assert len(unread) == 1

    processed = await admin_client.post(f"/api/admin/feedback/{rows[0]['id']}/process")
    assert processed.status_code == 200
    assert processed.json()["is_processed"] is True
    assert processed.json()["processed_at"] is not None

    assert (await admin_client.get("/api/admin/feedback?only_unread=true")).json() == []


async def test_process_unknown_feedback_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.post("/api/admin/feedback/98765/process")
    assert resp.status_code == 404


async def test_article_crud_and_unique_slugs(admin_client: AsyncClient) -> None:
    payload = {
        "title": "Как читать отчёт",
        "summary": "Коротко о метриках",
        "body_md": "# Заголовок\n\nТекст статьи.",
        "is_published": True,
    }
    first = await admin_client.post("/api/admin/articles", json=payload)
    assert first.status_code == 201
    assert first.json()["slug"] == "kak-chitat-otchet"
    assert first.json()["is_published"] is True

    # Второй раз тот же заголовок — slug получает суффикс.
    second = await admin_client.post("/api/admin/articles", json=payload)
    assert second.json()["slug"] == "kak-chitat-otchet-2"

    listed = (await admin_client.get("/api/admin/articles")).json()
    assert len(listed) == 2

    article_id = first.json()["id"]
    updated = await admin_client.patch(
        f"/api/admin/articles/{article_id}",
        json={**payload, "title": "Обновлённый заголовок", "is_published": False},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Обновлённый заголовок"
    assert updated.json()["is_published"] is False

    deleted = await admin_client.delete(f"/api/admin/articles/{article_id}")
    assert deleted.status_code == 204
    assert len((await admin_client.get("/api/admin/articles")).json()) == 1


async def test_article_validation_rejects_empty_body(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/admin/articles", json={"title": "Заголовок", "body_md": ""}
    )
    assert resp.status_code == 422


async def test_update_unknown_article_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.patch(
        "/api/admin/articles/55555", json={"title": "Нормальный заголовок", "body_md": "y"}
    )
    assert resp.status_code == 404


async def test_faq_crud(admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        "/api/admin/faq",
        json={
            "question": "Как загрузить фото?",
            "answer_md": "Откройте раздел Оценка.",
            "category": "upload",
            "sort_order": 2,
        },
    )
    assert created.status_code == 201
    assert created.json()["sort_order"] == 2

    item_id = created.json()["id"]
    updated = await admin_client.patch(
        f"/api/admin/faq/{item_id}",
        json={
            "question": "Как загрузить фото?",
            "answer_md": "Через кнопку «Загрузить».",
            "category": "upload",
            "sort_order": 1,
            "is_active": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["sort_order"] == 1
    assert updated.json()["is_active"] is False

    assert (await admin_client.delete(f"/api/admin/faq/{item_id}")).status_code == 204
    assert (await admin_client.get("/api/admin/faq")).json() == []


async def test_errors_endpoint_lists_saved_errors(admin_client: AsyncClient, db_session) -> None:
    await admin_service.log_error(db_session, "error", "Бум", path="/api/x", traceback_text="tb")
    rows = (await admin_client.get("/api/admin/errors")).json()
    assert len(rows) == 1
    assert rows[0]["message"] == "Бум"
    assert rows[0]["path"] == "/api/x"


async def test_log_error_truncates_long_payload(db_session) -> None:
    entry = await admin_service.log_error(
        db_session, "error", "x" * 5000, traceback_text="y" * 20000
    )
    assert len(entry.message) == 4000
    assert len(entry.traceback) == 8000
