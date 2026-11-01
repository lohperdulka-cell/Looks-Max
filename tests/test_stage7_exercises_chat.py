"""Тесты этапа 7: упражнения (библиотека, streak, прогресс) и ИИ-чат (SSE, план)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.models.exercise import Exercise, ExerciseCompletion
from app.models.user import User
from app.services import ai as ai_service
from app.services import exercises as exercises_service
from httpx import AsyncClient
from sqlalchemy import select


def _sse_text(raw: str) -> str:
    """Склеивает текстовые дельты из SSE-потока в единый ответ."""
    chunks: list[str] = []
    for block in raw.split("\n\n"):
        if "event: delta" not in block:
            continue
        data_line = next(
            (line for line in block.split("\n") if line.startswith("data: ")),
            None,
        )
        if data_line:
            chunks.append(json.loads(data_line[6:]).get("text", ""))
    return "".join(chunks)


async def _register(client: AsyncClient, username: str) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": username, "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201


@pytest.fixture
async def user_client(client: AsyncClient):
    """Клиент, залогиненный как обычный пользователь."""
    await _register(client, "ex_user")
    return client


def test_catalog_is_valid() -> None:
    """Каталог непустой, slug уникальны, у каждого упражнения есть метрики."""
    slugs = [spec.slug for spec in exercises_service.EXERCISE_CATALOG]
    assert len(slugs) == len(set(slugs))
    assert len(slugs) >= 8
    for spec in exercises_service.EXERCISE_CATALOG:
        assert spec.title and spec.description and spec.instructions
        assert spec.duration_seconds > 0
        assert spec.targets, f"{spec.slug}: нет целевых метрик"


async def test_seed_is_idempotent(db_session) -> None:
    first = await exercises_service.seed_exercises(db_session)
    second = await exercises_service.seed_exercises(db_session)
    assert first == len(exercises_service.EXERCISE_CATALOG)
    assert second == 0


async def test_list_exercises_seeds_and_returns_stats(user_client, db_session) -> None:
    resp = await user_client.get("/api/exercises")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == len(exercises_service.EXERCISE_CATALOG)
    assert body["streak"] == 0
    assert body["total_completions"] == 0
    assert all(item["category_label"] for item in body["items"])
    assert any(item["targets"] for item in body["items"])


async def test_filter_by_category(user_client) -> None:
    resp = await user_client.get("/api/exercises?category=jawline")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert all(item["category"] == "jawline" for item in items)


async def test_complete_exercise_updates_streak(user_client) -> None:
    slug = exercises_service.EXERCISE_CATALOG[0].slug
    resp = await user_client.post(f"/api/exercises/{slug}/complete", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["streak"] == 1
    assert body["total_completions"] == 1

    again = await user_client.post(f"/api/exercises/{slug}/complete", json={})
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["total_completions"] == 1


async def test_complete_unknown_exercise(user_client) -> None:
    resp = await user_client.post("/api/exercises/no-such-slug/complete", json={})
    assert resp.status_code == 404


async def test_get_exercise_detail(user_client) -> None:
    slug = exercises_service.EXERCISE_CATALOG[1].slug
    resp = await user_client.get(f"/api/exercises/{slug}")
    assert resp.status_code == 200
    assert resp.json()["instructions"]


async def test_progress_calendar_shape(user_client) -> None:
    slug = exercises_service.EXERCISE_CATALOG[0].slug
    await user_client.post(f"/api/exercises/{slug}/complete", json={})
    resp = await user_client.get("/api/exercises/progress?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["calendar"]) == 30
    assert body["calendar"][-1]["completed"] == 1
    assert body["streak"] == 1


async def test_streak_counts_consecutive_days(db_session) -> None:
    user = User(
        username="streaky",
        password_hash="x",
        biometric_consent_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    await exercises_service.seed_exercises(db_session)
    exercise = (await db_session.execute(select(Exercise).limit(1))).scalar_one()

    today = datetime.now(UTC).date()
    for offset in range(3):
        db_session.add(
            ExerciseCompletion(
                user_id=user.id,
                exercise_id=exercise.id,
                completed_on=today - timedelta(days=offset),
            )
        )
    await db_session.commit()

    assert await exercises_service.exercise_streak(db_session, user.id) == 3

    # Разрыв в серии обрывает счёт.
    db_session.add(
        ExerciseCompletion(
            user_id=user.id,
            exercise_id=exercise.id,
            completed_on=today - timedelta(days=5),
        )
    )
    await db_session.commit()
    assert await exercises_service.exercise_streak(db_session, user.id) == 3


async def test_streak_zero_when_last_activity_older(user_client, db_session) -> None:
    slug = exercises_service.EXERCISE_CATALOG[0].slug
    await user_client.post(f"/api/exercises/{slug}/complete", json={})
    user = (await db_session.execute(select(User))).scalars().first()

    rows = (
        (
            await db_session.execute(
                select(ExerciseCompletion).where(ExerciseCompletion.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.completed_on = datetime.now(UTC).date() - timedelta(days=4)
    await db_session.commit()

    assert await exercises_service.exercise_streak(db_session, user.id) == 0


def test_recommend_by_targets_prefers_overlap(db_session) -> None:
    class _Ex:
        def __init__(self, slug: str, title: str) -> None:
            self.slug = slug
            self.title = title

    jaw = _Ex("jaw-resistance", "Жевать")
    skin = _Ex("sun-and-skincare", "SPF")
    result = exercises_service.recommend_by_targets([skin, jaw], {"gonial_angle"}, limit=1)
    assert result[0].slug == "jaw-resistance"


def test_recommend_falls_back_when_no_overlap() -> None:
    class _Ex:
        def __init__(self, slug: str) -> None:
            self.slug = slug
            self.title = slug

    items = [_Ex("a"), _Ex("b")]
    assert exercises_service.recommend_by_targets(items, set(), limit=2) == items


async def test_recommended_endpoint_without_report(user_client) -> None:
    resp = await user_client.get("/api/exercises/recommended")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] is None
    assert body["items"]


async def test_chat_meta_lists_quick_prompts(user_client) -> None:
    resp = await user_client.get("/api/chat/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_enabled"] is False
    assert len(body["quick_prompts"]) >= 3
    assert all(p["title"] and p["prompt"] for p in body["quick_prompts"])


async def test_chat_session_created_and_empty(user_client) -> None:
    resp = await user_client.get("/api/chat/session")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] > 0
    assert body["messages"] == []


async def test_chat_stream_emits_sse_events(user_client) -> None:
    resp = await user_client.post(
        "/api/chat/stream", json={"message": "Что улучшить в первую очередь?"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    raw = resp.text
    assert "event: meta" in raw
    assert "event: delta" in raw
    assert "event: done" in raw
    assert "У вас пока нет сохранённого отчёта" in _sse_text(raw)


async def test_chat_stream_persists_messages(user_client) -> None:
    await user_client.post("/api/chat/stream", json={"message": "Привет, что делать?"})
    session = (await user_client.get("/api/chat/session")).json()
    roles = [m["role"] for m in session["messages"]]
    assert roles.count("user") == 1
    assert roles.count("assistant") == 1
    assert "Привет, что делать?" in session["messages"][0]["content"]


async def test_chat_stream_plan_keyword_produces_weeks(user_client) -> None:
    resp = await user_client.post("/api/chat/stream", json={"message": "Составь план на 30 дней"})
    answer = _sse_text(resp.text)
    assert "Неделя 1" in answer
    assert "Неделя 4" in answer


async def test_chat_session_ownership_enforced(client: AsyncClient) -> None:
    await _register(client, "owner_a")
    first = (await client.get("/api/chat/session")).json()
    await client.post("/api/auth/logout")

    await _register(client, "owner_b")
    resp = await client.post(
        "/api/chat/stream", json={"message": "чужой диалог", "session_id": first["id"]}
    )
    assert resp.status_code == 404


async def test_generate_plan_saved_and_listed(user_client) -> None:
    resp = await user_client.post("/api/chat/plans", json={"horizon_days": 30})
    assert resp.status_code == 201
    plan = resp.json()
    assert plan["horizon_days"] == 30
    assert "Неделя 1" in plan["content_md"]
    assert plan["model"] == "offline"

    listed = await user_client.get("/api/chat/plans")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


async def test_plan_horizon_is_recorded(user_client) -> None:
    resp = await user_client.post("/api/chat/plans", json={"horizon_days": 60})
    assert resp.status_code == 201
    assert resp.json()["horizon_days"] == 60
    assert "Горизонт: 60 дней" in resp.json()["content_md"]


async def test_plan_horizon_validation(user_client) -> None:
    resp = await user_client.post("/api/chat/plans", json={"horizon_days": 3})
    assert resp.status_code == 422


async def test_pages_redirect_guests(client: AsyncClient) -> None:
    for path in ("/exercises", "/chat"):
        resp = await client.get(path, follow_redirects=False)
        assert resp.status_code == 302


def test_offline_answer_without_report_mentions_upload() -> None:
    answer = ai_service.build_offline_answer("что делать?", ai_service.ReportContext(), [])
    assert "нет сохранённого отчёта" in answer


def test_offline_answer_uses_report_context() -> None:
    context = ai_service.ReportContext(
        report_id=7,
        overall=6.2,
        harmony=5.1,
        strengths=[{"label": "Симметрия", "score": 8.4, "key": "facial_symmetry"}],
        growth=[{"label": "Пропорции", "score": 3.1, "key": "facial_thirds"}],
        recommendations=[
            {
                "label": "Пропорции",
                "text": "Выровняйте вертикаль кадра.",
                "actions": ["Снимайте анфас", "Держите камеру на уровне глаз"],
            }
        ],
    )
    answer = ai_service.build_offline_answer("что делать?", context, [])
    assert "6.2/10" in answer
    assert "Симметрия" in answer
    assert "Пропорции" in answer
    assert "Снимайте анфас" in answer


def test_tokenizer_splits_text() -> None:
    tokens = ai_service._tokenize("слово " * 40, size=10)
    assert len(tokens) > 1
    assert "".join(tokens).strip().startswith("слово")


def test_json_event_format() -> None:
    event = ai_service.json_event("delta", text="привет")
    assert event.startswith("event: delta\n")
    payload = json.loads(event.split("data: ", 1)[1].strip())
    assert payload == {"text": "привет"}
