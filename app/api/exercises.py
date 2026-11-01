"""Роутер упражнений: библиотека, выполнение, прогресс, подбор под отчёт."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.ml.constants import CATEGORY_LABELS, MetricCategory
from app.models.exercise import Exercise, ExerciseCompletion
from app.models.report import Report
from app.schemas.exercises import (
    CompleteRequest,
    CompletionOut,
    ExerciseListOut,
    ExerciseOut,
    ExerciseProgressOut,
    ProgressDayOut,
    RecommendationExercisesOut,
)
from app.services import exercises as exercises_service
from app.services import profile as profile_service

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


def _category_label(category: str) -> str:
    try:
        return CATEGORY_LABELS[MetricCategory(category)]
    except (ValueError, KeyError):
        return {
            "jawline": "Линия челюсти",
            "posture": "Осанка",
            "skin": "Кожа",
            "eyes": "Глаза",
            "symmetry": "Симметрия",
        }.get(category, category)


def _spec_targets(slug: str) -> list[str]:
    spec = next((s for s in exercises_service.EXERCISE_CATALOG if s.slug == slug), None)
    return list(spec.targets) if spec else []


async def _completion_stats(db: DbSession, user_id: int) -> tuple[set[int], dict[int, int], set]:
    """Возвращает (выполненные сегодня id, счётчики по упражнениям, дни)."""
    today = datetime.now(UTC).date()
    today_rows = (
        (
            await db.execute(
                select(ExerciseCompletion.exercise_id).where(
                    ExerciseCompletion.user_id == user_id,
                    ExerciseCompletion.completed_on == today,
                )
            )
        )
        .scalars()
        .all()
    )

    counts = (
        await db.execute(
            select(ExerciseCompletion.exercise_id, func.count())
            .where(ExerciseCompletion.user_id == user_id)
            .group_by(ExerciseCompletion.exercise_id)
        )
    ).all()
    count_map = {int(ex_id): int(cnt) for ex_id, cnt in counts}

    days = await exercises_service.completion_days(db, user_id, days=365)
    return set(today_rows), count_map, days


def _serialize(exercise: Exercise, completed_today: bool, completions: int) -> ExerciseOut:
    return ExerciseOut(
        id=exercise.id,
        slug=exercise.slug,
        title=exercise.title,
        category=exercise.category,
        category_label=_category_label(exercise.category),
        description=exercise.description,
        instructions=exercise.instructions,
        duration_seconds=exercise.duration_seconds,
        difficulty=exercise.difficulty,
        targets=_spec_targets(exercise.slug),
        completed_today=completed_today,
        completions_count=completions,
    )


@router.get("", response_model=ExerciseListOut)
async def list_exercises(
    user: CurrentUser,
    db: DbSession,
    category: str | None = Query(default=None),
) -> ExerciseListOut:
    """Библиотека упражнений с отметками выполнения за сегодня."""
    await exercises_service.seed_exercises(db)
    items = await exercises_service.list_exercises(db, category=category)
    today_ids, counts, _ = await _completion_stats(db, user.id)

    serialized = [_serialize(ex, ex.id in today_ids, counts.get(ex.id, 0)) for ex in items]
    return ExerciseListOut(
        items=serialized,
        total=len(serialized),
        streak=await exercises_service.exercise_streak(db, user.id),
        total_completions=await exercises_service.total_completions(db, user.id),
        by_category=await exercises_service.category_progress(db, user.id),
    )


@router.get("/progress", response_model=ExerciseProgressOut)
async def progress(
    user: CurrentUser, db: DbSession, days: int = Query(default=30, ge=7, le=365)
) -> ExerciseProgressOut:
    """Прогресс по дням для календаря активности."""
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = (
        await db.execute(
            select(ExerciseCompletion.completed_on, func.count())
            .where(
                ExerciseCompletion.user_id == user.id,
                ExerciseCompletion.completed_on >= since,
            )
            .group_by(ExerciseCompletion.completed_on)
        )
    ).all()
    by_day = {day: int(count) for day, count in rows}

    today = datetime.now(UTC).date()
    calendar = [
        ProgressDayOut(
            day=today - timedelta(days=days - 1 - offset),
            completed=by_day.get(today - timedelta(days=days - 1 - offset), 0),
        )
        for offset in range(days)
    ]
    return ExerciseProgressOut(
        days=days,
        streak=await exercises_service.exercise_streak(db, user.id),
        total_completions=await exercises_service.total_completions(db, user.id),
        calendar=calendar,
        by_category=await exercises_service.category_progress(db, user.id),
    )


@router.get("/recommended", response_model=RecommendationExercisesOut)
async def recommended(user: CurrentUser, db: DbSession) -> RecommendationExercisesOut:
    """Подборка упражнений под зоны роста из последнего отчёта."""
    await exercises_service.seed_exercises(db)
    report = (
        await db.execute(
            select(Report)
            .where(Report.user_id == user.id)
            .order_by(Report.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    growth_keys: set[str] = set()
    if report is not None:
        from app.schemas.reports import ReportOut  # noqa: F401  (документирует связь)

        growth_keys = {
            item.get("key")
            for item in (report.recommendations or {}).get("items", [])
            if item.get("key")
        }

    all_exercises = await exercises_service.list_exercises(db)
    picked = exercises_service.recommend_by_targets(all_exercises, growth_keys, limit=4)
    today_ids, counts, _ = await _completion_stats(db, user.id)

    return RecommendationExercisesOut(
        report_id=report.id if report else None,
        growth_keys=sorted(growth_keys),
        items=[_serialize(ex, ex.id in today_ids, counts.get(ex.id, 0)) for ex in picked],
    )


@router.get("/{slug}", response_model=ExerciseOut)
async def get_exercise(slug: str, user: CurrentUser, db: DbSession) -> ExerciseOut:
    """Карточка упражнения по slug."""
    await exercises_service.seed_exercises(db)
    exercise = await exercises_service.get_exercise(db, slug)
    if exercise is None or not exercise.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Упражнение не найдено")

    today_ids, counts, _ = await _completion_stats(db, user.id)
    return _serialize(exercise, exercise.id in today_ids, counts.get(exercise.id, 0))


@router.post("/{slug}/complete", response_model=CompletionOut)
async def complete(
    slug: str, payload: CompleteRequest, user: CurrentUser, db: DbSession
) -> CompletionOut:
    """Отмечает выполнение упражнения за сегодня и обновляет активность."""
    await exercises_service.seed_exercises(db)
    exercise = await exercises_service.get_exercise(db, slug)
    if exercise is None or not exercise.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Упражнение не найдено")

    completion, created = await exercises_service.complete_exercise(
        db, user.id, exercise, note=payload.note
    )
    await profile_service.touch_activity(db, user)
    stats = await profile_service.collect_stats(db, user)

    from app.services import achievements as achievements_service

    await achievements_service.award_achievements(
        db, user, profile_service.stats_to_achievement_stats(stats)
    )

    return CompletionOut(
        id=completion.id,
        exercise_id=completion.exercise_id,
        completed_on=completion.completed_on,
        note=completion.note,
        created=created,
        streak=await exercises_service.exercise_streak(db, user.id),
        total_completions=await exercises_service.total_completions(db, user.id),
    )
