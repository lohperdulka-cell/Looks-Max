"""Роутер профиля: статистика, прогресс, до/после, достижения, обновление."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, DbSession
from app.models.report import MetricValue
from app.models.user import User
from app.schemas.profile import (
    AchievementOut,
    BeforeAfterOut,
    CompareMetric,
    ProfileOut,
    ProfileStatsOut,
    ProfileUpdateRequest,
    ProgressOut,
    ProgressPointOut,
)
from app.services import achievements as achievements_service
from app.services import profile as profile_service

router = APIRouter(prefix="/api/profile", tags=["profile"])


async def _build_stats(db: AsyncSession, user: User) -> ProfileStatsOut:
    stats = await profile_service.collect_stats(db, user)
    wins, losses = await profile_service.battle_record(db, user.id)
    return ProfileStatsOut(
        photos_count=stats.photos_count,
        reports_count=stats.reports_count,
        exercises_count=stats.exercises_count,
        battle_votes=stats.battle_votes,
        best_score=stats.best_score,
        latest_score=stats.latest_score,
        average_score=stats.average_score,
        improvement=stats.improvement,
        streak_days=stats.streak_days,
        elo=stats.elo,
        public_reports=stats.public_reports,
        wins=wins,
        losses=losses,
    )


@router.get("", response_model=ProfileOut)
async def get_profile(user: CurrentUser, db: DbSession) -> ProfileOut:
    """Профиль текущего пользователя со статистикой."""
    return ProfileOut(
        username=user.username,
        display_name=user.display_name,
        gender=user.gender,
        birth_year=user.birth_year,
        created_at=user.created_at,
        elo=user.elo,
        streak_days=user.streak_days,
        show_in_battles=user.show_in_battles,
        stats=await _build_stats(db, user),
    )


@router.patch("", response_model=ProfileOut)
async def update_profile(
    payload: ProfileUpdateRequest, user: CurrentUser, db: DbSession
) -> ProfileOut:
    """Обновляет профиль. Отметка активности обновляет streak."""
    changed = False
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None
        changed = True
    if payload.gender is not None:
        user.gender = payload.gender
        changed = True
    if payload.birth_year is not None:
        user.birth_year = payload.birth_year
        changed = True
    if payload.show_in_battles is not None:
        user.show_in_battles = payload.show_in_battles
        changed = True

    if changed:
        await db.commit()
        await db.refresh(user)
        await profile_service.touch_activity(db, user)

    return ProfileOut(
        username=user.username,
        display_name=user.display_name,
        gender=user.gender,
        birth_year=user.birth_year,
        created_at=user.created_at,
        elo=user.elo,
        streak_days=user.streak_days,
        show_in_battles=user.show_in_battles,
        stats=await _build_stats(db, user),
    )


@router.get("/progress", response_model=ProgressOut)
async def get_progress(
    user: CurrentUser, db: DbSession, days: int = Query(default=30, ge=7, le=365)
) -> ProgressOut:
    """Ряд прогресса по дням для line-графика."""
    points = await profile_service.progress_series(db, user.id, days=days)
    scored = [p.overall_score for p in points if p.overall_score is not None]
    delta = round(scored[-1] - scored[0], 2) if len(scored) >= 2 else 0.0
    return ProgressOut(
        days=days,
        points=[
            ProgressPointOut(
                day=p.day, overall_score=p.overall_score, reports_count=p.reports_count
            )
            for p in points
        ],
        delta=delta,
    )


@router.get("/before-after", response_model=BeforeAfterOut)
async def get_before_after(user: CurrentUser, db: DbSession) -> BeforeAfterOut:
    """Сравнение первого и последнего отчёта по метрикам."""
    first, last = await profile_service.before_after(db, user.id)
    if first is None or last is None or first.id == last.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нужно минимум два отчёта для сравнения",
        )

    before_rows = (
        (await db.execute(select(MetricValue).where(MetricValue.report_id == first.id)))
        .scalars()
        .all()
    )
    after_rows = (
        (await db.execute(select(MetricValue).where(MetricValue.report_id == last.id)))
        .scalars()
        .all()
    )

    after_map = {row.key: row for row in after_rows}
    metrics: list[CompareMetric] = []
    for row in before_rows:
        other = after_map.get(row.key)
        if other is None:
            continue
        metrics.append(
            CompareMetric(
                key=row.key,
                label=row.label,
                before=round(row.score, 2),
                after=round(other.score, 2),
                delta=round(other.score - row.score, 2),
            )
        )
    metrics.sort(key=lambda m: m.delta, reverse=True)

    return BeforeAfterOut(
        before_report_id=first.id,
        after_report_id=last.id,
        before_date=first.created_at,
        after_date=last.created_at,
        before_overall=first.overall_score,
        after_overall=last.overall_score,
        overall_delta=round(last.overall_score - first.overall_score, 2),
        metrics=metrics,
    )


@router.get("/achievements", response_model=list[AchievementOut])
async def list_achievements(user: CurrentUser, db: DbSession) -> list[AchievementOut]:
    """Все достижения: полученные и доступные, с прогрессом выполнения."""
    stats = await profile_service.collect_stats(db, user)
    achievement_stats = profile_service.stats_to_achievement_stats(stats)

    earned_rows = await achievements_service.list_user_achievements(db, user.id)
    earned = {row.code: awarded_at for row, awarded_at in earned_rows}

    out: list[AchievementOut] = []
    for spec in achievements_service.catalog_preview():
        awarded_at = earned.get(spec["code"])
        out.append(
            AchievementOut(
                code=spec["code"],
                title=spec["title"],
                description=spec["description"],
                icon=spec["icon"],
                threshold=spec["threshold"],
                awarded_at=awarded_at,
                earned=awarded_at is not None,
                progress=achievements_service.progress_ratio(spec["code"], achievement_stats),
            )
        )
    out.sort(key=lambda a: (not a.earned, a.code))
    return out


@router.post("/achievements/sync", response_model=list[AchievementOut])
async def sync_achievements(user: CurrentUser, db: DbSession) -> list[AchievementOut]:
    """Пересчитывает и выдаёт достижения по текущей статистике."""
    stats = await profile_service.collect_stats(db, user)
    await achievements_service.award_achievements(
        db, user, profile_service.stats_to_achievement_stats(stats)
    )
    return await list_achievements(user, db)
